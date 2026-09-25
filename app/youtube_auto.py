from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .config import SettingsStore
from .database import Database
from .paths import AppPaths
from .providers.base import ProviderError
from .providers.http import post_json
from .youtube_source import YouTubeSourceService, _words


_SEARCH_NOISE = {
    "cinematic", "composition", "documentary", "dramatic", "detailed", "realistic",
    "photorealistic", "lighting", "camera", "shot", "view", "scene", "visual",
    "foreground", "background", "wide", "close", "closeup", "portrait", "landscape",
    "centered", "colour", "color", "palette", "style", "image", "showing", "shows",
}
_STOPWORDS = {
    "a", "about", "across", "after", "again", "also", "among", "an", "and", "any", "are", "around", "as", "at",
    "back", "be", "because", "been", "before", "being", "between", "but", "by", "can", "could", "did", "do",
    "does", "down", "during", "each", "even", "every", "for", "from", "front", "had", "has", "have", "he",
    "her", "here", "his", "how", "in", "into", "is", "it", "its", "just", "like", "many", "more", "most",
    "much", "of", "off", "on", "once", "one", "only", "or", "our", "out", "over", "she", "should", "so",
    "some", "still", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "to", "too", "toward", "towards", "under", "until", "up", "very", "was", "we",
    "were", "what", "when", "where", "which", "while", "who", "why", "will", "with", "would", "you", "your",
}
# Titles with these words are usually talk, reactions, or music rather than usable B-roll.
_NON_FOOTAGE = {
    "podcast", "radio", "interview", "reaction", "reacts", "prank", "pranked", "lyrics", "karaoke",
    "trailer", "review", "unboxing", "asmr", "livestream", "shorts", "tutorial", "explained",
}
_FOOTAGE_MARKERS = {"footage", "film", "archive", "archival", "newsreel", "stock", "commercial", "1080p", "35mm", "16mm"}
_HISTORICAL_MARKERS = {
    "ancient", "battle", "campaign", "century", "empire", "historic", "historical",
    "medieval", "military", "ottoman", "soldier", "troops", "war", "wwi", "wwii",
}


def _content_tokens(source: str) -> list[str]:
    tokens = re.findall(r"[\w'-]+", source, flags=re.UNICODE)
    return [
        token for token in tokens
        if token.lower() not in _SEARCH_NOISE and token.lower() not in _STOPWORDS
        and (len(token) > 2 or (len(token) == 2 and token.isupper()))
    ]


def _with_footage_suffix(query: str, historical: bool) -> str:
    if not re.search(r"\b(footage|video|archive|archival|film)\b", query, re.IGNORECASE):
        query += " archival footage" if historical else " footage"
    return " ".join(query.split())[:240]


def scene_search_query(scene: dict[str, Any]) -> str:
    """Build a compact footage query while retaining named things from the scene plan."""
    return scene_search_queries(scene)[0]


def scene_search_queries(scene: dict[str, Any]) -> list[str]:
    """Return a short keyword query plus a shorter fallback; long sentences return junk on YouTube."""
    subject = " ".join(str(scene.get("visual_subject") or "").split())
    narration = " ".join(str(scene.get("narration") or "").split())
    useful = _content_tokens(subject or narration) or (subject or narration).split()[:6]
    historical = bool({item.lower() for item in useful} & _HISTORICAL_MARKERS)
    # Years/decades and proper names carry the most search signal, so the
    # fallback keeps them before ordinary words.
    priority = [token for token in useful if re.fullmatch(r"\d{2,4}s?", token) or token[:1].isupper()]
    ordinary = [token for token in useful if token not in priority]
    queries = [
        _with_footage_suffix(" ".join(useful[:7]), historical),
        _with_footage_suffix(" ".join((priority + ordinary)[:4]), historical),
    ]
    return list(dict.fromkeys(query for query in queries if query.strip()))


