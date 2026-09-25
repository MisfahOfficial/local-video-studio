from __future__ import annotations

import unittest

from app.youtube_source import _iso_duration, best_caption_timestamp


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


if __name__ == "__main__":
    unittest.main()
