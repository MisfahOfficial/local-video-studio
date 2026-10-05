from __future__ import annotations

import unittest
from unittest import mock


class TranscriptPickTest(unittest.TestCase):
    def test_caption_events_become_plain_words(self):
        from app.transcript_pick import transcript_text

        events = [{"segs": [{"utf8": "Today we make"}, {"utf8": " Heinz soup\n"}]}, {}, {"segs": [{"utf8": "at home"}]}]
        self.assertEqual(transcript_text(events), "Today we make Heinz soup at home")
        self.assertEqual(len(transcript_text(events, limit=10)), 10)

    def test_videos_about_the_item_come_first_and_unknown_ids_are_ignored(self):
        from app.transcript_pick import rank_videos

        answer = {"videos": [{"id": "b", "about_item": False, "score": 2, "reason": "tomato soup"},
                             {"id": "a", "about_item": True, "score": 6, "reason": "heinz advert"},
                             {"id": "c", "about_item": True, "score": 9, "reason": "tasting the tin"},
                             {"id": "zz", "about_item": True, "score": 10, "reason": "not offered"}]}
        videos = [{"id": key, "title": key, "transcript": ""} for key in "abc"]
        with mock.patch("app.llm.gemini_json", return_value=answer) as ask:
            ranked = rank_videos(object(), "heinz golden vegetable soup", videos, "1970s")
        self.assertEqual([entry[0] for entry in ranked], ["c", "a", "b"])
        self.assertIn("heinz golden vegetable soup", ask.call_args[0][1])


if __name__ == "__main__":
    unittest.main()


class GoogleImagesTest(unittest.TestCase):
    def test_google_results_come_first_when_configured(self):
        from app import photo_source

        answer = {"items": [{"link": "https://x/soup.jpg", "title": "Batchelors Cup-a-Soup 1978",
                             "displayLink": "ebay.co.uk", "image": {"width": 900, "height": 700,
                                                                    "thumbnailLink": "https://t/s.jpg", "contextLink": "https://ebay"}},
                            {"link": "https://x/tiny.jpg", "image": {"width": 120}}]}
        photo_source.configure_google("key", "cx")
        try:
            with mock.patch.object(photo_source, "request_json", return_value=answer), \
                 mock.patch.object(photo_source, "_openverse", return_value=[]), \
                 mock.patch.object(photo_source, "_archive_images", return_value=[]):
                found = photo_source.search_photos("batchelors cup-a-soup", count=5)
            self.assertEqual([item["url"] for item in found], ["https://x/soup.jpg"])  # the tiny one is skipped
            self.assertIn("Google Images", photo_source.attribution(found[0]))
        finally:
            photo_source.configure_google("", "")
        self.assertEqual(photo_source.image_sources()[0][0], "openverse")  # without a key nothing changes
