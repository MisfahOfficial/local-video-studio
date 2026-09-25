from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import SettingsStore
from .database import Database
from .paths import AppPaths
from .providers.base import ProviderError
from .providers.http import post_json
from .chapter_cards import build_chapter_cards, heading_scenes
from .logo_guard import analyse_clip
from .photo_source import load_image, save_photo, search_photos
from .vintage_still import generate_vintage_still
from .footage_match import (
    NON_FOOTAGE_TITLE, looks_like_ai_slideshow, SYNTHETIC_THRESHOLD, ClipScorer, FootageVerifier, auto_topic, core_subject, detect_era, mentions_topic,
    heading_subject, scene_subjects, section_recipes, signature_words, storyboard_frames, topic_queries,
)
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
    problems: list[str] | None = None, topic: str = "",
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
            "no quotation marks. Turn figurative words into what the camera would literally show "
            "(\"impact\" in a wildlife film is the animals, not an explosion)."
            + (f" The whole video is about {topic}: every query must name {topic}." if topic else "")
            + "\n\nSCENES\n" + json.dumps(batch, ensure_ascii=False)
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


# Clips whose best moment matched the topic less confidently than this are flagged.
REVIEW_BELOW = 0.75

# A candidate needs at least one scene word in its title to count as on-topic.
_GOOD_MATCH = 4.0


class _NoFootage(Exception):
    """No real YouTube footage passed the checks for a scene."""


# Scenes sourced at the same time. More would mostly add YouTube "please sign in" blocks.
SCENE_WORKERS = 3


@dataclass
class _Run:
    """Shared state for one sourcing run; every mutable field is guarded by `lock`."""

    project_id: str
    settings: Any
    service: YouTubeSourceService
    verifier: FootageVerifier | None
    ai_queries: dict[int, list[str]]
    topic: str
    era: str
    used: dict[str, list[float]]
    subject_by_id: dict[str, str]
    recipe_by_id: dict[str, str]
    hook_ids: set[str]
    exclude_videos: set[str]
    model: str
    empty_label: str
    max_attempts: int
    lock: threading.Lock = field(default_factory=threading.Lock)
    searches: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    infos: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    review: list[int] = field(default_factory=list)
    generated: list[int] = field(default_factory=list)
    photos: list[int] = field(default_factory=list)
    used_photos: set[str] = field(default_factory=set)


