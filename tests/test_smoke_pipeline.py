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
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1_000)
            self.assertTrue((root / "project" / "captions.srt").is_file())


if __name__ == "__main__":
    unittest.main()
