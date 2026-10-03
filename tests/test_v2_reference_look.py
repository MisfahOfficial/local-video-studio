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
        self.assertTrue(v2.get("ingredient_cards", True))  # ingredient cards are back (Ishaq, 3 Oct)
        self.assertEqual((v2["pacing"]["shot_seconds_min"], v2["pacing"]["shot_seconds_max"]), (3.0, 7.0))
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
    def test_long_sentence_is_cut_in_equal_parts_of_three_to_seven_seconds(self):
        from app.whisper_planner import WhisperScenePlanner

        words = ("Real fruitcake was dark and dense and packed with candied cherries nuts and raisins every year "
                 "and soaked in brandy for weeks").split()

        class Ears:
            def transcribe(self, _path):
                return [{"words": [{"word": word, "start": index * 0.5, "end": index * 0.5 + 0.4}
                                   for index, word in enumerate(words)]}]

        script = " ".join(words) + "."
        end = len(words) * 0.5
        drafts = WhisperScenePlanner(Ears()).plan(script=script, voiceover_path=Path("vo.mp3"), duration_seconds=end,
                                                  theme_id="us_nostalgia")
        lengths = [draft.end_seconds - draft.start_seconds for draft in drafts]
        self.assertEqual(len(drafts), 2)  # 10.5 s -> two parts, not three short ones
        self.assertTrue(all(3.0 <= length <= 7.0 for length in lengths), lengths)
        self.assertLess(abs(lengths[0] - lengths[1]), 1.5)
        shorter = WhisperScenePlanner(Ears(), max_scene_seconds=4.0).plan(
            script=script, voiceover_path=Path("vo.mp3"), duration_seconds=end, theme_id="us_nostalgia")
        self.assertGreaterEqual(len(shorter), 3)  # a channel may still ask for shorter shots
        self.assertTrue(all(draft.end_seconds - draft.start_seconds <= 4.6 for draft in shorter))

    def test_short_sentences_are_never_flash_shots(self):
        from app.whisper_planner import _group_by_pacing

        sentences = ["FRUITCAKE", "It was dense.", "Packed with fruit.", "Soaked in brandy for weeks before Christmas."]
        spans = [(0.0, 1.2), (1.5, 2.6), (2.9, 4.1), (4.4, 7.9)]
        groups = _group_by_pacing(sentences, spans, 7.0, 3.0)
        self.assertEqual(groups[0], [0])  # the heading keeps its own shot
        cuts = [0.0, 1.35, 2.75, 4.25, 7.9]
        for group in groups[1:]:
            self.assertGreaterEqual(cuts[group[-1] + 1] - cuts[group[0]], 3.0)


if __name__ == "__main__":
    unittest.main()


class FragmentHeadingTest(unittest.TestCase):
    def test_a_piece_of_a_sentence_is_never_a_heading(self):
        from app.footage_match import heading_subject
        from app.whisper_planner import split_long_sentences

        sentence = "In 1976 the FDA banned that red dye after laboratory tests on rats and the Candy Company changed it."
        words = sentence.rstrip(".").split()
        segments = [{"words": [{"word": word, "start": index * 0.42, "end": index * 0.42 + 0.38}
                               for index, word in enumerate(words)]}]
        for limit in (2.0, 3.0, 4.0):
            units = split_long_sentences([sentence], [(0, len(words) * 0.42)], segments, limit)
            self.assertFalse([text for text, _start, _end in units if heading_subject(text)], limit)
            self.assertEqual(" ".join(text for text, _start, _end in units), sentence)
