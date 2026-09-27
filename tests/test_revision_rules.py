from __future__ import annotations

import unittest

from app.footage_match import ingredient_list, modern_face, plural_items
from app.logo_guard import cut_free_start
from app.stock_video import best_file
from app.timeline.renderer import _subscribe_clip


class RevisionRuleTests(unittest.TestCase):
    def test_ingredient_lists_become_cards(self) -> None:
        self.assertEqual(ingredient_list("cents per batch when butter and eggs were too expensive."), ["butter", "eggs"])
        self.assertEqual(ingredient_list("Just oats, sugar, and determination."), ["oats", "sugar"])
        self.assertEqual(ingredient_list("Grandmas mixed two cups of rolled oats with sugar"), [])  # a step: video
        self.assertEqual(ingredient_list("The dough came together without any eggs or butter."), [])
        self.assertEqual(ingredient_list("It used only molasses."), [])

    def test_counts_of_dishes_become_a_gallery(self) -> None:
        self.assertEqual(plural_items("We're bringing back thirty forgotten dollar desserts"), "desserts")
        self.assertEqual(plural_items("Here are 25 old recipes"), "recipes")
        self.assertEqual(plural_items("The desserts were cheap"), "")

    def test_single_shot_window(self) -> None:
        self.assertEqual(cut_free_start([], 9.0, 5.0, 2.0), 2.0)
        self.assertEqual(cut_free_start([1.0], 9.0, 5.0, 2.0), 2.0)  # 2-7 s has no cut
        self.assertAlmostEqual(cut_free_start([1.0], 9.0, 5.0, 0.5), 1.2)  # moved just past the cut
        self.assertIsNone(cut_free_start([3.0, 6.0], 9.0, 5.0, 2.0))

    def test_modern_faces_are_rejected_archival_people_kept(self) -> None:
        self.assertTrue(modern_face(0.8, 0.2))
        self.assertFalse(modern_face(0.8, 0.7))
        self.assertFalse(modern_face(0.2, 0.2))

    def test_subscribe_goes_where_the_voice_says_it(self) -> None:
        scenes = {"a": {"narration": "Intro"}, "b": {"narration": "Please subscribe now"}, "c": {"narration": "End"}}
        clips = [{"scene_id": key, "start_seconds": index * 5.0, "end_seconds": index * 5.0 + 5.0}
                 for index, key in enumerate("abc")]
        self.assertEqual(_subscribe_clip(clips, scenes)["scene_id"], "b")
        scenes["b"] = {"narration": "Middle"}
        clips.append({"scene_id": "c", "start_seconds": 15.0, "end_seconds": 40.0})
        # No mention: about a quarter of the way in (10 s of 40 s) -> the clip starting at 10 s.
        self.assertEqual(_subscribe_clip(clips, scenes)["start_seconds"], 10.0)

    def test_pexels_file_choice(self) -> None:
        item = {"video_files": [
            {"link": "a", "file_type": "video/mp4", "width": 3840, "height": 2160},
            {"link": "b", "file_type": "video/mp4", "width": 1920, "height": 1080},
            {"link": "c", "file_type": "video/mp4", "width": 1080, "height": 1920},
        ]}
        self.assertEqual(best_file(item)["link"], "b")


if __name__ == "__main__":
    unittest.main()
