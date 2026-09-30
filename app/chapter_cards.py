"""Full-screen chapter title cards for script headings ("POOR MAN'S COOKIES")."""
from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

from .channel_styles import get_style
from .database import Database
import re

from .footage_match import _NUMBERING, heading_subject
from .paths import AppPaths

SIZE = (1920, 1080)
THEME_TINTS = {
    "us_nostalgia": (196, 140, 72),
    "british_nostalgia": (112, 134, 104),
    "food_documentary": (188, 118, 60),
    "history_documentary": (150, 118, 78),
}
TITLE_FONTS = (
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
    "C:/Windows/Fonts/georgiab.ttf",
)
LABEL_FONTS = (
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/Avenir Next.ttc",
    "C:/Windows/Fonts/arial.ttf",
)


def _font(candidates: tuple[str, ...], size: int) -> Any:
    from PIL import ImageFont

    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def video_frame(path: Path, at_seconds: float, ffmpeg_path: str = "ffmpeg") -> Any:
    """One decoded frame as a PIL image, or None."""
    from PIL import Image

    try:
        result = subprocess.run(
            [ffmpeg_path, "-v", "error", "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(path),
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, timeout=60, check=True,
        )
        return Image.open(io.BytesIO(result.stdout)).convert("RGB")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def card_text_layout(title: str, number: int, style: Any = None) -> list[dict[str, Any]]:
    """Where a chapter card's words sit (label, then each title line), so an editor can get
    them as separate, editable text layers on top of the text-free card."""
    from PIL import Image, ImageDraw

    from .channel_styles import get_style

    style = style or get_style(None)
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    width, height = SIZE
    script = style.chapter_title_fonts[0].endswith(("SnellRoundhand.ttc", "Brush Script.ttf"))
    words = (title.title() if script else title.upper()).split()
    size = 132
    while True:
        title_font = _font(style.chapter_title_fonts, size)
        lines = _wrap(draw, words, title_font, width * 0.78)
        if len(lines) <= 2 or size <= 64:
            break
        size -= 8
    line_height = int(size * 1.15)
    block = line_height * len(lines)
    top = height // 2 - block // 2 + 30
    label_font = _font(style.chapter_label_fonts, 34)
    # The card spaces the label's letters ("C   H   A   P"); as editable text it stays a word with letter spacing.
    layers = [{
        "role": "chapter label", "text": f"{style.chapter_label} {number}", "x": width / 2, "y": top - 110 + 17,
        "size": 34, "color": tuple(style.chapter_accent), "font": getattr(label_font, "path", ""), "letter_spacing": 1.2,
    }]
    for index, line in enumerate(lines):
        layers.append({
            "role": "chapter title", "text": line, "x": width / 2, "y": top + index * line_height + size * 0.55,
            "size": size, "color": (246, 240, 228), "font": getattr(title_font, "path", ""),
        })
    return layers


def render_card(background: Any, title: str, number: int, theme_id: str, style: Any = None,
                with_text: bool = True) -> Any:
    """Dark, blurred, tinted background with a centred chapter title in the channel's style."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    from .channel_styles import get_style

    style = style or get_style(None)
    tint = style.chapter_tint
    if background is None:
        card = Image.new("RGB", SIZE, tuple(int(channel * 0.35) for channel in tint))
    else:
        card = ImageOps.fit(background, SIZE).filter(ImageFilter.GaussianBlur(14))
        card = Image.blend(card, Image.new("RGB", SIZE, (0, 0, 0)), 0.55)
    card = Image.blend(card, Image.new("RGB", SIZE, tint), 0.18)
    # Vignette: darken the edges so the title reads on any footage.
    mask = Image.new("L", SIZE, 0)
    ImageDraw.Draw(mask).ellipse((-SIZE[0] * 0.15, -SIZE[1] * 0.3, SIZE[0] * 1.15, SIZE[1] * 1.3), fill=255)
    card = Image.composite(card, Image.new("RGB", SIZE, (0, 0, 0)), mask.filter(ImageFilter.GaussianBlur(160)))
    if not with_text:
        return card  # the editable export puts the words on top as text layers

    draw = ImageDraw.Draw(card)
    width, height = SIZE
    # Script lettering (V2) is unreadable in capitals; every other style keeps the capitals.
    words = (title.title() if style.chapter_title_fonts[0].endswith(("SnellRoundhand.ttc", "Brush Script.ttf"))
             else title.upper()).split()
    size = 132
    while True:
        title_font = _font(style.chapter_title_fonts, size)
        lines = _wrap(draw, words, title_font, width * 0.78)
        if len(lines) <= 2 or size <= 64:
            break
        size -= 8
    line_height = int(size * 1.15)
    block = line_height * len(lines)
    top = height // 2 - block // 2 + 30
    label_font = _font(style.chapter_label_fonts, 34)
    label = "   ".join(f"{style.chapter_label} {number}")
    label_width = draw.textlength(label, font=label_font)
    accent = style.chapter_accent
    draw.text(((width - label_width) / 2, top - 110), label, font=label_font, fill=accent)
    rule = 220
    draw.line(((width - rule) / 2, top - 45, (width + rule) / 2, top - 45), fill=accent, width=3)
    for index, line in enumerate(lines):
        line_width = draw.textlength(line, font=title_font)
        x, y = (width - line_width) / 2, top + index * line_height
        draw.text((x + 3, y + 4), line, font=title_font, fill=(0, 0, 0))
        draw.text((x, y), line, font=title_font, fill=(246, 240, 228))
    draw.line(((width - rule) / 2, top + block + 30, (width + rule) / 2, top + block + 30), fill=accent, width=3)
    return card


def _wrap(draw: Any, words: list[str], font: Any, max_width: float) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join([*current, word])
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def heading_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [scene for scene in scenes if heading_subject(str(scene.get("narration") or ""))]


def build_chapter_cards(db: Database, paths: AppPaths, project_id: str, ffmpeg_path: str = "ffmpeg") -> int:
    """Create and select a chapter card for every heading scene. Returns how many were made."""
    project = db.get_project(project_id) or {}
    scenes = db.list_scenes(project_id)
    assets = {str(asset["id"]): asset for asset in db.list_assets(project_id)}
    by_position = {int(scene["position"]): scene for scene in scenes}
    made = 0
    for count, scene in enumerate(heading_scenes(scenes), start=1):
        raw = str(scene["narration"]).strip()
        numbered = re.match(r"\s*#?(\d+)", raw) if _NUMBERING.match(raw) else None
        number = int(numbered.group(1)) if numbered else count
        title = _NUMBERING.sub("", raw).strip(" .:")
        background = None
        # The section's own footage (the scenes right after the heading) sets the mood.
        for offset in (1, 2, 3, 0):
            neighbour = by_position.get(int(scene["position"]) + offset)
            asset = assets.get(str((neighbour or {}).get("selected_asset_id") or ""))
            if not asset or asset.get("provider") == "chapter" or not Path(str(asset.get("local_path"))).is_file():
                continue
            local = Path(str(asset["local_path"]))
            if asset.get("media_kind") == "video":
                background = video_frame(local, 1.0, ffmpeg_path)
            else:
                from PIL import Image
                try:
                    background = Image.open(local).convert("RGB")
                except OSError:
                    background = None
            if background is not None:
                break
        card = render_card(background, title, number, str(project.get("theme_id") or ""),
                           get_style((project.get("effects") or {}).get("channel_style")))
        destination = paths.project_dir(project_id) / "assets" / "chapters" / f"chapter-{number:03d}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        card.save(destination)
        plain = destination.with_name(f"chapter-{number:03d}-plain.png")
        render_card(background, title, number, str(project.get("theme_id") or ""),
                    get_style((project.get("effects") or {}).get("channel_style")), with_text=False).save(plain)
        asset = db.add_asset(
            project_id=project_id, scene_id=str(scene["id"]),
            candidate_index=db.next_asset_candidate_index(str(scene["id"])),
            media_kind="image", provider="chapter", model="chapter-card",
            local_path=str(destination), remote_url=None, provider_asset_id=None, cost=0.0,
            metadata={"chapter": number, "title": title, "plain_path": str(plain)},
        )
        db.select_asset(str(scene["id"]), str(asset["id"]))
        # The card already shows the heading, so no caption on top of it.
        db.update_scene(str(scene["id"]), {"caption_text": "", "timeline_actions": [
            {"type": "motion", "params": {"preset": "slow_push", "strength": 0.4}},
            {"type": "transition", "params": {"preset": "fade", "duration": 0.32}},
        ]})
        made += 1
    return made
