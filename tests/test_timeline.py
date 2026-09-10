from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

from app.domain import GenerationRequest
from app.providers.mock import MockImageProvider
from app.timeline.actions import build_default_motion_registry
from app.timeline.renderer import FFmpegRenderer, build_subtitle_style, write_scene_ass, write_scene_srt


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
                [{"start_seconds": 0.0, "end_seconds": 2.345, "narration": "Narration.", "caption_text": "A first caption."}],
                destination,
            )
            text = destination.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:02,345", text)
            self.assertIn("A first caption.", text)
            self.assertNotIn("Narration.", text)

    def test_caption_style_builds_safe_ass_options(self) -> None:
        style = build_subtitle_style({
            "font": "Georgia", "size": 48, "position": "top",
            "text_color": "#F0E0D0", "background_color": "#102030",
            "background_opacity": 0.5,
        })
        self.assertIn("FontName=Georgia", style)
        self.assertIn("FontSize=48", style)
        self.assertIn("Alignment=8", style)
        self.assertIn("PrimaryColour=&H00D0E0F0", style)

    def test_ass_supports_transform_case_and_advanced_style(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "captions.ass"
            write_scene_ass(
                [{"start_seconds": 0, "end_seconds": 2.5, "caption_text": "Mixed Case"}],
                destination, 1920, 1080,
                {"font": "Georgia", "size": 60, "case": "upper", "position": "middle",
                 "alignment": "right", "position_x": -10, "position_y": 5, "scale": 115,
                 "rotation": 2, "background_enabled": False, "stroke_enabled": True,
                 "stroke_color": "#112233", "stroke_width": 4},
            )
            text = destination.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 1920", text)
            self.assertIn("Style: Default,Georgia,60", text)
            self.assertIn("MIXED CASE", text)
            self.assertIn("\\pos(", text)

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
