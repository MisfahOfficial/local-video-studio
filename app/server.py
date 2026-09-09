from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
import sys
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .config import SettingsStore
from .database import Database
from .generation import GenerationManager
from .gemini_analyzer import GeminiSceneEnhancer
from .paths import AppPaths
from .scene_planner import RuleBasedScenePlanner, estimate_generation_count, validate_plan_inputs
from .themes import get_theme, list_themes
from .timeline import RenderManager, build_default_motion_registry
from .transcription import probe_duration


DEFAULT_CAPTION_STYLE = {
    "font": "Arial",
    "size": 54,
    "position": "bottom",
    "text_color": "#FFFFFF",
    "background_color": "#000000",
    "background_opacity": 0.72,
}
CAPTION_FONTS = {"Arial", "Helvetica", "Verdana", "Georgia"}


def normalize_caption_style(value: Any) -> dict[str, Any]:
    supplied = value if isinstance(value, dict) else {}
    style = {**DEFAULT_CAPTION_STYLE, **supplied}
    font = str(style.get("font") or "Arial")
    if font not in CAPTION_FONTS:
        raise ApiError("Unknown caption font")
    try:
        size = int(style.get("size", 54))
        opacity = float(style.get("background_opacity", 0.72))
    except (TypeError, ValueError) as error:
        raise ApiError("Caption size and background opacity must be valid numbers") from error
    if not 16 <= size <= 96:
        raise ApiError("Caption size must be between 16 and 96")
    if not 0 <= opacity <= 1:
        raise ApiError("Caption background opacity must be between 0 and 1")
    position = str(style.get("position") or "bottom")
    if position not in {"top", "middle", "bottom"}:
        raise ApiError("Unknown caption position")
    colors: dict[str, str] = {}
    for key in ("text_color", "background_color"):
        color = str(style.get(key) or DEFAULT_CAPTION_STYLE[key]).upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise ApiError(f"Invalid {key.replace('_', ' ')}")
        colors[key] = color
    return {
        "font": font,
        "size": size,
        "position": position,
        **colors,
        "background_opacity": opacity,
    }


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class StudioApplication:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self.db = Database(paths.database)
        self.settings = SettingsStore(paths.settings)
        self.generation = GenerationManager(self.db, paths, self.settings)
        self.rendering = RenderManager(self.db, paths, self.settings)
        self.planner = RuleBasedScenePlanner()

    def project_payload(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project:
            raise ApiError("Project not found", HTTPStatus.NOT_FOUND)
        project = dict(project)
        project["caption_style"] = normalize_caption_style(project.get("caption_style"))
        project["voiceover_media_url"] = ""
        if project.get("voiceover_path"):
            try:
                relative = Path(str(project["voiceover_path"])).resolve().relative_to(
                    self.paths.project_dir(project_id).resolve()
                )
                project["voiceover_media_url"] = f"/media/{project_id}/{urllib.parse.quote(relative.as_posix())}"
            except ValueError:
                pass
        scenes = self.db.list_scenes(project_id)
        assets = self.asset_payloads(project_id)
        warnings = validate_plan_inputs(
            str(project.get("script") or ""),
            duration_seconds=float(project.get("duration_seconds") or 0),
            target_scene_count=int(project.get("requested_scene_count") or project.get("target_scene_count") or 0),
            actual_scene_count=len(scenes) if scenes else None,
        ) if project.get("script") else []
        return {"project": project, "scenes": scenes, "assets": assets, "warnings": warnings}

    def asset_payloads(self, project_id: str) -> list[dict[str, Any]]:
        assets = self.db.list_assets(project_id)
        project_dir = self.paths.project_dir(project_id).resolve()
        payloads: list[dict[str, Any]] = []
        for asset in assets:
            local = Path(asset["local_path"]).resolve()
            try:
                relative = local.relative_to(project_dir)
                media_url = f"/media/{project_id}/{urllib.parse.quote(relative.as_posix())}"
            except ValueError:
                media_url = ""
            payloads.append({**asset, "media_url": media_url})
        return payloads

    def render_payload(self, project_id: str) -> dict[str, Any] | None:
        render = self.db.latest_render_job(project_id)
        if not render:
            return None
        output_path = render.get("output_path")
        if output_path:
            project_dir = self.paths.project_dir(project_id).resolve()
            try:
                relative = Path(str(output_path)).resolve().relative_to(project_dir)
                render["media_url"] = f"/media/{project_id}/{urllib.parse.quote(relative.as_posix())}"
            except ValueError:
                render["media_url"] = ""
        return render

    def open_project_folder(self, project_id: str, kind: str) -> Path:
        if not self.db.get_project(project_id):
            raise ApiError("Project not found", HTTPStatus.NOT_FOUND)
        project_dir = self.paths.project_dir(project_id).resolve()
        target = project_dir / "renders" if kind == "renders" else project_dir
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            command = ["open", str(target)]
        elif sys.platform == "win32":
            command = ["explorer", str(target)]
        else:
            command = ["xdg-open", str(target)]
        executable = command[0]
        if sys.platform != "win32" and not shutil.which(executable):
            raise ApiError(f"Cannot open the folder automatically because {executable} is unavailable")
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return target

    def plan_project(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project:
            raise ApiError("Project not found", HTTPStatus.NOT_FOUND)
        script = str(body.get("script") or project.get("script") or "").strip()
        if not script:
            raise ApiError("Paste a script before creating the visual plan")
        theme_id = str(body.get("theme_id") or project.get("theme_id") or "us_nostalgia")
        try:
            duration = float(body.get("duration_seconds") or project.get("duration_seconds") or 0)
            image_count = int(body.get("image_count") or 0) or None
            seconds_per_scene = float(body.get("seconds_per_scene") or 12.5)
        except (TypeError, ValueError) as error:
            raise ApiError("Duration, target images, and scene seconds must be valid numbers") from error
        if duration < 0:
            raise ApiError("Duration cannot be negative")
        if image_count is not None and not 1 <= image_count <= 3000:
            raise ApiError("Target images must be between 1 and 3000")
        if not 2 <= seconds_per_scene <= 60:
            raise ApiError("Average scene seconds must be between 2 and 60")
        settings = self.settings.load()
        drafts = self.planner.plan(
            script,
            theme_id=theme_id,
            duration_seconds=duration,
            target_scene_count=image_count,
            seconds_per_scene=seconds_per_scene,
        )
        if str(body.get("planner", "local")) == "gemini":
            drafts = GeminiSceneEnhancer(settings.gemini_api_key, settings.gemini_model).enhance(
                drafts, get_theme(theme_id)
            )

        generation_count = estimate_generation_count(drafts)
        warnings = validate_plan_inputs(
            script,
            duration_seconds=duration or drafts[-1].end_seconds,
            target_scene_count=image_count,
            actual_scene_count=len(drafts),
            voiceover_duration=float(project.get("duration_seconds") or 0) if project.get("voiceover_path") else None,
        )
        estimated_cost = generation_count * float(body.get("estimated_unit_cost") or settings.estimated_unit_cost)
        self.db.update_project(
            project_id,
            script=script,
            theme_id=theme_id,
            duration_seconds=duration or drafts[-1].end_seconds,
            requested_scene_count=image_count or len(drafts),
            target_scene_count=len(drafts),
            estimated_cost=estimated_cost,
        )
        scenes = self.db.replace_scenes(project_id, drafts)
        return {
            "scenes": scenes,
            "generation_count": generation_count,
            "estimated_cost": estimated_cost,
            "warnings": warnings,
        }


def build_handler(application: StudioApplication):
    class StudioRequestHandler(BaseHTTPRequestHandler):
        server_version = f"LocalVideoStudio/{__version__}"

        def log_message(self, format_string: str, *args: object) -> None:
            print(f"[{self.log_date_time_string()}] {format_string % args}")

        def do_GET(self) -> None:
            try:
                self._get()
            except ApiError as error:
                self._json({"error": str(error)}, error.status)
            except Exception as error:
                self._json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            try:
                self._post()
            except ApiError as error:
                self._json({"error": str(error)}, error.status)
            except Exception as error:
                self._json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_PATCH(self) -> None:
            try:
                self._patch()
            except ApiError as error:
                self._json({"error": str(error)}, error.status)
            except Exception as error:
                self._json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _get(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/health":
                self._json({
                    "status": "ok",
                    "version": __version__,
                    "schema_version": application.db.schema_version(),
                    "ffmpeg": bool(shutil.which(application.settings.load().ffmpeg_path)),
                })
                return
            if path == "/api/themes":
                self._json({"themes": list_themes(), "motions": build_default_motion_registry().names()})
                return
            if path == "/api/settings":
                self._json(application.settings.load().public_dict())
                return
            if path == "/api/projects":
                self._json({"projects": application.db.list_projects()})
                return

            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)", path)
            if match:
                self._json(application.project_payload(match.group(1)))
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/scenes", path)
            if match:
                self._json({"scenes": application.db.list_scenes(match.group(1))})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/assets", path)
            if match:
                self._json({"assets": application.asset_payloads(match.group(1))})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/generation-status", path)
            if match:
                project_id = match.group(1)
                status = application.db.generation_status(project_id)
                self._json({
                    "counts": {key: status[key] for key in ("pending", "running", "complete", "failed")},
                    "failures": status["failures"],
                    "progress": status["progress"],
                    "running": application.generation.is_running(project_id),
                })
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/render-status", path)
            if match:
                self._json({"render": application.render_payload(match.group(1))})
                return
            match = re.fullmatch(r"/media/([a-zA-Z0-9_-]+)/(.+)", path)
            if match:
                self._serve_media(match.group(1), urllib.parse.unquote(match.group(2)))
                return
            if path == "/":
                self._serve_file(application.paths.static / "index.html")
                return
            if path.startswith("/static/"):
                relative = path.removeprefix("/static/")
                target = (application.paths.static / relative).resolve()
                if application.paths.static.resolve() not in target.parents:
                    raise ApiError("Invalid static path", HTTPStatus.FORBIDDEN)
                self._serve_file(target)
                return
            raise ApiError("Not found", HTTPStatus.NOT_FOUND)

        def _post(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/settings":
                settings = application.settings.update(self._read_json())
                self._json(settings.public_dict())
                return
            if path == "/api/projects":
                body = self._read_json()
                project = application.db.create_project(str(body.get("name", "Untitled project")), str(body.get("theme_id", "us_nostalgia")))
                self._json(project, HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/voiceover", path)
            if match:
                project_id = match.group(1)
                if not application.db.get_project(project_id):
                    raise ApiError("Project not found", HTTPStatus.NOT_FOUND)
                length = self._content_length(maximum=4 * 1024 * 1024 * 1024)
                filename = Path(self.headers.get("X-Filename", "voiceover.mp3")).name
                suffix = Path(filename).suffix.lower() if Path(filename).suffix else ".mp3"
                destination = application.paths.project_dir(project_id) / f"voiceover{suffix}"
                remaining = length
                with destination.open("wb") as file:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        file.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    raise ApiError("Voice-over upload ended before all bytes arrived")
                duration = 0.0
                try:
                    settings = application.settings.load()
                    duration = probe_duration(destination, settings.ffprobe_path)
                except Exception:
                    pass
                project = application.db.update_project(project_id, voiceover_path=str(destination), duration_seconds=duration)
                self._json({"project": project, "filename": filename, "duration_seconds": duration})
                return

            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/plan", path)
            if match:
                self._json(application.plan_project(match.group(1), self._read_json()))
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/caption-style", path)
            if match:
                project_id = match.group(1)
                if not application.db.get_project(project_id):
                    raise ApiError("Project not found", HTTPStatus.NOT_FOUND)
                style = normalize_caption_style(self._read_json())
                application.db.update_project(project_id, caption_style=style)
                self._json({"caption_style": style})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/generate", path)
            if match:
                project_id = match.group(1)
                body = self._read_json()
                scene_ids = self._scene_ids(body.get("scene_ids"))
                queued = application.db.queue_generation(project_id, scene_ids, bool(body.get("force", False)))
                application.generation.start(project_id)
                self._json({"queued": queued})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/retry-failed", path)
            if match:
                project_id = match.group(1)
                body = self._read_json()
                queued = application.db.retry_failed_generation(project_id, self._scene_ids(body.get("scene_ids")))
                if queued:
                    application.generation.start(project_id)
                self._json({"queued": queued})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/scenes/bulk", path)
            if match:
                project_id = match.group(1)
                body = self._read_json()
                scene_ids = self._scene_ids(body.get("scene_ids"))
                changes = body.get("changes")
                if not isinstance(changes, dict):
                    raise ApiError("Bulk changes are required")
                motion = changes.get("motion")
                if motion is not None and motion not in build_default_motion_registry().names():
                    raise ApiError("Unknown motion preset")
                transition = changes.get("transition")
                if transition is not None and transition not in {"fade", "cut"}:
                    raise ApiError("Unknown transition preset")
                try:
                    scenes = application.db.bulk_update_scenes(project_id, scene_ids, changes)
                except (KeyError, ValueError) as error:
                    raise ApiError(str(error)) from error
                if body.get("save_as_default") and scene_ids is None:
                    defaults: dict[str, Any] = {}
                    for source, target in (
                        ("provider", "default_provider"),
                        ("model_role", "default_model_role"),
                        ("candidate_count", "default_candidate_count"),
                        ("motion", "default_motion"),
                        ("transition", "default_transition"),
                    ):
                        if source in changes:
                            defaults[target] = changes[source]
                    if defaults:
                        application.db.update_project(project_id, **defaults)
                all_scenes = application.db.list_scenes(project_id)
                estimated_cost = sum(max(1, int(scene["candidate_count"])) for scene in all_scenes) * application.settings.load().estimated_unit_cost
                application.db.update_project(project_id, estimated_cost=estimated_cost)
                self._json({"updated": len(scenes), "scenes": scenes, "estimated_cost": estimated_cost})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/scenes/reorder", path)
            if match:
                scene_ids = self._scene_ids(self._read_json().get("scene_ids"))
                if scene_ids is None:
                    raise ApiError("scene_ids are required")
                try:
                    scenes = application.db.reorder_scenes(match.group(1), scene_ids)
                except ValueError as error:
                    raise ApiError(str(error)) from error
                self._json({"scenes": scenes})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/open-folder", path)
            if match:
                body = self._read_json()
                kind = str(body.get("kind") or "renders")
                if kind not in {"renders", "project"}:
                    raise ApiError("Folder kind must be renders or project")
                target = application.open_project_folder(match.group(1), kind)
                self._json({"opened": True, "path": str(target)})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/(pause|resume)", path)
            if match:
                project_id, action = match.groups()
                getattr(application.generation, action)(project_id)
                self._json({"status": action + "d"})
                return
            match = re.fullmatch(r"/api/projects/([a-zA-Z0-9_-]+)/render", path)
            if match:
                project_id = match.group(1)
                project = application.db.get_project(project_id)
                if not project:
                    raise ApiError("Project not found", HTTPStatus.NOT_FOUND)
                options = self._read_json()
                style = normalize_caption_style(options.get("caption_style") or project.get("caption_style"))
                options["caption_style"] = style
                application.db.update_project(project_id, caption_style=style)
                job_id = application.rendering.start(project_id, options)
                self._json({"render_job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            match = re.fullmatch(r"/api/scenes/([a-zA-Z0-9_-]+)/asset", path)
            if match:
                scene = application.db.get_scene(match.group(1))
                if not scene:
                    raise ApiError("Scene not found", HTTPStatus.NOT_FOUND)
                length = self._content_length(maximum=2 * 1024 * 1024 * 1024)
                if length == 0:
                    raise ApiError("Choose an image or video file")
                filename = Path(self.headers.get("X-Filename", "replacement.png")).name
                suffix = Path(filename).suffix.lower()
                image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
                video_suffixes = {".mp4", ".mov", ".m4v", ".webm"}
                if suffix not in image_suffixes | video_suffixes:
                    raise ApiError("Use PNG, JPG, WebP, MP4, MOV, M4V, or WebM media")
                media_kind = "video" if suffix in video_suffixes else "image"
                asset_dir = application.paths.project_dir(str(scene["project_id"])) / "assets" / "imports"
                asset_dir.mkdir(parents=True, exist_ok=True)
                destination = asset_dir / f"scene-{int(scene['position']):04d}-{uuid.uuid4().hex[:10]}{suffix}"
                remaining = length
                with destination.open("wb") as file:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        file.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    destination.unlink(missing_ok=True)
                    raise ApiError("Media upload ended before all bytes arrived")
                asset = application.db.add_asset(
                    project_id=str(scene["project_id"]), scene_id=str(scene["id"]),
                    candidate_index=application.db.next_asset_candidate_index(str(scene["id"])),
                    media_kind=media_kind, provider="local", model="uploaded",
                    local_path=str(destination), remote_url=None, provider_asset_id=None,
                    cost=0.0, metadata={"original_filename": filename},
                )
                selected_scene = application.db.select_asset(str(scene["id"]), str(asset["id"]))
                payload = next(
                    item for item in application.asset_payloads(str(scene["project_id"])) if item["id"] == asset["id"]
                )
                self._json({"asset": payload, "scene": selected_scene}, HTTPStatus.CREATED)
                return
            match = re.fullmatch(r"/api/scenes/([a-zA-Z0-9_-]+)/select-asset", path)
            if match:
                body = self._read_json()
                scene = application.db.select_asset(match.group(1), str(body.get("asset_id", "")))
                self._json(scene)
                return
            raise ApiError("Not found", HTTPStatus.NOT_FOUND)

        def _patch(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            match = re.fullmatch(r"/api/scenes/([a-zA-Z0-9_-]+)", path)
            if not match:
                raise ApiError("Not found", HTTPStatus.NOT_FOUND)
            body = self._read_json()
            duration = body.pop("duration_seconds", None)
            caption_text = body.get("caption_text")
            if caption_text is not None and len(str(caption_text)) > 5000:
                raise ApiError("A scene caption cannot exceed 5,000 characters")
            actions = body.get("timeline_actions")
            if actions is not None:
                if not isinstance(actions, list):
                    raise ApiError("Timeline actions must be a list")
                for action in actions:
                    if not isinstance(action, dict) or action.get("type") not in {"motion", "transition"}:
                        raise ApiError("Unknown timeline action")
                    preset = action.get("params", {}).get("preset") if isinstance(action.get("params"), dict) else None
                    if action.get("type") == "motion" and preset not in build_default_motion_registry().names():
                        raise ApiError("Unknown motion preset")
                    if action.get("type") == "transition" and preset not in {"fade", "cut"}:
                        raise ApiError("Unknown transition preset")
            try:
                if duration is not None:
                    application.db.set_scene_duration(match.group(1), float(duration))
                scene = application.db.update_scene(match.group(1), body)
            except KeyError as error:
                raise ApiError(str(error), HTTPStatus.NOT_FOUND) from error
            except (ValueError, TypeError) as error:
                raise ApiError(str(error)) from error
            self._json(scene)

        def _read_json(self) -> dict[str, Any]:
            length = self._content_length(maximum=30 * 1024 * 1024)
            if length == 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ApiError("Request body must be valid JSON") from error
            if not isinstance(data, dict):
                raise ApiError("Request body must be a JSON object")
            return data

        @staticmethod
        def _scene_ids(value: Any) -> list[str] | None:
            if value is None:
                return None
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ApiError("scene_ids must be a list of scene identifiers")
            return value

        def _content_length(self, maximum: int) -> int:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ApiError("Invalid Content-Length header") from error
            if length < 0 or length > maximum:
                raise ApiError("Request is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return length

        def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_media(self, project_id: str, relative: str) -> None:
            project_dir = application.paths.project_dir(project_id).resolve()
            target = (project_dir / relative).resolve()
            if project_dir not in target.parents or not target.is_file():
                raise ApiError("Media not found", HTTPStatus.NOT_FOUND)
            self._serve_file(target)

        def _serve_file(self, path: Path) -> None:
            if not path.is_file():
                raise ApiError("Not found", HTTPStatus.NOT_FOUND)
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            size = path.stat().st_size
            start, end = 0, max(0, size - 1)
            status = HTTPStatus.OK
            range_header = self.headers.get("Range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match:
                    raise ApiError("Invalid media range", HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                elif last:
                    length = min(int(last), size)
                    start = size - length
                    end = size - 1
                if start < 0 or start >= size or end < start:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
            content_length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store" if path.suffix in {".html", ".js", ".css"} else "private, max-age=3600")
            self.end_headers()
            with path.open("rb") as file:
                file.seek(start)
                remaining = content_length
                while remaining:
                    chunk = file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return StudioRequestHandler


def create_server(paths: AppPaths, host: str, port: int) -> ThreadingHTTPServer:
    application = StudioApplication(paths)
    server = ThreadingHTTPServer((host, port), build_handler(application))
    server.daemon_threads = True
    return server
