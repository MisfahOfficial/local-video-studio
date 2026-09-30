from __future__ import annotations

import json
import re
import subprocess
import tempfile
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
from .text_guard import available as text_guard_available, face_areas, has_burned_in_text, sample_frames
from .ai_judge import ClaudeJudge, best_usable
from .content_profile import BUILTIN, DEFAULT_KIND, profile_for
from .archive_source import MultiSourceService, load_drive_index
from .drive_vision import DriveVisualIndex
from .motion_designs import plan_designs, remember_designs, render_ingredients
from .channel_styles import get_style
from .source_library import analysis_copy, clean_cache, read_source
from .logo_guard import analyse_clip, content_box, cut_free_start, fit_crop, shot_cuts, trim
from .youtube_source import pick_video_stream
from .ingredient_library import dish_images, item_image
from .stock_video import download_stock_video, search_stock_videos
from .photo_source import load_image, load_images, save_photo, search_photos
from .vintage_still import generate_vintage_still
from .footage_match import (
    MODERN_TITLE, NON_FOOTAGE_TITLE, looks_like_ai_slideshow, SYNTHETIC_THRESHOLD, ClipScorer, FootageVerifier, auto_topic, core_subject, detect_era, mentions_topic,
    PLURAL_FOODS, heading_subject, hook_theme, is_process_scene, vague_heading, scene_keywords, ingredient_list, off_cuisine, plural_items, scene_subjects, section_recipes, signature_words, storyboard_frames, topic_queries,
)
from .youtube_source import YouTubeSourceService, _words


# Words too broad to prove a video is about the hook's theme on their own.
_GENERIC_THEME = {"casseroles", "casserole", "desserts", "dessert", "recipes", "recipe", "dishes", "dish",
                  "dinners", "dinner", "foods", "food", "meals", "meal", "treats"}
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


MAX_SOURCE_SECONDS = 45 * 60
_READ_SLOTS = threading.BoundedSemaphore(2)


def _has_video(path: Path, ffmpeg_path: str = "ffmpeg") -> bool:
    """A saved clip must really contain video (a broken download once left a 261-byte file)."""
    probe = str(Path(ffmpeg_path).with_name("ffprobe")) if "/" in ffmpeg_path else "ffprobe"
    try:
        result = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                                 "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return path.is_file() and path.stat().st_size > 10_000
    return bool(result.stdout.strip()) and path.stat().st_size > 1000


class _NoFootage(Exception):
    """No real YouTube footage passed the checks for a scene."""


# Seconds downloaded either side of a moment, so a cut-free stretch can be chosen.
SHOT_PADDING = 2.0

# Scenes sourced at the same time. More would mostly add YouTube "please sign in" blocks.
SCENE_WORKERS = 4


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
    theme: str
    script: str
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
    photo_titles: list[set[str]] = field(default_factory=list)
    card_ids: set[str] = field(default_factory=set)
    gallery_id: str = ""
    gallery_done: bool = False
    judge: Any = None
    photo_budget: int = 0
    notes: dict[int, str] = field(default_factory=dict)
    photo_slots: set[str] = field(default_factory=set)
    style: Any = None  # the project's channel style (motion graphics look)
    profile: Any = field(default_factory=lambda: BUILTIN[DEFAULT_KIND])  # what the footage should be
    designs: dict[str, str] = field(default_factory=dict)  # ingredient scene -> motion design (AI-chosen)
    # scene id -> (candidate, start, topic score) options planned from its section's footage pool.
    planned: dict[str, list[tuple[dict[str, Any], float | None, float | None]]] = field(default_factory=dict)
    # (dish subject, recipe, sources) of every list section, in story order: the hook's teaser shots.
    section_pools: list[tuple[str, str, list[dict[str, Any]]]] = field(default_factory=list)
    teasers: dict[str, list[tuple[dict[str, Any], float | None, float | None]]] = field(default_factory=dict)
    plan_for: set[str] = field(default_factory=set)
    by_position: dict[int, str] = field(default_factory=dict)
    stock: list[int] = field(default_factory=list)
    graphics: list[int] = field(default_factory=list)


def _photo_title(item: dict[str, Any]) -> set[str]:
    return set(_words(str(item.get("title") or ""))) - _STOPWORDS


def _seen_title(item: dict[str, Any], seen_titles: list[set[str]]) -> bool:
    """Four shots of the same diner are still the same picture to a viewer."""
    title = _photo_title(item)
    return bool(title) and any(
        len(title & seen) >= max(2, 0.6 * min(len(title), len(seen))) for seen in seen_titles
    )


def usable_source(run: "_Run", item: dict[str, Any], is_hook: bool, core: str, signature: set[str]) -> bool:
    """One rule for every search: the right subject, real footage, not another cuisine or a slideshow."""
    text = f"{item.get('title')} {item.get('description')}"
    title = str(item.get("title") or "")
    # A list section's clips must be that exact dish: its name or signature
    # ingredients ("oatmeal", "molasses"), not gingerbread or chocolate chip.
    exact = not signature or any(mentions_topic(text, word) for word in signature)
    # Drive clips found by what they show ("FlexClip_12") need no telling title.
    seen = item.get("source") == "drive" and bool(item.get("visual_score"))
    # The hook may show any part of its theme ("church" or "potluck"), the dish scenes the dish.
    if is_hook:
        named = True  # the hook matches the sentence visually; titles need not name the theme
    else:
        # "Chicken and Rice Casserole" must be the video's own dish: every word of the name in its
        # title ("Buffalo Chicken Dynamite Rice" only lists it among others in the description).
        dish_words = [word for word in core.split() if word not in _STOPWORDS]
        named = (all(mentions_topic(title, word) for word in dish_words)
                 if len(dish_words) >= 2 else mentions_topic(text, core))
    channel = str(item.get("channel") or item.get("uploader") or "")
    if is_hook and run.era and MODERN_TITLE.search(title):
        return False  # a period opening never shows a 2026 store haul
    return (not NON_FOOTAGE_TITLE.search(title) and not NON_FOOTAGE_TITLE.search(channel)
            and not off_cuisine(title, run.script)
            and str(item.get("video_id")) not in run.exclude_videos
            and not looks_like_ai_slideshow(item, run.settings.blocked_channels)
            and run.profile.title_allowed(title)
            and (seen or (named and exact)))


