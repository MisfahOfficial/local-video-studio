from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.scene_planner import RuleBasedScenePlanner


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "studio.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_project_scene_queue_asset_and_selection(self) -> None:
        project = self.db.create_project("Test production", "us_nostalgia")
        drafts = RuleBasedScenePlanner().plan(
            "First a forgotten room waited quietly. Then the truth was revealed at last.",
            theme_id="us_nostalgia",
            duration_seconds=12,
            target_scene_count=2,
        )
        scenes = self.db.replace_scenes(project["id"], drafts)
        self.assertEqual(len(scenes), 2)
        self.assertEqual(self.db.queue_generation(project["id"]), 2)
        job = self.db.claim_generation_job(project["id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "pending")

        asset_file = Path(self.temporary.name) / "asset.png"
        asset_file.write_bytes(b"png")
        asset = self.db.add_asset(
            project_id=project["id"], scene_id=scenes[0]["id"], candidate_index=0,
            media_kind="image", provider="mock", model="offline-placeholder",
            local_path=str(asset_file), remote_url=None, provider_asset_id=None,
            cost=0.0, metadata={"test": True},
        )
        selected = self.db.select_asset(scenes[0]["id"], asset["id"])
        self.assertEqual(selected["selected_asset_id"], asset["id"])
        self.assertEqual(self.db.list_assets(project["id"])[0]["metadata"], {"test": True})


if __name__ == "__main__":
    unittest.main()

