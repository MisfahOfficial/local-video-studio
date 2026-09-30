import tempfile
import unittest
from pathlib import Path

from app.chapter_cards import card_text_layout
from app.edit_export import AI_IMAGE, MISSING, review_markers, write_premiere_xml, write_srt
from app.motion.templates import ingredient_label_layout


class EditableWords(unittest.TestCase):
    def test_card_words_are_plain_words_with_positions(self):
        layers = card_text_layout("Ambrosia Salad", 1)
        self.assertEqual([layer["text"] for layer in layers], ["CHAPTER 1", "AMBROSIA SALAD"])
        self.assertTrue(all(0 < layer["x"] < 1920 and 0 < layer["y"] < 1080 for layer in layers))
        labels = ingredient_label_layout(["pineapple", "marshmallows"])
        self.assertEqual([layer["text"] for layer in labels], ["PINEAPPLE", "MARSHMALLOWS"])
        self.assertLess(labels[0]["x"], labels[1]["x"])


class ReviewMarkers(unittest.TestCase):
    def test_missing_and_ai_scenes_are_marked_in_premiere(self):
        scenes = {"a": {"narration": "Real clip"}, "b": {"narration": "No footage yet", "selected_asset_id": None},
                  "c": {"narration": "An AI still", "selected_asset_id": "x"}}
        scenes["a"]["selected_asset_id"] = "y"
        assets = {"x": {"provider": "generated"}, "y": {"provider": "youtube"}}
        clips = [{"scene_id": key, "position": index, "start_seconds": index * 2.0, "end_seconds": index * 2.0 + 2}
                 for index, key in enumerate("abc", start=1)]
        markers = review_markers(clips, scenes, assets, {2})
        self.assertEqual([(marker["position"], marker["kind"]) for marker in markers], [(2, MISSING), (3, AI_IMAGE)])
        with tempfile.TemporaryDirectory() as folder:
            xml = Path(folder) / "edit.xml"
            files = [(Path(folder) / f"clip-{index}.mp4", index * 2.0, index * 2.0 + 2) for index in range(1, 4)]
            write_premiere_xml(xml, "Test", files, None, 8.0, 30, 1920, 1080, markers)
            text = xml.read_text()
            self.assertIn("<marker><name>MISSING FOOTAGE</name>", text)
            self.assertIn("<label2>Rose</label2>", text)
            self.assertIn("<name>MISSING FOOTAGE - scene 2</name>", text)
            srt = Path(folder) / "words.srt"
            write_srt(srt, [(1.0, 2.5, "CHAPTER 1"), (0.0, 1.0, "Hello")])
            self.assertTrue(srt.read_text().startswith("1\n00:00:00,000 --> 00:00:01,000\nHello"))


if __name__ == "__main__":
    unittest.main()
