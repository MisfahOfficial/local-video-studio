"""Real archival photographs from Openverse (Flickr, Wikimedia Commons, museums).

Used when no YouTube footage passes: a genuine period photo beats a generated one.
Only licences that allow commercial use and modification are requested, because
videos are monetised and photos are cropped and zoomed.
"""
from __future__ import annotations

import io
import re
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

# Google image search (Programmable Search Engine, JSON API): set from Settings, tried first when present.
# It finds the photos the free archives never have - old brand packets, adverts, shop fronts - which otherwise
# became AI stills (Ishaq, 5 Oct: "AI generated ke bajaye Google se images"). Used like the YouTube clips
# (fair-use mode), not as licensed photos.
GOOGLE = "https://www.googleapis.com/customsearch/v1"
_google: dict[str, str] = {"key": "", "cx": "", "serper": ""}
SERPER = "https://google.serper.dev/images"


def configure_serper(api_key: str) -> None:
    """Serper.dev returns Google Images results (2,500 free searches); Google's own API refuses new projects."""
    _google["serper"] = str(api_key or "").strip()


def _serper_images(query: str, count: int) -> list[dict[str, Any]]:
    if not _google["serper"]:
        return []
    import json
    import urllib.request

    request = urllib.request.Request(SERPER, data=json.dumps({"q": query, "num": min(20, max(10, count))}).encode(),
                                     headers={"X-API-KEY": _google["serper"], "Content-Type": "application/json"},
                                     method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        found = json.loads(response.read().decode("utf-8") or "{}")
    results = []
    for item in found.get("images") or []:
        url = str(item.get("imageUrl") or "")
        if not url or int(item.get("imageWidth") or 0) < 500:
            continue
        results.append({"id": f"g-{url}", "url": url, "thumbnail": item.get("thumbnailUrl") or url,
                        "title": str(item.get("title") or ""), "creator": str(item.get("domain") or item.get("source") or ""),
                        "license": "fair-use", "source": "Google Images",
                        "foreign_landing_url": item.get("link") or url,
                        "width": item.get("imageWidth"), "height": item.get("imageHeight")})
    return results


def configure_google(api_key: str, engine_id: str) -> None:
    engine = str(engine_id or "").strip()
    # The control panel also shows an embed snippet (<script ... cse.js?cx=ID>); pasted whole, it is a
    # 400 "invalid argument", so the id is taken out of it.
    found = re.search(r"cx=([A-Za-z0-9:_-]+)", engine)
    _google["key"], _google["cx"] = str(api_key or "").strip(), found.group(1) if found else engine


def _google_images(query: str, count: int) -> list[dict[str, Any]]:
    if not (_google["key"] and _google["cx"]):
        return []
    params = {"key": _google["key"], "cx": _google["cx"], "q": query, "searchType": "image",
              "num": min(10, max(1, count)), "imgSize": "large", "safe": "active"}
    found = request_json(f"{GOOGLE}?{urllib.parse.urlencode(params)}", timeout=30)
    assert isinstance(found, dict)
    results = []
    for item in found.get("items") or []:
        image = item.get("image") or {}
        if not item.get("link") or int(image.get("width") or 0) < 500:
            continue
        results.append({"id": f"g-{item['link']}", "url": item["link"], "thumbnail": image.get("thumbnailLink") or item["link"],
                        "title": str(item.get("title") or ""), "creator": str(item.get("displayLink") or ""),
                        "license": "fair-use", "source": "Google Images",
                        "foreign_landing_url": image.get("contextLink") or item["link"],
                        "width": image.get("width"), "height": image.get("height")})
    return results


def _openverse(query: str, count: int, sources: str = "") -> list[dict[str, Any]]:
    params = {"q": query, "page_size": count, "category": "photograph", "mature": "false",
              "license_type": "commercial,modification"}
    if sources:
        params["source"] = sources
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
    google = ([("serper", _serper_images)] if _google["serper"] else []) + (
        [("google", _google_images)] if _google["key"] and _google["cx"] else [])
    return google + ([general, history, archive, stock] if period else [general, stock, history, archive])


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
    if item.get("license") == "fair-use":
        return f'"{title}" from {creator} via Google Images (fair use)'
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
