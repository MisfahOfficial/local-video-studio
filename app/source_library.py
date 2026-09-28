"""Whole-video checks for the few sources each section is cut from.

A light 360p copy of every chosen video is downloaded once (in parallel byte ranges:
YouTube slows a single connection to ~400 KB/s) and read in one FFmpeg pass: one frame
per second for the visual checks plus every hard cut. Final clips are still cut from the
full-quality stream, only for the moments that were picked.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

CHUNK_BYTES = 1 << 20
# Enough to beat the per-connection limit without looking like a flood to YouTube.
WORKERS = 6
ANALYSIS_HEIGHT = 360
CACHE_DAYS = 14
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def analysis_format(info: dict[str, Any]) -> dict[str, Any] | None:
    """The smallest-but-readable direct video stream (H.264, about 360p, known size)."""
    formats = [
        item for item in info.get("formats") or []
        if item.get("url") and item.get("vcodec") not in (None, "none") and str(item.get("protocol") or "") == "https"
        and (item.get("filesize") or item.get("filesize_approx")) and 144 <= int(item.get("height") or 0) <= 480
    ]
    if not formats:
        return None
    return min(formats, key=lambda item: (
        not str(item.get("vcodec") or "").startswith("avc1"), abs(int(item.get("height") or 0) - ANALYSIS_HEIGHT),
    ))


def fetch_parallel(url: str, size: int, destination: Path, headers: dict[str, str] | None = None) -> None:
    """Download `size` bytes as many 1 MB ranges at once; one slow range never stalls the rest."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".part")
    with open(partial, "wb") as handle:
        handle.truncate(size)
    ranges = [(start, min(size, start + CHUNK_BYTES) - 1) for start in range(0, size, CHUNK_BYTES)]
    lock = threading.Lock()

    def fetch(bounds: tuple[int, int]) -> None:
        first, last = bounds
        for attempt in range(4):
            try:
                request = urllib.request.Request(url, headers={**(headers or {}), "Range": f"bytes={first}-{last}"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read()
                if len(data) != last - first + 1:
                    raise OSError("short read")
                with lock, open(partial, "r+b") as handle:
                    handle.seek(first)
                    handle.write(data)
                return
            except OSError:
                time.sleep(0.5 * (attempt + 1))
        raise OSError(f"bytes {first}-{last} failed")

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(fetch, ranges))
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)


def analysis_copy(info: dict[str, Any], cache: Path) -> Path | None:
    """Cached light copy of a source for whole-video checks (shared by every project)."""
    video_id = str(info.get("id") or "")
    stream = analysis_format(info)
    if not video_id or stream is None:
        return None
    destination = cache / f"{video_id}-{int(stream.get('height') or 0)}.mp4"
    with _locks_guard:
        lock = _locks.setdefault(video_id, threading.Lock())
    with lock:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        try:
            fetch_parallel(str(stream["url"]), int(stream.get("filesize") or stream.get("filesize_approx")),
                           destination, stream.get("http_headers"))
        except OSError:
            return None
    return destination


def read_source(path: Path, ffmpeg_path: str = "ffmpeg", duration: float = 0.0) -> tuple[list[tuple[float, Any]], list[float]]:
    """One FFmpeg pass: (time, frame) once per second (every 2 s past 20 minutes) and every hard cut."""
    from PIL import Image

    step = 2.0 if duration > 1200 else 1.0
    with tempfile.TemporaryDirectory() as folder:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-hwaccel", "videotoolbox", "-i", str(path), "-an", "-filter_complex",
             f"[0:v]scdet=threshold=10,split[a][b];[a]fps=1/{step},scale=384:-2[f];[b]nullsink",
             "-map", "[f]", "-q:v", "4", f"{folder}/%05d.jpg"],
            capture_output=True, text=True, timeout=600,
        )
        cuts = [float(value) for value in re.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", result.stderr)]
        frames = []
        for index, frame in enumerate(sorted(Path(folder).glob("*.jpg"))):
            with Image.open(frame) as image:
                frames.append((index * step, image.convert("RGB")))
    return frames, cuts


def clean_cache(cache: Path) -> None:
    """Light copies are only needed while sourcing; old ones are removed."""
    if not cache.is_dir():
        return
    limit = time.time() - CACHE_DAYS * 86400
    for path in cache.glob("*.mp4"):
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            continue