def gemini_footage_queries(
    scenes: list[dict[str, Any]], api_key: str, model: str, batch_size: int = 40,
    problems: list[str] | None = None,
) -> dict[int, list[str]]:
    """Ask Gemini for short footage searches per scene; failures are reported in `problems`, never raised."""
    if not api_key.strip() or not scenes:
        return {}
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key.strip()}"
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {"position": {"type": "INTEGER"}, "queries": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["position", "queries"],
        },
    }
    found: dict[int, list[str]] = {}
    for start in range(0, len(scenes), batch_size):
        batch = [
            {"position": int(scene.get("position") or 0), "narration": str(scene.get("narration") or ""),
             "visual": str(scene.get("visual_subject") or "")}
            for scene in scenes[start:start + batch_size]
        ]
        prompt = (
            "You pick B-roll for a documentary edited from real YouTube footage. For every scene return 3 YouTube "
            "search queries of 3 to 6 words, best first, describing something a camera actually filmed that shows "
            "the scene. Keep the era, place, and named brands or people. No full sentences, no abstract ideas, "
            "no quotation marks.\n\nSCENES\n" + json.dumps(batch, ensure_ascii=False)
        )
        try:
            response = post_json(endpoint, {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json", "response_schema": schema, "temperature": 0.3},
            }, {}, timeout=120)
            items = json.loads(response["candidates"][0]["content"]["parts"][0]["text"])
        except (ProviderError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            if problems is not None:
                quota = "429" in str(error) or "quota" in str(error).lower()
                problems.append(
                    "Gemini quota is used up (free tier), so keyword search was used instead"
                    if quota else f"Gemini search help failed, so keyword search was used instead: {str(error)[:160]}"
                )
            continue
        for item in items if isinstance(items, list) else []:
            try:
                position = int(item.get("position"))
            except (AttributeError, TypeError, ValueError):
                continue
            queries = [" ".join(str(query).replace('"', "").split())[:120] for query in item.get("queries") or []]
            queries = [query for query in queries if 1 < len(query.split()) <= 10]
            if queries:
                found[position] = queries[:3]
    return found


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
    raw_title = str(candidate.get("title") or "").lower()
    title_words = set(re.findall(r"[a-z0-9]+", raw_title))
    kind_score = -4.0 if title_words & _NON_FOOTAGE or "#shorts" in raw_title else 0.0
    kind_score += 1.5 if title_words & _FOOTAGE_MARKERS else 0.0
    return (
        title_overlap * 4.0 + description_overlap * 1.25 + channel_overlap * 0.4
        + duration_score + short_penalty + kind_score
    )


# A candidate needs at least one scene word in its title to count as on-topic.
_GOOD_MATCH = 4.0


class AutoYouTubeManager:
    """Background, project-wide B-roll sourcing (Creative Commons or fair-use mode)."""

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
                "current_scene": None, "errors": [], "notice": "",
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
        service = YouTubeSourceService(settings.youtube_api_key, settings.ffmpeg_path, settings.youtube_license_mode)
        model = "fair-use-auto-source" if service.fair_use else "creative-commons-auto-source"
        problems: list[str] = []
        ai_queries = gemini_footage_queries(scenes, settings.gemini_api_key, settings.gemini_model, problems=problems)
        if problems:
            self._update(project_id, notice=problems[0])
        empty_label = "YouTube" if service.fair_use else "Creative Commons"
        # Each API search spends daily quota; fair-use search can fall back to quota-free yt-dlp.
        max_attempts = 4 if service.fair_use else 2
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
                queries = list(dict.fromkeys(ai_queries.get(position, []) + scene_search_queries(scene)))
                query = queries[0]
                try:
                    pool: dict[str, dict[str, Any]] = {}
                    for attempt in queries[:max_attempts]:
                        for item in service.search(attempt, maximum=10):
                            pool.setdefault(str(item.get("video_id")), {**item, "_query": attempt})
                        if any(candidate_relevance(item, scene) >= _GOOD_MATCH for item in pool.values()):
                            break
                    ranked = sorted(
                        pool.values(),
                        key=lambda item: (str(item.get("video_id")) not in used_ids, candidate_relevance(item, scene)),
                        reverse=True,
                    )
                    if not ranked:
                        raise ProviderError(f'No {empty_label} footage matched "{query}"')
                    duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
                    last_error: Exception | None = None
                    for candidate in ranked[:4]:
                        video_id = str(candidate["video_id"])
                        query = str(candidate.get("_query") or query)
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
                                media_kind="video", provider="youtube", model=model,
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
