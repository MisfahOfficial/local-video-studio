from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

from app.domain import GenerationRequest
from app.providers.mock import MockImageProvider
from app.timeline.actions import build_default_motion_registry
from app.timeline.renderer import FFmpegRenderer, write_scene_srt


class TimelineTests(unittest.TestCase):
    def test_default_motion_presets_build_filters(self) -> None:
        registry = build_default_motion_registry()
        expected = {"static", "slow_push", "detail_push", "slow_pull", "pan_left", "pan_right"}
        self.assertEqual(set(registry.names()), expected)
        for name in expected:
            self.assertIn("fps=12", registry.build(name, 320, 180, 12, 1.0))

    def test_srt_uses_scene_timecodes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "captions.srt"
            write_scene_srt(
                [{"start_seconds": 0.0, "end_seconds": 2.345, "narration": "A first caption."}],
                destination,
            )
            text = destination.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:02,345", text)
            self.assertIn("A first caption.", text)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for filter validation")
    def test_all_motion_presets_execute_in_ffmpeg(self) -> None:
        registry = build_default_motion_registry()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            source.write_bytes(
                MockImageProvider().generate(
                    GenerationRequest("filter test", "", "offline-placeholder", 640, 360)
                ).content
            )
            renderer = FFmpegRenderer()
            for name in registry.names():
                destination = root / f"{name}.mp4"
                scene = {
                    "timeline_actions": [
                        {"type": "motion", "params": {"preset": name}},
                        {"type": "transition", "params": {"preset": "cut", "duration": 0}},
                    ]
                }
                renderer._render_clip(source, destination, "image", 0.5, scene, 320, 180, 10, "libx264")
                self.assertGreater(destination.stat().st_size, 500)


if __name__ == "__main__":
    unittest.main()
