"""Reference-style graphics: polaroid stack, graph-paper card, keyword-highlight captions."""
from __future__ import annotations

import random
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .engine import (
    SANS_FONTS, SERIF_ITALIC_FONTS, SIZE, Frame, ease_in_out, ease_out_back, ease_out_cubic, font,
)

W, H = SIZE


def sepia(image: Image.Image, strength: float = 0.85) -> Image.Image:
    grey = ImageOps.grayscale(image)
    toned = ImageOps.colorize(grey, black=(28, 22, 16), white=(244, 232, 208), mid=(150, 128, 100))
    return Image.blend(image.convert("RGB"), toned, strength)


def _shadow(size: tuple[int, int], radius: int, blur: int, opacity: int = 150, corner: int = 0) -> Image.Image:
    pad = blur * 3
    layer = Image.new("RGBA", (size[0] + pad * 2, size[1] + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (pad, pad, pad + size[0], pad + size[1]), radius=corner, fill=(0, 0, 0, opacity),
    )
    return layer.filter(ImageFilter.GaussianBlur(blur))


# ---------------------------------------------------------------- polaroid stack
def polaroid_stack(photo: Image.Image, *, seed: int = 7, vintage: bool = True) -> Frame:
    """Several white-bordered prints cut from one photo slide in and settle, tilted, over a dim copy."""
    rng = random.Random(seed)
    base = sepia(ImageOps.fit(photo.convert("RGB"), SIZE)) if vintage else ImageOps.fit(photo.convert("RGB"), SIZE)
    background = Image.blend(base.filter(ImageFilter.GaussianBlur(3)), Image.new("RGB", SIZE, (0, 0, 0)), 0.45)
    prints: list[dict[str, Any]] = []
    layouts = [  # (crop centre x, y, print width, final x, final y, angle, delay)
        (0.30, 0.35, 0.46, 0.27, 0.30, -6.0, 0.00),
        (0.62, 0.55, 0.62, 0.55, 0.58, 4.5, 0.45),
        (0.85, 0.72, 0.36, 0.86, 0.80, -9.0, 0.90),
    ]
    for cx, cy, width_share, fx, fy, angle, delay in layouts:
        inner_w = int(W * width_share)
        inner_h = int(inner_w * 0.66)
        left = int(min(max(cx * W - inner_w / 2, 0), W - inner_w))
        top = int(min(max(cy * H - inner_h / 2, 0), H - inner_h))
        crop = base.crop((left, top, left + inner_w, top + inner_h))
        border = max(14, inner_w // 28)
        card = Image.new("RGBA", (inner_w + border * 2, inner_h + border * 2), (246, 244, 238, 255))
        card.paste(crop, (border, border))
        shadow = _shadow(card.size, 0, 18, 140)
        combo = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
        combo.alpha_composite(shadow, (10, 16))
        pad = (shadow.size[0] - card.size[0]) // 2
        combo.alpha_composite(card, (pad, pad))
        combo = combo.rotate(angle + rng.uniform(-1.5, 1.5), resample=Image.BICUBIC, expand=True)
        prints.append({"image": combo, "x": fx * W, "y": fy * H, "delay": delay,
                       "from": rng.choice([(-1, 0), (1, 0), (0, 1), (0, -1)])})

    def frame(t: float, duration: float) -> Image.Image:
        zoom = 1.0 + 0.05 * ease_in_out(t / max(duration, 0.1))
        scaled = background.resize((int(W * zoom), int(H * zoom)))
        canvas = scaled.crop(((scaled.width - W) // 2, (scaled.height - H) // 2,
                              (scaled.width - W) // 2 + W, (scaled.height - H) // 2 + H)).convert("RGBA")
        for item in prints:
            progress = ease_out_back((t - item["delay"]) / 0.55, 1.1)
            if t < item["delay"]:
                continue
            dx, dy = item["from"]
            x = item["x"] + dx * (1 - progress) * W * 0.8 - item["image"].width / 2
            y = item["y"] + dy * (1 - progress) * H * 0.8 - item["image"].height / 2
            canvas.alpha_composite(item["image"], (int(x), int(y)))
        return canvas.convert("RGB")

    return frame


# ---------------------------------------------------------------- graph paper card
def graph_paper_background() -> Image.Image:
    paper = Image.new("RGB", SIZE, (238, 238, 234))
    draw = ImageDraw.Draw(paper)
    step = 36
    for x in range(0, W, step):
        draw.line((x, 0, x, H), fill=(205, 205, 205) if x % (step * 5) else (185, 185, 185), width=1)
    for y in range(0, H, step):
        draw.line((0, y, W, y), fill=(205, 205, 205) if y % (step * 5) else (185, 185, 185), width=1)
    return paper


def graph_paper_card(photo: Image.Image, *, vintage: bool = True) -> Frame:
    """A rounded photo with an inner vignette pops onto graph paper, then drifts in slowly."""
    paper = graph_paper_background()
    card_w, card_h = int(W * 0.86), int(H * 0.82)
    image = ImageOps.fit(photo.convert("RGB"), (card_w, card_h))
    if vintage:
        image = sepia(image, 0.35)
    radius = 38
    mask = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_w, card_h), radius=radius, fill=255)
    # Inner vignette, like a print mounted behind a rounded window.
    vignette = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(vignette).rounded_rectangle((0, 0, card_w, card_h), radius=radius, outline=190, width=60)
    image = Image.composite(Image.new("RGB", (card_w, card_h), (0, 0, 0)), image,
                            vignette.filter(ImageFilter.GaussianBlur(45)).point(lambda value: value * 0.55))
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    card.paste(image, (0, 0), mask)
    shadow = _shadow((card_w, card_h), 0, 22, 120, radius)

    def frame(t: float, duration: float) -> Image.Image:
        appear = ease_out_back(t / 0.6, 1.2)
        scale = (0.82 + 0.18 * appear) * (1.0 + 0.035 * ease_in_out(t / max(duration, 0.1)))
        canvas = paper.copy().convert("RGBA")
        if t <= 0:
            return canvas.convert("RGB")
        size = (max(2, int(card_w * scale)), max(2, int(card_h * scale)))
        scaled_card = card.resize(size)
        scaled_shadow = shadow.resize((int(shadow.width * scale), int(shadow.height * scale)))
        alpha = min(1.0, t / 0.25)
        if alpha < 1:
            scaled_card.putalpha(scaled_card.getchannel("A").point(lambda value: int(value * alpha)))
        canvas.alpha_composite(scaled_shadow, ((W - scaled_shadow.width) // 2 + 8, (H - scaled_shadow.height) // 2 + 18))
        canvas.alpha_composite(scaled_card, ((W - size[0]) // 2, (H - size[1]) // 2))
        return canvas.convert("RGB")

    return frame


# ---------------------------------------------------------------- keyword-highlight caption
HIGHLIGHT = (242, 194, 48)
HIGHLIGHT_TEXT = (30, 24, 14)


def highlight_words(text: str, keywords: set[str]) -> list[tuple[str, bool]]:
    return [(word, re.sub(r"[^\w'-]", "", word).lower() in keywords) for word in text.split()]


def highlight_caption(text: str, keywords: set[str], *, max_width: float = 0.8, bottom: float = 0.88) -> Frame:
    """White sans caption revealed word by word; key words sit in yellow boxes in serif italic."""
    size = 58
    sans, serif = font(SANS_FONTS, size, index=0), font(SERIF_ITALIC_FONTS, int(size * 0.98))
    words = highlight_words(text, keywords)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    space = probe.textlength(" ", font=sans)
    pad_x, pad_y = 14, 6
    lines: list[list[tuple[str, bool, float]]] = [[]]
    line_width = 0.0
    for word, key in words:
        width = probe.textlength(word, font=serif if key else sans) + (pad_x * 2 if key else 0)
        if lines[-1] and line_width + space + width > W * max_width:
            lines.append([])
            line_width = 0.0
        lines[-1].append((word, key, width))
        line_width += width + (space if line_width else 0)
    line_height = int(size * 1.35)
    top = int(H * bottom) - line_height * len(lines)
    placed: list[tuple[str, bool, float, int]] = []
    for row, line in enumerate(lines):
        total = sum(width for _word, _key, width in line) + space * (len(line) - 1)
        x = (W - total) / 2
        for word, key, width in line:
            placed.append((word, key, x, top + row * line_height))
            x += width + space
    per_word = 0.12

    def frame(t: float, duration: float) -> Image.Image:
        canvas = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        fade_out = min(1.0, max(0.0, (duration - t) / 0.25))
        for index, (word, key, x, y) in enumerate(placed):
            progress = ease_out_cubic((t - index * per_word) / 0.22)
            if progress <= 0:
                continue
            alpha = int(255 * progress * fade_out)
            lift = int((1 - progress) * 18)
            if key:
                width = draw.textlength(word, font=serif) + pad_x * 2
                box = (x, y - pad_y + lift, x + width, y + size + pad_y + lift)
                draw.rectangle(box, fill=(*HIGHLIGHT, alpha))
                draw.text((x + pad_x, y + lift - 2), word, font=serif, fill=(*HIGHLIGHT_TEXT, alpha))
            else:
                draw.text((x + 2, y + lift + 3), word, font=sans, fill=(0, 0, 0, int(alpha * 0.6)))
                draw.text((x, y + lift), word, font=sans, fill=(255, 255, 255, alpha))
        return canvas

    return frame


def caption_keywords(text: str, limit: int = 3) -> set[str]:
    """Words worth a yellow box: numbers, prices, years and ingredients; else the longest word."""
    from ..footage_match import INGREDIENTS
    from ..key_captions import NUMBER_WORDS

    words = [re.sub(r"[^\w'$-]", "", word).lower() for word in text.split()]
    picked = [word for word in words if word and (
        any(character.isdigit() for character in word) or word in NUMBER_WORDS or word in INGREDIENTS
    )]
    if not picked:
        picked = sorted((word for word in words if len(word) >= 5), key=len, reverse=True)[:1]
    return set(picked[:limit])
