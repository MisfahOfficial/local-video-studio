"""Per-channel motion-graphics styles ("brand kits").

Every graphic (ingredient cards, galleries, highlight captions, chapter cards, the
subscribe button) reads one of these, so each channel's videos keep their own look.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SANS = ("/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf")
SANS_BOLD = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/HelveticaNeue.ttc",
             "C:/Windows/Fonts/arialbd.ttf")
FUTURA = ("/System/Library/Fonts/Supplemental/Futura.ttc", *SANS_BOLD)
SERIF_BOLD = ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", "/System/Library/Fonts/Supplemental/Baskerville.ttc",
              "C:/Windows/Fonts/georgiab.ttf")
SERIF_ITALIC = ("/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
                "/System/Library/Fonts/Supplemental/Georgia Italic.ttf", "C:/Windows/Fonts/georgiaz.ttf")
SCRIPT = ("/System/Library/Fonts/Supplemental/SnellRoundhand.ttc", "/System/Library/Fonts/Supplemental/Brush Script.ttf",
          *SERIF_ITALIC)
HANDWRITTEN = ("/System/Library/Fonts/Noteworthy.ttc", "/System/Library/Fonts/MarkerFelt.ttc", *SANS)
TYPEWRITER = ("/System/Library/Fonts/Supplemental/American Typewriter.ttc", "/System/Library/Fonts/Courier.ttc",
              *SERIF_BOLD)

Color = tuple[int, int, int]


@dataclass(frozen=True)
class ChannelStyle:
    key: str
    name: str
    # Background behind ingredient cards: "vignette" (two colours), "gingham" (paper + checked border),
    # "wood" (warm planks) or "diner" (cream with a red-and-blue stripe).
    background: str
    bg_inner: Color
    bg_outer: Color
    # Ingredient card: "rounded" photo, "recipe" index card (white with a coloured rule) or "polaroid".
    card: str
    card_accent: Color
    label_color: Color
    label_fonts: tuple[str, ...]
    label_spaced: bool  # "S U G A R" letter spacing
    # Keyword boxes in highlight captions.
    highlight: Color
    highlight_text: Color
    highlight_fonts: tuple[str, ...]
    caption_fonts: tuple[str, ...]
    # Chapter cards.
    chapter_tint: Color
    chapter_accent: Color
    chapter_title_fonts: tuple[str, ...]
    chapter_label_fonts: tuple[str, ...]
    chapter_label: str  # "CHAPTER", "RECIPE" ...
    print_border: Color  # polaroid / gallery print paper
    sepia: float  # how aged photos look inside graphics (0-1)
    subscribe: Color


STYLES: dict[str, ChannelStyle] = {
    # V1 Vintage Life of USA: 1950s Americana diner, Kodachrome warmth, red-white-blue.
    "v1": ChannelStyle(
        key="v1", name="V1 · Vintage Life of USA (Americana diner)",
        background="diner", bg_inner=(244, 234, 214), bg_outer=(226, 208, 176),
        card="polaroid", card_accent=(178, 34, 40), label_color=(38, 52, 92), label_fonts=FUTURA, label_spaced=True,
        highlight=(178, 34, 40), highlight_text=(255, 250, 240), highlight_fonts=FUTURA, caption_fonts=SANS,
        chapter_tint=(196, 140, 72), chapter_accent=(214, 64, 58), chapter_title_fonts=FUTURA,
        chapter_label_fonts=FUTURA, chapter_label="CHAPTER", print_border=(250, 246, 236), sepia=0.35,
        subscribe=(204, 0, 0),
    ),
    # V2 Forgotten Flavors of USA: sweets, candy and Christmas; handwritten recipe cards on gingham.
    "v2": ChannelStyle(
        key="v2", name="V2 · Forgotten Flavors (recipe card & gingham)",
        background="gingham", bg_inner=(252, 244, 232), bg_outer=(214, 72, 84),
        card="recipe", card_accent=(214, 72, 84), label_color=(92, 48, 40), label_fonts=HANDWRITTEN, label_spaced=False,
        highlight=(246, 186, 196), highlight_text=(88, 24, 34), highlight_fonts=SCRIPT, caption_fonts=SANS,
        chapter_tint=(200, 96, 110), chapter_accent=(252, 214, 220), chapter_title_fonts=SCRIPT,
        chapter_label_fonts=HANDWRITTEN, chapter_label="RECIPE", print_border=(255, 252, 246), sepia=0.2,
        subscribe=(214, 72, 84),
    ),
    # V3 Britain We Lived In: the current look - bottle-green vignette, serif titles, mustard highlights.
    "v3": ChannelStyle(
        key="v3", name="V3 · Britain We Lived In (bottle green & serif)",
        background="vignette", bg_inner=(18, 64, 40), bg_outer=(8, 26, 18),
        card="rounded", card_accent=(236, 232, 220), label_color=(236, 232, 220), label_fonts=SANS, label_spaced=True,
        highlight=(242, 194, 48), highlight_text=(30, 24, 14), highlight_fonts=SERIF_ITALIC, caption_fonts=SANS,
        chapter_tint=(112, 134, 104), chapter_accent=(196, 214, 170), chapter_title_fonts=SERIF_BOLD,
        chapter_label_fonts=FUTURA, chapter_label="CHAPTER", print_border=(246, 244, 238), sepia=0.6,
        subscribe=(204, 0, 0),
    ),
    # V4 Canada We Lived In: cabin kitchen - warm wood, maple red, typewriter labels.
    "v4": ChannelStyle(
        key="v4", name="V4 · Canada We Lived In (cabin wood & maple)",
        background="wood", bg_inner=(122, 78, 46), bg_outer=(62, 36, 20),
        card="recipe", card_accent=(176, 30, 36), label_color=(248, 240, 226), label_fonts=TYPEWRITER, label_spaced=False,
        highlight=(176, 30, 36), highlight_text=(255, 248, 240), highlight_fonts=TYPEWRITER, caption_fonts=SANS,
        chapter_tint=(150, 60, 44), chapter_accent=(232, 170, 120), chapter_title_fonts=TYPEWRITER,
        chapter_label_fonts=TYPEWRITER, chapter_label="CHAPTER", print_border=(250, 244, 232), sepia=0.45,
        subscribe=(176, 30, 36),
    ),
}
DEFAULT_STYLE = "v3"


def get_style(key: Any) -> ChannelStyle:
    """A built-in channel style, or the style of a channel created from its example videos."""
    name = str(key or "").lower()
    if name:
        try:
            from .channel_kits import custom_styles
            from .paths import AppPaths

            custom = custom_styles(AppPaths.resolve().root)
            if name in custom:
                return custom[name]  # a changed or example-made look (house or channel overrides)
        except Exception:  # a broken kits file must never stop a render
            pass
    return STYLES.get(name, STYLES[DEFAULT_STYLE])
