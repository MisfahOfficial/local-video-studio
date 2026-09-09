from __future__ import annotations

import json
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ..config import SettingsStore
from ..database import Database
from ..paths import AppPaths
from .actions import MotionRegistry, build_default_motion_registry


ProgressCallback = Callable[[float], None]


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_scene_srt(scenes: list[dict[str, Any]], destination: Path) -> None:
    blocks: list[str] = []
    for index, scene in enumerate(scenes, start=1):
        text = str(scene["narration"]).replace("--> ", "→ ").strip()
        blocks.append(
            f"{index}\n{_srt_time(float(scene['start_seconds']))} --> {_srt_time(float(scene['end_seconds']))}\n{text}\n"
        )
    destination.write_text("\n".join(blocks), encoding="utf-8")


def _motion_name(scene: dict[str, Any]) -> str:
    for action in scene.get("timeline_actions") or []:
        if action.get("type") == "motion":
            return str(action.get("params", {}).get("preset", "slow_push"))
    return "slow_push"


def _fade_duration(scene: dict[str, Any]) -> float:
    for action in scene.get("timeline_actions") or []:
        if action.get("type") == "transition" and action.get("params", {}).get("preset") == "fade":
            return max(0.0, min(1.5, float(action.get("params", {}).get("duration", 0.32))))
    return 0.0


def _escape_subtitle_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


