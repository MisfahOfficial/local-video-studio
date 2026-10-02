"""Fixes from reviewing the V2 '25 Christmas desserts' test video."""
from __future__ import annotations

import unittest

from app.footage_match import bracket_alias, core_subject, topic_queries


class BracketHeadingTest(unittest.TestCase):
    def test_brackets_are_a_second_name_not_required_words(self):
        self.assertEqual(core_subject("fruitcake (old english style)"), "fruitcake")
        self.assertEqual(bracket_alias("fruitcake (old english style)"), "old english style fruitcake")
        self.assertEqual(core_subject("rum cake (bacardi rum cake)"), "rum cake")
        self.assertEqual(bracket_alias("rum cake (bacardi rum cake)"), "bacardi rum cake")
        self.assertEqual(bracket_alias("custard slices"), "")

    def test_searches_drop_the_brackets(self):
        queries = topic_queries({"narration": "soaked in brandy for weeks"}, "fruitcake (old english style)")
        self.assertTrue(all("(" not in query for query in queries))
        self.assertTrue(all("fruitcake" in query for query in queries))


class LayoutTest(unittest.TestCase):
    def test_ingredient_photos_stay_inside_the_frame(self):
        from app.motion_designs import BUILTIN_LAYOUTS

        for name, layout in BUILTIN_LAYOUTS.items():
            for count, slots in (layout.get("slots") or {}).items():
                for photo_x, _photo_y, _text_x, _text_y in slots:
                    self.assertLessEqual(photo_x + layout["photo"], 1920 - 60, f"{name} with {count} items")
                    self.assertGreaterEqual(photo_x, 40, f"{name} with {count} items")


class HighlightTest(unittest.TestCase):
    def test_words_always_read_on_their_box(self):
        from app.channel_styles import STYLES
        from app.motion.templates import contrast, readable_ink

        for key, style in STYLES.items():
            self.assertGreaterEqual(contrast(style.highlight, readable_ink(style.highlight, style.highlight_text)), 4.5, key)
        self.assertEqual(readable_ink((246, 186, 196), (250, 200, 210)), (20, 16, 12))

    def test_script_highlight_is_not_written_in_capitals(self):
        from app.channel_styles import get_style
        from app.motion.templates import is_script_font

        self.assertTrue(is_script_font(get_style("v2").highlight_fonts))
        self.assertFalse(is_script_font(get_style("v1").highlight_fonts))


class StillPromptTest(unittest.TestCase):
    def test_stills_vary_and_carry_no_text(self):
        from app.vintage_still import COMPOSITIONS, still_prompt

        prompts = {still_prompt("soaked in brandy for weeks", "fruitcake", "1970s", shot=index)
                   for index in range(len(COMPOSITIONS))}
        self.assertEqual(len(prompts), len(COMPOSITIONS))
        self.assertTrue(all("no people" in prompt and "No text" in prompt for prompt in prompts))
        self.assertIn("No text", still_prompt("Families bought it for Sunday dinner", "fruitcake", "1970s"))


class EraCheckTest(unittest.TestCase):
    def test_period_channels_refuse_present_day_looks(self):
        from app.scene_checker import era_rule

        self.assertIn("smartwatch", era_rule("1970s"))
        self.assertEqual(era_rule(""), "")


if __name__ == "__main__":
    unittest.main()
