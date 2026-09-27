"""Reusable pictures of single ingredients and dishes for motion graphics.

Each item is found once (a real Openverse photo, checked with CLIP; an aged
generated still only when allowed) and cached for every later video.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from .footage_match import FootageVerifier
from .photo_source import frame_photo, load_image, load_images, search_photos
from .vintage_still import generate_vintage_still

_lock = threading.Lock()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def item_image(
    name: str, library: Path, verifier: FootageVerifier, settings: Any, era: str = "",
    allow_generated: bool = True, exclude: set[str] | None = None,
) -> Path | None:
    """Cached picture of `name` (e.g. 'molasses'), else a fresh real photo, else a generated still."""
    library.mkdir(parents=True, exist_ok=True)
    cached = library / f"{_slug(name)}.jpg"
    if cached.is_file() and str(cached) not in (exclude or set()):
        return cached
    items: dict[str, dict[str, Any]] = {}
    for query in (f"{name} bowl", f"{name} kitchen", f"{name} ingredient", name):
        for item in search_photos(query, count=12):
            items.setdefault(str(item.get("id") or item["url"]), item)
        if len(items) >= 12:
            break
    pool = list(items.values())[:14]
    candidates = list(zip(pool, load_images([str(item.get("thumbnail") or item["url"]) for item in pool])))
    candidates = [(item, image) for item, image in candidates if image is not None]
    ranked = verifier.rank_ingredient_photos([image for _item, image in candidates], name) if candidates else []
    for index, _score in ranked[:2]:
        photo = load_image(str(candidates[index][0]["url"]))
        if photo is not None:
            with _lock:
                frame_photo(photo).save(cached, quality=92)
            return cached
    if not allow_generated:
        return None
    from PIL import Image

    for _attempt in range(2):
        try:
            generate_vintage_still(
                settings, f"A close-up of {name} in a simple bowl on a wooden kitchen counter.", name, era, cached,
                people=False,
            )
        except Exception:
            return None
        # Generated stills are checked like photos: a cook in the background means another try.
        with Image.open(cached) as still:
            if verifier.rank_ingredient_photos([still.convert("RGB")], name, "food"):
                return cached
        cached.unlink(missing_ok=True)
    return None


def dish_images(
    noun: str, dishes: list[str], library: Path, verifier: FootageVerifier, settings: Any, era: str,
    allow_generated: bool, count: int = 4,
) -> list[Path]:
    """Pictures for 'desserts': the video's own dishes when it lists them, else period examples."""
    if len(dishes) >= 3:
        pictures = [item_image(name, library, verifier, settings, era, allow_generated) for name in dishes[:count]]
        return [path for path in pictures if path is not None]
    # One pooled search, one ranking, then the best distinct photos.
    items: dict[str, dict[str, Any]] = {}
    for query in (f"{era} {noun}".strip(), f"vintage {noun}", f"homemade {noun}", noun):
        for item in search_photos(query, count=12):
            items.setdefault(str(item.get("id") or item["url"]), item)
    pool = list(items.values())[:30]
    candidates = list(zip(pool, load_images([str(item.get("thumbnail") or item["url"]) for item in pool])))
    candidates = [(item, image) for item, image in candidates if image is not None]
    ranked = verifier.rank_ingredient_photos([image for _item, image in candidates], noun, "homemade food")
    library.mkdir(parents=True, exist_ok=True)
    pictures: list[Path] = []
    for index, _score in ranked:
        photo = load_image(str(candidates[index][0]["url"]))
        if photo is None:
            continue
        path = library / f"{_slug(f'{era} {noun}')}-{len(pictures) + 1}.jpg"
        frame_photo(photo).save(path, quality=92)
        pictures.append(path)
        if len(pictures) >= count:
            break
    return pictures
