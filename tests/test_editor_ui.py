from __future__ import annotations

import unittest
from pathlib import Path


class EditorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        static = Path(__file__).parents[1] / "app" / "static"
        cls.html = (static / "index.html").read_text(encoding="utf-8")
        cls.javascript = (static / "app.js").read_text(encoding="utf-8")

    def test_professional_track_order_and_controls(self) -> None:
        caption = self.html.index('id="captionTrack"')
        video = self.html.index('id="timelineList"')
        audio = self.html.index('id="audioTrack"')
        self.assertLess(caption, video)
        self.assertLess(video, audio)
        for control_id in (
            "captionVisibilityButton",
            "videoVisibilityButton",
            "audioMuteButton",
            "previewMuteButton",
        ):
            self.assertIn(f'id="{control_id}"', self.html)

    def test_caption_drag_is_saved_and_track_toggles_are_wired(self) -> None:
        self.assertIn('$("#previewCaption").addEventListener("pointerdown", beginCaptionDrag)', self.javascript)
        self.assertIn("await saveCaptionStyle(true)", self.javascript)
        self.assertIn('$("#captionVisibilityButton").addEventListener("click", toggleCaptionVisibility)', self.javascript)
        self.assertIn('$("#videoVisibilityButton").addEventListener("click", toggleVisualVisibility)', self.javascript)
        self.assertIn('$("#audioMuteButton").addEventListener("click", toggleAudioMute)', self.javascript)


if __name__ == "__main__":
    unittest.main()