# Titles that promise a whole, period-style recipe: better pool sources than quick hacks.
_CLASSIC_TITLE = re.compile(
    r"\b(old[- ]fashioned|vintage|classic|grandma'?s?|homemade|from scratch|original|19[3-7]0s|retro|church|potluck)\b",
    re.IGNORECASE,
)
# Each item is cut from its two best source videos (two spares are read in case one fails).
POOL_SIZE = 2


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
        # Own Drive footage and public-domain archive films first; YouTube only for what they lack.
        service = MultiSourceService(settings.youtube_api_key, settings.ffmpeg_path, settings.youtube_license_mode,
                                     drive_files=load_drive_index(self.paths.root / "drive_index.json"))
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
        script = str(project.get("script") or "")
        # The opening is about the whole video ("church potluck casseroles"), not the first dish.
        theme = topic or hook_theme(" ".join(str(item["narration"]) for item in all_scenes[:first_heading or 0]))
        profile = profile_for(project)
        service.period = profile.period
        if verifier is not None:
            service.visual_index = DriveVisualIndex(self.paths.root)
            def embed_text(text: str) -> Any:
                with verifier.lock:  # the CLIP model is not safe to call from several threads at once
                    return verifier.scorer.embed_texts([text])[0]
            service.embed_text = embed_text
        run = _Run(
            project_id=project_id, settings=settings, service=service, verifier=verifier, ai_queries=ai_queries,
            topic=topic, era=detect_era(str(project.get("script") or "")) if profile.period else "", used=used,
            subject_by_id=dict(zip((str(item["id"]) for item in all_scenes), subjects)),
            recipe_by_id=dict(zip((str(item["id"]) for item in all_scenes), section_recipes(all_scenes, subjects))),
            hook_ids=hook_ids, exclude_videos=set(exclude_videos or ()), theme=theme, script=script,
            model="fair-use-auto-source" if service.fair_use else "creative-commons-auto-source",
            empty_label="YouTube" if service.fair_use else "Creative Commons",
            # Each API search spends daily quota; fair-use search can fall back to quota-free yt-dlp.
            max_attempts=4 if service.fair_use else 2, profile=profile,
        )
        # Graphics are planned in story order: one gallery per video.
        # Every scene that talks about ingredients is an ingredient card (motion graphics, never a clip).
        for item in all_scenes if profile.recipe_cards else []:
            if ingredient_list(str(item["narration"])) and not heading_subject(str(item["narration"])):
                run.card_ids.add(str(item["id"]))
        run.gallery_id = next((str(item["id"]) for item in all_scenes if profile.recipe_cards
                               and not ingredient_list(str(item["narration"])) and plural_items(str(item["narration"]))), "")
        # A gallery already on the timeline (outside this run) counts as the video's one gallery.
        redo = {str(item["id"]) for item in scenes}
        selected = {str(item.get("selected_asset_id") or "") for item in all_scenes if str(item["id"]) not in redo}
        run.gallery_done = any(
            str(asset["id"]) in selected and asset.get("provider") == "graphic" and asset.get("model") == "gallery"
            for asset in self.db.list_assets(project_id)
        )
        if run.gallery_id in redo:
            run.gallery_done = False
        run.judge = ClaudeJudge.from_settings(settings)
        run.style = get_style((project.get("effects") or {}).get("channel_style"))
        # Each ingredient graphic gets a design the AI picked for this video, never the same look twice in a row
        # and led by one this channel has not used lately.
        card_order = [str(item["id"]) for item in all_scenes if str(item["id"]) in run.card_ids]
        planned_designs = plan_designs("ingredients", len(card_order), script, run.style.key, self.paths.root, settings)
        run.designs = dict(zip(card_order, planned_designs))
        # 80:20 - four of five filmable scenes are real video; photos may replace a weak clip only
        # while they stay under a fifth (a scene with no usable video still gets a photo).
        filmable = [item for item in all_scenes if not heading_subject(str(item["narration"]))
                    and str(item["id"]) not in run.card_ids and str(item["id"]) != run.gallery_id]
        run.photo_budget = len(filmable) // 5
        headings = {str(item["id"]) for item in heading_scenes(scenes)}
        clean_cache(self.paths.root / "source_cache")
        if verifier is not None:
            try:
                self._plan_sections(run, scenes, all_scenes)
            except Exception as error:  # planning only improves continuity; per-scene search still works
                run.errors.append({"scene": 0, "error": f"Section planning skipped: {str(error)[:200]}", "query": ""})
        try:
            # Several scenes at once: most of the time is spent waiting on YouTube.
            with ThreadPoolExecutor(max_workers=SCENE_WORKERS) as pool:
                list(pool.map(lambda scene: self._source_scene_safely(run, scene), [
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
            used = [run.designs[key] for key in card_order if key in run.designs]
            if used:
                try:
                    remember_designs(self.paths.root, run.style.key, list(dict.fromkeys(used)))
                except OSError:
                    pass
            with run.lock:
                self._update(
                    project_id, running=False, current_scene=None, completed=run.completed, failed=run.failed,
                    errors=list(run.errors), review=sorted(run.review), generated=sorted(run.generated),
                    photos=sorted(run.photos), graphics=sorted(run.graphics), stock=sorted(run.stock),
                    judge_cost=round(run.judge.cost, 3) if run.judge else 0.0,
                    judge_calls=run.judge.calls if run.judge else 0,
                    judge_notice=run.judge.disabled if run.judge else "",
                    notes={str(key): value for key, value in sorted(run.notes.items())},
                    youtube_blocked=bool(getattr(run.service, "youtube_blocked", False)),
                )
            with self._lock:
                self._threads.pop(project_id, None)

    # ------------------------------------------------------------------ section footage pools
    def _plan_sections(
        self, run: "_Run", scenes: list[dict[str, Any]], all_scenes: list[dict[str, Any]] | None = None,
    ) -> None:
        """Edit like a person: pick the few best source videos for each section (the hook, each dish),
        then cut every sentence from them in story order, so the steps follow one cook's video.

        Sources come from the whole video's sections (the hook's teasers need them even when only
        the hook is re-sourced); moments are only planned for the scenes being sourced now."""
        wanted = {str(scene["id"]) for scene in scenes}
        sections: dict[str, list[dict[str, Any]]] = {}
        for scene in sorted(all_scenes or scenes, key=lambda item: int(item.get("position") or 0)):
            scene_id = str(scene["id"])
            if (scene_id in run.card_ids or scene_id == run.gallery_id
                    or heading_subject(str(scene.get("narration") or ""))):
                continue
            key = "hook" if scene_id in run.hook_ids else (
                run.recipe_by_id.get(scene_id) or run.subject_by_id.get(scene_id) or "")
            if key:
                sections.setdefault(key, []).append(scene)
        hook = [scene for scene in sections.pop("hook", []) if str(scene["id"]) in wanted]
        run.plan_for = wanted
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda item: self._plan_section(run, item[0], item[1]), sections.items()))
        order = {key: index for index, key in enumerate(sections)}
        run.section_pools.sort(key=lambda item: order.get(item[1] or item[0], 0))
        if hook:
            self._plan_teasers(run, hook)

    def _plan_teasers(self, run: "_Run", members: list[dict[str, Any]]) -> None:
        """The opening shows the dishes to come (finished, served), like a trailer; genuine period footage
        planned for a sentence stays first, the teasers follow as the reliable next choice."""
        pools = [item for item in run.section_pools if item[2]]
        if not pools:
            return
        taken: dict[str, list[float]] = {vid: list(times) for vid, times in run.used.items()}
        for plans in run.planned.values():
            for candidate, start, _topic in plans[:1]:
                taken.setdefault(str(candidate["video_id"]), []).append(float(start or 0))
        for index, scene in enumerate(members):
            # This sentence's own dish first (dishes in story order), then the others as spares.
            ordered = pools[index % len(pools):] + pools[:index % len(pools)]
            duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
            options: list[tuple[float, dict[str, Any], float, float]] = []
            for rank, (subject, recipe, sources) in enumerate(ordered):
                dish = (recipe if vague_heading(subject) else core_subject(subject)) or recipe or core_subject(subject)
                for candidate in sources:
                    video_id = str(candidate["video_id"])
                    avoid = list(taken.get(video_id, []))
                    # Up to three different moments per source, so one failed check never empties the scene.
                    for _extra in range(3):
                        moment = run.verifier.best_moment(
                            video_id, run.infos[video_id], subject,
                            f"a finished {dish} served on a table" if run.profile.recipe_cards else dish, duration,
                            avoid=avoid, recipe=recipe, prefer_vintage=run.profile.period, avoid_radius=6.0,
                        )
                        if moment is None:
                            break
                        options.append((moment[2] - 0.1 * rank, candidate, moment[0], moment[1]))
                        avoid.append(moment[0])
            options.sort(key=lambda item: item[0], reverse=True)
            if not options:
                continue
            _score, best, start, _topic = options[0]
            taken.setdefault(str(best["video_id"]), []).append(start)
            run.teasers[str(scene["id"])] = [(candidate, start, topic) for _s, candidate, start, topic in options]

    def _plan_section(self, run: "_Run", key: str, members: list[dict[str, Any]]) -> None:
        is_hook = key == "hook"
        first = str(members[0]["id"])
        era = run.era or ("vintage" if run.profile.period else "")
        if is_hook:
            if not run.theme:
                return
            subject = " ".join(word for word in run.theme.split() if word not in _GENERIC_THEME) or run.theme
            recipe, signature = "", set()
            queries = run.profile.queries(run.profile.hook_queries, item=subject, era=era, theme=run.theme)
            dish = subject
        else:
            subject = run.subject_by_id.get(first, "")
            recipe = run.recipe_by_id.get(first, "")
            signature = signature_words(recipe, subject) if recipe else set()
            # A named dish is searched by its name ("Magic Cookie Bars", not "condensed graham bars");
            # a vague heading ("Poor Man's Cookies") by its ingredients. The other adds one more search.
            dish = (recipe if vague_heading(subject) else core_subject(subject)) or recipe or core_subject(subject)
            queries = run.profile.queries(run.profile.section_queries, item=dish, era=era, theme=run.theme)
            other = core_subject(subject) if dish == recipe else recipe
            if other and other != dish:
                queries += run.profile.queries(run.profile.section_queries[:1], item=other, era=era, theme=run.theme)
        core = subject if is_hook else core_subject(subject)
        found: dict[str, dict[str, Any]] = {}
        for query in dict.fromkeys(queries):
            for item in self._search(run, query):
                found.setdefault(str(item.get("video_id")), {**item, "_query": query})
        usable = [item for item in found.values() if usable_source(run, item, is_hook, core, signature)]

        def rank(item: dict[str, Any]) -> float:
            title = str(item.get("title") or "")
            length = float(item.get("duration_seconds") or item.get("duration") or 0)
            # Own footage first (no YouTube request, no bot check); archive films for a period opening.
            source = str(item.get("source") or "")
            return (3.0 * (source == "drive") + 1.5 * (source == "archive" and is_hook)
                    + 2.0 * mentions_topic(title, dish) + 0.5 * bool(recipe and dish != recipe and mentions_topic(title, recipe))
                    + 1.0 * run.profile.good_title(title)
                    + 1.0 * (180 <= length <= 1500) + 0.3 * candidate_relevance(item, members[0]))

        ordered = sorted(usable, key=rank, reverse=True)
        sources: list[dict[str, Any]] = []
        # Read the two best first; spares only when one is unusable (AI look, vertical, unreadable).
        for start in range(0, min(len(ordered), POOL_SIZE + 4), POOL_SIZE):
            sources += self._prepare_sources(run, ordered[start:start + POOL_SIZE])
            if len(sources) >= POOL_SIZE:
                break
        sources = sources[:POOL_SIZE]
        if not is_hook:
            with run.lock:
                run.section_pools.append((subject, recipe, sources))
        if not sources:
            return
        taken: dict[str, list[float]] = {vid: list(times) for vid, times in run.used.items()}
        previous: tuple[str, float] | None = None
        best_scores: dict[str, float] = {}
        for scene in members:
            duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
            scene_text = " ".join(filter(None, [str(scene.get("visual_subject") or ""), str(scene.get("narration") or "")]))
            options: list[tuple[float, dict[str, Any], float, float]] = []
            for candidate in sources:
                video_id = str(candidate["video_id"])
                moment = run.verifier.best_moment(
                    video_id, run.infos[video_id], subject, scene_text, duration, avoid=taken.get(video_id),
                    recipe=recipe, prefer_vintage=bool(run.era) or (is_hook and run.profile.period),
                    require_vintage=is_hook and run.profile.period,
                )
                if moment is None:
                    continue
                start, topic_score, score = moment
                if previous and previous[0] == video_id:
                    # The next step comes later in the same cook's video; going backwards looks wrong.
                    score += 0.06 if start > previous[1] else -0.08
                options.append((score, candidate, start, topic_score))
            options.sort(key=lambda item: item[0], reverse=True)
            if not options:
                continue
            if not run.plan_for or str(scene["id"]) in run.plan_for:
                run.planned[str(scene["id"])] = [(candidate, start, topic) for _score, candidate, start, topic in options]
            best_scores[str(scene["id"])] = options[0][0]
            _score, best, start, _topic = options[0]
            taken.setdefault(str(best["video_id"]), []).append(start)
            previous = (str(best["video_id"]), start)
        if not is_hook:
            # 80:20 - the weakest fifth of the item's moments (and any with no moment) are offered to a photo.
            ranked_scenes = sorted((str(scene["id"]) for scene in members), key=lambda scene_id: best_scores.get(scene_id, -1.0))
            with run.lock:
                run.photo_slots.update(ranked_scenes[:len(members) // 5])

    def _prepare_sources(self, run: "_Run", candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Inspect candidates in parallel and read each one whole (a light copy: a frame every second and
        every cut); keep landscape, real (non-AI) footage. Storyboards are the fallback."""
        cache = self.paths.root / "source_cache"

        def prepare(candidate: dict[str, Any]) -> dict[str, Any] | None:
            video_id = str(candidate["video_id"])
            try:
                info = run.infos.get(video_id) or run.service.inspect(video_id)
            except ProviderError:
                return None
            run.infos[video_id] = info
            if float(info.get("duration") or 0) > MAX_SOURCE_SECONDS:
                return None  # hour-long archive reels: too slow and too much memory to read whole
            if not run.verifier.has_whole_video(video_id):
                # A whole read holds hundreds of frames; two at a time keeps an 8 GB Mac alive.
                with _READ_SLOTS:
                    copy = analysis_copy(info, cache)
                    frames, cuts = read_source(copy, run.service.ffmpeg_path, float(info.get("duration") or 0)) if copy else ([], [])
                    read = bool(frames)
                    if read:
                        run.verifier.set_whole_video(video_id, frames, cuts)
                    del frames
                if not read and not run.verifier.has_frames(video_id):
                    tiles = storyboard_frames(info)
                    if not tiles:
                        return None
                    run.verifier.add_frames(video_id, tiles)
            run.verifier.mark_faces(video_id)
            return candidate

        with ThreadPoolExecutor(max_workers=4) as pool:
            prepared = [item for item in pool.map(prepare, candidates) if item is not None]
        kept = []
        for candidate in prepared:
            video_id = str(candidate["video_id"])
            info = run.infos[video_id]
            stream = pick_video_stream(info) or {}
            if int(stream.get("height") or info.get("height") or 0) > int(stream.get("width") or info.get("width") or 1):
                continue
            if looks_like_ai_slideshow({**candidate, "description": info.get("description")}, run.settings.blocked_channels):
                continue
            if run.verifier.synthetic_score(video_id) >= SYNTHETIC_THRESHOLD:
                continue
            kept.append(candidate)
        return kept

    def _search(self, run: "_Run", query: str) -> list[dict[str, Any]]:
        """Searches are cached per run: scenes in one list section repeat the same queries."""
        with run.lock:
            if query in run.searches:
                return run.searches[query]
        results = run.service.search(query, maximum=10)
        with run.lock:
            run.searches[query] = results
        return results

    def _source_scene_safely(self, run: "_Run", scene: dict[str, Any]) -> None:
        """One scene's unexpected error (a network timeout, a broken picture) marks only that scene for
        review; it must never stop the other scenes, which silently left most of a video empty."""
        try:
            self._source_scene(run, scene)
        except Exception as error:
            with run.lock:
                run.failed += 1
                run.errors.append({"scene": int(scene.get("position") or 0), "error": str(error)[:500], "query": ""})
            self._progress(run)

    def _source_scene(
        self, run: "_Run", scene: dict[str, Any], use_theme: bool = False, skip_plan: bool = False,
        teasers_only: bool = False,
    ) -> None:
        position = int(scene.get("position") or 0)
        self._update(run.project_id, current_scene=position)
        scene_text = " ".join(filter(None, [str(scene.get("visual_subject") or ""), str(scene.get("narration") or "")]))
        subject = run.subject_by_id.get(str(scene["id"]), run.topic)
        recipe = run.recipe_by_id.get(str(scene["id"]), "")
        core = core_subject(subject)
        signature = signature_words(recipe, subject) if recipe else set()
        is_hook = str(scene["id"]) in run.hook_ids
        # The hook is searched like testing 10 did it: the sentence's own words with the era
        # ("1950s christmas table footage"), period footage preferred, no theme requirement.
        try:
            graphic = run.verifier is not None and self._motion_graphic(run, scene, position, is_hook)
        except Exception as error:  # a failed card falls back to footage like any other scene
            with run.lock:
                run.notes[position] = f"Motion graphic failed: {str(error)[:200]}"
            graphic = False
        if graphic:
            with run.lock:
                run.completed += 1
                run.graphics.append(position)
            self._progress(run)
            return
        own = topic_queries(scene, subject, run.era, recipe) or scene_search_queries(scene)
        queries = list(dict.fromkeys(
            [q if not subject or mentions_topic(q, core) else f"{subject} {q}" for q in run.ai_queries.get(position, [])]
            + own
        ))
        query = queries[0] if queries else subject

        def usable(item: dict[str, Any]) -> bool:
            return usable_source(run, item, is_hook, core, signature)

        planned = [
            (candidate, start, topic) for candidate, start, topic in ([] if skip_plan else run.planned.get(str(scene["id"]), []))
            if not any(abs(float(start or 0) - used) < 15 for used in run.used.get(str(candidate["video_id"]), []))
        ]
        try:
            pool: dict[str, dict[str, Any]] = {}
            if teasers_only:
                planned = [
                    (candidate, start, topic) for candidate, start, topic in run.teasers.get(str(scene["id"]), [])
                    if not any(abs(float(start or 0) - used) < 6 for used in run.used.get(str(candidate["video_id"]), []))
                ]
            for attempt in ([] if planned or teasers_only else queries[:run.max_attempts]):
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
                # A period opening prefers genuine archive films; everything else our own Drive footage.
                key=lambda item: ((2 if is_hook and run.profile.period and item.get("source") == "archive"
                                   else 1 if item.get("source") == "drive" else 0),
                                  str(item.get("video_id")) not in used_now, candidate_relevance(item, scene)),
                reverse=True,
            )
            duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
            choices = planned or (self._choose(
                run.service, run.verifier, ranked[:4], subject, scene_text, duration, used_now, run.infos,
                run.settings.blocked_channels, recipe, bool(run.era) or (is_hook and run.profile.period),
                is_hook and run.profile.period,
            ) if ranked else [])
            if not choices and is_hook and not planned and not skip_plan:
                # Nothing fits the sentence: a teaser shot of the dishes to come.
                planned = [
                    (candidate, start, topic) for candidate, start, topic in run.teasers.get(str(scene["id"]), [])
                    if not any(abs(float(start or 0) - used) < 6 for used in run.used.get(str(candidate["video_id"]), []))
                ]
                choices = planned
            need = self._need(run, scene, subject, recipe, is_hook)
            judged = self._judge_videos(run, need, choices, duration)
            if judged is not None:
                choices = judged
            if not choices:
                raise _NoFootage()
            with run.lock:
                photo_room = len(run.photos) < run.photo_budget
            weak = (choices[0][2] is not None and choices[0][2] < REVIEW_BELOW) if judged is None else False
            weak = weak or str(scene["id"]) in run.photo_slots
            if weak and photo_room and self._real_photo(
                run, scene, position, scene_text, subject, queries,
            ):
                # Order is video, image, stock, AI: a weak clip loses to a photo that clearly fits.
                with run.lock:
                    run.completed += 1
                    run.photos.append(position)
                self._progress(run)
                return
            last_error: Exception | None = None
            rejected: list[str] = []
            # Archival films cut often, so the hook tries more sources for one clean shot.
            for candidate, start_time, topic_score in choices[:5 if is_hook else 3]:
                video_id = str(candidate["video_id"])
                with run.lock:
                    radius = 6 if teasers_only else 15  # a teaser previews a later shot, never repeats it
                    if start_time is not None and any(abs(start_time - used) < radius for used in run.used.get(video_id, [])):
                        continue  # another scene took this moment meanwhile
                    if not planned and video_id in {run.by_position.get(position - 1), run.by_position.get(position + 1)}:
                        continue  # unplanned: the same source in neighbouring scenes may repeat a shot
                    run.used.setdefault(video_id, []).append(float(start_time or 0))
                    run.by_position[position] = video_id
                destination = (
                    self.paths.project_dir(run.project_id) / "assets" / "youtube"
                    / f"scene-{position:04d}-{uuid.uuid4().hex[:10]}.mp4"
                )
                try:
                    metadata = run.service.source_clip(
                        video_id=video_id,
                        query=" ".join(filter(None, [query, str(scene.get("narration") or "")])),
                        duration=duration, destination=destination, source_start_seconds=start_time,
                        info=run.infos.get(video_id), padding=SHOT_PADDING,
                    )
                    self._single_shot(run, destination, metadata, start_time, duration)
                    if not _has_video(destination, run.service.ffmpeg_path):
                        raise ProviderError("The downloaded clip is empty or unreadable")
                    bars = content_box(destination, run.service.ffmpeg_path)
                    if bars and bars[4] < 1.25:
                        raise ProviderError("Portrait picture inside black bars")
                    if has_burned_in_text(destination, run.service.ffmpeg_path):
                        raise ProviderError("Another creator's captions are burned into this shot")
                    if run.verifier is not None and self._shows_creator(run, destination, archival_ok=is_hook):
                        raise ProviderError("A present-day person or show host is on camera")
                    break
                except Exception as error:
                    destination.unlink(missing_ok=True)
                    last_error = error
                    rejected.append(f"{candidate.get('title', video_id)}"[:40] + f": {str(error)[:70]}")
                    with run.lock:
                        if run.by_position.get(position) == video_id:
                            run.by_position.pop(position, None)
            else:
                if is_hook and not teasers_only and run.teasers.get(str(scene["id"])):
                    # The sentence's clips all cut too fast or failed a check: a teaser shot instead.
                    self._source_scene(run, scene, teasers_only=True)
                    return
                if planned and not teasers_only:
                    # The section's own sources failed the clip checks here: search for this sentence instead.
                    with run.lock:
                        run.errors.append({"scene": position, "error": "Planned clips rejected: " + " | ".join(rejected[:3]), "query": ""})
                    self._source_scene(run, scene, use_theme=use_theme, skip_plan=True)
                    return
                raise ProviderError("Every candidate was rejected: " + " | ".join(rejected[:3])
                                    if rejected else str(last_error or "No downloadable result was found"))
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
            if bars:
                # Letterboxed/pillarboxed source: zoom to the real picture (and past any logo).
                logo["safe_crop"] = fit_crop(bars, logo.get("safe_crop"))
                logo["black_bars"] = [round(value, 4) for value in bars[:4]]
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
            with run.lock:
                run.notes[position] = "no matching clip" if isinstance(error, _NoFootage) else str(error)[:300]
            try:
                photo_subject = subject or (run.section_pools[0][1] or run.section_pools[0][0] if run.section_pools else run.theme)
                if self._real_photo(run, scene, position, scene_text, photo_subject, queries):
                    with run.lock:
                        run.completed += 1
                        run.photos.append(position)
                elif self._stock_video(run, scene, position, scene_text, subject, queries):
                    with run.lock:
                        run.completed += 1
                        run.stock.append(position)
                elif is_hook and self._theme_gallery(run, scene, position):
                    # Last real option for the opening: a gallery of genuine photos of the theme's dishes.
                    with run.lock:
                        run.completed += 1
                        run.graphics.append(position)
                elif is_hook:
                    raise ProviderError("No real footage or photo passed for this hook scene; AI images are never used in the hook")
                else:
                    if getattr(run.service, "youtube_blocked", False):
                        # A blocked YouTube is not "no footage exists": leave the scene for the next run.
                        raise ProviderError("YouTube is blocking this computer for now; run sourcing again later")
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
        if str(scene["id"]) in run.hook_ids and subject:
            # Archives name photos plainly ("church supper, 1941"): search the theme without its list noun too.
            plain = " ".join(word for word in subject.split() if word not in _GENERIC_THEME) or subject
            photo_queries = list(dict.fromkeys([f"{run.era} {plain}".strip(), f"vintage {plain}", plain, *photo_queries]))
        items: dict[str, dict[str, Any]] = {}
        for query in [item for item in photo_queries if item.strip()][:4]:
            with run.lock:
                cached = run.searches.get(f"photo:{query}")
            if cached is None:
                # Scenes of one section repeat the same archive searches.
                cached = search_photos(query, period=run.profile.period)
                with run.lock:
                    run.searches[f"photo:{query}"] = cached
            for item in cached:
                items.setdefault(str(item.get("id") or item["url"]), item)
            if len(items) >= 12:
                break
        with run.lock:
            fresh = [item for key, item in items.items()
                     if key not in run.used_photos and not _seen_title(item, run.photo_titles)]
        candidates = list(zip(fresh[:12], load_images([str(item.get("thumbnail") or item["url"]) for item in fresh[:12]])))
        candidates = [(item, image) for item, image in candidates if image is not None]
        ranked = run.verifier.rank_photos(
            [image for _item, image in candidates], subject, f"{run.era} {scene_text}".strip(),
            run.recipe_by_id.get(str(scene["id"]), ""),
        )
        is_hook = str(scene["id"]) in run.hook_ids
        need = self._need(run, scene, subject, run.recipe_by_id.get(str(scene["id"]), ""), is_hook)
        for index in self._judge_photos(run, need, [image for _item, image in candidates], [i for i, _ in ranked])[:2]:
            item = candidates[index][0]
            photo = load_image(str(item["url"]))
            if photo is None:
                continue
            key = str(item.get("id") or item["url"])
            title = _photo_title(item)
            with run.lock:
                if key in run.used_photos or _seen_title(item, run.photo_titles):
                    continue
                run.used_photos.add(key)
                if title:
                    run.photo_titles.append(title)
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

    @staticmethod
    def _need(run: "_Run", scene: dict[str, Any], subject: str, recipe: str, is_hook: bool) -> str:
        """What the judge should see, in plain words."""
        sentence = str(scene.get("narration") or "").strip()
        era = run.era or "mid-century"
        if is_hook and not run.profile.period:
            return (f'The opening of a documentary about {run.theme or subject}. Narration: "{sentence}". '
                    "Needs real footage or photos showing what the sentence is about.")
        if is_hook:
            return (f'The opening of a nostalgic documentary about {run.theme or subject}. Narration: "{sentence}". '
                    f"Needs genuinely old footage or photos from the {era} (modern footage does not fit), showing "
                    "what the sentence is about.")
        dish = recipe or subject
        step = " It is a recipe step, so the action itself should be visible." if is_process_scene(sentence) else ""
        return f'A documentary section about {dish}. Narration: "{sentence}".{step} Show {dish} or this exact moment.'

    def _judge_videos(
        self, run: "_Run", need: str, choices: list[tuple[dict[str, Any], float | None, float | None]], duration: float,
    ) -> list[tuple[dict[str, Any], float | None, float | None]] | None:
        """Claude's order of the best few video choices (unusable ones dropped); None without a judge."""
        if run.judge is None or run.verifier is None or not choices:
            return None
        top = choices[:4]
        rows = [run.verifier.moment_frames(str(item["video_id"]), float(start or 0), duration) for item, start, _ in top]
        keep = [index for index, frames in enumerate(rows) if frames]
        verdicts = run.judge.judge(need, [rows[index] for index in keep])
        if verdicts is None:
            return None
        return [top[keep[index]] for index in best_usable(verdicts)]

    def _judge_photos(self, run: "_Run", need: str, images: list[Any], order: list[int]) -> list[int]:
        """Claude's pick among the CLIP-ranked photos; the CLIP order when there is no judge."""
        if run.judge is None or not order:
            return order
        top = order[:4]
        verdicts = run.judge.judge(need, [[images[index]] for index in top])
        if verdicts is None:
            return order
        return [top[index] for index in best_usable(verdicts)]

    def _theme_gallery(self, run: "_Run", scene: dict[str, Any], position: int) -> bool:
        """A gallery of real photos of the video's dishes, for a hook scene nothing else could fill."""
        from PIL import Image

        from .motion.engine import encode
        from .motion.templates import gallery_stack

        with run.lock:
            if run.verifier is None or not run.theme or run.gallery_done or run.gallery_id:
                return False  # one gallery per video; a second reads as a repeat
            run.gallery_done = True
        noun = next((word for word in reversed(run.theme.split()) if word in PLURAL_FOODS), "dishes")
        dishes = [heading_subject(str(item.get("narration") or "")) for item in self.db.list_scenes(run.project_id)]
        paths = dish_images(noun, [dish for dish in dishes if dish], self.paths.root / "ingredient_library",
                            run.verifier, run.settings, run.era, False, judge=run.judge)
        if len(paths) < 3:
            return False
        duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
        destination = self.paths.project_dir(run.project_id) / "assets" / "graphics" / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.mp4"
        encode(gallery_stack([Image.open(path) for path in paths], run.style), duration, destination,
               ffmpeg_path=run.service.ffmpeg_path)
        asset = self.db.add_asset(
            project_id=run.project_id, scene_id=str(scene["id"]),
            candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
            media_kind="video", provider="graphic", model="gallery", local_path=str(destination),
            remote_url=None, provider_asset_id=None, cost=0.0, metadata={"graphic": "gallery", "items": [{"label": path.stem, "image": str(path)} for path in paths]},
        )
        self.db.select_asset(str(scene["id"]), str(asset["id"]))
        return True

    def _motion_graphic(self, run: "_Run", scene: dict[str, Any], position: int, is_hook: bool) -> bool:
        """Ingredient lists become ingredient cards and 'thirty desserts' becomes a gallery."""
        from PIL import Image

        from .motion.engine import encode
        from .motion.templates import gallery_stack, ingredient_cards

        text = str(scene.get("narration") or "")
        ingredients = ingredient_list(text) if str(scene["id"]) in run.card_ids else []
        many = "" if ingredients or str(scene["id"]) != run.gallery_id else plural_items(text)
        if not ingredients and not many:
            return False
        library = self.paths.root / "ingredient_library"
        # The hook never shows generated images, so there only real photos may fill a graphic.
        allow_generated = not is_hook
        if ingredients:
            pictures = [(name, item_image(name, library, run.verifier, run.settings, run.era, allow_generated,
                                          judge=run.judge)) for name in ingredients]
            pictures = [(name, path) for name, path in pictures if path is not None]
            if len(pictures) < 2:
                return False
            items = [{"label": name, "image": str(path)} for name, path in pictures]
            metadata = {"graphic": "ingredients", "items": items}
            design = run.designs.get(str(scene["id"]), "cards")
            duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
            destination = (self.paths.project_dir(run.project_id) / "assets" / "graphics"
                           / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.mp4")
            if design != "cards":
                try:
                    payload = render_ingredients(design, items, getattr(run.style, "key", ""), duration, destination,
                                                 run.service.ffmpeg_path)
                    return self._save_graphic(run, scene, destination, {**metadata, "design": design, "payload": payload})
                except Exception as error:  # a web design that fails falls back to the classic cards
                    with run.lock:
                        run.notes[position] = f"{design} design failed, classic cards used: {str(error)[:150]}"
            frame = ingredient_cards([(name, Image.open(path)) for name, path in pictures], run.style)
            metadata["design"] = "cards"
        else:
            dishes = [heading_subject(str(item.get("narration") or "")) for item in self.db.list_scenes(run.project_id)]
            paths = dish_images(many, [dish for dish in dishes if dish], library, run.verifier, run.settings,
                                run.era, allow_generated, judge=run.judge)
            if len(paths) < 3:
                return False
            frame = gallery_stack([Image.open(path) for path in paths], run.style)
            metadata = {"graphic": "gallery", "items": [{"label": path.stem, "image": str(path)} for path in paths]}
            with run.lock:
                run.gallery_done = True
        duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
        destination = self.paths.project_dir(run.project_id) / "assets" / "graphics" / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.mp4"
        encode(frame, duration, destination, ffmpeg_path=run.service.ffmpeg_path)
        return self._save_graphic(run, scene, destination, metadata)

    def _save_graphic(self, run: "_Run", scene: dict[str, Any], destination: Path, metadata: dict[str, Any]) -> bool:
        asset = self.db.add_asset(
            project_id=run.project_id, scene_id=str(scene["id"]),
            candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
            media_kind="video", provider="graphic", model=str(metadata["graphic"]), local_path=str(destination),
            remote_url=None, provider_asset_id=None, cost=0.0, metadata=metadata,
        )
        self.db.select_asset(str(scene["id"]), str(asset["id"]))
        return True

    def _stock_video(
        self, run: "_Run", scene: dict[str, Any], position: int, scene_text: str, subject: str, queries: list[str],
    ) -> bool:
        """Free Pexels footage, checked like everything else; False without a key or a match."""
        if run.verifier is None or not run.settings.pexels_api_key:
            return False
        recipe = run.recipe_by_id.get(str(scene["id"]), "")
        items: dict[str, dict[str, Any]] = {}
        for query in list(dict.fromkeys([recipe or core_subject(subject), *(q.replace(" footage", "") for q in queries[:2])])):
            if not query.strip():
                continue
            for item in search_stock_videos(run.settings.pexels_api_key, query):
                items.setdefault(str(item.get("id")), item)
            if len(items) >= 10:
                break
        with run.lock:
            fresh = [item for key, item in items.items() if f"pexels-{key}" not in run.used_photos]
        posters = list(zip(fresh[:10], load_images([str(item.get("image") or "") for item in fresh[:10]])))
        posters = [(item, image) for item, image in posters if image is not None]
        duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
        for index, _score in run.verifier.rank_photos([image for _item, image in posters], subject, scene_text, recipe)[:2]:
            item = posters[index][0]
            if float(item.get("duration") or 0) < duration + 0.5:
                continue
            key = f"pexels-{item.get('id')}"
            with run.lock:
                if key in run.used_photos:
                    continue
                run.used_photos.add(key)
            destination = self.paths.project_dir(run.project_id) / "assets" / "stock" / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.mp4"
            try:
                metadata = download_stock_video(item, destination)
                cuts = shot_cuts(destination, run.service.ffmpeg_path)
                start = cut_free_start(cuts, float(metadata["duration"]), duration, 1.0)
                if start is None:
                    raise ProviderError("No single-shot stretch")
                trim(destination, start, duration, run.service.ffmpeg_path)
                if has_burned_in_text(destination, run.service.ffmpeg_path):
                    raise ProviderError("Burned-in captions")
            except Exception:
                destination.unlink(missing_ok=True)
                continue
            asset = self.db.add_asset(
                project_id=run.project_id, scene_id=str(scene["id"]),
                candidate_index=self.db.next_asset_candidate_index(str(scene["id"])),
                media_kind="video", provider="stock", model="pexels", local_path=str(destination),
                remote_url=metadata["source_url"], provider_asset_id=key, cost=0.0, metadata=metadata,
            )
            self.db.select_asset(str(scene["id"]), str(asset["id"]))
            return True
        return False

    @staticmethod
    def _shows_creator(run: "_Run", clip: Path, archival_ok: bool = False) -> bool:
        from PIL import Image

        try:
            with tempfile.TemporaryDirectory() as folder:
                paths = sample_frames(clip, Path(folder), run.service.ffmpeg_path)
                frames = [Image.open(path).convert("RGB") for path in paths]
                areas = [face_areas(path) for path in paths] if text_guard_available() else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return False
        return run.verifier.shows_creator(frames, areas, archival_ok)

    @staticmethod
    def _single_shot(run: "_Run", clip: Path, metadata: dict[str, Any], wanted: float | None, duration: float) -> None:
        """Trim a padded download to one continuous shot; raise when the moment has no such stretch."""
        downloaded_from = float(metadata.get("source_start_seconds") or 0)
        total = float(metadata.get("source_end_seconds") or downloaded_from + duration) - downloaded_from
        preferred = max(0.0, float(wanted if wanted is not None else downloaded_from) - downloaded_from)
        cuts = shot_cuts(clip, run.service.ffmpeg_path)
        start = cut_free_start(cuts, total, duration, preferred)
        if start is None:
            raise ProviderError("Every stretch of this moment contains a cut")
        trim(clip, start, duration, run.service.ffmpeg_path)
        metadata["source_start_seconds"] = round(downloaded_from + start, 3)
        metadata["source_end_seconds"] = round(downloaded_from + start + duration, 3)
        metadata["single_shot"] = True

    def _progress(self, run: "_Run") -> None:
        with run.lock:
            self._update(
                run.project_id, completed=run.completed, failed=run.failed, errors=list(run.errors),
                review=sorted(run.review), generated=sorted(run.generated), photos=sorted(run.photos),
                graphics=sorted(run.graphics), stock=sorted(run.stock),
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
        blocked_channels: str = "", recipe: str = "", prefer_vintage: bool = False, require_vintage: bool = False,
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
                verifier.mark_faces(video_id)
        passing: list[tuple[float, dict[str, Any], float, float]] = []
        for candidate in candidates:
            video_id = str(candidate["video_id"])
            info = infos.get(video_id)
            if info is None:
                continue
            stream = pick_video_stream(info) or {}
            if int(stream.get("height") or info.get("height") or 0) > int(stream.get("width") or info.get("width") or 1):
                continue  # vertical/Shorts footage leaves black side bars in 16:9
            if len(used.get(video_id, [])) >= 2:
                continue  # one source at most twice per video, so it never feels repetitive
            # The full description is only known after inspection (AI voice/image disclosures).
            if looks_like_ai_slideshow({**candidate, "description": info.get("description")}, blocked_channels):
                continue
            if verifier.synthetic_score(video_id) >= SYNTHETIC_THRESHOLD:
                continue  # looks like AI imagery; prefer real camera footage
            moment = verifier.best_moment(
                video_id, info, subject, scene_text, duration, avoid=used.get(video_id), recipe=recipe,
                prefer_vintage=prefer_vintage or require_vintage, require_vintage=require_vintage,
            )
            if moment is None:
                continue
            start_time, topic_score, score = moment
            if video_id in used:
                score -= 0.05  # prefer a fresh source when quality is similar
            passing.append((score, candidate, start_time, topic_score))
        passing.sort(key=lambda item: item[0], reverse=True)
        return [(candidate, start_time, topic_score) for _score, candidate, start_time, topic_score in passing]
