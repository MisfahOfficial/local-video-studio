from __future__ import annotations

import unittest
from unittest import mock

from PIL import Image


class GeminiPickTest(unittest.TestCase):
    def _rows(self):
        frame = Image.new("RGB", (64, 36), (200, 150, 90))
        return [{"label": "1", "subject": "eggnog pie", "sentence": "Dissolve gelatin in cold water.",
                 "options": [[frame, frame], [frame, frame], [frame]]},
                {"label": "2", "subject": "eggnog pie", "sentence": "Pour it into the crust.",
                 "options": [[frame], [frame]]}]

    def test_picks_an_option_or_none_and_ignores_nonsense(self):
        from app import gemini_pick

        answer = {"picks": [{"scene": "1", "best": "C", "score": 8, "reason": "dissolving"},
                            {"scene": "Scene 2", "best": "none", "score": 2, "reason": "no crust"},
                            {"scene": "9", "best": "A", "score": 9, "reason": "unknown scene"}]}
        with mock.patch("app.llm.gemini_look", return_value=answer) as look:
            picks = gemini_pick.pick_moments(object(), self._rows(), era="1970s")
        self.assertEqual(picks, {"1": (2, 8), "2": (None, 2)})
        prompt, images = look.call_args[0][1], look.call_args[0][2]
        self.assertEqual(len(images), 2)  # one contact sheet per sentence
        self.assertIn("1970s", prompt)

    def test_a_low_score_counts_as_none(self):
        from app import gemini_pick

        answer = {"picks": [{"scene": "1", "best": "A", "score": 3, "reason": "weak"}]}
        with mock.patch("app.llm.gemini_look", return_value=answer):
            self.assertEqual(gemini_pick.pick_moments(object(), self._rows()[:1]), {"1": (None, 3)})


if __name__ == "__main__":
    unittest.main()
