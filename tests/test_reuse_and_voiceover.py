from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.database import Database
from app.domain import Emotion, NarrativeRole, SceneDraft

FFMPEG = shutil.which("ffmpeg")


def _clip(path: Path, seconds: float, colour: str = "orange") -> Path:
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:d={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _draft(position: int, start: float, end: float, text: str) -> SceneDraft:
    return SceneDraft(position=position, start_seconds=start, end_seconds=end, narration=text, visual_subject=text,
                      emotion=list(Emotion)[0], narrative_role=list(NarrativeRole)[0], importance=1, prompt=text, negative_prompt="",
                      model_role="photoreal", candidate_count=1, timeline_actions=[])


@unittest.skipUnless(FFMPEG, "ffmpeg is needed")
class ReusedShotTest(unittest.TestCase):
    """Scenes with no footage of their own (V2 test, 3 Oct): never loop the previous 2-4 s clip."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.db = Database(self.folder / "studio.sqlite3")
        self.project = self.db.create_project("Reuse", "us_nostalgia")
        texts = ["EGGNOG PIE", "Eggnog pie was set with gelatin.", "It sat in a graham cracker crust.",
                 "Your grandmother chilled it overnight.", "It was served cold."]
        self.scenes = self.db.replace_scenes(self.project["id"], [
            _draft(index + 1, index * 4.0, index * 4.0 + 4.0, text) for index, text in enumerate(texts)])
        for scene, colour in ((self.scenes[1], "red"), (self.scenes[2], "blue")):
            clip = _clip(self.folder / f"{colour}.mp4", 4.0, colour)
            asset = self.db.add_asset(project_id=self.project["id"], scene_id=scene["id"], candidate_index=0,
                                      media_kind="video", provider="youtube", model="t", local_path=str(clip),
                                      remote_url=None, provider_asset_id=colour, cost=0.0, metadata={"video_id": colour})
            self.db.select_asset(scene["id"], asset["id"])

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def _manager(self):
        from app.auto_build import AutoBuildManager

        app = SimpleNamespace(db=self.db, settings=SimpleNamespace(load=lambda: SimpleNamespace(ffmpeg_path=FFMPEG)))
        return AutoBuildManager(app)

    def test_empty_scenes_reuse_a_shot_of_their_item_changed_never_looped(self):
        held = self._manager()._hold_empty(self.project["id"])
        self.assertEqual(held, [1, 4, 5])  # the heading (a name-label channel's first shot of the item) too
        assets = {asset["id"]: asset for asset in self.db.list_assets(self.project["id"])}
        scenes = self.db.list_scenes(self.project["id"])
        fourth, fifth = (assets[scenes[index]["selected_asset_id"]]["metadata"] for index in (3, 4))
        # Scene 1 took blue (mirrored). Scene 4 is next to scene 3 (blue): it takes the farther red shot, mirrored.
        self.assertEqual((fourth["video_id"], fourth["variant"]), ("red", "flip"))
        # Scene 5: blue is its neighbour and red was already reused once, so red comes back in the old TV.
        self.assertEqual((fifth["video_id"], fifth["variant"]), ("red", "tv"))
        self.assertGreaterEqual(fourth["speed"], 0.5)

    def test_a_shot_the_checker_refused_is_never_reused(self):
        red = next(asset for asset in self.db.list_assets(self.project["id"]) if asset["provider_asset_id"] == "red")
        self.db.update_asset_metadata(red["id"], {"checker": "refused"})
        self._manager()._hold_empty(self.project["id"])
        assets = {asset["id"]: asset for asset in self.db.list_assets(self.project["id"])}
        reused = [assets[scene["selected_asset_id"]]["metadata"]["video_id"]
                  for scene in self.db.list_scenes(self.project["id"])
                  if scene["selected_asset_id"] and assets[scene["selected_asset_id"]]["metadata"].get("variant")]
        self.assertNotIn("red", reused)
        self.assertTrue(reused)

    def test_reused_shots_are_not_checked_again(self):
        from app.reuse_look import variant_of

        self.assertEqual(variant_of({"metadata": json.dumps({"variant": "tv", "speed": 0.3})}),
                         {"variant": "tv", "speed": 0.5})
        self.assertEqual(variant_of({"metadata": {}}), {"variant": "", "speed": 1.0})


class TvBezelTest(unittest.TestCase):
    def test_bezel_has_a_see_through_screen(self):
        from PIL import Image

        from app.reuse_look import screen_box, tv_bezel

        folder = Path(tempfile.mkdtemp())
        try:
            path = tv_bezel(320, 180, folder)
            x, y, w, h = screen_box(320, 180)
            with Image.open(path) as image:
                self.assertLess(image.getpixel((x + w // 2, y + h // 2))[3], 80)  # the picture shows through
                self.assertEqual(image.getpixel((2, 2))[3], 255)  # the cabinet does not
        finally:
            shutil.rmtree(folder, ignore_errors=True)


@unittest.skipUnless(FFMPEG, "ffmpeg is needed")
class DarkShotTest(unittest.TestCase):
    def test_nearly_black_clips_are_refused(self):
        from app.youtube_auto import too_dark

        folder = Path(tempfile.mkdtemp())
        try:
            self.assertTrue(too_dark(_clip(folder / "dark.mp4", 1.0, "0x141414"), FFMPEG))
            self.assertFalse(too_dark(_clip(folder / "bright.mp4", 1.0, "0x8a6a4a"), FFMPEG))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class VoiceoverTest(unittest.TestCase):
    def test_script_is_read_with_the_voice_and_saved(self):
        from app.voiceover_tts import make_voiceover

        folder = Path(tempfile.mkdtemp())
        audio = folder / "made.mp3"
        audio.write_bytes(b"ID3fake")
        calls: list[str] = []
        answers = iter([{"success": True, "task_id": "t1"}, {"status": "doing", "progress": 40},
                        {"status": "done", "metadata": {"audio_url": audio.as_uri()}}])

        def opener(request, timeout=60):
            calls.append(request.full_url)
            if request.data:
                self.assertIn(b"elevenlabs_qAZH0aMXY8tw1QufPN0D", request.data)
                self.assertEqual(request.get_header("Xi-api-key"), "key")
            return next(answers)

        try:
            saved = make_voiceover("key", "Fruitcake was aged in brandy.", folder / "voiceover.mp3",
                                   sleep=lambda _seconds: None, opener=opener)
            self.assertEqual(saved.read_bytes(), b"ID3fake")
            self.assertTrue(calls[0].endswith("/v3/text-to-speech"))
            self.assertTrue(calls[1].endswith("/v1/task/t1"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_missing_key_is_explained(self):
        from app.voiceover_tts import VoiceoverError, make_voiceover

        with self.assertRaises(VoiceoverError):
            make_voiceover("", "text", Path("x.mp3"))


if __name__ == "__main__":
    unittest.main()


class V3StyleTest(unittest.TestCase):
    def test_v3_follows_its_three_references(self):
        from app.channel_kits import kit_for
        from app.channel_styles import get_style
        from app.motion.templates import gallery_stack
        from PIL import Image

        kit = kit_for(Path(tempfile.mkdtemp()), "v3")
        self.assertEqual(kit["chapter_designs"], ["v3_paint"])
        self.assertFalse(kit["ingredient_cards"])  # none of the three references has one
        self.assertEqual(kit["extras"], [])  # no maps, prices or timelines either
        self.assertTrue(kit["hook_collage"])
        frame = gallery_stack([Image.new("RGB", (64, 36), (200, 50, 50))] * 3, get_style("v3"), title="These were not just sweets")
        self.assertEqual(frame(1.0, 3.0).size, (1920, 1080))
