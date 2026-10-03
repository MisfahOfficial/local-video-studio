"""The AI checker: after sourcing, an AI looks at every real clip and photo next to its sentence.

Clips that do not show what the sentence is about (a coffee cake for "Peet's Coffee", a sandwich film
for almond biscuits) are sent back for new footage. Every rejection is remembered per subject, and so
is every clip Ishaq replaces by hand; a source refused twice for a subject is never offered for it again.
Ten scenes go in one labelled contact sheet, so a 277-scene video is about 28 requests.
"""
from __future__ import annotations

import io
import json
import string
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

MEMORY_FILE = "checker_memory.json"
BAN_AFTER = 2  # rejections of one source for one subject before it is never used for that subject again
PER_SHEET = 10
REAL = {"youtube", "photo", "stock"}  # stock too: a strawberry pie stock shot covered "eggnog pie" (3 Oct test)
_lock = threading.Lock()

SCHEMA = {"type": "OBJECT", "properties": {"verdicts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "label": {"type": "STRING"}, "fits": {"type": "BOOLEAN"}, "score": {"type": "INTEGER"},
    "reason": {"type": "STRING"}}, "required": ["label", "fits", "score", "reason"]}}}, "required": ["verdicts"]}


# ------------------------------------------------------------------ memory
def _memory_path(root: Path) -> Path:
    return Path(root) / MEMORY_FILE


def load_memory(root: Path) -> dict[str, dict[str, int]]:
    try:
        data = json.loads(_memory_path(root).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_rejection(root: Path, subject: str, video_id: str, weight: int = 1) -> None:
    """weight 1: the AI checker refused it; 2: Ishaq replaced it himself (banned at once)."""
    subject, video_id = subject.strip().lower(), str(video_id or "").strip()
    if not subject or not video_id:
        return
    with _lock:
        memory = load_memory(root)
        memory.setdefault(subject, {})[video_id] = int(memory.get(subject, {}).get(video_id, 0)) + weight
        path = _memory_path(root)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(memory, indent=1))
        temporary.replace(path)


def banned_sources(root: Path) -> dict[str, set[str]]:
    """subject -> sources never to use for it again."""
    return {subject: {video for video, count in videos.items() if count >= BAN_AFTER}
            for subject, videos in load_memory(root).items()}


# ------------------------------------------------------------------ looking
def _frames(path: Path, ffmpeg_path: str, video: bool) -> list[Any]:
    from PIL import Image

    if not path.is_file():
        return []
    if not video:
        with Image.open(path) as image:
            return [image.convert("RGB").copy()]
    ffprobe = str(Path(ffmpeg_path).with_name("ffprobe")) if "/" in ffmpeg_path else "ffprobe"
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30)
    try:
        length = float(probe.stdout.strip() or 0)
    except ValueError:
        length = 0.0
    frames = []
    with tempfile.TemporaryDirectory() as folder:
        for index, share in enumerate((0.2, 0.5, 0.8)):
            target = Path(folder) / f"{index}.jpg"
            at = max(0.0, length * share)
            subprocess.run([ffmpeg_path, "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=320:-2", str(target)], capture_output=True, timeout=60)
            if target.is_file():
                with Image.open(target) as image:
                    frames.append(image.convert("RGB").copy())
    return frames


def era_rule(era: str) -> str:
    """For channels about the past: a present-day look is wrong even when the dish is right (V2 test:
    hands with a smartwatch in a 1970s divinity scene)."""
    if not era:
        return ""
    return (f"This channel tells stories of the {era} past. fits = false also when the shot clearly shows present-day "
            "things: a smartwatch or smartphone, a modern stainless or induction kitchen, modern clothes or hairstyles "
            "in close-up, or a present-day presenter. Old footage, old photos and simple close-ups of food or hands "
            "without modern objects are fine.\n")


def _ask(settings: Any, rows: list[dict[str, Any]], sheet: Any, era: str = "", style_lines: str = "") -> dict[str, dict[str, Any]]:
    from .llm import gemini_look

    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    lines = "\n".join(f"{row['label']}: section \"{row['subject'] or 'general'}\" | sentence: \"{row['sentence'][:220]}\""
                      for row in rows)
    prompt = (
        "You check B-roll for a faceless YouTube documentary, the way a human editor would. The image has labelled "
        "rows; each row is one scene's clip (frames left to right) or photo. Each label below gives the SECTION "
        "(the dish, brand, restaurant or place that part of the video is about) and the narrator's sentence.\n"
        "fits = true when the picture shows the section's subject in any way: being made, its ingredients, the "
        "finished item, the shop/restaurant, its packaging or ads. Story sentences (history, arguments, prices, "
        "memories) are normally covered with such shots, so do NOT ask for the sentence's exact moment, and a "
        "different step of making the SAME item (rolling pastry while the sentence mentions the jam) still fits.\n"
        "fits = false only when: it shows a DIFFERENT dish, brand, restaurant or place (coffee cake is not a coffee "
        "shop, a brownie sundae is not ice cream cone cakes, Applebee's is not Red Robin); or it is unrelated to "
        "the section (a sandwich film for biscuits, random people, a blank or title screen); or the sentence "
        "clearly names something else that should be seen instead (a map, a factory, a specific person); or the "
        "frames show a person doing something unrelated to the food (opening a drawer or cupboard, walking, "
        "talking, tidying) instead of the food or its making; or the picture is too dark to see the food.\n"
        + era_rule(era) + style_lines +
        "Lines that talk to the viewer (comment, subscribe, which one do you miss) fit any shot of the section's "
        "subject.\n"
        "score 0-10 for how well it works there. reason: at most 10 words.\n\n" + lines)
    for attempt in range(3):
        try:
            answer = gemini_look(settings, prompt, [buffer.getvalue()], SCHEMA)
            break
        except Exception as error:
            # "high demand" (503) and dropped connections pass in seconds; a used-up quota (429) does not.
            if attempt == 2 or not any(code in str(error) for code in (
                    "503", "UNAVAILABLE", "high demand", "Broken pipe", "timed out", "Connection reset", "urlopen error")):
                raise
            time.sleep(8 * (attempt + 1))
    return {str(item.get("label") or "").strip().upper(): item for item in (answer or {}).get("verdicts") or []}


