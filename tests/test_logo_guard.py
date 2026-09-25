from __future__ import annotations

import unittest

import numpy as np

from app.logo_guard import crop_filter, safe_crop, static_boxes


class LogoGuardTests(unittest.TestCase):
    def test_static_corner_logo_is_found_in_moving_footage(self) -> None:
        rng = np.random.default_rng(1)
        frames = rng.uniform(0, 255, size=(30, 180, 320)).astype(np.float32)
        frames[:, 10:30, 270:310] = 0
        frames[:, 14:26, 274:306] = 255  # a white bug with a black border, top right
        boxes = static_boxes(frames, edge_threshold=10, motion_threshold=35, max_area=0.06,
                             min_density=0.08, max_static_share=0.3)
        self.assertEqual(len(boxes), 1)
        x0, y0, x1, y1 = boxes[0]
        self.assertGreater(x0, 0.8)
        self.assertLess(y1, 0.2)

    def test_crop_hides_corner_logo_with_small_zoom(self) -> None:
        crop, ok = safe_crop([(0.85, 0.03, 0.97, 0.12)])
        self.assertTrue(ok)
        self.assertTrue(crop["x"] + crop["w"] <= 0.85 + 1e-6 or crop["y"] >= 0.12 - 1e-6)
        self.assertLess(1 / crop["w"], 1.2)
        self.assertIn("crop=", crop_filter(crop))

    def test_no_logo_means_no_crop(self) -> None:
        self.assertEqual(safe_crop([]), (None, True))
        self.assertEqual(crop_filter(None), "")

    def test_centre_watermark_cannot_be_hidden(self) -> None:
        crop, ok = safe_crop([(0.3, 0.8, 0.7, 0.95), (0.02, 0.02, 0.2, 0.15), (0.8, 0.8, 0.98, 0.98), (0.8, 0.02, 0.98, 0.15), (0.02, 0.8, 0.2, 0.98)])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()


class BlackBarTests(unittest.TestCase):
    def test_pillarboxed_clip_is_detected_and_cropped(self) -> None:
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        from app.logo_guard import content_box, fit_crop

        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg is required")
        with tempfile.TemporaryDirectory() as temporary:
            clip = Path(temporary) / "pillar.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=2",
                 "-vf", "pad=1280:720:320:180:black", "-pix_fmt", "yuv420p", str(clip)],
                check=True,
            )
            box = content_box(clip)
            self.assertIsNotNone(box)
            self.assertAlmostEqual(box[2], 0.5, delta=0.03)
            self.assertAlmostEqual(box[4], 16 / 9, delta=0.1)
            crop = fit_crop(box, None)
            self.assertGreaterEqual(crop["x"], box[0] - 0.01)
            self.assertLessEqual(crop["x"] + crop["w"], box[0] + box[2] + 0.01)
