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


# ---------------------------------------------------------------- ingredient cards
CARD_BACKGROUND = ((8, 26, 18), (18, 64, 40))  # dark green vignette like the reference


def _green_background() -> Image.Image:
    import numpy as np

    ys, xs = np.ogrid[:H, :W]
    distance = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H * 0.55) / (H / 1.6)) ** 2)
    mix = np.clip(1 - distance, 0, 1)[..., None]
    inner, outer = np.array(CARD_BACKGROUND[1]), np.array(CARD_BACKGROUND[0])
    pixels = outer + (inner - outer) * mix
    noise = np.random.default_rng(3).normal(0, 4, (H, W, 1))
    return Image.fromarray(np.clip(pixels + noise, 0, 255).astype("uint8"))


def _rounded(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGB"), size)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    card.paste(fitted, (0, 0), mask)
    return card


def ingredient_cards(items: list[tuple[str, Image.Image]]) -> Frame:
    """Each ingredient pops in as a rounded photo card while its name types out beneath it."""
    items = items[:5]
    count = len(items)
    gap = 48
    card_w = min(480, int((W * 0.86 - gap * (count - 1)) / max(count, 1)))
    card_h = int(card_w * 1.15)
    background = _green_background()
    label_font = font(SANS_FONTS, 38)
    cards = [(label.upper(), _rounded(image, (card_w, card_h), 26), _shadow((card_w, card_h), 0, 20, 150, 26))
             for label, image in items]
    total_w = card_w * count + gap * (count - 1)
    left = (W - total_w) / 2
    top = (H - card_h) / 2 - 40
    stagger = 0.45

    def frame(t: float, duration: float) -> Image.Image:
        drift = 1.0 + 0.03 * ease_in_out(t / max(duration, 0.1))
        scaled = background.resize((int(W * drift), int(H * drift)))
        canvas = scaled.crop(((scaled.width - W) // 2, (scaled.height - H) // 2,
                              (scaled.width - W) // 2 + W, (scaled.height - H) // 2 + H)).convert("RGBA")
        draw = ImageDraw.Draw(canvas)
        for index, (label, card, shadow) in enumerate(cards):
            local = t - index * stagger
            if local <= 0:
                continue
            pop = ease_out_back(local / 0.5, 1.3)
            scale = 0.6 + 0.4 * pop
            size = (max(2, int(card_w * scale)), max(2, int(card_h * scale)))
            x = left + index * (card_w + gap) + (card_w - size[0]) / 2
            y = top + (card_h - size[1]) / 2 + (1 - min(1.0, local / 0.5)) * 60
            canvas.alpha_composite(shadow.resize((int(shadow.width * scale), int(shadow.height * scale))),
                                   (int(x) - int(60 * scale) + 6, int(y) - int(60 * scale) + 14))
            canvas.alpha_composite(card.resize(size), (int(x), int(y)))
            letters = int(len(label) * min(1.0, max(0.0, (local - 0.35) / 0.6)))
            text = " ".join(label[:letters])  # letter-spaced like the reference
            width = draw.textlength(text, font=label_font)
            draw.text((left + index * (card_w + gap) + (card_w - width) / 2, top + card_h + 34), text,
                      font=label_font, fill=(236, 232, 220))
        return canvas.convert("RGB")

    return frame


# ---------------------------------------------------------------- gallery (many items)
def gallery_stack(photos: list[Image.Image]) -> Frame:
    """Several different photos drop onto a dim sepia table as tilted prints (plural mentions)."""
    rng = random.Random(len(photos))
    base = sepia(ImageOps.fit(photos[0].convert("RGB"), SIZE))
    background = Image.blend(base.filter(ImageFilter.GaussianBlur(8)), Image.new("RGB", SIZE, (0, 0, 0)), 0.55)
    spots = [(0.22, 0.32), (0.74, 0.30), (0.30, 0.72), (0.70, 0.72), (0.50, 0.50)]
    prints = []
    for index, photo in enumerate(photos[:5]):
        inner_w = int(W * 0.30)
        inner_h = int(inner_w * 0.75)
        border = 18
        card = Image.new("RGBA", (inner_w + border * 2, inner_h + border * 3), (246, 244, 238, 255))
        card.paste(sepia(ImageOps.fit(photo.convert("RGB"), (inner_w, inner_h)), 0.6), (border, border))
        shadow = _shadow(card.size, 0, 16, 140)
        combo = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
        combo.alpha_composite(shadow, (8, 14))
        pad = (shadow.size[0] - card.size[0]) // 2
        combo.alpha_composite(card, (pad, pad))
        prints.append((combo.rotate(rng.uniform(-9, 9), resample=Image.BICUBIC, expand=True), spots[index], index * 0.35))

    def frame(t: float, duration: float) -> Image.Image:
        canvas = background.copy().convert("RGBA")
        for image, (sx, sy), delay in prints:
            local = t - delay
            if local <= 0:
                continue
            drop = ease_out_back(local / 0.45, 1.2)
            scale = 1.35 - 0.35 * drop
            sized = image.resize((max(2, int(image.width * scale)), max(2, int(image.height * scale))))
            if local < 0.2:
                sized.putalpha(sized.getchannel("A").point(lambda value: int(value * local / 0.2)))
            canvas.alpha_composite(sized, (int(sx * W - sized.width / 2), int(sy * H - sized.height / 2)))
        return canvas.convert("RGB")

    return frame


# ---------------------------------------------------------------- subscribe button
def _bell(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, angle: float, color: tuple) -> None:
    import math

    points = []
    for step in range(0, 181, 12):  # dome
        radians = math.radians(180 + step)
        points.append((cx + math.cos(radians) * size * 0.42, cy - size * 0.1 + math.sin(radians) * size * 0.45))
    points += [(cx + size * 0.5, cy + size * 0.3), (cx - size * 0.5, cy + size * 0.3)]
    swing = math.radians(angle)
    rotated = [(cx + (x - cx) * math.cos(swing) - (y - cy) * math.sin(swing),
                cy + (x - cx) * math.sin(swing) + (y - cy) * math.cos(swing)) for x, y in points]
    draw.polygon(rotated, fill=color)
    draw.ellipse((cx - size * 0.1, cy + size * 0.32, cx + size * 0.1, cy + size * 0.5), fill=color)


def subscribe_overlay(duration_hint: float = 4.0) -> Frame:
    """Red SUBSCRIBE button rises, a cursor clicks it, it turns grey and the bell rings."""
    import math

    button_font = font(("/System/Library/Fonts/Supplemental/Arial Bold.ttf", *SANS_FONTS), 44)
    bw, bh = 330, 92
    cx, cy = W * 0.5, H * 0.80

    def frame(t: float, duration: float) -> Image.Image:
        canvas = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        rise = ease_out_back(t / 0.5, 1.4)
        leave = min(1.0, max(0.0, (duration - t) / 0.35))
        alpha = int(255 * min(1.0, t / 0.2) * leave)
        y = cy + (1 - rise) * 160
        clicked = t >= 1.35
        press = 0.94 if 1.3 <= t < 1.45 else 1.0
        w, h = bw * press, bh * press
        x0 = cx - w / 2 - 40
        fill = (90, 90, 90, alpha) if clicked else (204, 0, 0, alpha)
        draw.rounded_rectangle((x0, y - h / 2, x0 + w, y + h / 2), radius=h / 2, fill=fill)
        label = "SUBSCRIBED" if clicked else "SUBSCRIBE"
        text_w = draw.textlength(label, font=button_font)
        draw.text((x0 + (w - text_w) / 2, y - 26), label, font=button_font, fill=(255, 255, 255, alpha))
        bell_x = x0 + w + 70
        ring = 22 * math.sin((t - 1.6) * 18) * math.exp(-(t - 1.6) * 3) if t > 1.6 else 0.0
        draw.ellipse((bell_x - 46, y - 46, bell_x + 46, y + 46), fill=(245, 245, 245, alpha))
        _bell(draw, bell_x, y, 52, ring, (40, 40, 40, alpha))
        # cursor: glides in, clicks the button, then the bell
        if 0.6 < t < duration - 0.3:
            target = (x0 + w * 0.62, y + 12) if t < 1.55 else (bell_x + 8, y + 14)
            start = (W * 0.72, H * 1.02)
            travel = ease_in_out((t - 0.6) / 0.7) if t < 1.55 else 1.0
            if t >= 1.55:
                start, travel = (x0 + w * 0.62, y + 12), ease_in_out((t - 1.55) / 0.4)
            px = start[0] + (target[0] - start[0]) * travel
            py = start[1] + (target[1] - start[1]) * travel
            arrow = [(px, py), (px, py + 46), (px + 12, py + 35), (px + 22, py + 56), (px + 31, py + 52),
                     (px + 21, py + 31), (px + 36, py + 31)]
            draw.polygon(arrow, fill=(255, 255, 255, alpha), outline=(0, 0, 0, alpha))
        return canvas

    return frame
