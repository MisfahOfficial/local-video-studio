"""Many looks for the same kind of motion graphic, chosen per video by AI so graphics never
repeat the same way every time.

Designs run on three engines: the tool's own Python templates, HyperFrames (HTML + GSAP)
and Remotion (React). Every design gets its word positions from here, so the same numbers
place the words in the render and as editable text layers in Premiere/CapCut.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .channel_styles import ChannelStyle, get_style

WEB_ROOT = Path(__file__).resolve().parent.parent / "motion_web"
W, H = 1920, 1080


@dataclass(frozen=True)
class Design:
    key: str
    kind: str  # "ingredients" or "chapter"
    engine: str  # "python", "hyperframes", "remotion"
    name: str
    mood: str  # what it suits, for the AI picker


DESIGNS: dict[str, Design] = {item.key: item for item in (
    Design("cards", "ingredients", "python", "Photo cards on the channel backdrop", "clean, classic, any topic"),
    Design("recipe_book", "ingredients", "hyperframes", "Handwritten recipe notebook with polaroids and ticks",
           "homely, grandma, baking, desserts, nostalgic"),
    Design("carousel", "ingredients", "remotion", "Shelf of photos dropping in with price-tag labels",
           "lively, shopping, brands, groceries, playful"),
    Design("classic", "chapter", "python", "Blurred footage with centred serif title", "calm, documentary, any topic"),
    Design("film_slate", "chapter", "hyperframes", "Film-leader countdown, then the title over sepia footage",
           "old films, history, 1930s-1960s, cinematic"),
    Design("newspaper", "chapter", "remotion", "Spinning newspaper stops on a headline", "news, scandal, events, brands, 1920s-1970s"),
)}


def web_engines_ready() -> bool:
    if os.environ.get("LVS_NO_WEB_DESIGNS"):  # tests and machines without Node
        return False
    return (WEB_ROOT / "node_modules" / ".bin" / "hyperframes").exists() and _node() is not None


def _node() -> str | None:
    for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node", shutil.which("node") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def available(kind: str, remotion: bool = True) -> list[str]:
    ready = web_engines_ready()
    return [key for key, design in DESIGNS.items() if design.kind == kind
            and (design.engine == "python" or (ready and (design.engine != "remotion" or remotion)))]


# ------------------------------------------------------------------ choosing (AI, never the same way twice)
def _history_path(root: Path) -> Path:
    return root / "motion_history.json"


def recent_designs(root: Path, channel: str) -> list[str]:
    try:
        return list(json.loads(_history_path(root).read_text()).get(channel, []))[-12:]
    except (OSError, ValueError):
        return []


def remember_designs(root: Path, channel: str, used: list[str]) -> None:
    path = _history_path(root)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data[channel] = (list(data.get(channel, [])) + used)[-40:]
    path.write_text(json.dumps(data))


def plan_designs(kind: str, count: int, script: str, channel: str, root: Path, settings: Any = None,
                 remotion: bool = True) -> list[str]:
    """One design per graphic of this kind, in story order: AI picks what suits the script;
    the channel's recent videos and the previous graphic are never repeated."""
    options = available(kind, remotion)
    if count <= 0 or not options:
        return []
    recent = recent_designs(root, channel)
    ranked = _ai_rank(kind, options, script, recent, settings) or sorted(
        options, key=lambda key: (recent[-6:].count(key), random.random()))
    plan: list[str] = []
    for index in range(count):
        # Rotate through the AI's order; the same look never appears twice in a row.
        choice = ranked[index % len(ranked)]
        if plan and choice == plan[-1] and len(ranked) > 1:
            choice = ranked[(index + 1) % len(ranked)]
        plan.append(choice)
    return plan


def _ai_rank(kind: str, options: list[str], script: str, recent: list[str], settings: Any) -> list[str]:
    key = str(getattr(settings, "gemini_api_key", "") or "")
    model = str(getattr(settings, "gemini_model", "") or "gemini-2.5-flash")
    if not key:
        return []
    from .providers.base import ProviderError
    from .providers.http import post_json

    catalog = [{"key": option, "look": DESIGNS[option].name, "suits": DESIGNS[option].mood} for option in options]
    prompt = (
        f"You pick motion-graphic designs for a faceless YouTube video. Kind: {kind}.\n"
        f"Designs: {json.dumps(catalog)}\nUsed in this channel's last videos (avoid leading with these): {recent[-6:]}\n"
        f"Script (start): {script[:2500]}\n"
        "Return every design key once, best fit for this video first. Vary from the recent ones."
    )
    try:
        response = post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key.strip()}",
            {"contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"response_mime_type": "application/json", "temperature": 0.9,
                                  "response_schema": {"type": "ARRAY", "items": {"type": "STRING"}}}},
            timeout=40,
        )
        picked = json.loads(response["candidates"][0]["content"]["parts"][0]["text"])
    except (ProviderError, KeyError, IndexError, TypeError, ValueError):
        return []
    ordered = [item for item in dict.fromkeys(picked) if item in options]
    return ordered + [item for item in options if item not in ordered]


