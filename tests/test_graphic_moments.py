import unittest

from app.graphic_moments import moment_for, plan_moments
from app.motion_designs import extra_payload
from app.channel_styles import get_style


class ExtraGraphics(unittest.TestCase):
    def test_script_sentences_call_for_the_right_graphic(self):
        self.assertEqual(moment_for("Yorkshire, Lancashire and the Midlands all said they invented them first.")["type"], "map")
        price = moment_for("A single biscuit cost about sixpence in the 1970s.")
        self.assertEqual((price["type"], price["price"], price["when"]), ("price", "sixpence", "1970s"))
        years = moment_for("British families bought them throughout the 1960s to the 1980s.")
        self.assertEqual((years["start"], years["end"]), ("1960s", "1980s"))
        self.assertEqual(moment_for("Do you remember those tiny sugar eggs? Let us know in the comments.")["type"], "comment")
        self.assertIsNone(moment_for("They tasted buttery, fruity and sweet."))

    def test_one_of_each_kind_per_section_and_questions_spaced_out(self):
        scenes = [
            {"id": "h1", "narration": "TRAFFIC LIGHT BISCUITS", "start_seconds": 0},
            {"id": "a", "narration": "It cost sixpence in the 1970s.", "start_seconds": 5},
            {"id": "b", "narration": "A box cost a shilling in the 1970s.", "start_seconds": 10},
            {"id": "c", "narration": "What do you think? Comment below.", "start_seconds": 20},
            {"id": "h2", "narration": "NEST CAKES", "start_seconds": 30},
            {"id": "d", "narration": "Each cost threepence in the 1970s.", "start_seconds": 35},
            {"id": "e", "narration": "Do you remember them? Let us know in the comments.", "start_seconds": 60},
        ]
        chosen = plan_moments(scenes, "1970s")
        self.assertEqual(sorted(chosen), ["a", "c", "d"])  # "b" repeats a price in its section, "e" is too soon

    def test_words_stay_where_the_design_draws_them(self):
        payload = extra_payload(moment_for("Yorkshire and Lancashire both claimed them."), get_style("v3"), 3.0)
        self.assertEqual(sorted(text["text"] for text in payload["texts"]), ["Lancashire", "Yorkshire"])
        self.assertTrue(all(0 < text["x"] < 1920 and 0 < text["y"] < 1080 for text in payload["texts"]))


if __name__ == "__main__":
    unittest.main()
