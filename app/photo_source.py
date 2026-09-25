"""Real archival photographs from Openverse (Flickr, Wikimedia Commons, museums).

Used when no YouTube footage passes: a genuine period photo beats a generated one.
Only licences that allow commercial use and modification are requested, because
videos are monetised and photos are cropped and zoomed.
"""
from __future__ import annotations

import io
import urllib.parse
from pathlib import Path
from typing import Any

from .providers.base import ProviderError
from .providers.http import download_bytes, request_json

OPENVERSE = "https://api.openverse.org/v1/images/"
SIZE = (1920, 1080)


def search_photos(query: str, count: int = 12) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "q": query, "page_size": count, "category": "photograph", "mature": "false",
        "license_type": "commercial,modification",
    })
    try:
        found = request_json(f"{OPENVERSE}?{params}", timeout=30)
    except ProviderError:
        return []
    assert isinstance(found, dict)
    return [
        item for item in found.get("results") or []
        if item.get("url") and int(item.get("width") or 0) >= 500
    ]


def load_image(url: str) -> Any:
    from PIL import Image

    try:
        return Image.open(io.BytesIO(download_bytes(url, timeout=40, max_bytes=25 * 1024 * 1024))).convert("RGB")
    except (ProviderError, OSError, ValueError):
        return None


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
