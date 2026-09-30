"""Real archival photographs from Openverse (Flickr, Wikimedia Commons, museums).

Used when no YouTube footage passes: a genuine period photo beats a generated one.
Only licences that allow commercial use and modification are requested, because
videos are monetised and photos are cropped and zoomed.
"""
from __future__ import annotations

import io
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .providers.base import ProviderError
from .providers.http import download_bytes, request_json

OPENVERSE = "https://api.openverse.org/v1/images/"
SIZE = (1920, 1080)


# Free image sources, tried in turn until there are enough candidates. A source that errors
# or rate-limits rests for a while and the next one takes over. Every picture is still checked
# (CLIP, and the AI judge when set) by the caller before it is used.
HISTORY_SOURCES = ("smithsonian_american_history_museum,smithsonian_african_american_history_museum,"
                   "smithsonian_institution_archives,europeana,wikimedia")
STOCK_SOURCES = "rawpixel,stocksnap,flickr"
COOLDOWN_SECONDS = 600
_resting: dict[str, float] = {}


def _openverse(query: str, count: int, sources: str = "") -> list[dict[str, Any]]:
    params = {"q": query, "page_size": count, "mature": "false", "license_type": "commercial,modification"}
    if sources:
        params["source"] = sources
    else:
        params["category"] = "photograph"
    found = request_json(f"{OPENVERSE}?{urllib.parse.urlencode(params)}", timeout=30)
    assert isinstance(found, dict)
    return [item for item in found.get("results") or [] if item.get("url") and int(item.get("width") or 0) >= 500]


def _archive_images(query: str, count: int) -> list[dict[str, Any]]:
    """Public-domain / Creative Commons pictures on the Internet Archive."""
    from concurrent.futures import ThreadPoolExecutor

    words = " ".join(query.split())
    params = urllib.parse.urlencode([
        ("q", f"({words}) AND mediatype:image AND (licenseurl:*publicdomain* OR licenseurl:*creativecommons*)"),
        ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "creator"), ("fl[]", "licenseurl"),
        ("rows", str(count)), ("output", "json"),
    ])
    docs = (request_json(f"https://archive.org/advancedsearch.php?{params}", timeout=30) or {}).get("response", {}).get("docs", [])

    def resolve(doc: dict[str, Any]) -> dict[str, Any] | None:
        identifier = str(doc.get("identifier") or "")
        try:
            files = (request_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}", timeout=30) or {}).get("files", [])
        except ProviderError:
            return None
        pictures = [item for item in files if str(item.get("name", "")).lower().endswith((".jpg", ".jpeg", ".png"))
                    and item.get("source") == "original" and int(float(item.get("width") or 0)) >= 500]
        if not pictures:
            return None
        best = max(pictures, key=lambda item: int(float(item.get("width") or 0)))
        url = f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(best['name'])}"
        license_url = str(doc.get("licenseurl") or "")
        return {
            "id": f"ia-{identifier}", "url": url, "thumbnail": f"https://archive.org/services/img/{urllib.parse.quote(identifier)}",
            "width": int(float(best.get("width") or 0)), "title": str(doc.get("title") or identifier),
            "creator": str(doc.get("creator") or "unknown"), "license": "pdm" if "publicdomain" in license_url else "cc",
            "license_url": license_url, "source": "Internet Archive",
            "foreign_landing_url": f"https://archive.org/details/{identifier}",
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        return [item for item in pool.map(resolve, docs[:count]) if item]


def image_sources(period: bool = True) -> list[tuple[str, Any]]:
    """The order to ask: period archives first for old footage stories, stock first for today's topics."""
    history = ("openverse-history", lambda q, n: _openverse(q, n, HISTORY_SOURCES))
    general = ("openverse", lambda q, n: _openverse(q, n))
    stock = ("openverse-stock", lambda q, n: _openverse(q, n, STOCK_SOURCES))
    archive = ("internet-archive", _archive_images)
    return [general, history, archive, stock] if period else [general, stock, history, archive]


def search_photos(query: str, count: int = 12, period: bool = True) -> list[dict[str, Any]]:
    """Candidates from the free sources in turn, until `count` are found."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, search in image_sources(period):
        if len(results) >= count:
            break
        if time.time() < _resting.get(name, 0):
            continue
        try:
            found = search(query, count)
        except (ProviderError, OSError, ValueError, AssertionError, KeyError, TypeError):
            _resting[name] = time.time() + COOLDOWN_SECONDS  # limit reached or down: let the next source work
            continue
        for item in found:
            key = str(item.get("url"))
            if key not in seen:
                seen.add(key)
                results.append(item)
    return results[:count]


def load_image(url: str) -> Any:
    from PIL import Image

    try:
        return Image.open(io.BytesIO(download_bytes(url, timeout=40, max_bytes=25 * 1024 * 1024))).convert("RGB")
    except (ProviderError, OSError, ValueError):
        return None


def load_images(urls: list[str]) -> list[Any]:
    """Several thumbnails at once (None where one fails); one by one this took most of a scene's time."""
    from concurrent.futures import ThreadPoolExecutor

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        return list(pool.map(load_image, urls))


def frame_photo(photo: Any) -> Any:
    """Fill 16:9: landscape photos are cropped, others sit on a blurred copy of themselves."""
    from PIL import Image, ImageFilter, ImageOps

    ratio = photo.width / photo.height
    if ratio >= 1.45:
        return ImageOps.fit(photo, SIZE)
    background = ImageOps.fit(photo, SIZE).filter(ImageFilter.GaussianBlur(28))
    background = Image.blend(background, Image.new("RGB", SIZE, (0, 0, 0)), 0.35)
    foreground = ImageOps.contain(photo, SIZE)
    background.paste(foreground, ((SIZE[0] - foreground.width) // 2, (SIZE[1] - foreground.height) // 2))
    return background


def attribution(item: dict[str, Any]) -> str:
    title = item.get("title") or "Untitled"
    creator = item.get("creator") or "unknown"
    licence = f"CC {str(item.get('license') or '').upper()} {item.get('license_version') or ''}".strip()
    if str(item.get("license")) in {"cc0", "pdm"}:
        licence = "public domain" if item.get("license") == "pdm" else "CC0"
    return f'"{title}" by {creator} ({licence}) via {item.get("source") or "Openverse"}'


def save_photo(item: dict[str, Any], photo: Any, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame_photo(photo).save(destination, quality=92)
    return {
        "title": str(item.get("title") or ""), "creator": str(item.get("creator") or ""),
        "license": str(item.get("license") or ""), "license_url": str(item.get("license_url") or ""),
        "source": str(item.get("source") or ""), "source_url": str(item.get("foreign_landing_url") or item.get("url")),
        "attribution": attribution(item), "real_photo": True,
    }
