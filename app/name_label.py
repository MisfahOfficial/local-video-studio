"""The item name as a small orange label over the item's first shot (reference V2 style).

Ishaq's most viral V2 video opens every item on its own product or footage shot with the name in a
lower-left orange box ("WHIZ BAR"), not a full-screen chapter card. The label is drawn on top of the
footage at render time, so the footage stays editable and the label is a separate text layer in the
Premiere/CapCut export.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

LABEL_ORANGE = (243, 107, 33)
LABEL_TEXT = (255, 255, 255)
SIZE = (1920, 1080)
FONT_SIZE = 68  # measured on the reference: the box is ~92 px tall at 1080p
PAD_X, PAD_Y = 26, 16
LEFT, BOTTOM = 44, 108  # box corner from the left and bottom edges
MAX_WIDTH = 1100
FONTS = (
    str(Path.home() / "Library/Application Support/LocalVideoStudio/fonts/Montserrat-ExtraBold.ttf"),
    str(Path.home() / "Library/Application Support/LocalVideoStudio/fonts/Montserratwght.ttf"),
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def _font(size: int) -> Any:
    from PIL import ImageFont

    for path in FONTS:
        if Path(path).is_file():
            font = ImageFont.truetype(path, size)
            try:
                font.set_variation_by_name("ExtraBold")  # Montserrat is a variable font
            except (AttributeError, OSError, ValueError):
                pass
            return font
    return ImageFont.load_default()


def label_layout(text: str) -> dict[str, Any]:
    """Where the box and the words go: the words shrink to fit, then wrap to two lines; never cut."""
    from PIL import Image, ImageDraw

    words = " ".join(text.upper().split())
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def widest(lines: list[str], size: int) -> float:
        font = _font(size)
        return max(draw.textlength(line, font=font) for line in lines)

    lines = [words]
    size = FONT_SIZE
    while widest(lines, size) > MAX_WIDTH and size > 40:
        size -= 2
    if widest(lines, size) > MAX_WIDTH:  # still too long: two balanced lines, sized to fit
        parts = words.split()
        cut = min(range(1, len(parts)), key=lambda index: abs(len(" ".join(parts[:index])) - len(" ".join(parts[index:]))),
                  default=1)
        lines = [" ".join(parts[:cut]), " ".join(parts[cut:])] if len(parts) > 1 else lines
        size = FONT_SIZE
        while widest(lines, size) > MAX_WIDTH and size > 16:
            size -= 2
    font = _font(size)
    width = widest(lines, size)
    top_offset = draw.textbbox((0, 0), lines[0], font=font)[1]
    line_height = int(size * 1.18)
    height = line_height * (len(lines) - 1) + size + PAD_Y * 2
    box = (LEFT, SIZE[1] - BOTTOM - height, LEFT + int(width) + PAD_X * 2, SIZE[1] - BOTTOM)
    return {"text": "\n".join(lines), "lines": lines, "size": size, "line_height": line_height, "box": box,
            "text_x": LEFT + PAD_X, "text_y": box[1] + PAD_Y - top_offset,
            "font": next((path for path in FONTS if Path(path).is_file()), "")}


def render_label(text: str, destination: Path) -> Path:
    """A transparent 1920x1080 PNG with the label, to lay over the footage."""
    from PIL import Image, ImageDraw

    layout = label_layout(text)
    canvas = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = layout["box"]
    draw.rectangle((x0 + 4, y0 + 5, x1 + 4, y1 + 5), fill=(0, 0, 0, 70))  # soft drop shadow
    draw.rectangle(layout["box"], fill=(*LABEL_ORANGE, 255))
    for index, line in enumerate(layout["lines"]):
        draw.text((layout["text_x"], layout["text_y"] + index * layout["line_height"]), line,
                  font=_font(layout["size"]), fill=(*LABEL_TEXT, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)
    return destination


def label_text(scene: dict[str, Any]) -> str:
    for action in scene.get("timeline_actions") or []:
        if isinstance(action, dict) and action.get("type") == "label":
            return str((action.get("params") or {}).get("text") or "").strip()
    return ""


def label_layers(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """The label as an editable text layer (export), at the same place as the render."""
    text = label_text(scene)
    if not text:
        return []
    layout = label_layout(text)
    x0, y0, x1, y1 = layout["box"]
    return [{"role": "item name label", "text": layout["text"], "x": (x0 + x1) / 2, "y": (y0 + y1) / 2,
             "size": layout["size"], "color": LABEL_TEXT, "font": layout["font"], "appear": 0.15,
             "box_color": LABEL_ORANGE}]
