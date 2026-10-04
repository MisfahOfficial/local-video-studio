"""Reference-style graphics: polaroid stack, graph-paper card, keyword-highlight captions."""
from __future__ import annotations

import random
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..channel_styles import ChannelStyle, get_style
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
def polaroid_stack(photo: Image.Image, *, seed: int = 7, vintage: bool = True, style: ChannelStyle | None = None) -> Frame:
    """Several white-bordered prints cut from one photo slide in and settle, tilted, over a dim copy."""
    style = style or get_style(None)
    rng = random.Random(seed)
    fitted = ImageOps.fit(photo.convert("RGB"), SIZE)
    base = sepia(fitted, max(style.sepia, 0.35)) if vintage else fitted
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
        card = Image.new("RGBA", (inner_w + border * 2, inner_h + border * 2), (*style.print_border, 255))
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


def is_script_font(paths: tuple[str, ...]) -> bool:
    return any(name in str(path) for path in paths[:1] for name in ("Snell", "Brush Script", "Zapfino"))


def _luminance(color: tuple[int, ...]) -> float:
    channels = [value / 255 for value in color[:3]]
    linear = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: tuple[int, ...], second: tuple[int, ...]) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def readable_ink(box: tuple[int, ...], ink: tuple[int, ...], minimum: float = 4.5) -> tuple[int, int, int]:
    """The style's word colour when it reads on its box; otherwise near-black or white, whichever reads better."""
    if contrast(box, ink) >= minimum:
        return tuple(ink[:3])  # type: ignore[return-value]
    return (20, 16, 12) if contrast(box, (20, 16, 12)) >= contrast(box, (255, 255, 255)) else (255, 255, 255)


def highlight_words(text: str, keywords: set[str]) -> list[tuple[str, bool]]:
    return [(word, re.sub(r"[^\w'-]", "", word).lower() in keywords) for word in text.split()]


def highlight_caption(
    text: str, keywords: set[str], *, max_width: float = 0.8, bottom: float = 0.88, style: ChannelStyle | None = None,
) -> Frame:
    """White sans caption revealed word by word; key words sit in coloured boxes (the channel's style)."""
    style = style or get_style(None)
    size = 58
    script = is_script_font(style.highlight_fonts)
    # A script face is unreadable in capitals ("HUMIDITY" in V2's pink box): its key words are written in
    # normal case, a little larger and with a thin outline of their own colour to thicken the strokes.
    sans, serif = font(style.caption_fonts, size, index=0), font(style.highlight_fonts, int(size * (1.12 if script else 0.98)))
    ink = readable_ink(style.highlight, style.highlight_text)
    words = [(word.capitalize() if script and key and word.isupper() else word, key)
             for word, key in highlight_words(text, keywords)]
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
                box = (x, y - pad_y + lift, x + width, y + size + pad_y + lift + (12 if script else 0))  # room for tails
                draw.rectangle(box, fill=(*style.highlight, alpha))
                draw.text((x + pad_x, y + lift - 2), word, font=serif, fill=(*ink, alpha),
                          stroke_width=1 if script else 0, stroke_fill=(*ink, alpha))
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
    return style_background(get_style("v3"))


