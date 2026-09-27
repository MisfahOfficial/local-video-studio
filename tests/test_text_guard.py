from __future__ import annotations

import unittest

from app.text_guard import is_overlay


class TextGuardTests(unittest.TestCase):
    def test_sentences_count_as_overlays(self) -> None:
        self.assertTrue(is_overlay([("Didn't realize I had", 1.0), ("flour on my skirt lol", 1.0)]))
        self.assertTrue(is_overlay([("BAKE 350 FOR 90 MINUTE", 1.0)]))

    def test_small_or_unsure_text_is_ignored(self) -> None:
        self.assertFalse(is_overlay([("TOaLAT", 0.3)]))
        self.assertFalse(is_overlay([("mol355e5", 0.3)]))
        self.assertFalse(is_overlay([("KitchenAid", 1.0)]))
        self.assertFalse(is_overlay([]))
