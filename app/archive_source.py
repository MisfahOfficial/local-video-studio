"""Footage beyond YouTube: the Internet Archive (public-domain period films, ads, home movies)
and the team's own project folders on Google Drive.

Both hand the sourcing engine the same shape yt-dlp does (an id, a title and direct video
streams), so reading the whole source, matching each sentence and cutting clips work unchanged.
Neither has YouTube's bot check, and YouTube is only asked when these have nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .providers.base import ProviderError
from .youtube_source import YouTubeSourceService

ARCHIVE_PREFIX = "ia-"
DRIVE_PREFIX = "drive-"
USER_AGENT = "LocalVideoStudio/0.8 (footage research; contact via archive.org)"
# Period collections: public-domain industrial/educational films, TV ads and home movies.
PERIOD_COLLECTIONS = ("prelinger", "classic_tv_commercials", "home_movies", "ephemera", "educationalfilms")
_archive_slots = threading.BoundedSemaphore(3)  # the Archive asks for gentle, few-at-a-time use

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
# Project folders also hold our own finished edits, sound effects and overlays; none of that is footage.
_NOT_FOOTAGE = re.compile(
    r"(x264|render|export|final|outro|intro|subscribe|like[- ]and|transition|film ?burn|overlay|whatsapp|"
    r"sequence|preview|proxy|green ?screen|snow[- ]falls|light ?leak|dust|grain|countdown|"
    r"^v\d|--|\bv[1-4]\b)",
    re.IGNORECASE,
)
_SKIP_FOLDERS = {"media cache", "media cache files", "adobe premiere pro auto-save", "adobe premiere pro video previews",
                 "adobe premiere pro audio previews", ".tmp.drivedownload"}


def _get_json(url: str, timeout: float = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _archive_slots:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 2:
                    time.sleep(5 * (attempt + 1))  # asked to slow down
                    continue
                raise ProviderError(f"Internet Archive returned HTTP {error.code}") from error
            except (OSError, ValueError) as error:
                if attempt == 2:
                    raise ProviderError(f"Internet Archive request failed: {error}") from error
                time.sleep(2)
    raise ProviderError("Internet Archive request failed")


def search_archive(query: str, maximum: int = 10, period: bool = True) -> list[dict[str, Any]]:
    """Public-domain / Creative Commons films matching the words, oldest collections first."""
    words = " ".join(re.findall(r"[A-Za-z0-9'-]+", query))
    if not words:
        return []
    scope = " OR ".join(f"collection:{name}" for name in PERIOD_COLLECTIONS)
    period_filter = f" AND (({scope}) OR year:[1920 TO 1985])" if period else ""
    q = f"({words}) AND mediatype:movies{period_filter}"
    params = urllib.parse.urlencode([
        ("q", q), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "description"), ("fl[]", "year"),
        ("fl[]", "collection"), ("fl[]", "licenseurl"), ("rows", str(maximum)), ("output", "json"),
        ("sort[]", "downloads desc"),
    ])
    try:
        docs = _get_json(f"https://archive.org/advancedsearch.php?{params}")["response"]["docs"]
    except (ProviderError, KeyError, TypeError):
        return []
    results = []
    for doc in docs:
        identifier = str(doc.get("identifier") or "")
        if not identifier:
            continue
        collection = doc.get("collection")
        collection = collection if isinstance(collection, list) else [collection]
        description = doc.get("description")
        results.append({
            "video_id": ARCHIVE_PREFIX + identifier,
            "title": str(doc.get("title") or identifier),
            "description": " ".join(description) if isinstance(description, list) else str(description or ""),
            "channel": "Internet Archive · " + ", ".join(str(item) for item in collection if item)[:60],
            "year": doc.get("year"),
            "source": "archive",
        })
    return results


def archive_info(identifier: str) -> dict[str, Any]:
    data = _get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    metadata = data.get("metadata") or {}
    formats = []
    duration = 0.0
    for item in data.get("files") or []:
        name = str(item.get("name") or "")
        kind = str(item.get("format") or "")
        if not name.lower().endswith(".mp4"):
            continue
        height, width = int(float(item.get("height") or 0)), int(float(item.get("width") or 0))
        duration = max(duration, float(item.get("length") or 0) if re.fullmatch(r"[\d.]+", str(item.get("length") or "")) else 0)
        formats.append({
            "format_id": kind or name, "url": f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}",
            "height": height or 240, "width": width or 320, "vcodec": "avc1", "acodec": "mp4a", "protocol": "https",
            "ext": "mp4", "filesize": int(item.get("size") or 0) or None,
            "http_headers": {"User-Agent": USER_AGENT},
        })
    if not formats:
        raise ProviderError("This Internet Archive item has no MP4 video")
    title = metadata.get("title")
    description = metadata.get("description")
    return {
        "id": ARCHIVE_PREFIX + identifier, "title": " ".join(title) if isinstance(title, list) else str(title or identifier),
        "description": " ".join(description) if isinstance(description, list) else str(description or ""),
        "channel": "Internet Archive", "duration": duration, "formats": formats,
        "height": max(item["height"] for item in formats), "width": max(item["width"] for item in formats),
        "webpage_url": f"https://archive.org/details/{identifier}",
        "license": str(metadata.get("licenseurl") or "Internet Archive (check item rights)"),
    }


# ---------------------------------------------------------------- Google Drive project folders
def _is_footage(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS and not _NOT_FOOTAGE.search(path.stem)


def build_drive_index(roots: list[str], index_path: Path, max_seconds: float = 600) -> dict[str, Any]:
    """Names of every footage file under the chosen folders (listing only, nothing is downloaded)."""
    started = time.time()
    files: list[dict[str, Any]] = []
    complete = True
    for root in roots:
        for folder, subfolders, names in os.walk(root):
            subfolders[:] = [name for name in subfolders if name.lower() not in _SKIP_FOLDERS]
            for name in names:
                path = Path(folder) / name
                if _is_footage(path):
                    files.append({"path": str(path), "name": path.stem, "folder": Path(folder).name})
            if time.time() - started > max_seconds:
                complete = False
                break
    index = {"roots": roots, "files": files, "complete": complete, "built": time.time()}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index))
    return index


def load_drive_index(index_path: Path) -> list[dict[str, Any]]:
    try:
        return list(json.loads(index_path.read_text()).get("files") or [])
    except (OSError, ValueError):
        return []


_WORD = re.compile(r"[a-z0-9]+")


def search_drive(files: list[dict[str, Any]], query: str, maximum: int = 6) -> list[dict[str, Any]]:
    """Files whose names share the query's words (all of them first)."""
    wanted = [word for word in _WORD.findall(query.lower()) if len(word) > 2 and word not in {"the", "and", "recipe", "old", "fashioned", "vintage", "footage", "film"}]
    if not wanted:
        return []
    scored = []
    seen: set[str] = set()
    for item in files:
        # The same download is often copied into several project folders: one copy is enough.
        key = item["name"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        name_words = set(_WORD.findall(item["name"].lower()))
        hits = sum(word in name_words for word in wanted)
        if hits and hits >= min(2, len(wanted)):
            scored.append((hits / len(wanted), item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{
        "video_id": drive_id(item["path"]), "title": item["name"], "description": item["folder"],
        "channel": "Team Drive", "local_path": item["path"], "source": "drive",
    } for _score, item in scored[:maximum]]


def drive_id(path: str) -> str:
    return DRIVE_PREFIX + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def drive_info(path: str, ffprobe_path: str) -> dict[str, Any]:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", path], capture_output=True, text=True, timeout=600,
    )
    try:
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError) as error:
        raise ProviderError(f"Could not read the Drive file: {error}") from error
    if not width or not height:
        raise ProviderError("The Drive file has no readable video")
    shown = min(height, 1080)
    return {
        "id": drive_id(path), "title": Path(path).stem, "channel": "Team Drive",
        "duration": duration, "width": width, "height": height, "local_path": path,
        "formats": [{"format_id": "local", "url": path, "height": shown, "width": width, "vcodec": "avc1",
                     "acodec": "none", "protocol": "file", "ext": Path(path).suffix.lstrip(".")}],
        "webpage_url": "file://" + path, "license": "Own footage (team Drive)",
    }