def check_scenes(db: Any, root: Path, settings: Any, project_id: str, ffmpeg_path: str = "ffmpeg",
                 progress: Any = None, only_ids: set[str] | None = None,
                 skip_ids: set[str] | None = None) -> dict[str, Any]:
    """Look at every real clip/photo (or only `only_ids`, minus `skip_ids`: one item while sourcing goes on).
    Returns {"checked", "rejected": [scene ids], "notes", "error"}."""
    from .ai_judge import contact_sheet
    from .footage_match import scene_subjects

    scenes = db.list_scenes(project_id)
    assets = {str(asset["id"]): asset for asset in db.list_assets(project_id)}
    subjects = scene_subjects(scenes, "")
    era = ""
    try:  # channels about the past (vintage recipes, history) also refuse present-day looks
        from .content_profile import profile_for
        from .footage_match import detect_era

        project = db.get_project(project_id) or {}
        if profile_for(project).period:
            era = detect_era(str(project.get("script") or "")) or "mid-century"
    except Exception:
        era = ""
    style_lines = ""
    try:  # the channel's editing style: its never-show list and reject/accept rules
        from .editing_style import checker_rules, load_style

        style_lines = checker_rules(load_style(root, ((db.get_project(project_id) or {}).get("effects") or {}).get("channel_style")))
    except Exception:
        style_lines = ""
    todo = []
    for scene, subject in zip(scenes, subjects):
        if (only_ids is not None and str(scene["id"]) not in only_ids) or str(scene["id"]) in (skip_ids or ()):
            continue
        asset = assets.get(str(scene.get("selected_asset_id") or ""))
        if not asset or asset.get("provider") not in REAL or (asset.get("metadata") or {}).get("variant"):
            continue
        metadata = asset.get("metadata") or {}
        todo.append({"scene": scene, "asset": asset, "subject": str(metadata.get("topic") or subject or ""),
                     "sentence": str(scene.get("narration") or "")})
    result: dict[str, Any] = {"checked": 0, "rejected": [], "notes": {}, "error": ""}
    batches = [todo[start:start + PER_SHEET] for start in range(0, len(todo), PER_SHEET)]
    done = [0]
    tally: dict[tuple[str, str], list[int]] = {}  # (subject, source) -> [clips checked, clips refused]

    def look(batch: list[dict[str, Any]]) -> None:
        if result["error"]:
            return  # quota or key trouble: stop asking, keep what is sourced
        rows, frames = [], []
        for item in batch:
            shots = _frames(Path(str(item["asset"]["local_path"])), ffmpeg_path, item["asset"].get("media_kind") == "video")
            if shots:
                rows.append({**item, "label": string.ascii_uppercase[len(rows)]})
                frames.append(shots)
        if not rows:
            return
        try:
            verdicts = _ask(settings, rows, contact_sheet(frames), era, style_lines)
        except Exception as error:  # no quota or no key: the video is kept as sourced, and the user is told
            with _lock:
                result["error"] = f"AI checker stopped early: {str(error)[:160]}"
            return
        for item in rows:
            verdict = verdicts.get(item["label"])
            if not verdict:
                continue
            refused = not verdict.get("fits") or int(verdict.get("score") or 0) < 4
            try:  # remembered on the clip: only approved shots may be reused for a scene with no footage
                db.update_asset_metadata(str(item["asset"]["id"]), {"checker": "refused" if refused else "ok"})
            except Exception:
                pass
            source = (item["subject"], str(item["asset"].get("provider_asset_id") or ""))
            with _lock:
                result["checked"] += 1
                tally.setdefault(source, [0, 0])[0] += 1
                if refused:
                    tally[source][1] += 1
                    result["rejected"].append(str(item["scene"]["id"]))
                    result["notes"][int(item["scene"].get("position") or 0)] = str(verdict.get("reason") or "")[:120]
        with _lock:
            done[0] += len(batch)
        if progress:
            progress(done[0], len(todo))

    # Five sheets at a time: the AI's answer takes ~20 s per sheet (one at a time was 15 min per video).
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(look, batches))
    # A source counts as wrong for its subject only when most of its clips were refused in this video
    # (two refused moments of the right dish's video must not ban it); it is banned after two such videos.
    for (subject, video_id), (checked, refused) in tally.items():
        if refused * 2 > checked:
            remember_rejection(root, subject, video_id)
    return result


def remember_replacement(db: Any, root: Path, scene_id: str, new_asset_id: str) -> None:
    """Ishaq picked different media for an auto-sourced scene: that source is wrong for this subject."""
    scene = db.get_scene(scene_id) or {}
    old_id = str(scene.get("selected_asset_id") or "")
    if not old_id or old_id == new_asset_id:
        return
    old = next((asset for asset in db.list_assets(str(scene.get("project_id")), scene_id) if str(asset["id"]) == old_id), None)
    metadata = (old or {}).get("metadata") or {}
    if old and old.get("provider") == "youtube" and metadata.get("auto_sourced") and metadata.get("topic"):
        remember_rejection(root, str(metadata["topic"]), str(old.get("provider_asset_id") or ""), weight=BAN_AFTER)
