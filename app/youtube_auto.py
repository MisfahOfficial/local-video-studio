from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .config import SettingsStore
from .database import Database
from .paths import AppPaths
from .providers.base import ProviderError
from .youtube_source import YouTubeSourceService, _words


_SEARCH_NOISE = {
    "cinematic", "composition", "documentary", "dramatic", "detailed", "realistic",
    "photorealistic", "lighting", "camera", "shot", "view", "scene", "visual",
    "foreground", "background", "wide", "close", "closeup", "portrait", "landscape",
    "centered", "colour", "color", "palette", "style", "image", "showing", "shows",
}
_HISTORICAL_MARKERS = {
    "ancient", "battle", "campaign", "century", "empire", "historic", "historical",
    "medieval", "military", "ottoman", "soldier", "troops", "war", "wwi", "wwii",
}


def scene_search_query(scene: dict[str, Any]) -> str:
    """Build a compact footage query while retaining named things from the scene plan."""
    subject = " ".join(str(scene.get("visual_subject") or "").split())
    narration = " ".join(str(scene.get("narration") or "").split())
    source = subject or narration
    tokens = re.findall(r"[\w'-]+", source, flags=re.UNICODE)
    useful = [token for token in tokens if len(token) > 2 and token.lower() not in _SEARCH_NOISE]
    query = " ".join(useful[:18]).strip() or source[:220]
    searchable = {item.lower() for item in useful}
    suffix = " archival footage" if searchable & _HISTORICAL_MARKERS else " real footage"
    if not re.search(r"\b(footage|video|archive|archival)\b", query, re.IGNORECASE):
        query += suffix
    return " ".join(query.split())[:240]


def candidate_relevance(candidate: dict[str, Any], scene: dict[str, Any]) -> float:
    wanted = _words(" ".join((
        str(scene.get("visual_subject") or ""),
        str(scene.get("narration") or ""),
    ))) - _SEARCH_NOISE
    title = _words(str(candidate.get("title") or ""))
    description = _words(str(candidate.get("description") or ""))
    channel = _words(str(candidate.get("channel") or ""))
    title_overlap = len(wanted & title)
    description_overlap = len(wanted & description)
    channel_overlap = len(wanted & channel)
    duration = float(candidate.get("duration_seconds") or 0)
    scene_duration = max(0.25, float(scene.get("end_seconds") or 0) - float(scene.get("start_seconds") or 0))
    duration_score = 1.2 if duration >= scene_duration + 1 else -2.0
    short_penalty = -2.5 if duration and duration < 12 else 0.0
    return title_overlap * 4.0 + description_overlap * 1.25 + channel_overlap * 0.4 + duration_score + short_penalty


class AutoYouTubeManager:
    """Background, project-wide Creative Commons B-roll sourcing."""

    def __init__(self, db: Database, paths: AppPaths, settings_store: SettingsStore):
        self.db = db
        self.paths = paths
        self.settings_store = settings_store
        self._threads: dict[str, threading.Thread] = {}
        self._statuses: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, project_id: str, scene_ids: list[str] | None = None, force: bool = False) -> dict[str, Any]:
        with self._lock:
            active = self._threads.get(project_id)
            if active and active.is_alive():
                return dict(self._statuses[project_id])
            scenes = self.db.list_scenes(project_id)
            selected = set(scene_ids or [])
            if selected:
                scenes = [scene for scene in scenes if str(scene["id"]) in selected]
            if not force:
                assets = {str(asset["id"]): asset for asset in self.db.list_assets(project_id)}
                scenes = [
                    scene for scene in scenes
                    if assets.get(str(scene.get("selected_asset_id") or ""), {}).get("provider") != "youtube"
                ]
            status = {
                "running": bool(scenes), "total": len(scenes), "completed": 0, "failed": 0,
                "current_scene": None, "errors": [],
            }
            self._statuses[project_id] = status
            if scenes:
                thread = threading.Thread(
                    target=self._run, args=(project_id, scenes), daemon=True,
                    name=f"youtube-auto-{project_id[:8]}",
                )
                self._threads[project_id] = thread
                thread.start()
            return dict(status)

    def status(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._statuses.get(project_id, {
                "running": False, "total": 0, "completed": 0, "failed": 0,
                "current_scene": None, "errors": [],
            }))

    def _update(self, project_id: str, **changes: Any) -> None:
        with self._lock:
            self._statuses.setdefault(project_id, {}).update(changes)

    def _run(self, project_id: str, scenes: list[dict[str, Any]]) -> None:
        settings = self.settings_store.load()
        service = YouTubeSourceService(settings.youtube_api_key, settings.ffmpeg_path)
        used_ids = {
            str(asset.get("provider_asset_id") or "")
            for asset in self.db.list_assets(project_id) if asset.get("provider") == "youtube"
        }
        completed = failed = 0
        errors: list[dict[str, Any]] = []
        try:
            for scene in scenes:
                position = int(scene.get("position") or 0)
                self._update(project_id, current_scene=position)
                query = scene_search_query(scene)
                try:
                    results = service.search(query, maximum=10)
                    ranked = sorted(
                        results,
                        key=lambda item: (str(item.get("video_id")) not in used_ids, candidate_relevance(item, scene)),
                        reverse=True,
                    )
                    if not ranked:
                        raise ProviderError(f'No Creative Commons footage matched "{query}"')
                    duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
                    last_error: Exception | None = None
                    for candidate in ranked[:4]:
                        video_id = str(candidate["video_id"])
                        destination = (
                            self.paths.project_dir(project_id) / "assets" / "youtube"
                            / f"scene-{position:04d}-{uuid.uuid4().hex[:10]}.mp4"
                        )
                        try:
                            metadata = service.source_clip(
                                video_id=video_id,
                                query=" ".join(filter(None, [query, str(scene.get("narration") or "")])),
                                duration=duration,
                                destination=destination,
                            )
                            metadata.update({
                                "auto_sourced": True,
                                "search_query": query,
                                "relevance_score": round(candidate_relevance(candidate, scene), 3),
                            })
                            asset = self.db.add_asset(
                                project_id=project_id, scene_id=str(scene["id"]),
                                candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
                                media_kind="video", provider="youtube", model="creative-commons-auto-source",
                                local_path=str(destination), remote_url=metadata["source_url"],
                                provider_asset_id=video_id, cost=0.0, metadata=metadata,
                            )
                            self.db.select_asset(str(scene["id"]), str(asset["id"]))
                            used_ids.add(video_id)
                            completed += 1
                            break
                        except Exception as error:
                            destination.unlink(missing_ok=True)
                            last_error = error
                    else:
                        raise ProviderError(str(last_error or "No downloadable result was found"))
                except Exception as error:
                    failed += 1
                    errors.append({"scene": position, "error": str(error)[:500], "query": query})
                self._update(project_id, completed=completed, failed=failed, errors=list(errors))
        finally:
            self._update(project_id, running=False, current_scene=None, completed=completed, failed=failed, errors=errors)
            with self._lock:
                self._threads.pop(project_id, None)
