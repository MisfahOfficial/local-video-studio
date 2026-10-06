"""Remember what YouTube already told us, so it is asked as little as possible (Ishaq, 6 Oct: blocks).

Most YouTube requests were not downloads but questions: searches and a full page read ("inspect") of every
candidate video. Both are now kept on disk and shared by every project:
- a search's results for 7 days (a video made again, or the next video on the same items, searches nothing);
- a video's full details for 4 hours (its stream links stay valid about 6 hours, so downloads still work).
Only the sourcing side uses this; nothing in editing changes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

SEARCH_SECONDS = 7 * 24 * 3600
INFO_SECONDS = 4 * 3600
_lock = threading.Lock()
_folder: list[Path] = []


def configure(root: Path) -> None:
    """Where the memory lives (the app's data folder); without it nothing is cached."""
    folder = Path(root) / "youtube_memory"
    folder.mkdir(parents=True, exist_ok=True)
    _folder[:] = [folder]


def _path(kind: str, key: str) -> Path | None:
    if not _folder:
        return None
    name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
    return _folder[0] / f"{kind}-{name}.json.gz"


def _read(kind: str, key: str, max_age: float) -> Any:
    path = _path(kind, key)
    if path is None or not path.is_file() or time.time() - path.stat().st_mtime > max_age:
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write(kind: str, key: str, value: Any) -> None:
    path = _path(kind, key)
    if path is None:
        return
    try:
        partial = path.with_suffix(".tmp")
        with _lock, gzip.open(partial, "wt", encoding="utf-8") as handle:
            json.dump(value, handle, default=str)  # yt-dlp details may hold odd objects
        partial.replace(path)
    except (OSError, TypeError, ValueError):
        pass


def cached_search(query: str, maximum: int) -> list[dict[str, Any]] | None:
    value = _read("search", f"{query.strip().lower()}|{maximum}", SEARCH_SECONDS)
    return value if isinstance(value, list) else None


def remember_search(query: str, maximum: int, results: list[dict[str, Any]]) -> None:
    if results:  # an empty answer may be a hiccup; ask again next time
        _write("search", f"{query.strip().lower()}|{maximum}", results)


def cached_info(video_id: str) -> dict[str, Any] | None:
    value = _read("info", video_id, INFO_SECONDS)
    return value if isinstance(value, dict) else None


def remember_info(video_id: str, info: dict[str, Any]) -> None:
    _write("info", video_id, info)


def clean(max_files: int = 4000) -> None:
    """Old entries go first when the folder grows past `max_files` (a few hundred MB at most)."""
    if not _folder:
        return
    files = sorted(_folder[0].glob("*.json.gz"), key=lambda item: item.stat().st_mtime)
    for path in files[:max(0, len(files) - max_files)]:
        path.unlink(missing_ok=True)
