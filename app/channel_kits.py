"""Channel Kits: each channel's locked look.

A kit fixes the channel's colours, fonts, text and caption style, backgrounds and the 2-3
motion designs it may use for chapter cards and ingredient graphics. Videos only rotate
inside that approved set, so every video looks like the channel while graphics stay fresh.
Kits change only when Ishaq changes them. New channels get a kit read from their example
videos (see reference_style.py).

Inheritance (change one place, not every channel):
  HOUSE_STYLE          what every channel shares (captions, extra graphics, film look ...)
    -> "house" entry     in channel_kits.json: Ishaq's changes for ALL channels at once
      -> channel kit      only what makes that channel different (colours via its style, designs)
        -> channel entry  in channel_kits.json: Ishaq's changes for ONE channel
Style fields (colours, fonts) inherit the same way through "style_overrides".
Nothing here changes a look unless an override is written, so existing videos stay identical.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from .channel_styles import STYLES, ChannelStyle

KIT_FILE = "channel_kits.json"
_lock = threading.Lock()

# The one place for the editing style all channels share.
HOUSE_STYLE: dict[str, Any] = {
    "profile": "vintage_recipe",
    "captions": {"animation": "highlight", "position": "bottom"},
    "extras": ["map", "price", "years", "comment"],
    "style_overrides": {},  # e.g. {"caption_fonts": [...]} would change captions on every channel
}

# Each channel lists only what differs from the house style.
DEFAULT_KITS: dict[str, dict[str, Any]] = {
    "v1": {"name": "V1 · Vintage Life of USA", "style": "v1", "country": "US",
           "chapter_designs": ["vintage_tv", "newspaper"], "ingredient_designs": ["carousel", "scrapbook"]},
    # V2 follows Ishaq's most viral V2 video (uVYtlsvecP4, checked frame by frame on 2 Oct): no chapter cards
    # (an orange name label over the item's first shot), lime-yellow bold captions on key facts only, no film
    # look, no photo frames. Shots are 3-7 s like every channel (Ishaq, 3 Oct: the reference's 2-4 s cuts felt
    # rushed and drifted from the voice), and ingredient lists get a card again. Captions use the reference's Montserrat
    # ExtraBold (static file in the data fonts folder; the variable one is not read by the subtitle engine).
    # The previous look is kept as "v2_classic".
    "v2": {"name": "V2 · Forgotten Flavors of USA", "style": "v2", "country": "US",
           "chapter_style": "name_label", "chapter_designs": [], "ingredient_designs": ["recipe_book", "scrapbook"],
           "film_look": False, "photo_graphics": False, "pacing": {"shot_seconds_max": 7.0, "shot_seconds_min": 3.0},
           "story_stock": True,  # laws, companies, sales: present-day stock shots, like the reference
           "extras": ["map", "price", "years", "comment", "fact"],  # + big counting numbers ("260°F", "2,000 locations")
           # Graphics (maps, price, years, question cards) in the reference palette: near-black, lime-yellow,
           # orange labels with white type, bold sans; no sepia. v2_classic keeps the old pink/gingham colours.
           "style_overrides": {"background": "vignette", "bg_inner": [40, 40, 40], "bg_outer": [14, 14, 14],
                               "card": "rounded", "card_accent": [243, 107, 33], "label_color": [255, 255, 255],
                               "highlight": [216, 228, 24], "highlight_text": [17, 17, 17],
                               "chapter_tint": [58, 58, 58], "chapter_accent": [243, 107, 33],
                               "print_border": [243, 107, 33], "sepia": 0.0, "subscribe": [243, 107, 33],
                               "chapter_title_fonts": ["/System/Library/Fonts/Supplemental/Futura.ttc"],
                               "chapter_label_fonts": ["/System/Library/Fonts/Supplemental/Futura.ttc"],
                               "label_fonts": ["/System/Library/Fonts/Supplemental/Futura.ttc"]},
           "references": ["https://www.youtube.com/watch?v=uVYtlsvecP4"],
           "caption_style": {"animation": "pop", "font": "Montserrat ExtraBold", "bold": True, "text_color": "#D8E418",
                             "stroke_enabled": True, "stroke_color": "#000000", "stroke_width": 5,
                             "shadow_enabled": True, "shadow_color": "#000000", "shadow_blur": 8, "shadow_x": 0,
                             "shadow_y": 4, "background_enabled": False, "position": "middle", "case": "upper",
                             "size": 112, "words_per_line": 4, "max_lines": 2}},
    "v2_classic": {"name": "V2 · Forgotten Flavors (classic look, backup)", "style": "v2", "country": "US",
                   "chapter_designs": ["typewriter_card", "classic"], "ingredient_designs": ["recipe_book", "scrapbook"]},
    # V3 follows its reference LeFVmd1L4a4 (checked 3 Oct): the paint-stroke chapter card, white captions in a
    # heavy condensed face with a black outline (key words yellow), film look kept.
    "v3": {"name": "V3 · Britain We Lived In", "style": "v3", "country": "GB",
           "chapter_designs": ["v3_paint"], "ingredient_designs": ["cards"],
           "references": ["https://www.youtube.com/watch?v=LeFVmd1L4a4"],
           "caption_style": {"animation": "pop", "font": "Impact", "bold": False, "text_color": "#FFFFFF",
                             "stroke_enabled": True, "stroke_color": "#000000", "stroke_width": 5,
                             "shadow_enabled": True, "shadow_color": "#000000", "shadow_blur": 6, "shadow_x": 0,
                             "shadow_y": 4, "background_enabled": False, "position": "middle", "case": "title",
                             "size": 100, "words_per_line": 6, "max_lines": 2, "highlight_color": "#FFE234"}},
    "v4": {"name": "V4 · Canada We Lived In", "style": "v4", "country": "CA",
           "chapter_designs": ["typewriter_card", "film_slate"], "ingredient_designs": ["scrapbook", "chalkboard"]},
}
HOUSE = "house"  # the channel_kits.json entry that changes every channel


def _merge(base: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Nested dicts merge key by key (so one caption setting can change alone); anything else replaces."""
    merged = dict(base)
    for key, value in change.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def _path(root: Path) -> Path:
    return Path(root) / KIT_FILE


