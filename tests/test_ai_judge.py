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


class _FakeJudge:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.seen = []

    def judge(self, need, candidates):
        self.seen.append((need, len(candidates)))
        return self.verdicts


class _FakeVerifier:
    def moment_frames(self, video_id, start, duration, count=3):
        return [Image.new("RGB", (160, 90))] * 3


class JudgeWiringTests(unittest.TestCase):
    def test_videos_are_reordered_and_rejects_dropped(self) -> None:
        from types import SimpleNamespace

        from app.youtube_auto import AutoYouTubeManager

        judge = _FakeJudge([
            Verdict("A", True, 5, False, False, False, ""),
            Verdict("B", False, 9, False, False, False, "wrong dish"),
            Verdict("C", True, 8, False, False, False, ""),
        ])
        run = SimpleNamespace(judge=judge, verifier=_FakeVerifier())
        choices = [({"video_id": "a"}, 10.0, 0.8), ({"video_id": "b"}, 5.0, 0.9), ({"video_id": "c"}, 1.0, 0.7)]
        picked = AutoYouTubeManager._judge_videos(None, run, "need", choices, 4.0)
        self.assertEqual([item[0]["video_id"] for item in picked], ["c", "a"])
        self.assertEqual(judge.seen, [("need", 3)])

    def test_without_a_judge_nothing_changes(self) -> None:
        from types import SimpleNamespace

        from app.youtube_auto import AutoYouTubeManager

        run = SimpleNamespace(judge=None, verifier=_FakeVerifier())
        self.assertIsNone(AutoYouTubeManager._judge_videos(None, run, "need", [({"video_id": "a"}, 0, 1)], 4.0))
