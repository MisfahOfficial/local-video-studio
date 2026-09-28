from __future__ import annotations

import unittest

from PIL import Image

from app.channel_styles import STYLES, get_style
from app.motion.templates import ingredient_cards, style_background
from app.server import normalize_effects


class ChannelStyleTests(unittest.TestCase):
    def test_every_channel_has_a_style_and_unknown_falls_back(self) -> None:
        self.assertEqual(set(STYLES), {"v1", "v2", "v3", "v4"})
        self.assertEqual(get_style("nope").key, "v3")
        self.assertEqual(get_style("V2").key, "v2")

    def test_project_effects_keep_a_valid_style(self) -> None:
        self.assertEqual(normalize_effects({"channel_style": "v4"})["channel_style"], "v4")
        self.assertEqual(normalize_effects({"channel_style": "bogus"})["channel_style"], "v3")
        self.assertTrue(normalize_effects({})["film_look"])

    def test_each_style_draws_cards(self) -> None:
        items = [("oats", Image.new("RGB", (400, 400), "tan")), ("sugar", Image.new("RGB", (400, 400), "white"))]
        for style in STYLES.values():
            self.assertEqual(style_background(style).size, (1920, 1080))
            self.assertEqual(ingredient_cards(items, style)(2.0, 4.0).size, (1920, 1080))
