from __future__ import annotations

import unittest
from unittest.mock import patch

from app.providers.base import ProviderError
from app.youtube_source import (
    CREATIVE_COMMONS, FAIR_USE, YouTubeSourceService, _iso_duration, best_caption_timestamp,
    normalize_license_mode, ytdlp_search_result,
)

FLAT_RESULT = [{"video_id": "abcdefghijk", "title": "Vintage diner footage", "license": "youtube"}]


class YouTubeSourceTests(unittest.TestCase):
    def test_iso_duration(self) -> None:
        self.assertEqual(_iso_duration("PT1H2M3S"), 3723)
        self.assertEqual(_iso_duration("PT45S"), 45)
        self.assertEqual(_iso_duration("invalid"), 0)

    def test_caption_matching_picks_relevant_window(self) -> None:
        events = [
            {"tStartMs": 0, "segs": [{"utf8": "welcome to the programme"}]},
            {"tStartMs": 23000, "segs": [{"utf8": "allied battleships approach the dardanelles strait"}]},
            {"tStartMs": 28000, "segs": [{"utf8": "the fleet intended to reach istanbul"}]},
        ]
        timestamp = best_caption_timestamp(events, "Allied fleet crossing Dardanelles toward Istanbul", 8)
        self.assertGreaterEqual(timestamp, 22)
        self.assertLess(timestamp, 24)

    def test_caption_matching_defaults_to_beginning_without_overlap(self) -> None:
        events = [{"tStartMs": 9000, "segs": [{"utf8": "unrelated cooking demonstration"}]}]
        self.assertEqual(best_caption_timestamp(events, "naval fleet", 5), 0)


class LicenseModeTests(unittest.TestCase):
    def test_unknown_mode_falls_back_to_creative_commons(self) -> None:
        self.assertEqual(normalize_license_mode(None), CREATIVE_COMMONS)
        self.assertEqual(normalize_license_mode("anything"), CREATIVE_COMMONS)
        self.assertEqual(normalize_license_mode(" FAIR_USE "), FAIR_USE)

    def test_creative_commons_mode_still_requires_api_key(self) -> None:
        service = YouTubeSourceService("", license_mode=CREATIVE_COMMONS)
        with self.assertRaisesRegex(ProviderError, "API key"):
            service.search("vintage diner")

    def test_creative_commons_mode_keeps_licence_filter(self) -> None:
        service = YouTubeSourceService("key", license_mode=CREATIVE_COMMONS)
        with patch("app.youtube_source.request_json", return_value={"items": []}) as request:
            service.search("vintage diner")
        self.assertIn("videoLicense=creativeCommon", request.call_args.args[0])

    def test_fair_use_mode_drops_licence_filter(self) -> None:
        service = YouTubeSourceService("key", license_mode=FAIR_USE)
        with patch("app.youtube_source.request_json", return_value={"items": []}) as request:
            service.search("vintage diner")
        self.assertNotIn("videoLicense", request.call_args.args[0])

    def test_fair_use_without_key_searches_with_ytdlp(self) -> None:
        service = YouTubeSourceService("", license_mode=FAIR_USE)
        with patch.object(YouTubeSourceService, "_search_ytdlp", return_value=FLAT_RESULT) as flat:
            self.assertEqual(service.search("vintage diner"), FLAT_RESULT)
        flat.assert_called_once()

    def test_fair_use_quota_error_switches_to_ytdlp_for_rest_of_run(self) -> None:
        service = YouTubeSourceService("key", license_mode=FAIR_USE)
        quota = ProviderError('Provider returned HTTP 403: {"error": {"errors": [{"reason": "quotaExceeded"}]}}')
        with patch("app.youtube_source.request_json", side_effect=quota) as request, \
                patch.object(YouTubeSourceService, "_search_ytdlp", return_value=FLAT_RESULT):
            self.assertEqual(service.search("diner"), FLAT_RESULT)
            self.assertEqual(service.search("drive-in"), FLAT_RESULT)
        self.assertEqual(request.call_count, 1)

    def test_fair_use_other_api_errors_are_reported(self) -> None:
        service = YouTubeSourceService("key", license_mode=FAIR_USE)
        with patch("app.youtube_source.request_json", side_effect=ProviderError("Provider returned HTTP 400: bad key")):
            with self.assertRaisesRegex(ProviderError, "HTTP 400"):
                service.search("diner")

    def test_bare_ffmpeg_name_is_resolved_for_ytdlp(self) -> None:
        with patch("app.youtube_source.shutil.which", return_value="/opt/homebrew/bin/ffmpeg"):
            self.assertEqual(YouTubeSourceService("", "ffmpeg").ffmpeg_path, "/opt/homebrew/bin/ffmpeg")
        with patch("app.youtube_source.shutil.which", return_value=None):
            self.assertEqual(YouTubeSourceService("", "ffmpeg").ffmpeg_path, "ffmpeg")

    def test_ytdlp_entry_conversion(self) -> None:
        result = ytdlp_search_result({
            "id": "abcdefghijk", "title": "1970s supermarket", "channel": "Archive",
            "duration": 312, "thumbnails": [{"url": "small.jpg"}, {"url": "large.jpg"}],
        })
        assert result is not None
        self.assertEqual(result["duration_seconds"], 312)
        self.assertEqual(result["thumbnail_url"], "large.jpg")
        self.assertEqual(result["watch_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertIsNone(ytdlp_search_result({"id": "abcdefghijk", "live_status": "is_live"}))
        self.assertIsNone(ytdlp_search_result({"id": "bad"}))


if __name__ == "__main__":
    unittest.main()
