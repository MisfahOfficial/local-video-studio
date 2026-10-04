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
