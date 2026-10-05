"""A shot used a second time must not look like a repeat (Ishaq, 3 Oct).

When a scene finds no footage of its own, an earlier shot of the same item may come back once, but
changed: first mirrored ("flip"), then inside an old television set ("tv"). Before this, such scenes
kept the previous 2-4 s clip running past its end, so the same seconds looped two or three times.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VARIANTS = ("flip",)  # the old-TV frame looked dated (Ishaq, 5 Oct): a reused shot only comes back mirrored
# The screen of the television, as shares of the frame (left, top, right, bottom); knobs sit on the right.
SCREEN = (0.075, 0.095, 0.755, 0.905)


def variant_of(asset: dict[str, Any]) -> dict[str, Any]:
    """{"variant": "flip"|"tv"|"", "speed": 0.5-1.0} for an asset (empty variant for an ordinary shot)."""
    metadata = asset.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except ValueError:
            metadata = {}
    variant = str(metadata.get("variant") or "")
    try:
        speed = float(metadata.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    return {"variant": variant if variant in VARIANTS else "", "speed": max(0.5, min(1.0, speed))}


def screen_box(width: int, height: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the TV screen in pixels, even numbers for the encoder."""
    left, top, right, bottom = SCREEN
    x, y = int(width * left) // 2 * 2, int(height * top) // 2 * 2
    return x, y, int(width * (right - left)) // 2 * 2, int(height * (bottom - top)) // 2 * 2


def tv_bezel(width: int, height: int, folder: Path) -> Path:
    """A 1970s wood-cabinet television with a see-through screen (PNG, made once per size)."""
    from PIL import Image, ImageDraw, ImageFilter

    path = Path(folder) / f"tv-bezel-{width}x{height}.png"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(body)
    # Walnut cabinet with a soft vertical gradient and fine grain lines.
    for row in range(height):
        shade = 1.0 - 0.35 * abs(row / height - 0.45)
        draw.line([(0, row), (width, row)], fill=(int(74 * shade), int(46 * shade), int(28 * shade), 255))
    for line in range(0, height, max(3, height // 180)):
        draw.line([(0, line), (width, line + height // 60)], fill=(52, 31, 18, 70))
    x, y, w, h = screen_box(width, height)
    radius = int(min(w, h) * 0.09)
    # Dark rim around the glass, then the glass itself is cut out (transparent).
    rim = max(6, width // 120)
    draw.rounded_rectangle([x - rim * 2, y - rim * 2, x + w + rim * 2, y + h + rim * 2], radius + rim * 2, fill=(22, 18, 15, 255))
    draw.rounded_rectangle([x - rim, y - rim, x + w + rim, y + h + rim], radius + rim, fill=(58, 54, 50, 255))
    hole = Image.new("L", (width, height), 0)
    ImageDraw.Draw(hole).rounded_rectangle([x, y, x + w, y + h], radius, fill=255)
    alpha = body.getchannel("A").point(lambda value: value)
    alpha.paste(0, mask=hole)
    body.putalpha(alpha)
    # Glass: faint scan lines and a dark edge (curved picture tube), drawn over the video.
    glass = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glass_draw = ImageDraw.Draw(glass)
    for line in range(y, y + h, 4):
        glass_draw.line([(x, line), (x + w, line)], fill=(0, 0, 0, 34))
    edge = Image.new("L", (width, height), 0)
    ImageDraw.Draw(edge).rounded_rectangle([x, y, x + w, y + h], radius, outline=200, width=max(8, w // 28))
    edge = edge.filter(ImageFilter.GaussianBlur(max(6, w // 40)))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow.putalpha(edge.point(lambda value: int(value * 0.75)))
    glass = Image.alpha_composite(glass, shadow)
    glass.putalpha(Image.composite(glass.getchannel("A"), Image.new("L", (width, height), 0), hole))
    frame = Image.alpha_composite(glass, body)
    # Control panel: two knobs and a speaker grille.
    panel = ImageDraw.Draw(frame)
    knob_x = int(width * 0.86)
    for share in (0.27, 0.47):
        size = int(height * 0.075)
        centre_y = int(height * share)
        panel.ellipse([knob_x - size, centre_y - size, knob_x + size, centre_y + size], fill=(30, 26, 22, 255),
                      outline=(150, 128, 92, 255), width=max(2, size // 9))
        panel.line([(knob_x, centre_y - size + 6), (knob_x, centre_y - size // 3)], fill=(190, 170, 130, 255), width=max(2, size // 8))
    for line in range(int(height * 0.62), int(height * 0.86), max(6, height // 70)):
        panel.line([(int(width * 0.80), line), (int(width * 0.92), line)], fill=(28, 20, 14, 255), width=max(2, height // 260))
    frame.save(path)
    return path


def tv_filters(width: int, height: int) -> str:
    """Fit the (already moving) picture into the TV screen, slightly faded like an old tube."""
    x, y, w, h = screen_box(width, height)
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"eq=saturation=0.8:contrast=1.06,pad={width}:{height}:{x}:{y}:color=black")
