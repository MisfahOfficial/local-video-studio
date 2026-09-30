"""Reusable pictures of single ingredients and dishes for motion graphics.

Each item is found once (a real Openverse photo, checked with CLIP; an aged
generated still only when allowed) and cached for every later video.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from .ai_judge import best_usable
from .footage_match import FootageVerifier
from .photo_source import frame_photo, load_image, load_images, search_photos
from .vintage_still import generate_vintage_still

_lock = threading.Lock()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def redrawable_items(metadata: dict[str, Any], library: Path | None) -> list[dict[str, str]]:
    """An ingredient card's items with their pictures, so it can be drawn again (e.g. without names).
    Older cards stored only names; their pictures are the library's cached ones. [] if any is missing."""
    items = []
    for item in metadata.get("items") or []:
        if isinstance(item, dict) and item.get("image") and Path(str(item["image"])).is_file():
            items.append({"label": str(item.get("label") or ""), "image": str(item["image"])})
            continue
        name = str(item.get("label") if isinstance(item, dict) else item)
        cached = (library / f"{_slug(name)}.jpg") if library else None
        if not cached or not cached.is_file():
            return []
        items.append({"label": name, "image": str(cached)})
    return items


def item_image(
    name: str, library: Path, verifier: FootageVerifier, settings: Any, era: str = "",
    allow_generated: bool = True, exclude: set[str] | None = None, judge: Any = None,
) -> Path | None:
    """Cached picture of `name` (e.g. 'molasses'), else a fresh real photo, else a generated still."""
    from PIL import Image

    library.mkdir(parents=True, exist_ok=True)
    cached = library / f"{_slug(name)}.jpg"
    approved = cached.with_suffix(".judged")
    need = f"Plain {name} on its own as a cooking ingredient (raw, not a finished dish), no people, a real photograph."
    if cached.is_file() and str(cached) not in (exclude or set()):
        if judge is None or approved.is_file():
            return cached
        # Pictures cached before the judge existed are checked once ("rice" that was a cheesy rice bowl).
        with Image.open(cached) as picture:
            verdicts = judge.judge(need, [[picture.convert("RGB")]])
        if verdicts is None or verdicts[0].fits and not verdicts[0].present_day_person:
            approved.touch()
            return cached
        cached.unlink(missing_ok=True)
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
    order = [index for index, _score in ranked]
    if judge is not None and order:
        verdicts = judge.judge(need, [[candidates[index][1]] for index in order[:4]])
        if verdicts is not None:
            order = [order[index] for index in best_usable(verdicts)]
    for index in order[:2]:
        photo = load_image(str(candidates[index][0]["url"]))
        if photo is not None:
            with _lock:
                frame_photo(photo).save(cached, quality=92)
                if judge is not None:
                    approved.touch()
            return cached
    if not allow_generated:
        return None

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
            still = still.convert("RGB")
        verdicts = judge.judge(need, [[still]]) if judge is not None else None
        # A generated still is AI by design: only the subject and the absence of people matter.
        if verdicts is not None and verdicts[0].fits and not verdicts[0].present_day_person:
            approved.touch()
            return cached
        if verdicts is None and verifier.rank_ingredient_photos([still], name, "food"):
            return cached
        cached.unlink(missing_ok=True)
    return None


def dish_images(
    noun: str, dishes: list[str], library: Path, verifier: FootageVerifier, settings: Any, era: str,
    allow_generated: bool, count: int = 4, judge: Any = None,
) -> list[Path]:
    """Pictures for 'desserts': the video's own dishes when it lists them, else period examples."""
    if len(dishes) >= 3:
        pictures = [item_image(name, library, verifier, settings, era, allow_generated, judge=judge)
                    for name in dishes[:count]]
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
    order = [index for index, _score in ranked]
    if judge is not None and order:
        verdicts = judge.judge(f"A real photo of homemade {era} {noun}, food only, no people.".replace("  ", " "),
                               [[candidates[index][1]] for index in order[:4]])
        if verdicts is not None:
            order = [order[index] for index in best_usable(verdicts)]
    library.mkdir(parents=True, exist_ok=True)
    pictures: list[Path] = []
    for index in order:
        photo = load_image(str(candidates[index][0]["url"]))
        if photo is None:
            continue
        path = library / f"{_slug(f'{era} {noun}')}-{len(pictures) + 1}.jpg"
        frame_photo(photo).save(path, quality=92)
        pictures.append(path)
        if len(pictures) >= count:
            break
    return pictures