# ------------------------------------------------------------------ layout (shared by render and editable export)
def _hex(color: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(int(channel) for channel in color[:3])


def _family(paths: tuple[str, ...]) -> str:
    for path in paths:
        if Path(path).is_file():
            stem = Path(path).stem
            return {"SnellRoundhand": "Snell Roundhand", "AmericanTypewriter": "American Typewriter",
                    "Avenir Next": "Avenir Next"}.get(stem, stem.replace(" Bold Italic", "").replace(" Bold", ""))
    return "Georgia"


def _font_file(paths: tuple[str, ...]) -> str:
    return next((path for path in paths if Path(path).is_file()), "")


def ingredient_payload(design: str, items: list[dict[str, str]], style: ChannelStyle, seconds: float,
                       show_text: bool = True) -> dict[str, Any]:
    items = items[:5]
    count = max(1, len(items))
    placed = []
    if design == "recipe_book":
        for index, item in enumerate(items):
            placed.append({**item, "photo_x": 1150 + (index % 2) * 160, "photo_y": 150 + index * 150,
                           "tilt": (-6, 5, -3, 7, -5)[index], "text_x": 700, "text_y": 290 + index * 120})
    else:  # carousel
        gap = 60
        left = (W - (360 * count + gap * (count - 1))) / 2
        for index, item in enumerate(items):
            x = left + index * (360 + gap)
            placed.append({**item, "photo_x": x, "photo_y": 400, "text_x": x + 180, "text_y": 850})
    return {
        "seconds": seconds, "show_text": show_text, "bg_inner": _hex(style.bg_inner), "bg_outer": _hex(style.bg_outer),
        "accent": _hex(style.highlight), "ink": _hex(style.highlight_text if design == "carousel" else style.label_color
                                                    if style.background != "vignette" else (40, 34, 26)),
        "label_font": _family(style.label_fonts), "title_font": _family(style.chapter_title_fonts),
        "heading": "Ingredients" if design == "recipe_book" else "", "heading_x": 760, "heading_y": 170,
        "heading_color": _hex(style.card_accent),
        "items": [{"label": item["label"].title(), "image": item["image"], "photo_x": item["photo_x"],
                   "photo_y": item["photo_y"], "tilt": item.get("tilt", 0), "text_x": item["text_x"],
                   "text_y": item["text_y"]} for item in placed],
    }


def chapter_payload(design: str, title: str, number: int, style: ChannelStyle, seconds: float, background: str = "",
                    show_text: bool = True) -> dict[str, Any]:
    title_font = _family(style.chapter_title_fonts)
    label_font = _family(style.chapter_label_fonts)
    if design == "newspaper":
        texts = [
            {"text": "THE DAILY KITCHEN", "x": 960, "y": 245, "size": 70, "color": "#2b241b", "font": "Georgia", "spacing": 4},
            {"text": f"{style.chapter_label} {number}", "x": 960, "y": 370, "size": 36, "color": "#6b5a44", "font": label_font, "spacing": 6},
            {"text": title.upper(), "x": 960, "y": 520, "size": 104 if len(title) < 18 else 78, "color": "#1e180e", "font": title_font},
        ]
    else:  # film_slate
        texts = [
            {"text": f"{style.chapter_label} {number}", "x": 960, "y": 400, "size": 38, "color": _hex(style.chapter_accent),
             "font": label_font, "spacing": 10},
            {"text": title.title() if "Snell" in title_font else title.upper(), "x": 960, "y": 540,
             "size": 120 if len(title) < 16 else 88, "color": "#f6f0e4", "font": title_font},
        ]
    return {"seconds": seconds, "show_text": show_text, "texts": texts, "background": background,
            "bg_inner": _hex(style.bg_inner), "bg_outer": _hex(style.bg_outer), "accent": _hex(style.chapter_accent),
            "ink": "#1e180e", "label_font": label_font, "title_font": title_font}


def text_layers(metadata: dict[str, Any], style: ChannelStyle) -> list[dict[str, Any]]:
    """Editable-export text for a web design (same positions as the render)."""
    design = str(metadata.get("design") or "")
    payload = metadata.get("payload") or {}
    if design in {"recipe_book", "carousel"}:
        layers = []
        for index, item in enumerate(payload.get("items") or []):
            layers.append({"role": "ingredient label", "text": item["label"], "x": item["text_x"], "y": item["text_y"],
                           "size": 54 if design == "recipe_book" else 44, "color": _rgb(payload.get("ink", "#222222")),
                           "font": _font_file(style.label_fonts), "appear": 0.7 + index * 0.45})
        if payload.get("heading"):
            layers.append({"role": "heading", "text": payload["heading"], "x": payload["heading_x"],
                           "y": payload["heading_y"], "size": 64, "color": _rgb(payload.get("heading_color", "#aa3333")),
                           "font": _font_file(style.chapter_title_fonts), "appear": 0.4})
        return layers
    if design in {"film_slate", "newspaper"}:
        return [{"role": "chapter text", "text": text["text"], "x": text["x"], "y": text["y"], "size": text["size"],
                 "color": _rgb(text["color"]), "font": _font_file(style.chapter_title_fonts if index else style.chapter_label_fonts),
                 "letter_spacing": float(text.get("spacing") or 0) / 10, "appear": 1.0}
                for index, text in enumerate(payload.get("texts") or [])]
    return []


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


# ------------------------------------------------------------------ rendering
_render_lock = threading.Lock()  # one Chrome render at a time keeps an 8 GB Mac responsive


def render_design(design: str, payload: dict[str, Any], destination: Path, ffmpeg_path: str = "ffmpeg") -> Path:
    """Render a HyperFrames or Remotion design to an MP4 (pictures are copied next to the template)."""
    spec = DESIGNS[design]
    node = _node()
    if node is None:
        raise RuntimeError("Node.js is not installed, so web motion designs cannot render")
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1",
           "PATH": f"{Path(node).parent}:{Path(ffmpeg_path).parent if '/' in ffmpeg_path else ''}:{os.environ.get('PATH', '')}"}
    with _render_lock, tempfile.TemporaryDirectory(dir=WEB_ROOT) as work:
        folder = Path(work)
        payload = json.loads(json.dumps(payload))
        pictures = [item for item in payload.get("items") or []]
        if spec.engine == "hyperframes":
            shutil.copytree(WEB_ROOT / "hf" / design, folder, dirs_exist_ok=True)
            shutil.copy2(WEB_ROOT / "hf" / "gsap.min.js", folder / "gsap.min.js")
            for index, item in enumerate(pictures):
                name = f"img{index}{Path(item['image']).suffix or '.jpg'}"
                shutil.copy2(item["image"], folder / name)
                item["image"] = name
            if payload.get("background"):
                shutil.copy2(payload["background"], folder / "background.jpg")
                payload["background"] = "background.jpg"
            html = (folder / "index.html").read_text()
            (folder / "index.html").write_text(html.replace(
                'data-duration="6"', f'data-duration="{payload["seconds"]:.2f}"').replace(
                'data-duration="4"', f'data-duration="{payload["seconds"]:.2f}"'))
            variables = folder / "variables.json"
            variables.write_text(json.dumps({"payload": json.dumps(payload)}))
            command = [str(WEB_ROOT / "node_modules" / ".bin" / "hyperframes"), "render", str(folder),
                       "-o", str(destination), "--variables-file", str(variables), "--quiet", "--workers", "2"]
        else:
            public = WEB_ROOT / "remotion" / "public" / folder.name
            public.mkdir(parents=True)
            try:
                for index, item in enumerate(pictures):
                    name = f"{folder.name}/img{index}{Path(item['image']).suffix or '.jpg'}"
                    shutil.copy2(item["image"], WEB_ROOT / "remotion" / "public" / name)
                    item["image"] = name
                if payload.get("background"):
                    name = f"{folder.name}/background.jpg"
                    shutil.copy2(payload["background"], WEB_ROOT / "remotion" / "public" / name)
                    payload["background"] = name
                props = folder / "props.json"
                props.write_text(json.dumps({"payload": payload}))
                command = [str(WEB_ROOT / "node_modules" / ".bin" / "remotion"), "render", "remotion/src/index.tsx",
                           {"carousel": "Carousel", "newspaper": "Newspaper"}[design], str(destination),
                           f"--props={props}", "--log=error", "--public-dir=remotion/public", "--concurrency=2"]
                return _run(command, env, destination)
            finally:
                shutil.rmtree(public, ignore_errors=True)
        return _run(command, env, destination)


def _run(command: list[str], env: dict[str, str], destination: Path) -> Path:
    result = subprocess.run(command, cwd=WEB_ROOT, env=env, capture_output=True, text=True, timeout=900)
    if result.returncode or not destination.is_file():
        raise RuntimeError(f"Motion design render failed: {(result.stderr or result.stdout)[-400:]}")
    return destination


def render_ingredients(design: str, items: list[dict[str, str]], style_key: str, seconds: float, destination: Path,
                       ffmpeg_path: str = "ffmpeg", show_text: bool = True) -> dict[str, Any]:
    style = get_style(style_key)
    payload = ingredient_payload(design, items, style, seconds, show_text)
    render_design(design, payload, destination, ffmpeg_path)
    return payload


def render_chapter(design: str, title: str, number: int, style_key: str, seconds: float, background: str,
                   destination: Path, ffmpeg_path: str = "ffmpeg", show_text: bool = True) -> dict[str, Any]:
    style = get_style(style_key)
    payload = chapter_payload(design, title, number, style, seconds, background, show_text)
    render_design(design, payload, destination, ffmpeg_path)
    return payload
