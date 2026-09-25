from __future__ import annotations

import json
import html
import math
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .providers.base import ProviderError
from .providers.http import request_json, verified_ssl_context


YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _iso_duration(value: str) -> float:
    match = re.fullmatch(r"P(?:([0-9.]+)D)?T?(?:([0-9.]+)H)?(?:([0-9.]+)M)?(?:([0-9.]+)S)?", value or "")
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(item or 0) for item in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _words(value: str) -> set[str]:
    ignored = {"the", "and", "that", "this", "with", "from", "into", "their", "were", "was", "for", "but", "are", "you", "your"}
    return {word for word in re.findall(r"[a-z0-9']+", value.lower()) if len(word) > 2 and word not in ignored}


def best_caption_timestamp(events: list[dict[str, Any]], query: str, duration: float) -> float:
    """Return the caption window with the strongest scene-keyword overlap."""
    wanted = _words(query)
    if not wanted or not events:
        return 0.0
    best_score = 0.0
    best_start = 0.0
    window: list[tuple[float, str]] = []
    for event in events:
        try:
            start = float(event.get("tStartMs", 0)) / 1000
        except (TypeError, ValueError):
            continue
        text = "".join(str(segment.get("utf8", "")) for segment in event.get("segs", []) if isinstance(segment, dict))
        if not text.strip():
            continue
        window.append((start, text))
        window = [item for item in window if start - item[0] <= max(18.0, duration * 2)]
        found = _words(" ".join(item[1] for item in window))
        overlap = wanted & found
        score = len(overlap) / max(1, len(wanted)) + len(overlap) * 0.02
        if score > best_score:
            best_score = score
            best_start = window[0][0]
    return max(0.0, best_start - 0.8) if best_score > 0 else 0.0


class YouTubeSourceService:
    def __init__(self, api_key: str, ffmpeg_path: str = "ffmpeg"):
        self.api_key = api_key.strip()
        self.ffmpeg_path = ffmpeg_path

    def search(self, query: str, maximum: int = 8) -> list[dict[str, Any]]:
        if not self.api_key:
            raise ProviderError("Add a YouTube Data API key in Settings first")
        clean_query = " ".join(query.split())[:240]
        if not clean_query:
            raise ProviderError("Enter a YouTube search description")
        params = urllib.parse.urlencode({
            "part": "snippet", "type": "video", "q": clean_query,
            "maxResults": max(1, min(12, maximum)), "safeSearch": "moderate",
            "videoEmbeddable": "true", "videoLicense": "creativeCommon", "key": self.api_key,
        })
        found = request_json(f"{YOUTUBE_SEARCH_URL}?{params}", timeout=45)
        assert isinstance(found, dict)
        ids = [str(item.get("id", {}).get("videoId", "")) for item in found.get("items", [])]
        ids = [item for item in ids if re.fullmatch(r"[A-Za-z0-9_-]{11}", item)]
        if not ids:
            return []
        details_params = urllib.parse.urlencode({
            "part": "snippet,contentDetails,status", "id": ",".join(ids), "key": self.api_key,
        })
        details = request_json(f"{YOUTUBE_VIDEOS_URL}?{details_params}", timeout=45)
        assert isinstance(details, dict)
        by_id = {str(item.get("id")): item for item in details.get("items", [])}
        results: list[dict[str, Any]] = []
        for video_id in ids:
            item = by_id.get(video_id, {})
            snippet = item.get("snippet", {})
            thumbnails = snippet.get("thumbnails", {})
            thumbnail = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
            results.append({
                "video_id": video_id,
                "title": html.unescape(str(snippet.get("title") or "Untitled video")),
                "channel": html.unescape(str(snippet.get("channelTitle") or "Unknown channel")),
                "published_at": str(snippet.get("publishedAt") or ""),
                "thumbnail_url": str(thumbnail),
                "duration_seconds": _iso_duration(str(item.get("contentDetails", {}).get("duration") or "")),
                "license": str(item.get("status", {}).get("license") or "creativeCommon"),
                "watch_url": f"https://www.youtube.com/watch?v={video_id}",
            })
        return results

    def _creative_commons_license(self, video_id: str) -> str:
        if not self.api_key:
            raise ProviderError("Add a YouTube Data API key in Settings first")
        params = urllib.parse.urlencode({"part": "status", "id": video_id, "key": self.api_key})
        response = request_json(f"{YOUTUBE_VIDEOS_URL}?{params}", timeout=45)
        assert isinstance(response, dict)
        items = response.get("items", [])
        license_code = str(items[0].get("status", {}).get("license") or "") if items else ""
        if license_code != "creativeCommon":
            raise ProviderError("This video is not currently marked with a Creative Commons licence on YouTube")
        return "Creative Commons"

    @staticmethod
    def _caption_events(info: dict[str, Any]) -> list[dict[str, Any]]:
        tracks = info.get("subtitles") or info.get("automatic_captions") or {}
        choices: list[dict[str, Any]] = []
        for language in ("en", "en-US", "en-GB"):
            choices.extend(tracks.get(language, []))
        choice = next((item for item in choices if item.get("ext") == "json3" and item.get("url")), None)
        if not choice:
            return []
        try:
            request = urllib.request.Request(str(choice["url"]), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=30, context=verified_ssl_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("events", []) if isinstance(payload, dict) else []
        except (OSError, TimeoutError, json.JSONDecodeError):
            return []

    def source_clip(
        self, *, video_id: str, query: str, duration: float, destination: Path,
        source_start_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ProviderError("Invalid YouTube video identifier")
        if not math.isfinite(duration) or not 0.25 <= duration <= 600:
            raise ProviderError("The scene duration must be between 0.25 and 600 seconds")
        license_name = self._creative_commons_license(video_id)
        try:
            import yt_dlp
        except ImportError as error:
            raise ProviderError("YouTube sourcing needs yt-dlp. Run: python -m pip install -e .") from error
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as error:
            raise ProviderError(f"Could not inspect the YouTube source: {error}") from error
        start = float(source_start_seconds) if source_start_seconds is not None else best_caption_timestamp(
            self._caption_events(info), query, duration
        )
        source_duration = float(info.get("duration") or 0)
        if source_duration > 0:
            start = min(max(0.0, start), max(0.0, source_duration - duration))
        end = start + duration
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, "-m", "yt_dlp", url,
            "--no-playlist", "--download-sections", f"*{start:.3f}-{end:.3f}",
            "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "--ffmpeg-location", self.ffmpeg_path, "-o", str(destination),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProviderError(f"Could not download the YouTube excerpt: {error}") from error
        if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "yt-dlp did not create a clip")[-700:]
            raise ProviderError(f"Could not download the YouTube excerpt: {detail}")
        return {
            "video_id": video_id,
            "source_url": url,
            "title": str(info.get("title") or "YouTube source"),
            "channel": str(info.get("channel") or info.get("uploader") or "Unknown channel"),
            "license": license_name,
            "source_start_seconds": round(start, 3),
            "source_end_seconds": round(end, 3),
            "matched_from_captions": source_start_seconds is None and start > 0,
        }
