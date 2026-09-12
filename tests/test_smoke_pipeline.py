from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from app.database import Database
from app.domain import GenerationRequest
from app.providers.mock import MockImageProvider
from app.scene_planner import RuleBasedScenePlanner
from app.timeline.renderer import FFmpegRenderer
from app.transcription import probe_duration


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for the render smoke test")
class SmokePipelineTests(unittest.TestCase):
    def test_offline_images_render_to_playable_mp4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = Database(root / "studio.sqlite3")
            project = db.create_project("Offline smoke", "us_nostalgia")
            voiceover = root / "voiceover.wav"
            with wave.open(str(voiceover), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8_000)
                # Silence is enough to validate audio muxing without another tool.
                audio.writeframes(struct.pack("<h", 0) * 16_000)
            project = db.update_project(
                project["id"], voiceover_path=str(voiceover), duration_seconds=2.0
            )
            drafts = RuleBasedScenePlanner().plan(
                "The warm kitchen opened for the morning. Then the last light faded quietly.",
                theme_id="us_nostalgia", duration_seconds=2.0, target_scene_count=2,
            )
            scenes = db.replace_scenes(project["id"], drafts)
            assets_dir = root / "project" / "assets"
            assets_dir.mkdir(parents=True)
            provider = MockImageProvider()
            for index, scene in enumerate(scenes):
                generated = provider.generate(
                    GenerationRequest(
                        prompt=scene["prompt"], negative_prompt="", model="offline-placeholder",
                        width=640, height=360,
                    )
                )
                path = assets_dir / f"scene-{index}.png"
                path.write_bytes(generated.content)
                db.add_asset(
                    project_id=project["id"], scene_id=scene["id"], candidate_index=0,
                    media_kind="image", provider="mock", model=generated.model,
                    local_path=str(path), remote_url=None, provider_asset_id=None,
                    cost=0.0, metadata={},
                )

            output = FFmpegRenderer().render(
                project=db.get_project(project["id"]),
                scenes=db.list_scenes(project["id"]),
                assets=db.list_assets(project["id"]),
                project_dir=root / "project",
                width=320, height=180, fps=10, burn_captions=True,
                timeline_clips=db.list_timeline_clips(project["id"]),
                output_directory=root / "finished",
                output_name="My Test Export",
                video_bitrate_kbps=1_000,
                audio_bitrate_kbps=128,
                caption_style={
                    "font": "Arial", "size": 28, "case": "upper", "position": "bottom",
                    "alignment": "center", "text_color": "#FFFFFF", "opacity": 1,
                    "background_enabled": False, "background_color": "#000000", "background_opacity": .7,
                    "stroke_enabled": True, "stroke_color": "#000000", "stroke_width": 2,
                    "glow_enabled": True, "glow_color": "#88CCFF", "glow_radius": 3,
                    "shadow_enabled": True, "shadow_color": "#000000", "shadow_x": 2, "shadow_y": 2,
                },
            )
            self.assertTrue(output.is_file())
            self.assertEqual(output.name, "My Test Export.mp4")
            self.assertGreater(output.stat().st_size, 1_000)
            self.assertGreaterEqual(probe_duration(output), 1.9)
            self.assertTrue((root / "project" / "captions.srt").is_file())
            self.assertTrue((root / "project" / "captions.ass").is_file())


if __name__ == "__main__":
    unittest.main()
