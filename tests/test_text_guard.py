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


class VisionFileHandleTest(unittest.TestCase):
    def test_checks_leave_no_file_open(self):
        import os
        import sys
        import tempfile

        from app import text_guard

        if sys.platform != "darwin" or not text_guard.available():
            self.skipTest("macOS Vision only")
        from pathlib import Path

        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "frame.jpg"
            Image.new("RGB", (320, 180), (200, 180, 150)).save(image)
            before = len(os.listdir("/dev/fd"))
            for _ in range(60):
                text_guard.face_areas(image)
                text_guard.read_text(image)
            self.assertLessEqual(len(os.listdir("/dev/fd")), before + 2)  # a URL handler left one file open per check
