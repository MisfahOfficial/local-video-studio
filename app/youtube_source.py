from __future__ import annotations

import json
import html
import math
import re
import shutil
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
CREATIVE_COMMONS = "creative_commons"
FAIR_USE = "fair_use"


def normalize_license_mode(value: Any) -> str:
    """Unknown or missing values keep the original Creative Commons-only behaviour."""
    return FAIR_USE if str(value or "").strip().lower() == FAIR_USE else CREATIVE_COMMONS


def _is_quota_error(error: Exception) -> bool:
    text = str(error).lower()
    return "quota" in text or "http 403" in text or "http 429" in text


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


def pick_video_stream(info: dict[str, Any]) -> dict[str, Any] | None:
    """Best progressive-download video stream up to 1080p (H.264 preferred), or None."""
    streams = [
        item for item in info.get("formats") or []
        if item.get("url") and item.get("vcodec") not in (None, "none")
        and str(item.get("protocol") or "") in {"https", "http"}
        and 0 < int(item.get("height") or 0) <= 1080
    ]
    if not streams:
        return None
    return max(streams, key=lambda item: (
        str(item.get("vcodec") or "").startswith("avc1"), int(item.get("height") or 0),
        item.get("acodec") in (None, "none"),
    ))


def ytdlp_search_result(entry: Any) -> dict[str, Any] | None:
    """Convert one flat yt-dlp search entry to the API result shape; skip lives and invalid ids."""
    if not isinstance(entry, dict):
        return None
    video_id = str(entry.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    if entry.get("live_status") in {"is_live", "is_upcoming"}:
        return None
    try:
        duration = float(entry.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    thumbnails = [item for item in entry.get("thumbnails") or [] if isinstance(item, dict) and item.get("url")]
    thumbnail = str(thumbnails[-1]["url"]) if thumbnails else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return {
        "video_id": video_id,
        "title": str(entry.get("title") or "Untitled video"),
        "description": str(entry.get("description") or ""),
        "channel": str(entry.get("channel") or entry.get("uploader") or "Unknown channel"),
        "published_at": "",
        "thumbnail_url": thumbnail,
        "duration_seconds": duration,
        "license": "youtube",
        "watch_url": f"https://www.youtube.com/watch?v={video_id}",
    }


class YouTubeSourceService:
    def __init__(self, api_key: str, ffmpeg_path: str = "ffmpeg", license_mode: str = CREATIVE_COMMONS):
        self.api_key = api_key.strip()
        # yt-dlp treats --ffmpeg-location as a filesystem path, so a bare "ffmpeg"
        # setting must be resolved through PATH or every excerpt download fails.
        self.ffmpeg_path = shutil.which(ffmpeg_path) or ffmpeg_path
        self.license_mode = normalize_license_mode(license_mode)
        self._api_exhausted = False

    @property
    def fair_use(self) -> bool:
        return self.license_mode == FAIR_USE

    def search(self, query: str, maximum: int = 8) -> list[dict[str, Any]]:
        clean_query = " ".join(query.split())[:240]
        if not clean_query:
            raise ProviderError("Enter a YouTube search description")
        if not self.fair_use:
            if not self.api_key:
                raise ProviderError("Add a YouTube Data API key in Settings first")
            return self._search_api(clean_query, maximum, creative_commons=True)
        # Fair-use mode uses the API while quota lasts, then continues with
        # yt-dlp search, which needs no key and has no daily search quota.
        if self.api_key and not self._api_exhausted:
            try:
                return self._search_api(clean_query, maximum, creative_commons=False)
            except ProviderError as error:
                if not _is_quota_error(error):
                    raise
                self._api_exhausted = True
        return self._search_ytdlp(clean_query, maximum)

    def _search_api(self, clean_query: str, maximum: int, *, creative_commons: bool) -> list[dict[str, Any]]:
        search_params = {
            "part": "snippet", "type": "video", "q": clean_query,
            "maxResults": max(1, min(12, maximum)), "safeSearch": "moderate",
            "videoEmbeddable": "true", "key": self.api_key,
        }
        if creative_commons:
            search_params["videoLicense"] = "creativeCommon"
        found = request_json(f"{YOUTUBE_SEARCH_URL}?{urllib.parse.urlencode(search_params)}", timeout=45)
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
                "description": html.unescape(str(snippet.get("description") or "")),
                "channel": html.unescape(str(snippet.get("channelTitle") or "Unknown channel")),
                "published_at": str(snippet.get("publishedAt") or ""),
                "thumbnail_url": str(thumbnail),
                "duration_seconds": _iso_duration(str(item.get("contentDetails", {}).get("duration") or "")),
                "license": str(item.get("status", {}).get("license") or ("creativeCommon" if creative_commons else "youtube")),
                "watch_url": f"https://www.youtube.com/watch?v={video_id}",
            })
        return results

    def _search_ytdlp(self, clean_query: str, maximum: int) -> list[dict[str, Any]]:
        try:
            import yt_dlp
        except ImportError as error:
            raise ProviderError("YouTube search needs yt-dlp. Run: python -m pip install -e .") from error
        count = max(1, min(12, maximum))
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist"}
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                found = ydl.extract_info(f"ytsearch{count}:{clean_query}", download=False)
        except Exception as error:
            raise ProviderError(f"YouTube search failed: {error}") from error
        return [
            result for result in (ytdlp_search_result(entry) for entry in (found or {}).get("entries") or [])
            if result is not None
        ]

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

    def inspect(self, video_id: str) -> dict[str, Any]:
        """Full yt-dlp metadata for one video (formats, storyboards, captions)."""
        try:
            import yt_dlp
        except ImportError as error:
            raise ProviderError("YouTube sourcing needs yt-dlp. Run: python -m pip install -e .") from error
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        except Exception as error:
            if "not a bot" in str(error):
                raise ProviderError(
                    "YouTube is temporarily blocking this computer (\"confirm you're not a bot\"). "
                    "Wait a few hours before sourcing again."
                ) from error
            raise ProviderError(f"Could not inspect the YouTube source: {error}") from error

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
        source_start_seconds: float | None = None, info: dict[str, Any] | None = None, padding: float = 0.0,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ProviderError("Invalid YouTube video identifier")
        if not math.isfinite(duration) or not 0.25 <= duration <= 600:
            raise ProviderError("The scene duration must be between 0.25 and 600 seconds")
        license_name = None if self.fair_use else self._creative_commons_license(video_id)
        url = f"https://www.youtube.com/watch?v={video_id}"
        if info is None:
            info = self.inspect(video_id)
        start = float(source_start_seconds) if source_start_seconds is not None else best_caption_timestamp(
            self._caption_events(info), query, duration
        )
        if license_name is None:
            license_name = str(info.get("license") or "Standard YouTube Licence")
        source_duration = float(info.get("duration") or 0)
        if source_duration > 0:
            start = min(max(0.0, start), max(0.0, source_duration - duration))
        # Extra seconds either side let the caller pick a stretch without a hard cut.
        if padding > 0:
            padded_start = max(0.0, start - padding)
            padded_end = start + duration + padding
            if source_duration > 0:
                padded_end = min(padded_end, source_duration)
            start, duration = padded_start, max(duration, padded_end - padded_start)
        end = start + duration
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Fast path: cut straight from the stream URL already in `info`, instead of
        # letting yt-dlp re-extract the whole page (the slowest step of sourcing).
        if self._direct_excerpt(info, start, duration, destination):
            return self._clip_metadata(video_id, url, info, license_name, start, end, source_start_seconds)
        command = [
            sys.executable, "-m", "yt_dlp", url,
            "--no-playlist", "--download-sections", f"*{start:.3f}-{end:.3f}",
            "--force-keyframes-at-cuts", "--remux-video", "mp4", "--retries", "3",
            # The export only uses the voice-over, so the source audio is never
            # downloaded: smaller, faster excerpts with no borrowed soundtrack.
            "-f", "bestvideo[height<=1080][vcodec^=avc1]/bestvideo[height<=1080]/best[height<=1080]",
            "--ffmpeg-location", self.ffmpeg_path, "-o", str(destination),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProviderError(f"Could not download the YouTube excerpt: {error}") from error
        if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "yt-dlp did not create a clip")[-700:]
            raise ProviderError(f"Could not download the YouTube excerpt: {detail}")
        return self._clip_metadata(video_id, url, info, license_name, start, end, source_start_seconds)

    def _direct_excerpt(self, info: dict[str, Any], start: float, duration: float, destination: Path) -> bool:
        stream = pick_video_stream(info)
        if not stream:
            return False
        headers = "".join(f"{key}: {value}\r\n" for key, value in (stream.get("http_headers") or {}).items())
        command = [self.ffmpeg_path, "-y", "-v", "error"]
        if headers:
            command += ["-headers", headers]
        command += [
            "-ss", f"{start:.3f}", "-i", str(stream["url"]), "-t", f"{duration:.3f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(destination),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            destination.unlink(missing_ok=True)
            return False
        if result.returncode or not destination.is_file() or destination.stat().st_size < 1000:
            destination.unlink(missing_ok=True)
            return False
        return True

    def _clip_metadata(
        self, video_id: str, url: str, info: dict[str, Any], license_name: str, start: float, end: float,
        source_start_seconds: float | None,
    ) -> dict[str, Any]:
        return {
            "video_id": video_id,
            "source_url": url,
            "title": str(info.get("title") or "YouTube source"),
            "channel": str(info.get("channel") or info.get("uploader") or "Unknown channel"),
            "license": license_name,
            "usage_basis": self.license_mode,
            "source_start_seconds": round(start, 3),
            "source_end_seconds": round(end, 3),
            "matched_from_captions": source_start_seconds is None and start > 0,
        }
