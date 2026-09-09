from __future__ import annotations

import tempfile
import sqlite3
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

    def test_bulk_update_and_failed_retry(self) -> None:
        project = self.db.create_project("Bulk test", "us_nostalgia")
        drafts = RuleBasedScenePlanner().plan(
            "First a warm kitchen opened. Then an old recipe revealed the truth.",
            theme_id="us_nostalgia", duration_seconds=12, target_scene_count=2,
        )
        scenes = self.db.replace_scenes(project["id"], drafts)
        updated = self.db.bulk_update_scenes(project["id"], None, {
            "provider": "mock", "candidate_count": 2, "motion": "pan_left",
        })
        self.assertEqual(len(updated), 2)
        self.assertTrue(all(scene["provider"] == "mock" for scene in updated))
        self.assertTrue(all(scene["candidate_count"] == 2 for scene in updated))
        self.assertEqual(updated[0]["timeline_actions"][0]["params"]["preset"], "pan_left")

        self.assertEqual(self.db.queue_generation(project["id"], [scenes[0]["id"]]), 2)
        job = self.db.claim_generation_job(project["id"])
        self.db.finish_generation_job(job["id"], "failed", "temporary provider problem")
        status = self.db.generation_status(project["id"])
        self.assertEqual(status["failed"], 1)
        self.assertEqual(status["failures"][0]["error"], "temporary provider problem")
        self.assertEqual(self.db.retry_failed_generation(project["id"]), 1)
        self.assertEqual(self.db.generation_status(project["id"])["pending"], 2)

    def test_v1_database_is_backed_up_and_migrated(self) -> None:
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        path = root / "studio.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript(
                """
                CREATE TABLE projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, theme_id TEXT NOT NULL DEFAULT 'us_nostalgia',
                    script TEXT NOT NULL DEFAULT '', voiceover_path TEXT, duration_seconds REAL NOT NULL DEFAULT 0,
                    target_scene_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
                    estimated_cost REAL NOT NULL DEFAULT 0, actual_cost REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO projects (id, name, created_at, updated_at) VALUES ('kept', 'Existing work', 'now', 'now');
                """
            )
        migrated = Database(path)
        self.assertEqual(migrated.schema_version(), 2)
        self.assertEqual(migrated.get_project("kept")["name"], "Existing work")
        self.assertEqual(migrated.get_project("kept")["default_provider"], "runware")
        self.assertEqual(migrated.get_project("kept")["requested_scene_count"], 0)
        self.assertEqual(len(list((root / "backups").glob("studio-pre-v2-*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
