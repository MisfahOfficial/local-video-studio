"""V2 follows its most viral video: name labels, lime-yellow key captions, no film look, short shots."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class KitTest(unittest.TestCase):
    def test_v2_reference_look_and_classic_backup(self):
        from app.channel_kits import custom_styles, kit_for

        root = Path(tempfile.mkdtemp())
        v2 = kit_for(root, "v2")
        self.assertEqual(v2["chapter_style"], "name_label")
        self.assertFalse(v2["film_look"])
        self.assertFalse(v2["ingredient_cards"])
        self.assertEqual(v2["caption_style"]["text_color"], "#D8E418")
        self.assertTrue(v2["references"])
        classic = kit_for(root, "v2_classic")
        self.assertNotIn("chapter_style", classic)
        self.assertEqual(custom_styles(root)["v2_classic"].key, "v2_classic")
        self.assertNotIn("chapter_style", kit_for(root, "v3"))  # other channels unchanged


class LabelTest(unittest.TestCase):
    def test_long_names_shrink_inside_the_frame(self):
        from app.name_label import MAX_WIDTH, label_layout

        layout = label_layout("Rum Cake (Bacardi Rum Cake) And Its Many Other Long Names From The 1970s")
        self.assertLessEqual(layout["box"][2], 56 + MAX_WIDTH + 48 + 2)
        self.assertEqual(layout["text"], layout["text"].upper())

    def test_headings_get_a_label_and_no_caption(self):
        from app.chapter_cards import label_headings

        class Db:
            def __init__(self):
                self.scenes = [
                    {"id": "a", "narration": "Christmas baking began early.", "timeline_actions": []},
                    {"id": "b", "narration": "1. Fruitcake (Old English Style)", "caption_text": "FRUITCAKE",
                     "timeline_actions": [{"type": "motion", "params": {"preset": "slow_push"}}]},
                    {"id": "c", "narration": "OUTRO", "caption_text": "OUTRO", "timeline_actions": []},
                ]
                self.updates = {}

            def list_scenes(self, _project):
                return self.scenes

            def update_scene(self, scene_id, changes):
                self.updates[scene_id] = changes

        db = Db()
        self.assertEqual(label_headings(db, "p"), 1)
        self.assertEqual(db.updates["b"]["caption_text"], "")
        self.assertIn({"type": "label", "params": {"text": "Fruitcake (Old English Style)"}}, db.updates["b"]["timeline_actions"])
        self.assertEqual(db.updates["c"], {"caption_text": ""})  # signposts get no label
        self.assertNotIn("a", db.updates)


class PacingTest(unittest.TestCase):
    def test_channel_pacing_shortens_scenes(self):
        from app.whisper_planner import WhisperScenePlanner

        words = "Real fruitcake was dark and dense and packed with candied cherries nuts and raisins every year".split()

        class Ears:
            def transcribe(self, _path):
                return [{"words": [{"word": word, "start": index * 0.5, "end": index * 0.5 + 0.4}
                                   for index, word in enumerate(words)]}]

        script = " ".join(words) + "."
        slow = WhisperScenePlanner(Ears()).plan(script=script, voiceover_path=Path("vo.mp3"), duration_seconds=8.0, theme_id="us_nostalgia")
        fast = WhisperScenePlanner(Ears(), max_scene_seconds=4.0).plan(script=script, voiceover_path=Path("vo.mp3"),
                                                                       duration_seconds=8.0, theme_id="us_nostalgia")
        self.assertGreater(len(fast), len(slow))
        self.assertTrue(all(draft.end_seconds - draft.start_seconds <= 4.6 for draft in fast[:-1]))


if __name__ == "__main__":
    unittest.main()
