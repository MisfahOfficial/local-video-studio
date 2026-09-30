"""A new channel's kit, read from its example videos.

A few frames of each example video are pulled from YouTube (a handful of small requests),
an AI looks at them and describes the look (colours, lettering, captions, graphics, mood),
and that is turned into a locked Channel Kit: colours and fonts for every graphic and
caption, and the 2 chapter + 2 ingredient designs that suit it best.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .channel_styles import FUTURA, HANDWRITTEN, SANS, SANS_BOLD, SCRIPT, SERIF_BOLD, SERIF_ITALIC, TYPEWRITER, STYLES
from .content_profile import youtube_id

FONTS = {"serif": SERIF_BOLD, "sans": SANS_BOLD, "geometric": FUTURA, "script": SCRIPT, "typewriter": TYPEWRITER,
         "handwritten": HANDWRITTEN, "italic serif": SERIF_ITALIC, "clean sans": SANS}

LOOK = {"type": "OBJECT", "properties": {
    "background_dark": {"type": "STRING"}, "background_light": {"type": "STRING"}, "accent": {"type": "STRING"},
    "text_colour": {"type": "STRING"}, "highlight": {"type": "STRING"}, "highlight_text": {"type": "STRING"},
    "title_lettering": {"type": "STRING", "enum": list(FONTS)}, "label_lettering": {"type": "STRING", "enum": list(FONTS)},
    "caption_position": {"type": "STRING", "enum": ["bottom", "middle"]},
    "caption_highlight_words": {"type": "BOOLEAN"}, "film_look": {"type": "BOOLEAN"}, "sepia": {"type": "NUMBER"},
    "mood": {"type": "STRING"}, "era": {"type": "STRING"}, "country": {"type": "STRING", "enum": ["US", "GB", "CA", "other"]},
    "content": {"type": "STRING", "enum": ["vintage_recipe", "modern_brand", "history"]},
    "chapter_designs": {"type": "ARRAY", "items": {"type": "STRING"}},
    "ingredient_designs": {"type": "ARRAY", "items": {"type": "STRING"}},
}, "required": ["background_dark", "background_light", "accent", "text_colour", "highlight", "highlight_text",
                "title_lettering", "label_lettering", "mood", "content", "chapter_designs", "ingredient_designs"]}


def _hex_rgb(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value or "").strip())
    if not match:
        return fallback
    digits = match.group(1)
    return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def example_frames(urls: list[str], ffmpeg_path: str = "ffmpeg", per_video: int = 5) -> list[bytes]:
    """A few full-size frames spread through each example video (one page request + small reads)."""
    from .youtube_source import YouTubeSourceService, pick_video_stream

    service = YouTubeSourceService("", ffmpeg_path, "fair_use")
    frames: list[bytes] = []
    for url in urls[:3]:
        video_id = youtube_id(url)
        if not video_id:
            continue
        info = service.inspect(video_id)
        stream = pick_video_stream(info)
        duration = float(info.get("duration") or 0)
        if not stream or duration <= 0:
            continue
        headers = "".join(f"{key}: {value}\r\n" for key, value in (stream.get("http_headers") or {}).items())
        with tempfile.TemporaryDirectory() as folder:
            for index in range(per_video):
                at = duration * (index + 1) / (per_video + 1)
                target = Path(folder) / f"{index}.png"
                command = [ffmpeg_path, "-v", "error", "-y"] + (["-headers", headers] if headers else [])
                command += ["-ss", f"{at:.1f}", "-i", str(stream["url"]), "-frames:v", "1", "-vf", "scale=960:-2", str(target)]
                try:
                    subprocess.run(command, capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    continue
                if target.is_file():
                    frames.append(target.read_bytes())
    return frames


def analyse_look(frames: list[bytes], settings: Any, name: str) -> dict[str, Any]:
    from .llm import gemini_look
    from .motion_designs import all_designs

    options = {kind: {key: f"{item.name} ({item.mood})" for key, item in all_designs().items() if item.kind == kind}
               for kind in ("chapter", "ingredients")}
    prompt = (
        f"These are frames from example videos of the YouTube channel '{name}'. Describe the channel's visual identity "
        "so new videos can match it. Colours as #rrggbb (backgrounds of graphics/cards, accent, text, caption highlight "
        "box and its text). Pick the lettering families that match its titles and labels. Say where captions sit, "
        "whether single words are highlighted, whether footage looks aged/film-like and how sepia (0-1). "
        f"Choose the 2 best-matching chapter-card designs from {options['chapter']} and the 2 best ingredient designs "
        f"from {options['ingredients']} (return their keys). Also give mood, era, country and content type."
    )
    return gemini_look(settings, prompt, frames[:10], LOOK)


def kit_from_look(look: dict[str, Any], name: str, references: list[str]) -> dict[str, Any]:
    """Turn the AI's description into a locked Channel Kit (a full style plus approved designs)."""
    from .motion_designs import all_designs

    base = STYLES["v3"]
    dark = _hex_rgb(look.get("background_dark"), base.bg_outer)
    light = _hex_rgb(look.get("background_light"), base.bg_inner)
    accent = _hex_rgb(look.get("accent"), base.chapter_accent)
    text = _hex_rgb(look.get("text_colour"), base.label_color)
    highlight = _hex_rgb(look.get("highlight"), base.highlight)
    title_fonts = FONTS.get(str(look.get("title_lettering")), SERIF_BOLD)
    label_fonts = FONTS.get(str(look.get("label_lettering")), FUTURA)
    style_fields = {
        "key": "", "name": name, "background": "vignette", "bg_inner": light, "bg_outer": dark, "card": "rounded",
        "card_accent": accent, "label_color": text, "label_fonts": label_fonts, "label_spaced": label_fonts in (FUTURA, SANS),
        "highlight": highlight, "highlight_text": _hex_rgb(look.get("highlight_text"), base.highlight_text),
        "highlight_fonts": title_fonts, "caption_fonts": SANS, "chapter_tint": light, "chapter_accent": accent,
        "chapter_title_fonts": title_fonts, "chapter_label_fonts": label_fonts, "chapter_label": "CHAPTER",
        "print_border": (246, 244, 238), "sepia": max(0.0, min(1.0, float(look.get("sepia") or 0.3))), "subscribe": (204, 0, 0),
    }
    known = all_designs()
    chapter = [key for key in look.get("chapter_designs") or [] if known.get(key) and known[key].kind == "chapter"][:3]
    ingredients = [key for key in look.get("ingredient_designs") or [] if known.get(key) and known[key].kind == "ingredients"][:3]
    return {
        "name": name, "style_fields": style_fields, "country": look.get("country") or "US",
        "profile": look.get("content") or "vintage_recipe", "mood": look.get("mood") or "", "era": look.get("era") or "",
        "chapter_designs": chapter or ["classic", "typewriter_card"],
        "ingredient_designs": ingredients or ["cards", "chalkboard"],
        "captions": {"animation": "highlight" if look.get("caption_highlight_words", True) else "plain",
                     "position": look.get("caption_position") or "bottom"},
        "film_look": bool(look.get("film_look", True)), "extras": ["map", "price", "years", "comment"],
        "references": references, "locked": True, "from_examples": True,
    }


def create_channel(root: Path, name: str, references: list[str], settings: Any, ffmpeg_path: str = "ffmpeg") -> tuple[str, dict[str, Any]]:
    from .channel_kits import load_kits, save_kit

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "channel"
    key = f"c-{slug}"
    existing = load_kits(root)
    suffix = 2
    while key in existing:
        key = f"c-{slug}-{suffix}"
        suffix += 1
    frames = example_frames(references, ffmpeg_path)
    if not frames:
        raise RuntimeError("Could not read the example videos (check the links, or YouTube may be blocking for now)")
    kit = kit_from_look(analyse_look(frames, settings, name), name, references)
    kit["style_fields"]["key"] = key
    save_kit(root, key, kit)
    return key, kit
