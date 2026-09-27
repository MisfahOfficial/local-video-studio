from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.motion.engine import encode
from app.motion.templates import caption_keywords, graph_paper_card, highlight_caption, polaroid_stack
from app.timeline.renderer import _photo_graphic


class MotionTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.photo = Image.new("RGB", (800, 600), (180, 120, 60))

    def test_templates_draw_full_hd_frames(self) -> None:
        for template in (polaroid_stack(self.photo), graph_paper_card(self.photo)):
            self.assertEqual(template(1.0, 3.0).size, (1920, 1080))
        overlay = highlight_caption("350 degrees for twelve minutes", {"350"})(2.0, 3.0)
        self.assertEqual(overlay.mode, "RGBA")
        self.assertGreater(overlay.getchannel("A").getextrema()[1], 0)
        self.assertEqual(highlight_caption("text", set())(0.0, 3.0).getchannel("A").getextrema()[1], 0)

    def test_caption_keywords(self) -> None:
        self.assertEqual(caption_keywords("two cups of rolled oats"), {"two", "oats"})
        self.assertEqual(caption_keywords("How did one dollar fill a table in 1955?"), {"one", "1955"})
        self.assertEqual(caption_keywords("Forgotten desserts"), {"forgotten"})

    def test_photo_graphic_choice(self) -> None:
        image = {"media_kind": "image", "provider": "photo"}
        self.assertEqual(_photo_graphic({}, image, 0), "polaroid")
        self.assertEqual(_photo_graphic({}, image, 1), "graph_card")
        self.assertIsNone(_photo_graphic({}, {"media_kind": "video"}, 0))
        self.assertIsNone(_photo_graphic({}, {"media_kind": "image", "provider": "chapter"}, 0))
        chosen = {"timeline_actions": [{"type": "graphic", "params": {"preset": "none"}}]}
        self.assertIsNone(_photo_graphic(chosen, image, 0))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_encode_opaque_and_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = encode(graph_paper_card(self.photo), 0.5, Path(temporary) / "card.mp4", fps=10)
            overlay = encode(highlight_caption("two oats", {"oats"}), 0.5, Path(temporary) / "c.mov", fps=10, alpha=True)
            self.assertGreater(video.stat().st_size, 1000)
            self.assertGreater(overlay.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
