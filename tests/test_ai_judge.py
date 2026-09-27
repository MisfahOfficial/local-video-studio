from __future__ import annotations

import unittest

from PIL import Image

from app.ai_judge import Verdict, best_usable, contact_sheet


class JudgeTests(unittest.TestCase):
    def test_contact_sheet_stays_small(self) -> None:
        sheet = contact_sheet([[Image.new("RGB", (640, 360))] * 3 for _ in range(4)])
        self.assertLessEqual(sheet.width * sheet.height / 750, 1300)  # about 1,000 image tokens

    def test_only_clean_fits_are_used_best_first(self) -> None:
        verdicts = [
            Verdict("A", True, 6, False, False, False, ""),
            Verdict("B", True, 9, True, False, False, "host on camera"),
            Verdict("C", True, 8, False, False, False, ""),
            Verdict("D", False, 7, False, False, False, "wrong dish"),
        ]
        self.assertEqual(best_usable(verdicts), [2, 0])
        self.assertEqual(best_usable(None), [])