class AutoYouTubeManager:
    """Background, project-wide B-roll sourcing (Creative Commons or fair-use mode)."""

    def __init__(self, db: Database, paths: AppPaths, settings_store: SettingsStore):
        self.db = db
        self.paths = paths
        self.settings_store = settings_store
        self._threads: dict[str, threading.Thread] = {}
        self._statuses: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(
        self, project_id: str, scene_ids: list[str] | None = None, force: bool = False, topic: str | None = None,
        exclude_current: bool = False,
    ) -> dict[str, Any]:
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
                "current_scene": None, "errors": [], "notice": "", "topic": "", "generated": [],
            }
            self._statuses[project_id] = status
            if scenes:
                project = self.db.get_project(project_id) or {}
                chosen_topic = (topic if topic is not None else auto_topic(
                    str(project.get("script") or ""), str(project.get("name") or ""),
                )).strip()
                status["topic"] = chosen_topic
                exclude = {
                    str(asset.get("provider_asset_id")) for asset in self.db.list_assets(project_id)
                    if exclude_current and str(asset.get("scene_id")) in {str(scene["id"]) for scene in scenes}
                    and asset.get("provider") in {"youtube", "photo"}
                }
                thread = threading.Thread(
                    target=self._run, args=(project_id, scenes, chosen_topic, exclude), daemon=True,
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

    def _run(
        self, project_id: str, scenes: list[dict[str, Any]], topic: str = "", exclude_videos: set[str] | None = None,
    ) -> None:
        settings = self.settings_store.load()
        service = YouTubeSourceService(settings.youtube_api_key, settings.ffmpeg_path, settings.youtube_license_mode)
        problems: list[str] = []
        ai_queries = gemini_footage_queries(
            scenes, settings.gemini_api_key, settings.gemini_model, problems=problems, topic=topic,
        )
        if problems:
            self._update(project_id, notice=problems[0])
        verifier: FootageVerifier | None = None
        if ClipScorer.available():
            try:
                verifier = FootageVerifier()
            except Exception as error:  # a broken model must not stop sourcing
                self._update(project_id, notice=f"Visual check unavailable: {str(error)[:160]}")
        used: dict[str, list[float]] = {}
        for asset in self.db.list_assets(project_id):
            if asset.get("provider") == "youtube":
                start_time = float((asset.get("metadata") or {}).get("source_start_seconds") or 0)
                used.setdefault(str(asset.get("provider_asset_id") or ""), []).append(start_time)
        project = self.db.get_project(project_id) or {}
        # Subjects come from the whole plan so list sections ("POOR MAN'S COOKIES")
        # carry over to the scenes that follow the heading.
        all_scenes = self.db.list_scenes(project_id)
        subjects = scene_subjects(all_scenes, topic)
        # The hook is everything before the first list heading; it only ever gets real media.
        first_heading = next((index for index, item in enumerate(all_scenes) if heading_subject(str(item["narration"]))), None)
        hook_ids = {str(item["id"]) for item in all_scenes[:first_heading]} if first_heading else set()
        run = _Run(
            project_id=project_id, settings=settings, service=service, verifier=verifier, ai_queries=ai_queries,
            topic=topic, era=detect_era(str(project.get("script") or "")), used=used,
            subject_by_id=dict(zip((str(item["id"]) for item in all_scenes), subjects)),
            recipe_by_id=dict(zip((str(item["id"]) for item in all_scenes), section_recipes(all_scenes, subjects))),
            hook_ids=hook_ids, exclude_videos=set(exclude_videos or ()),
            model="fair-use-auto-source" if service.fair_use else "creative-commons-auto-source",
            empty_label="YouTube" if service.fair_use else "Creative Commons",
            # Each API search spends daily quota; fair-use search can fall back to quota-free yt-dlp.
            max_attempts=4 if service.fair_use else 2,
        )
        headings = {str(item["id"]) for item in heading_scenes(scenes)}
        try:
            # Several scenes at once: most of the time is spent waiting on YouTube.
            with ThreadPoolExecutor(max_workers=SCENE_WORKERS) as pool:
                list(pool.map(lambda scene: self._source_scene(run, scene), [
                    scene for scene in scenes if str(scene["id"]) not in headings  # headings become chapter cards
                ]))
            if headings:
                try:
                    made = build_chapter_cards(self.db, self.paths, project_id, service.ffmpeg_path)
                    with run.lock:
                        run.completed += made
                except Exception as error:  # cards are a finishing touch; never lose the footage
                    with run.lock:
                        run.errors.append({"scene": 0, "error": f"Chapter cards failed: {str(error)[:300]}", "query": ""})
        finally:
            with run.lock:
                self._update(
                    project_id, running=False, current_scene=None, completed=run.completed, failed=run.failed,
                    errors=list(run.errors), review=sorted(run.review), generated=sorted(run.generated),
                    photos=sorted(run.photos),
                )
            with self._lock:
                self._threads.pop(project_id, None)

    def _search(self, run: "_Run", query: str) -> list[dict[str, Any]]:
        """Searches are cached per run: scenes in one list section repeat the same queries."""
        with run.lock:
            if query in run.searches:
                return run.searches[query]
        results = run.service.search(query, maximum=10)
        with run.lock:
            run.searches[query] = results
        return results

    def _source_scene(self, run: "_Run", scene: dict[str, Any]) -> None:
        position = int(scene.get("position") or 0)
        self._update(run.project_id, current_scene=position)
        scene_text = " ".join(filter(None, [str(scene.get("visual_subject") or ""), str(scene.get("narration") or "")]))
        subject = run.subject_by_id.get(str(scene["id"]), run.topic)
        recipe = run.recipe_by_id.get(str(scene["id"]), "")
        core = core_subject(subject)
        signature = signature_words(recipe, subject) if recipe else set()
        is_hook = str(scene["id"]) in run.hook_ids
        own = topic_queries(scene, subject, run.era, recipe) or scene_search_queries(scene)
        queries = list(dict.fromkeys(
            [q if not subject or mentions_topic(q, core) else f"{subject} {q}" for q in run.ai_queries.get(position, [])]
            + own
        ))
        query = queries[0] if queries else subject

        def usable(item: dict[str, Any]) -> bool:
            text = f"{item.get('title')} {item.get('description')}"
            title = str(item.get("title") or "")
            # A list section's clips must be that exact dish: its name or signature
            # ingredients ("oatmeal", "molasses"), not gingerbread or chocolate chip.
            exact = not signature or any(mentions_topic(text, word) for word in signature)
            return (not NON_FOOTAGE_TITLE.search(title)
                    and str(item.get("video_id")) not in run.exclude_videos
                    and not looks_like_ai_slideshow(item, run.settings.blocked_channels)
                    and mentions_topic(text, core) and exact)

        try:
            pool: dict[str, dict[str, Any]] = {}
            for attempt in queries[:run.max_attempts]:
                for item in self._search(run, attempt):
                    pool.setdefault(str(item.get("video_id")), {**item, "_query": attempt})
                good = [item for item in pool.values() if usable(item)]
                if len(good) >= 4 and any(candidate_relevance(item, scene) >= _GOOD_MATCH for item in good):
                    break
            # Off-subject titles (buffalo calves for a musk ox film, lasagna in a
            # cookie section) and lyric/ASMR/podcast videos are never used.
            with run.lock:
                used_now = {key: list(value) for key, value in run.used.items()}
            ranked = sorted(
                (item for item in pool.values() if usable(item)),
                key=lambda item: (str(item.get("video_id")) not in used_now, candidate_relevance(item, scene)),
                reverse=True,
            )
            duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
            choices = self._choose(
                run.service, run.verifier, ranked[:4], subject, scene_text, duration, used_now, run.infos,
                run.settings.blocked_channels, recipe,
            ) if ranked else []
            if not choices:
                raise _NoFootage()
            last_error: Exception | None = None
            for candidate, start_time, topic_score in choices[:3]:
                video_id = str(candidate["video_id"])
                with run.lock:
                    if start_time is not None and any(abs(start_time - used) < 15 for used in run.used.get(video_id, [])):
                        continue  # another scene took this moment meanwhile
                    run.used.setdefault(video_id, []).append(float(start_time or 0))
                destination = (
                    self.paths.project_dir(run.project_id) / "assets" / "youtube"
                    / f"scene-{position:04d}-{uuid.uuid4().hex[:10]}.mp4"
                )
                try:
                    metadata = run.service.source_clip(
                        video_id=video_id,
                        query=" ".join(filter(None, [query, str(scene.get("narration") or "")])),
                        duration=duration, destination=destination, source_start_seconds=start_time,
                        info=run.infos.get(video_id),
                    )
                    break
                except Exception as error:
                    destination.unlink(missing_ok=True)
                    last_error = error
            else:
                raise ProviderError(str(last_error or "No downloadable result was found"))
            metadata.update({
                "auto_sourced": True,
                "search_query": str(candidate.get("_query") or query),
                "topic": subject,
                "relevance_score": round(candidate_relevance(candidate, scene), 3),
                "visual_match": None if topic_score is None else round(topic_score, 3),
                "needs_review": topic_score is not None and topic_score < REVIEW_BELOW,
            })
            try:
                logo = analyse_clip(
                    destination, run.service.ffmpeg_path, run.verifier.grey_frames(video_id) if run.verifier else None,
                )
            except Exception:  # logo hiding is best effort
                logo = {"logo_boxes": [], "safe_crop": None, "logo_hidden": True}
            metadata.update(logo)
            metadata["needs_review"] = metadata["needs_review"] or not logo["logo_hidden"]
            asset = self.db.add_asset(
                project_id=run.project_id, scene_id=str(scene["id"]),
                candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
                media_kind="video", provider="youtube", model=run.model,
                local_path=str(destination), remote_url=metadata["source_url"],
                provider_asset_id=video_id, cost=0.0, metadata=metadata,
            )
            self.db.select_asset(str(scene["id"]), str(asset["id"]))
            with run.lock:
                run.completed += 1
                if metadata["needs_review"]:
                    run.review.append(position)
        except Exception as error:
            # Never leave a gap: a real archival photo, then (outside the hook) an aged still.
            try:
                if self._real_photo(run, scene, position, scene_text, subject, queries):
                    with run.lock:
                        run.completed += 1
                        run.photos.append(position)
                elif is_hook:
                    raise ProviderError("No real footage or photo passed for this hook scene; AI images are never used in the hook")
                else:
                    self._fallback_still(run.project_id, scene, position, scene_text, subject, run.era, run.settings)
                    with run.lock:
                        run.completed += 1
                        run.generated.append(position)
            except Exception as fallback_error:
                reason = fallback_error if isinstance(error, _NoFootage) else error
                with run.lock:
                    run.failed += 1
                    run.errors.append({"scene": position, "error": str(reason)[:500], "query": query})
        self._progress(run)

    def _real_photo(
        self, run: "_Run", scene: dict[str, Any], position: int, scene_text: str, subject: str, queries: list[str],
    ) -> bool:
        """Place a genuine, commercially reusable archival photo; False when none passes."""
        if run.verifier is None:
            return False
        photo_queries = list(dict.fromkeys(
            ([f"{run.era} {core_subject(subject)}".strip()] if subject else [])
            + [query.replace(" footage", "") for query in queries[:2]]
        ))
        items: dict[str, dict[str, Any]] = {}
        for query in [item for item in photo_queries if item.strip()][:3]:
            for item in search_photos(query):
                items.setdefault(str(item.get("id") or item["url"]), item)
            if len(items) >= 12:
                break
        with run.lock:
            fresh = [item for key, item in items.items() if key not in run.used_photos]
        candidates = [(item, load_image(str(item.get("thumbnail") or item["url"]))) for item in fresh[:12]]
        candidates = [(item, image) for item, image in candidates if image is not None]
        for index, _score in run.verifier.rank_photos(
            [image for _item, image in candidates], subject, f"{run.era} {scene_text}".strip(),
            run.recipe_by_id.get(str(scene["id"]), ""),
        )[:2]:
            item = candidates[index][0]
            photo = load_image(str(item["url"]))
            if photo is None:
                continue
            key = str(item.get("id") or item["url"])
            with run.lock:
                if key in run.used_photos:
                    continue
                run.used_photos.add(key)
            destination = self.paths.project_dir(run.project_id) / "assets" / "photos" / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.jpg"
            metadata = save_photo(item, photo, destination)
            metadata["search_topic"] = subject
            asset = self.db.add_asset(
                project_id=run.project_id, scene_id=str(scene["id"]),
                candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
                media_kind="image", provider="photo", model="openverse", local_path=str(destination),
                remote_url=metadata["source_url"], provider_asset_id=key, cost=0.0, metadata=metadata,
            )
            self.db.select_asset(str(scene["id"]), str(asset["id"]))
            return True
        return False

    def _progress(self, run: "_Run") -> None:
        with run.lock:
            self._update(
                run.project_id, completed=run.completed, failed=run.failed, errors=list(run.errors),
                review=sorted(run.review), generated=sorted(run.generated),
            )

    def _fallback_still(
        self, project_id: str, scene: dict[str, Any], position: int, scene_text: str, subject: str, era: str,
        settings: Any,
    ) -> None:
        destination = self.paths.project_dir(project_id) / "assets" / "stills" / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.jpg"
        try:
            metadata = generate_vintage_still(settings, scene_text, subject, era, destination)
        except ProviderError as error:
            raise ProviderError(
                f'No real footage of "{subject or scene_text[:40]}" passed the checks and a fallback image '
                f"could not be made: {error}"
            ) from error
        asset = self.db.add_asset(
            project_id=project_id, scene_id=str(scene["id"]),
            candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
            media_kind="image", provider="generated", model=str(metadata["model"]),
            local_path=str(destination), remote_url=None, provider_asset_id=None,
            cost=float(metadata.get("cost") or 0), metadata=metadata,
        )
        self.db.select_asset(str(scene["id"]), str(asset["id"]))

    @staticmethod
    def _choose(
        service: YouTubeSourceService, verifier: FootageVerifier | None, candidates: list[dict[str, Any]],
        subject: str, scene_text: str, duration: float, used: dict[str, list[float]], infos: dict[str, dict[str, Any]],
        blocked_channels: str = "", recipe: str = "",
    ) -> list[tuple[dict[str, Any], float | None, float | None]]:
        """Candidates whose best moment passed the visual check, best first."""
        if verifier is None:
            fresh = [item for item in candidates if str(item["video_id"]) not in used]
            return [(item, None, None) for item in fresh + [item for item in candidates if item not in fresh]]

        def prepare(video_id: str) -> tuple[str, dict[str, Any] | None, list[tuple[float, Any]]]:
            try:
                info = infos.get(video_id) or service.inspect(video_id)
            except ProviderError:
                return video_id, None, []
            return video_id, info, [] if verifier.has_frames(video_id) else storyboard_frames(info)

        # Network work (metadata + storyboard sprites) runs in parallel; the
        # CLIP model then scores the frames one video at a time.
        with ThreadPoolExecutor(max_workers=4) as pool:
            prepared = list(pool.map(prepare, [str(item["video_id"]) for item in candidates]))
        for video_id, info, tiles in prepared:
            if info is not None:
                infos[video_id] = info
                if tiles:
                    verifier.add_frames(video_id, tiles)
        passing: list[tuple[float, dict[str, Any], float, float]] = []
        for candidate in candidates:
            video_id = str(candidate["video_id"])
            info = infos.get(video_id)
            if info is None:
                continue
            # The full description is only known after inspection (AI voice/image disclosures).
            if looks_like_ai_slideshow({**candidate, "description": info.get("description")}, blocked_channels):
                continue
            if verifier.synthetic_score(video_id) >= SYNTHETIC_THRESHOLD:
                continue  # looks like AI imagery; prefer real camera footage
            moment = verifier.best_moment(
                video_id, info, subject, scene_text, duration, avoid=used.get(video_id), recipe=recipe,
            )
            if moment is None:
                continue
            start_time, topic_score, score = moment
            if video_id in used:
                score -= 0.05  # prefer a fresh source when quality is similar
            passing.append((score, candidate, start_time, topic_score))
        passing.sort(key=lambda item: item[0], reverse=True)
        return [(candidate, start_time, topic_score) for _score, candidate, start_time, topic_score in passing]
