from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import scene_checker


class FakeDb:
    def __init__(self, rows):
        self.scenes = [{"id": f"s{i}", "position": i, "narration": text, "selected_asset_id": f"a{i}", "project_id": "p"}
                       for i, (text, _video, _topic) in enumerate(rows, start=1)]
        self.assets = [{"id": f"a{i}", "provider": "youtube", "media_kind": "video", "local_path": "/x.mp4",
                        "provider_asset_id": video, "metadata": {"topic": topic, "auto_sourced": True}, "scene_id": f"s{i}"}
                       for i, (_text, video, topic) in enumerate(rows, start=1)]

    def list_scenes(self, _project):
        return self.scenes

    def list_assets(self, _project, scene_id=None):
        return [asset for asset in self.assets if scene_id in (None, asset["scene_id"])]

    def get_scene(self, scene_id):
        return next(scene for scene in self.scenes if scene["id"] == scene_id)


def fake_ask(refuse):
    def ask(_settings, rows, _sheet, *_rest):
        return {row["label"]: {"label": row["label"], "fits": row["sentence"] not in refuse, "score": 2 if row["sentence"] in refuse else 8,
                               "reason": "wrong dish"} for row in rows}
    return ask


class CheckerTest(unittest.TestCase):
    def run_check(self, rows, refuse, root):
        frame = [Image.new("RGB", (32, 18))]
        with patch.object(scene_checker, "_frames", return_value=frame), patch.object(scene_checker, "_ask", fake_ask(refuse)):
            return scene_checker.check_scenes(FakeDb(rows), root, None, "p")

    def test_wrong_clips_are_refused_and_only_a_mostly_wrong_source_is_remembered(self):
        rows = [("sundae one", "brownie", "ice cream cone cakes"), ("sundae two", "brownie", "ice cream cone cakes"),
                ("cone one", "cones", "ice cream cone cakes"), ("cone two", "cones", "ice cream cone cakes"),
                ("cone three", "cones", "ice cream cone cakes")]
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_check(rows, {"sundae one", "sundae two", "cone one"}, Path(folder))
            self.assertEqual(len(result["rejected"]), 3)
            memory = scene_checker.load_memory(Path(folder))
            self.assertEqual(memory, {"ice cream cone cakes": {"brownie": 1}})  # 1 of 3 cone clips: not the source's fault
            self.assertEqual(scene_checker.banned_sources(Path(folder))["ice cream cone cakes"], set())
            self.run_check(rows, {"sundae one", "sundae two"}, Path(folder))
            self.assertEqual(scene_checker.banned_sources(Path(folder))["ice cream cone cakes"], {"brownie"})

    def test_a_clip_replaced_by_hand_is_banned_at_once(self):
        db = FakeDb([("red robin booth", "applebees-ad", "red robin")])
        with tempfile.TemporaryDirectory() as folder:
            scene_checker.remember_replacement(db, Path(folder), "s1", "new-asset")
            self.assertEqual(scene_checker.banned_sources(Path(folder))["red robin"], {"applebees-ad"})

    def test_no_quota_keeps_the_video_as_sourced(self):
        def broken(*_args):
            raise RuntimeError("429 quota")

        with tempfile.TemporaryDirectory() as folder, patch.object(scene_checker, "_frames", return_value=[Image.new("RGB", (8, 8))]), \
                patch.object(scene_checker, "_ask", broken):
            result = scene_checker.check_scenes(FakeDb([("a", "v", "pie")]), Path(folder), None, "p")
        self.assertEqual(result["rejected"], [])
        self.assertIn("stopped early", result["error"])


if __name__ == "__main__":
    unittest.main()
