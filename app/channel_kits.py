"""Channel Kits: each channel's locked look.

A kit fixes the channel's colours, fonts, text and caption style, backgrounds and the 2-3
motion designs it may use for chapter cards and ingredient graphics. Videos only rotate
inside that approved set, so every video looks like the channel while graphics stay fresh.
Kits change only when Ishaq changes them. New channels get a kit read from their example
videos (see reference_style.py).
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

DEFAULT_KITS: dict[str, dict[str, Any]] = {
    "v1": {"name": "V1 · Vintage Life of USA", "style": "v1", "country": "US", "profile": "vintage_recipe",
           "chapter_designs": ["vintage_tv", "newspaper"], "ingredient_designs": ["carousel", "scrapbook"],
           "captions": {"animation": "highlight", "position": "bottom"}},
    "v2": {"name": "V2 · Forgotten Flavors of USA", "style": "v2", "country": "US", "profile": "vintage_recipe",
           "chapter_designs": ["typewriter_card", "classic"], "ingredient_designs": ["recipe_book", "scrapbook"],
           "captions": {"animation": "highlight", "position": "bottom"}},
    "v3": {"name": "V3 · Britain We Lived In", "style": "v3", "country": "GB", "profile": "vintage_recipe",
           "chapter_designs": ["film_slate", "typewriter_card"], "ingredient_designs": ["chalkboard", "cards"],
           "captions": {"animation": "highlight", "position": "bottom"}},
    "v4": {"name": "V4 · Canada We Lived In", "style": "v4", "country": "CA", "profile": "vintage_recipe",
           "chapter_designs": ["typewriter_card", "film_slate"], "ingredient_designs": ["scrapbook", "chalkboard"],
           "captions": {"animation": "highlight", "position": "bottom"}},
}


def _path(root: Path) -> Path:
    return Path(root) / KIT_FILE


def load_kits(root: Path) -> dict[str, dict[str, Any]]:
    """Built-in kits for V1-V4, overridden or extended by the saved file (new channels, Ishaq's edits)."""
    kits = {key: dict(value) for key, value in DEFAULT_KITS.items()}
    try:
        saved = json.loads(_path(root).read_text())
    except (OSError, ValueError):
        saved = {}
    for key, value in saved.items() if isinstance(saved, dict) else []:
        if isinstance(value, dict):
            kits[key] = {**kits.get(key, {}), **value}
    return kits


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
    """The kit's look: a built-in style, or the full style a new channel's example videos produced."""
    custom = kit.get("style_fields")
    if isinstance(custom, dict):
        names = {field.name for field in fields(ChannelStyle)}
        base = asdict(STYLES[str(kit.get("style") or "v3")] if kit.get("style") in STYLES else STYLES["v3"])
        merged = {**base, **{key: value for key, value in custom.items() if key in names}}
        for key, value in merged.items():
            if isinstance(value, list):
                merged[key] = tuple(value)
        return ChannelStyle(**merged)
    return STYLES.get(str(kit.get("style") or ""), STYLES["v3"])


def kit_for(root: Path, key: Any) -> dict[str, Any]:
    kits = load_kits(root)
    return kits.get(str(key or "").lower()) or kits["v3"]


def allowed_designs(root: Path, key: Any, kind: str) -> list[str]:
    kit = kit_for(root, key)
    return list(kit.get("chapter_designs" if kind == "chapter" else "ingredient_designs") or [])


def custom_styles(root: Path) -> dict[str, ChannelStyle]:
    """Styles of channels created from example videos (for get_style)."""
    return {key: kit_style(kit) for key, kit in load_kits(root).items() if isinstance(kit.get("style_fields"), dict)}