class MultiSourceService(YouTubeSourceService):
    """YouTube plus the Internet Archive and the team Drive, behind one interface.

    After YouTube's bot check fires once, no more YouTube requests are made in this run
    (each one would only prolong the block)."""

    def __init__(self, *args: Any, drive_files: list[dict[str, Any]] | None = None, period: bool = True, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.drive_files = drive_files or []
        self.period = period
        self.youtube_blocked = False
        self._local: dict[str, str] = {}

    @property
    def ffprobe_path(self) -> str:
        path = Path(self.ffmpeg_path)
        return str(path.with_name("ffprobe")) if path.name == "ffmpeg" and path.parent != Path(".") else "ffprobe"

    def search(self, query: str, maximum: int = 8) -> list[dict[str, Any]]:
        drive = search_drive(self.drive_files, query, maximum=4)
        for item in drive:
            self._local[item["video_id"]] = item["local_path"]
        archive = search_archive(query, maximum=6, period=self.period)
        youtube: list[dict[str, Any]] = []
        if not self.youtube_blocked:
            try:
                youtube = super().search(query, maximum)
            except ProviderError:
                youtube = []
        # Own footage first, then public-domain films, then YouTube.
        return drive + archive + youtube

    def inspect(self, video_id: str) -> dict[str, Any]:
        if video_id.startswith(ARCHIVE_PREFIX):
            return archive_info(video_id[len(ARCHIVE_PREFIX):])
        if video_id.startswith(DRIVE_PREFIX):
            path = self._local.get(video_id)
            if not path:
                raise ProviderError("Unknown Drive file")
            return drive_info(path, self.ffprobe_path)
        if self.youtube_blocked:
            raise ProviderError("YouTube is blocking this computer for now; skipped")
        try:
            return super().inspect(video_id)
        except ProviderError as error:
            if "blocking this computer" in str(error):
                self.youtube_blocked = True
            raise

    def source_clip(self, *, video_id: str, query: str, duration: float, destination: Path,
                    source_start_seconds: float | None = None, info: dict[str, Any] | None = None,
                    padding: float = 0.0) -> dict[str, Any]:
        if not video_id.startswith((ARCHIVE_PREFIX, DRIVE_PREFIX)):
            return super().source_clip(video_id=video_id, query=query, duration=duration, destination=destination,
                                       source_start_seconds=source_start_seconds, info=info, padding=padding)
        info = info or self.inspect(video_id)
        start = float(source_start_seconds or 0)
        source_duration = float(info.get("duration") or 0)
        if source_duration > 0:
            start = min(max(0.0, start), max(0.0, source_duration - duration))
        if padding > 0:
            padded_start = max(0.0, start - padding)
            padded_end = min(start + duration + padding, source_duration) if source_duration else start + duration + padding
            start, duration = padded_start, max(duration, padded_end - padded_start)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not self._direct_excerpt(info, start, duration, destination):
            raise ProviderError("Could not cut a clip from this source")
        return {
            "video_id": video_id, "source_url": str(info.get("webpage_url") or ""),
            "title": str(info.get("title") or ""), "channel": str(info.get("channel") or ""),
            "license": str(info.get("license") or ""), "usage_basis": "public-domain" if video_id.startswith(ARCHIVE_PREFIX) else "own",
            "source_start_seconds": round(start, 3), "source_end_seconds": round(start + duration, 3),
            "matched_from_captions": False,
        }
