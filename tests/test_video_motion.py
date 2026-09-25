from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.timeline.renderer import FFmpegRenderer, video_safe_motion


def _frame_hash(path: Path, index: int) -> str:
    return hashlib.sha1(subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{index}),scale=64:36",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout).hexdigest()


class VideoSafeMotionTests(unittest.TestCase):
    def test_zoom_filters_emit_one_frame_per_input_frame(self) -> None:
        registry = FFmpegRenderer().motion_registry
        for name in ("slow_push", "detail_push", "slow_pull", "pop_in"):
            converted = video_safe_motion(registry.build(name, 640, 360, 30, 5), 30)
            self.assertIn(":d=1:", converted, name)
            self.assertIn("fps=30,zoompan=", converted, name)
            self.assertNotIn("zoom+", converted, name)
            self.assertNotIn("zoom-", converted, name)

    def test_non_zoom_filters_are_unchanged(self) -> None:
        registry = FFmpegRenderer().motion_registry
        for name in ("static", "pan_left", "pan_right"):
            motion = registry.build(name, 640, 360, 30, 5)
            self.assertEqual(video_safe_motion(motion, 30), motion)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_video_clip_keeps_playing_with_zoom_motion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "moving.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=3",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True,
            )
            output = root / "clip.mp4"
            # pop_in settles back to zoom 1 after ~10 frames, so any later change
            # can only come from the source video actually playing.
            scene = {"timeline_actions": [{"type": "motion", "params": {"preset": "pop_in"}}]}
            FFmpegRenderer()._render_clip(source, output, "video", 2.0, scene, 320, 180, 30, "libx264")
            self.assertNotEqual(_frame_hash(output, 20), _frame_hash(output, 50))


if __name__ == "__main__":
    unittest.main()
