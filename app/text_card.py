"""A scene with no real footage, no new photo and no room for AI shows its own words (Ishaq, 6 Oct).

The sentence's key phrase in big white letters (the key words in yellow) over a dark, blurred real picture
of the item when there is one. It is never a repeat of another scene's shot, so nothing looks duplicated.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SIZE = (1920, 1080)
FONTS = ("/System/Library/Fonts/Supplemental/Impact.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf",
         "/System/Library/Fonts/Helvetica.ttc")


TRAILING = {"the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "for", "with", "by", "at", "as",
            "that", "which", "when", "than", "their", "his", "her", "its", "was", "were", "is", "became", "because"}


def card_phrase(sentence: str, limit: int = 12) -> str:
    """A whole thought, never one cut in the middle ("...THE BISCUITS CRUMBLED IF" - 6 Oct): the sentence when
    it is short, else its first clause, else its first words without a dangling 'the/and/if' at the end."""
    text = " ".join(sentence.split()).strip()
    words = text.split()
    if len(words) > limit:
        clause = re.split(r"[,;:\u2014]| - ", text)[0].split()
        words = clause if 4 <= len(clause) <= limit else words[:limit]
    while len(words) > 3 and re.sub(r"[^\w']", "", words[-1]).lower() in TRAILING:
        words = words[:-1]
    return " ".join(words).strip(" ,.;:-\u2014")


def _font(size: int) -> Any:
    from PIL import ImageFont

    for path in FONTS:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _lines(words: list[str], font: Any, width: int) -> list[str]:
    from PIL import Image, ImageDraw

    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines: list[str] = []
    for word in words:
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and measure.textlength(trial, font=font) <= width:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def render_text_card(sentence: str, destination: Path, backdrop: Path | None = None,
                     highlight: tuple[int, int, int] = (255, 226, 52)) -> Path:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

    if backdrop is not None and backdrop.is_file():
        base = ImageOps.fit(Image.open(backdrop).convert("RGB"), SIZE).filter(ImageFilter.GaussianBlur(18))
        base = ImageEnhance.Brightness(base).enhance(0.38)
    else:
        base = Image.new("RGB", SIZE, (24, 18, 14))
    phrase = card_phrase(sentence).upper()
    words = phrase.split()
    keys = {word for word in words if re.search(r"\d", word)} or {max(words, key=len)} if words else set()
    size = 132
    font = _font(size)
    lines = _lines(words, font, 1500)
    while len(lines) > 3 and size > 70:
        size -= 12
        font = _font(size)
        lines = _lines(words, font, 1500)
    draw = ImageDraw.Draw(base)
    line_height = int(size * 1.12)
    top = (SIZE[1] - line_height * len(lines)) // 2
    for row, line in enumerate(lines):
        width = draw.textlength(line, font=font)
        x = (SIZE[0] - width) / 2
        for word in line.split():
            colour = highlight if word in keys else (255, 255, 255)
            draw.text((x, top + row * line_height), word, font=font, fill=colour, stroke_width=6, stroke_fill=(0, 0, 0))
            x += draw.textlength(word + " ", font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    base.save(destination, quality=92)
    return destination