def style_background(style: ChannelStyle) -> Image.Image:
    """The channel's card backdrop: vignette, gingham-bordered paper, cabin wood or a diner stripe."""
    import numpy as np

    ys, xs = np.ogrid[:H, :W]
    rng = np.random.default_rng(3)
    if style.background == "wood":
        # Vertical planks with grain lines and a soft centre light.
        grain = np.sin(xs / 7.0 + np.sin(ys / 90.0) * 3 + rng.normal(0, 0.2, (1, W))) * 0.5 + 0.5
        plank = ((xs // 240) % 2) * 0.08
        seams = (xs % 240 < 4) * 0.45
        shade = np.broadcast_to(0.55 + 0.25 * grain - plank - seams, (H, W))[..., None]
        base = np.array(style.bg_outer) + (np.array(style.bg_inner) - np.array(style.bg_outer)) * shade
        light = np.clip(1 - np.sqrt(((xs - W / 2) / W) ** 2 + ((ys - H / 2) / H) ** 2) * 1.2, 0.55, 1)[..., None]
        pixels = base * light + rng.normal(0, 3, (H, W, 1))
        return Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))
    if style.background in {"gingham", "diner"}:
        paper = np.ones((H, W, 3)) * np.array(style.bg_inner) + rng.normal(0, 3, (H, W, 1))
        image = Image.fromarray(np.clip(paper, 0, 255).astype("uint8"))
        draw = ImageDraw.Draw(image, "RGBA")
        if style.background == "gingham":
            band = 70  # checked tablecloth border around a paper centre
            for index in range(0, max(W, H), 35):
                shade = (*style.bg_outer, 110)
                draw.rectangle((index, 0, index + 17, band), fill=shade)
                draw.rectangle((index, H - band, index + 17, H), fill=shade)
                draw.rectangle((0, index, band, index + 17), fill=shade)
                draw.rectangle((W - band, index, W, index + 17), fill=shade)
            for edge in (0, 17):
                draw.rectangle((0, edge, W, edge + 17), fill=(*style.bg_outer, 70))
                draw.rectangle((0, H - band + edge, W, H - band + edge + 17), fill=(*style.bg_outer, 70))
            draw.rectangle((band, band, W - band, H - band), outline=(*style.bg_outer, 200), width=4)
        else:
            draw.rectangle((0, H - 120, W, H - 86), fill=(178, 34, 40, 255))
            draw.rectangle((0, H - 80, W, H - 58), fill=(38, 52, 92, 255))
            draw.rectangle((0, 58, W, 80), fill=(38, 52, 92, 255))
            draw.rectangle((0, 86, W, 120), fill=(178, 34, 40, 255))
        return image
    distance = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H * 0.55) / (H / 1.6)) ** 2)
    mix = np.clip(1 - distance, 0, 1)[..., None]
    inner, outer = np.array(style.bg_inner), np.array(style.bg_outer)
    pixels = outer + (inner - outer) * mix + rng.normal(0, 4, (H, W, 1))
    return Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))


