from __future__ import annotations

import unittest

from app.footage_match import best_window, detect_topic, mentions_topic, topic_queries

MUSK = (
    "Musk ox calves are born in the harshest place on Earth. Within hours, a musk ox calf must stand. "
    "The impact of a single blizzard can be deadly. When wolves appear, the adults circle the calves. "
    "Musk oxen have survived since the Ice Age."
)


class FootageMatchTests(unittest.TestCase):
    def test_topic_prefers_two_word_subject(self) -> None:
        self.assertEqual(detect_topic(MUSK), "musk ox")
        self.assertEqual(detect_topic(MUSK, "Musk Ox Calves"), "musk ox")

    def test_topic_spellings_and_lookalikes(self) -> None:
        self.assertTrue(mentions_topic("Newborn Muskox's First Day | PBS", "musk ox"))
        self.assertTrue(mentions_topic("Musk Oxen in Greenland", "musk ox"))
        self.assertFalse(mentions_topic("Baby buffalo calves playing", "musk ox"))
        self.assertTrue(mentions_topic("anything at all", ""))

    def test_queries_always_name_topic_and_drop_figurative_words(self) -> None:
        queries = topic_queries({"narration": "The impact of a single blizzard can be deadly."}, "musk ox")
        self.assertTrue(all(query.startswith("musk ox") for query in queries))
        self.assertFalse(any("impact" in query for query in queries))
        self.assertIn("blizzard", queries[0])

    def test_best_window_averages_over_clip_length(self) -> None:
        frames = [(0.0, 0.9), (2.0, 0.1), (4.0, 0.1), (10.0, 0.8), (12.0, 0.8), (14.0, 0.8)]
        start, score = best_window(frames, 5.0)
        self.assertEqual(start, 10.0)
        self.assertAlmostEqual(score, 0.8)


if __name__ == "__main__":
    unittest.main()