class FFmpegRenderer:
    def __init__(self, ffmpeg_path: str = "ffmpeg", motion_registry: MotionRegistry | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.motion_registry = motion_registry or build_default_motion_registry()

    def render(
        self,
        *,
        project: dict[str, Any],
        scenes: list[dict[str, Any]],
        assets: list[dict[str, Any]],
        project_dir: Path,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        burn_captions: bool = True,
        progress: ProgressCallback | None = None,
    ) -> Path:
        selected = {scene["id"]: scene.get("selected_asset_id") for scene in scenes}
        by_id = {asset["id"]: asset for asset in assets}
        missing = [scene["position"] for scene in scenes if not selected.get(scene["id"]) or selected[scene["id"]] not in by_id]
        if missing:
            preview = ", ".join(str(item) for item in missing[:12])
            raise RuntimeError(f"Scenes without selected assets: {preview}{'…' if len(missing) > 12 else ''}")

        render_dir = project_dir / "renders"
        clip_dir = project_dir / "cache" / "clips"
        render_dir.mkdir(parents=True, exist_ok=True)
        clip_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = project_dir / "captions.srt"
        write_scene_srt(scenes, subtitle_path)

        clip_paths: list[Path] = []
        total = max(1, len(scenes))
        encoder = self._choose_encoder()
        for index, scene in enumerate(scenes):
            asset = by_id[selected[scene["id"]]]
            source = Path(asset["local_path"])
            duration = max(0.1, float(scene["end_seconds"]) - float(scene["start_seconds"]))
            clip = clip_dir / f"scene-{int(scene['position']):04d}.mp4"
            self._render_clip(source, clip, str(asset["media_kind"]), duration, scene, width, height, fps, encoder)
            clip_paths.append(clip)
            if progress:
                progress(0.82 * (index + 1) / total)

        concat_file = project_dir / "cache" / "concat.txt"
        concat_file.parent.mkdir(parents=True, exist_ok=True)
        concat_file.write_text(
            "\n".join(f"file '{str(path.resolve()).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for path in clip_paths),
            encoding="utf-8",
        )
        silent_video = project_dir / "cache" / "assembled.mp4"
        self._run([self.ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent_video)])
        if progress:
            progress(0.88)

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        output = render_dir / f"{_safe_name(str(project['name']))}-{timestamp}.mp4"
        voiceover = Path(project["voiceover_path"]) if project.get("voiceover_path") else None
        command = [self.ffmpeg_path, "-y", "-i", str(silent_video)]
        if voiceover and voiceover.exists():
            command += ["-i", str(voiceover)]

        if burn_captions:
            command += ["-vf", f"subtitles='{_escape_subtitle_path(subtitle_path)}'", "-c:v", encoder]
            if encoder == "libx264":
                command += ["-preset", "veryfast", "-crf", "20"]
        else:
            command += ["-c:v", "copy"]
        if voiceover and voiceover.exists():
            command += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        else:
            command += ["-an"]
        command += ["-movflags", "+faststart", str(output)]
        self._run(command)
        if progress:
            progress(1.0)
        return output

    def _render_clip(self, source: Path, destination: Path, media_kind: str, duration: float,
                     scene: dict[str, Any], width: int, height: int, fps: int, encoder: str) -> None:
        motion = self.motion_registry.build(_motion_name(scene), width, height, fps, duration)
        fade = _fade_duration(scene)
        filters = [motion]
        if fade > 0 and duration > fade * 2:
            filters.extend([f"fade=t=in:st=0:d={fade}", f"fade=t=out:st={duration-fade}:d={fade}"])
        if media_kind == "video":
            command = [self.ffmpeg_path, "-y", "-stream_loop", "-1", "-i", str(source), "-t", str(duration)]
        else:
            command = [self.ffmpeg_path, "-y", "-loop", "1", "-i", str(source), "-t", str(duration)]
        command += ["-vf", ",".join(filters), "-r", str(fps), "-pix_fmt", "yuv420p", "-c:v", encoder]
        if encoder == "libx264":
            command += ["-preset", "veryfast", "-crf", "20"]
        command += ["-an", str(destination)]
        self._run(command)

    def _choose_encoder(self) -> str:
        try:
            result = subprocess.run([self.ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True)
            if "h264_amf" in result.stdout:
                return "h264_amf"
        except (OSError, subprocess.CalledProcessError):
            pass
        return "libx264"

    @staticmethod
    def _run(command: list[str]) -> None:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr[-1500:]}")


def _safe_name(name: str) -> str:
    safe = "".join(character.lower() if character.isalnum() else "-" for character in name)
    return "-".join(part for part in safe.split("-") if part)[:80] or "video"


class RenderManager:
    def __init__(self, db: Database, paths: AppPaths, settings_store: SettingsStore):
        self.db = db
        self.paths = paths
        self.settings_store = settings_store
        self._threads: dict[str, threading.Thread] = {}

    def start(self, project_id: str, options: dict[str, Any]) -> str:
        existing = self._threads.get(project_id)
        if existing and existing.is_alive():
            raise RuntimeError("This project is already rendering")
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.db.connection() as db:
            db.execute(
                "INSERT INTO render_jobs (id, project_id, status, settings, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                (job_id, project_id, json.dumps(options), now, now),
            )
        thread = threading.Thread(target=self._run, args=(job_id, project_id, options), daemon=True)
        self._threads[project_id] = thread
        thread.start()
        return job_id

    def _run(self, job_id: str, project_id: str, options: dict[str, Any]) -> None:
        try:
            self._update(job_id, status="running", progress=0.01)
            project = self.db.get_project(project_id)
            if not project:
                raise RuntimeError("Project not found")
            settings = self.settings_store.load()
            renderer = FFmpegRenderer(settings.ffmpeg_path)
            output = renderer.render(
                project=project,
                scenes=self.db.list_scenes(project_id),
                assets=self.db.list_assets(project_id),
                project_dir=self.paths.project_dir(project_id),
                width=int(options.get("width", 1920)),
                height=int(options.get("height", 1080)),
                fps=int(options.get("fps", 30)),
                burn_captions=bool(options.get("burn_captions", True)),
                progress=lambda value: self._update(job_id, progress=value),
            )
            self._update(job_id, status="complete", progress=1.0, output_path=str(output))
            self.db.update_project(project_id, status="rendered")
        except Exception as error:
            self._update(job_id, status="failed", error=str(error))
        finally:
            self._threads.pop(project_id, None)

    def _update(self, job_id: str, **changes: Any) -> None:
        allowed = {"status", "progress", "output_path", "error"}
        values = {key: value for key, value in changes.items() if key in allowed}
        values["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.db.connection() as db:
            db.execute(
                f"UPDATE render_jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