def load_kits(root: Path) -> dict[str, dict[str, Any]]:
    """Every channel's full kit: house style -> house changes -> the channel's own kit -> its changes."""
    try:
        saved = json.loads(_path(root).read_text())
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    house = _merge(HOUSE_STYLE, saved.get(HOUSE) if isinstance(saved.get(HOUSE), dict) else {})
    names = list(DEFAULT_KITS) + [key for key in saved if key not in DEFAULT_KITS and key != HOUSE]
    kits: dict[str, dict[str, Any]] = {}
    for key in names:
        own = saved.get(key) if isinstance(saved.get(key), dict) else {}
        kits[key] = _merge(_merge(house, DEFAULT_KITS.get(key, {})), own)
    return kits


def house_style(root: Path) -> dict[str, Any]:
    """The shared editing style after Ishaq's house changes (what every channel inherits)."""
    try:
        saved = json.loads(_path(root).read_text()).get(HOUSE) or {}
    except (OSError, ValueError, AttributeError):
        saved = {}
    return _merge(HOUSE_STYLE, saved if isinstance(saved, dict) else {})


def save_kit(root: Path, key: str, kit: dict[str, Any]) -> None:
    with _lock:
        try:
            saved = json.loads(_path(root).read_text())
        except (OSError, ValueError):
            saved = {}
        saved[key] = kit
        _path(root).parent.mkdir(parents=True, exist_ok=True)
        temporary = _path(root).with_suffix(".tmp")
        temporary.write_text(json.dumps(saved, indent=1))
        temporary.replace(_path(root))


def kit_style(kit: dict[str, Any]) -> ChannelStyle:
    """The kit's look: its base style (built-in, or the full style its example videos produced),
    then any style_overrides it inherited from the house style or set itself."""
    names = {field.name for field in fields(ChannelStyle)}
    base = STYLES.get(str(kit.get("style") or ""), STYLES["v3"])
    changes: dict[str, Any] = {}
    if isinstance(kit.get("style_fields"), dict):
        changes.update(kit["style_fields"])
    if isinstance(kit.get("style_overrides"), dict):
        changes.update(kit["style_overrides"])
    changes = {key: tuple(value) if isinstance(value, list) else value for key, value in changes.items() if key in names}
    if not changes:
        return base  # nothing overridden: exactly the old look
    return ChannelStyle(**{**asdict(base), **changes})


def kit_for(root: Path, key: Any) -> dict[str, Any]:
    kits = load_kits(root)
    return kits.get(str(key or "").lower()) or kits["v3"]


def allowed_designs(root: Path, key: Any, kind: str) -> list[str]:
    kit = kit_for(root, key)
    return list(kit.get("chapter_designs" if kind == "chapter" else "ingredient_designs") or [])


def custom_styles(root: Path) -> dict[str, ChannelStyle]:
    """Styles that differ from the built-in ones: channels made from example videos, any channel whose look
    was changed through style_overrides (its own or the house's), and kits that borrow a built-in style under
    their own name (v2_classic keeps its own key, so its own kit settings are found)."""
    styles: dict[str, ChannelStyle] = {}
    for key, kit in load_kits(root).items():
        if isinstance(kit.get("style_fields"), dict) or kit.get("style_overrides"):
            styles[key] = kit_style(kit)
        elif key not in STYLES:
            base = kit_style(kit)
            styles[key] = ChannelStyle(**{**asdict(base), "key": key, "name": str(kit.get("name") or base.name)})
    return styles
