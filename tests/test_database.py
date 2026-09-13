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

    def test_legacy_database_is_backed_up_and_migrated(self) -> None:
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
        self.assertEqual(migrated.schema_version(), 4)
        self.assertEqual(migrated.get_project("kept")["name"], "Existing work")
        self.assertEqual(migrated.get_project("kept")["default_provider"], "runware")
        self.assertEqual(migrated.get_project("kept")["requested_scene_count"], 0)
        self.assertEqual(migrated.get_project("kept")["caption_style"], {})
        self.assertEqual(len(list((root / "backups").glob("studio-pre-v4-*.sqlite3"))), 1)

    def test_scene_duration_caption_and_reorder(self) -> None:
        project = self.db.create_project("Timeline test", "us_nostalgia")
        drafts = RuleBasedScenePlanner().plan(
            "First the kitchen opened. Then the recipe returned. Finally the family gathered.",
            theme_id="us_nostalgia", duration_seconds=15, target_scene_count=3,
        )
        scenes = self.db.replace_scenes(project["id"], drafts)
        self.assertEqual(scenes[0]["caption_text"], scenes[0]["narration"])
        self.db.update_scene(scenes[1]["id"], {"caption_text": "A shorter caption"})
        retimed = self.db.set_scene_duration(scenes[0]["id"], 8.0)
        self.assertEqual(retimed[0]["end_seconds"], 8.0)
        self.assertEqual(retimed[1]["start_seconds"], 8.0)

        reordered = self.db.reorder_scenes(project["id"], [scenes[2]["id"], scenes[0]["id"], scenes[1]["id"]])
        self.assertEqual([scene["position"] for scene in reordered], [1, 2, 3])
        self.assertEqual(reordered[0]["id"], scenes[2]["id"])
        self.assertEqual(reordered[1]["start_seconds"], reordered[0]["end_seconds"])
        self.assertEqual(reordered[2]["caption_text"], "A shorter caption")

    def test_v2_scene_is_preserved_by_v3_migration(self) -> None:
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        path = root / "studio.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript(
                """
                CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                INSERT INTO schema_migrations VALUES (1, 'now');
                INSERT INTO schema_migrations VALUES (2, 'now');
                CREATE TABLE projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, theme_id TEXT NOT NULL DEFAULT 'us_nostalgia',
                    script TEXT NOT NULL DEFAULT '', voiceover_path TEXT, duration_seconds REAL NOT NULL DEFAULT 0,
                    target_scene_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
                    estimated_cost REAL NOT NULL DEFAULT 0, actual_cost REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    requested_scene_count INTEGER NOT NULL DEFAULT 0,
                    default_provider TEXT NOT NULL DEFAULT 'runware',
                    default_model_role TEXT NOT NULL DEFAULT 'photoreal',
                    default_candidate_count INTEGER NOT NULL DEFAULT 1,
                    default_motion TEXT NOT NULL DEFAULT 'slow_push',
                    default_transition TEXT NOT NULL DEFAULT 'fade'
                );
                CREATE TABLE scenes (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
                    narration TEXT NOT NULL, visual_subject TEXT NOT NULL, emotion TEXT NOT NULL,
                    narrative_role TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 1,
                    prompt TEXT NOT NULL, negative_prompt TEXT NOT NULL DEFAULT '', media_kind TEXT NOT NULL DEFAULT 'image',
                    provider TEXT NOT NULL DEFAULT 'runware', model_role TEXT NOT NULL DEFAULT 'photoreal',
                    candidate_count INTEGER NOT NULL DEFAULT 1, timeline_actions TEXT NOT NULL DEFAULT '[]',
                    generation_status TEXT NOT NULL DEFAULT 'pending', selected_asset_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id, position)
                );
                INSERT INTO projects (id, name, created_at, updated_at) VALUES ('project-v2', 'Preserved v2', 'now', 'now');
                INSERT INTO scenes (
                    id, project_id, position, start_seconds, end_seconds, narration, visual_subject,
                    emotion, narrative_role, prompt, created_at, updated_at
                ) VALUES ('scene-v2', 'project-v2', 1, 0, 5, 'Original caption', 'Kitchen', 'nostalgia', 'hook', 'Prompt', 'now', 'now');
                """
            )
        migrated = Database(path)
        self.assertEqual(migrated.schema_version(), 4)
        self.assertEqual(migrated.get_project("project-v2")["caption_style"], {})
        self.assertEqual(migrated.get_scene("scene-v2")["caption_text"], "Original caption")
        clips = migrated.list_timeline_clips("project-v2")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["scene_id"], "scene-v2")
        self.assertEqual(len(list((root / "backups").glob("studio-pre-v4-*.sqlite3"))), 1)

    def test_timeline_clip_trim_reorder_split_delete_and_restore(self) -> None:
        project = self.db.create_project("Editable clips", "us_nostalgia")
        drafts = RuleBasedScenePlanner().plan(
            "First the kitchen opened. Then the recipe returned. Finally everyone gathered.",
            theme_id="us_nostalgia", duration_seconds=15, target_scene_count=3,
        )
        scenes = self.db.replace_scenes(project["id"], drafts)
        clips = self.db.list_timeline_clips(project["id"])
        self.assertEqual(len(clips), 3)
        self.assertEqual([clip["scene_id"] for clip in clips], [scene["id"] for scene in scenes])

        trimmed = self.db.set_timeline_clip_duration(clips[0]["id"], 7.0, 1.25)
        self.assertEqual(trimmed[0]["end_seconds"], 7.0)
        self.assertEqual(trimmed[0]["source_in_seconds"], 1.25)
        self.assertEqual(trimmed[1]["start_seconds"], 7.0)

        reordered = self.db.reorder_timeline_clips(
            project["id"], [trimmed[2]["id"], trimmed[0]["id"], trimmed[1]["id"]]
        )
        self.assertEqual(reordered[0]["id"], trimmed[2]["id"])
        self.assertEqual(reordered[1]["start_seconds"], reordered[0]["end_seconds"])

        split_at = (reordered[0]["end_seconds"] - reordered[0]["start_seconds"]) / 2
        split, new_id = self.db.split_timeline_clip(reordered[0]["id"], split_at)
        self.assertEqual(len(split), 4)
        self.assertEqual(split[1]["id"], new_id)
        self.assertEqual(split[1]["scene_id"], split[0]["scene_id"])

        after_delete = self.db.delete_timeline_clip(new_id)
        self.assertEqual(len(after_delete), 3)
        restored = self.db.replace_timeline_clips(project["id"], split)
        self.assertEqual([clip["id"] for clip in restored], [clip["id"] for clip in split])

    def test_timeline_detects_and_fits_visual_track_to_voiceover(self) -> None:
        project = self.db.create_project("VO master clock", "us_nostalgia")
        project = self.db.update_project(
            project["id"], voiceover_path="/tmp/voiceover.mp3", duration_seconds=18.0
        )
        drafts = RuleBasedScenePlanner().plan(
            "First the kitchen opened. Then the recipe returned. Finally everyone gathered.",
            theme_id="us_nostalgia", duration_seconds=18, target_scene_count=3,
        )
        self.db.replace_scenes(project["id"], drafts)
        clips = self.db.list_timeline_clips(project["id"])
        shortened = self.db.set_timeline_clip_duration(clips[0]["id"], 2.0)
        original_durations = [clip["end_seconds"] - clip["start_seconds"] for clip in shortened]
        before = self.db.timeline_sync_status(project["id"])
        self.assertEqual(before["status"], "short")

        fitted = self.db.fit_timeline_to_duration(project["id"], 18.0)
        after = self.db.timeline_sync_status(project["id"])
        self.assertEqual(after["status"], "synced")
        self.assertTrue(after["complete"])
        self.assertAlmostEqual(fitted[0]["start_seconds"], 0.0)
        self.assertAlmostEqual(fitted[-1]["end_seconds"], 18.0)
        for previous, current in zip(fitted, fitted[1:]):
            self.assertAlmostEqual(previous["end_seconds"], current["start_seconds"])
        fitted_durations = [clip["end_seconds"] - clip["start_seconds"] for clip in fitted]
        self.assertLess(fitted_durations[0], fitted_durations[1])
        self.assertLess(original_durations[0], original_durations[1])

    def test_fit_repairs_flashing_tail_and_keeps_scene_captions_synchronized(self) -> None:
        project = self.db.create_project("Compressed ending", "us_nostalgia")
        drafts = RuleBasedScenePlanner().plan(
            "A measured opening sentence has several words. "
            "This important ending sentence must remain readable. "
            "The final call to action also needs enough time.",
            theme_id="us_nostalgia", duration_seconds=9, target_scene_count=3,
        )
        scenes = self.db.replace_scenes(project["id"], drafts)
        with self.db.connection() as db:
            ranges = ((0.0, 8.5), (8.5, 8.75), (8.75, 9.0))
            for scene, (start, end) in zip(scenes, ranges, strict=True):
                db.execute(
                    "UPDATE scenes SET start_seconds = ?, end_seconds = ? WHERE id = ?",
                    (start, end, scene["id"]),
                )
                db.execute(
                    "UPDATE timeline_clips SET start_seconds = ?, end_seconds = ? WHERE scene_id = ?",
                    (start, end, scene["id"]),
                )

        fitted = self.db.fit_timeline_to_duration(project["id"], 18.0)
        repaired_scenes = self.db.list_scenes(project["id"])

        self.assertTrue(all(clip["end_seconds"] - clip["start_seconds"] > 2 for clip in fitted))
        self.assertEqual(
            [(scene["start_seconds"], scene["end_seconds"]) for scene in repaired_scenes],
            [(clip["start_seconds"], clip["end_seconds"]) for clip in fitted],
        )
        self.assertEqual(self.db.get_project(project["id"])["duration_seconds"], 18.0)

    def test_planned_pop_motion_is_preserved_in_saved_scenes(self) -> None:
        project = self.db.create_project("Pop insert", "us_nostalgia")
        drafts = RuleBasedScenePlanner().plan(
            "Look!", theme_id="us_nostalgia", duration_seconds=1.5, target_scene_count=1,
        )

        scenes = self.db.replace_scenes(project["id"], drafts)

        self.assertEqual(scenes[0]["timeline_actions"][0]["params"]["preset"], "pop_in")


if __name__ == "__main__":
    unittest.main()