def _rounded(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGB"), size)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    card.paste(fitted, (0, 0), mask)
    return card


def _styled_card(image: Image.Image, size: tuple[int, int], style: ChannelStyle) -> Image.Image:
    """The photo as the channel shows it: rounded, a polaroid print, or a recipe index card."""
    if style.card == "rounded":
        return _rounded(image, size, 26)
    card = Image.new("RGBA", size, (*style.print_border, 255))
    draw = ImageDraw.Draw(card)
    if style.card == "recipe":
        # Index card: coloured top rule, faint blue lines, photo inset.
        draw.rectangle((0, 0, size[0], 16), fill=(*style.card_accent, 255))
        for y in range(int(size[1] * 0.78), size[1], 26):
            draw.line((14, y, size[0] - 14, y), fill=(150, 180, 220, 255), width=2)
        inset = (size[0] - 36, int(size[1] * 0.70))
        card.paste(ImageOps.fit(image.convert("RGB"), inset), (18, 30))
    else:  # polaroid
        inset = (size[0] - 36, int(size[1] * 0.72))
        card.paste(sepia(ImageOps.fit(image.convert("RGB"), inset), style.sepia * 0.5), (18, 18))
    return card


def ingredient_label_layout(labels: list[str], style: ChannelStyle | None = None) -> list[dict[str, Any]]:
    """Where each ingredient name lands (centre, size, colour, when it appears) for editable text layers."""
    style = style or get_style(None)
    labels = labels[:5]
    count = max(len(labels), 1)
    gap = 48
    card_w = min(480, int((W * 0.86 - gap * (count - 1)) / count))
    card_h = int(card_w * 1.15)
    size = 38 if style.label_spaced else 46
    label_font = font(style.label_fonts, size)
    on_card = style.card in {"recipe", "polaroid"}
    left = (W - (card_w * count + gap * (count - 1))) / 2
    top = (H - card_h) / 2 - 40
    layers = []
    for index, label in enumerate(labels):
        text = label.upper() if style.label_spaced else label.title()
        if on_card:
            y_text = top + card_h * 0.80
            colour = style.card_accent if style.card == "recipe" else style.label_color
        else:
            y_text, colour = top + card_h + 34, style.label_color
        layers.append({
            "role": "ingredient label", "text": text, "x": left + index * (card_w + gap) + card_w / 2,
            "y": y_text + size * 0.55, "size": size, "color": tuple(colour), "font": getattr(label_font, "path", ""),
            "appear": index * 0.45 + 0.35, "letter_spacing": 0.5 if style.label_spaced else 0.0,
        })
    return layers


def ingredient_cards(items: list[tuple[str, Image.Image]], style: ChannelStyle | None = None,
                     show_labels: bool = True) -> Frame:
    """Each ingredient pops in as a card (the channel's style) while its name types out."""
    style = style or get_style(None)
    items = items[:5]
    count = len(items)
    gap = 48
    card_w = min(480, int((W * 0.86 - gap * (count - 1)) / max(count, 1)))
    card_h = int(card_w * 1.15)
    background = style_background(style)
    label_font = font(style.label_fonts, 38 if style.label_spaced else 46)
    on_card = style.card in {"recipe", "polaroid"}  # the name is written on the card itself
    corner = 26 if style.card == "rounded" else 4
    cards = [(label.upper() if style.label_spaced else label.title(), _styled_card(image, (card_w, card_h), style),
              _shadow((card_w, card_h), 0, 20, 150, corner)) for label, image in items]
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
            if not show_labels:
                continue  # the editable export adds the names as text layers
            letters = int(len(label) * min(1.0, max(0.0, (local - 0.35) / 0.6)))
            text = " ".join(label[:letters]) if style.label_spaced else label[:letters]
            width = draw.textlength(text, font=label_font)
            if on_card and pop >= 0.98:
                y_text = top + card_h * 0.80
                colour = style.card_accent if style.card == "recipe" else style.label_color
            else:
                y_text, colour = top + card_h + 34, style.label_color
            if on_card and pop < 0.98:
                continue
            draw.text((left + index * (card_w + gap) + (card_w - width) / 2, y_text), text, font=label_font, fill=colour)
        return canvas.convert("RGB")

    return frame


# ---------------------------------------------------------------- gallery (many items)
IMPACT = ("/System/Library/Fonts/Supplemental/Impact.ttf",)
SCRIPT = ("/System/Library/Fonts/Supplemental/SnellRoundhand.ttc", "/System/Library/Fonts/Supplemental/Brush Script.ttf")


def collage_card(photos: list[Image.Image], title: str = "", style: ChannelStyle | None = None) -> Frame:
    """V3's hook collage (2 of its 3 references): lilac torn paper, 2-4 photos with white borders popping in,
    a bold white title with a dark-red outline and a small blue handwritten tagline."""
    rng = random.Random(len(photos) * 7 + len(title))
    paper = Image.new("RGB", SIZE, (198, 186, 226))
    noise = Image.effect_noise(SIZE, 18).convert("RGB")
    paper = Image.blend(paper, noise, 0.08)
    draw = ImageDraw.Draw(paper)
    for _ in range(5):  # torn white paper strips
        y = rng.randint(80, H - 80)
        points = [(x, y + rng.randint(-14, 14)) for x in range(-40, W + 80, 60)]
        draw.line(points, fill=(236, 230, 246), width=rng.randint(10, 26))
    words = re.sub(r"[^\w'\s-]", "", title).split()
    head = " ".join(words[:5]).upper()
    tail = " ".join(words[5:9])
    big = font(IMPACT + SANS_FONTS, 92)
    small = font(SCRIPT + SERIF_ITALIC_FONTS, 76)
    count = min(4, len(photos))
    slots = {1: [(0.5, 0.58)], 2: [(0.3, 0.6), (0.7, 0.6)], 3: [(0.27, 0.52), (0.73, 0.52), (0.5, 0.8)],
             4: [(0.27, 0.5), (0.73, 0.5), (0.27, 0.83), (0.73, 0.83)]}[max(1, count)]
    prints = []
    for index, photo in enumerate(photos[:count]):
        inner_w = int(W * (0.34 if count <= 2 else 0.27))
        inner_h = int(inner_w * 0.62)
        card = Image.new("RGBA", (inner_w + 16, inner_h + 16), (255, 255, 255, 255))
        card.paste(ImageOps.fit(photo.convert("RGB"), (inner_w, inner_h)), (8, 8))
        prints.append((card.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=True), slots[index], 0.25 + index * 0.25))

    def frame(t: float, duration: float) -> Image.Image:
        canvas = paper.copy().convert("RGBA")
        for image, (sx, sy), delay in prints:
            local = t - delay
            if local <= 0:
                continue
            scale = 0.6 + 0.4 * ease_out_back(min(1.0, local / 0.35), 1.4)
            sized = image.resize((max(2, int(image.width * scale)), max(2, int(image.height * scale))))
            canvas.alpha_composite(sized, (int(sx * W - sized.width / 2), int(sy * H - sized.height / 2)))
        writer = ImageDraw.Draw(canvas)
        if head and t > 0.05:
            drop = ease_out_cubic(min(1.0, t / 0.3))
            y = int(-60 + 140 * drop)
            box = writer.textbbox((0, 0), head, font=big, stroke_width=6)
            writer.text(((W - (box[2] - box[0])) // 2, y), head, font=big, fill=(255, 255, 255),
                        stroke_width=6, stroke_fill=(120, 18, 24))
        if tail and t > 0.4:
            box = writer.textbbox((0, 0), tail, font=small)
            writer.text(((W - (box[2] - box[0])) // 2, 196), tail, font=small, fill=(32, 60, 210), stroke_width=1, stroke_fill=(32, 60, 210))
        return canvas.convert("RGB")

    return frame


def gallery_stack(photos: list[Image.Image], style: ChannelStyle | None = None, title: str = "") -> Frame:
    """Several different photos drop onto the channel's backdrop as tilted prints (plural mentions)."""
    style = style or get_style(None)
    if style.gallery == "collage":
        return collage_card(photos, title, style)
    rng = random.Random(len(photos))
    if style.background == "vignette":
        base = sepia(ImageOps.fit(photos[0].convert("RGB"), SIZE))
        background = Image.blend(base.filter(ImageFilter.GaussianBlur(8)), Image.new("RGB", SIZE, (0, 0, 0)), 0.55)
    else:
        background = style_background(style)
    spots = [(0.22, 0.32), (0.74, 0.30), (0.30, 0.72), (0.70, 0.72), (0.50, 0.50)]
    prints = []
    for index, photo in enumerate(photos[:5]):
        inner_w = int(W * 0.30)
        inner_h = int(inner_w * 0.75)
        border = 18
        card = Image.new("RGBA", (inner_w + border * 2, inner_h + border * 3), (*style.print_border, 255))
        card.paste(sepia(ImageOps.fit(photo.convert("RGB"), (inner_w, inner_h)), style.sepia), (border, border))
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


def subscribe_overlay(duration_hint: float = 4.0, style: ChannelStyle | None = None) -> Frame:
    """SUBSCRIBE button (channel colour) rises, a cursor clicks it, it turns grey and the bell rings."""
    import math

    button_colour = (style or get_style(None)).subscribe
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
        fill = (90, 90, 90, alpha) if clicked else (*button_colour, alpha)
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
