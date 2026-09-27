"""Free stock footage from Pexels (free API key, free for commercial use, no credit needed)."""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from .providers.base import ProviderError
from .providers.http import download_bytes, request_json

PEXELS_VIDEOS = "https://api.pexels.com/videos/search"


def search_stock_videos(api_key: str, query: str, count: int = 10) -> list[dict[str, Any]]:
    if not api_key.strip():
        return []
    params = urllib.parse.urlencode({"query": query, "per_page": count, "orientation": "landscape", "size": "medium"})
    try:
        found = request_json(f"{PEXELS_VIDEOS}?{params}", headers={"Authorization": api_key.strip()}, timeout=30)
    except ProviderError:
        return []
    assert isinstance(found, dict)
    return [item for item in found.get("videos") or [] if best_file(item)]


def best_file(item: dict[str, Any]) -> dict[str, Any] | None:
    """The 1080p (else largest landscape up to 1080p) MP4 of a Pexels video."""
    files = [
        entry for entry in item.get("video_files") or []
        if entry.get("link") and str(entry.get("file_type") or "").endswith("mp4")
        and int(entry.get("width") or 0) >= int(entry.get("height") or 1) and 0 < int(entry.get("height") or 0) <= 1080
    ]
    return max(files, key=lambda entry: int(entry.get("height") or 0)) if files else None


def download_stock_video(item: dict[str, Any], destination: Path) -> dict[str, Any]:
    entry = best_file(item)
    if not entry:
        raise ProviderError("No usable Pexels file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(download_bytes(str(entry["link"]), timeout=180, max_bytes=300 * 1024 * 1024))
    user = item.get("user") or {}
    return {
        "title": f"Pexels video {item.get('id')}", "creator": str(user.get("name") or ""),
        "source_url": str(item.get("url") or ""), "license": "Pexels License",
        "attribution": f"Video by {user.get('name') or 'unknown'} on Pexels", "stock_video": True,
        "duration": float(item.get("duration") or 0),
    }
