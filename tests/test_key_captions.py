from __future__ import annotations

import unittest
from unittest.mock import patch

from app.key_captions import key_captions, key_phrase, local_key_captions
from app.providers.base import ProviderError


def _scenes(texts: list[str], length: float = 5.0) -> list[dict]:
    return [
        {"narration": text, "start_seconds": index * length, "end_seconds": (index + 1) * length}
        for index, text in enumerate(texts)
    ]


class KeyCaptionTests(unittest.TestCase):
    def test_key_phrase_keeps_the_number_clause(self) -> None:
        self.assertEqual(
            key_phrase("The cookies baked at 350 degrees for ten to twelve minutes until the edges turned golden."),
            "350 degrees for ten to twelve minutes",
        )
        self.assertEqual(key_phrase("It cost less than fifty cents per batch when butter was scarce."), "fifty cents per batch")
        self.assertEqual(key_phrase("The dough came together without eggs."), "")
        self.assertEqual(key_phrase("Grandmas mixed two cups of rolled oats with one cup"), "two cups of rolled oats")

    def test_hook_question_is_kept_whole(self) -> None:
        self.assertEqual(key_phrase("How did one dollar fill a table in 1955?", hook=True), "How did one dollar fill a table in 1955?")

    def test_captions_are_sparse_and_skip_headings(self) -> None:
        texts = [
            "How did one dollar fill a table in 1955?", "It took 3 cups of oats.", "POOR MAN'S COOKIES",
            "They baked for 12 minutes.", "The dough was simple.", "Each batch cost 50 cents.",
            "Families loved them.", "By 1960 they were everywhere.",
        ]
        captions = local_key_captions(_scenes(texts))
        self.assertIn(0, captions)
        self.assertNotIn(2, captions)
        starts = sorted(captions)
        self.assertTrue(all(later - earlier >= 3 for earlier, later in zip(starts, starts[1:])))

    def test_falls_back_to_local_rules_without_gemini(self) -> None:
        with patch("app.key_captions.gemini_key_captions", side_effect=ProviderError("quota")):
            captions, source = key_captions(_scenes(["It took 3 cups of oats to start."]), "key", "model")
        self.assertEqual(source, "local")
        self.assertEqual(captions, {0: "3 cups of oats"})


if __name__ == "__main__":
    unittest.main()


class SentenceCaptionTests(unittest.TestCase):
    def test_captions_come_from_whole_sentences_and_end_on_full_words(self) -> None:
        from app.key_captions import body_phrase, local_key_captions

        self.assertEqual(body_phrase("Over 2,000 locations across the country closed."), "Over 2,000 locations")
        self.assertEqual(body_phrase("It opened on November 3rd 1973 in Fayetteville."), "November 3rd 1973")
        self.assertEqual(body_phrase("Press a pecan half into each piece."), "")
        scenes = [
            {"narration": "Do you remember when Christmas baking started before Christmas Day?", "start_seconds": 0, "end_seconds": 4},
            {"narration": "But the 1970s Bacardi", "start_seconds": 20, "end_seconds": 21.5},
            {"narration": "version was made with boxed cake mix.", "start_seconds": 21.5, "end_seconds": 24},
        ]
        captions = local_key_captions(scenes)
        self.assertEqual(captions[0], "Christmas baking started before Christmas Day")
        self.assertNotIn("1970s Bacardi", captions.values())
