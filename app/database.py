from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .domain import SceneDraft


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    LATEST_SCHEMA_VERSION = 3

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._requires_upgrade():
            self._backup_before_upgrade()
        self.migrate()

    def _requires_upgrade(self) -> bool:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return False
        try:
            with sqlite3.connect(self.path) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if "projects" not in tables:
                    return False
                project_columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
                scene_columns = {row[1] for row in db.execute("PRAGMA table_info(scenes)")} if "scenes" in tables else set()
                return "default_provider" not in project_columns or "caption_style" not in project_columns or (
                    scene_columns and "caption_text" not in scene_columns
                )
        except sqlite3.DatabaseError:
            return False

    def _backup_before_upgrade(self) -> None:
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"studio-pre-v3-{timestamp}.sqlite3"
        with sqlite3.connect(self.path) as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    theme_id TEXT NOT NULL DEFAULT 'us_nostalgia',
                    script TEXT NOT NULL DEFAULT '',
                    voiceover_path TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    target_scene_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    estimated_cost REAL NOT NULL DEFAULT 0,
                    actual_cost REAL NOT NULL DEFAULT 0,
                    caption_style TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scenes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    narration TEXT NOT NULL,
                    caption_text TEXT NOT NULL DEFAULT '',
                    visual_subject TEXT NOT NULL,
                    emotion TEXT NOT NULL,
                    narrative_role TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 1,
                    prompt TEXT NOT NULL,
                    negative_prompt TEXT NOT NULL DEFAULT '',
                    media_kind TEXT NOT NULL DEFAULT 'image',
                    provider TEXT NOT NULL DEFAULT 'runware',
                    model_role TEXT NOT NULL DEFAULT 'photoreal',
                    candidate_count INTEGER NOT NULL DEFAULT 1,
                    timeline_actions TEXT NOT NULL DEFAULT '[]',
                    generation_status TEXT NOT NULL DEFAULT 'pending',
                    selected_asset_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, position)
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
                    candidate_index INTEGER NOT NULL DEFAULT 0,
                    media_kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    remote_url TEXT,
                    provider_asset_id TEXT,
                    cost REAL NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
                    candidate_index INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    provider TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scene_id, candidate_index)
                );

                CREATE TABLE IF NOT EXISTS render_jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress REAL NOT NULL DEFAULT 0,
                    output_path TEXT,
                    error TEXT,
                    settings TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scenes_project ON scenes(project_id, position);
                CREATE INDEX IF NOT EXISTS idx_assets_scene ON assets(scene_id, candidate_index);
                CREATE INDEX IF NOT EXISTS idx_generation_jobs_project ON generation_jobs(project_id, status);
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (utc_now(),),
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            v2_columns = {
                "requested_scene_count": "INTEGER NOT NULL DEFAULT 0",
                "default_provider": "TEXT NOT NULL DEFAULT 'runware'",
                "default_model_role": "TEXT NOT NULL DEFAULT 'photoreal'",
                "default_candidate_count": "INTEGER NOT NULL DEFAULT 1",
                "default_motion": "TEXT NOT NULL DEFAULT 'slow_push'",
                "default_transition": "TEXT NOT NULL DEFAULT 'fade'",
            }
            for name, declaration in v2_columns.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE projects ADD COLUMN {name} {declaration}")
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (2, ?)",
                (utc_now(),),
            )
            project_columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            if "caption_style" not in project_columns:
                db.execute("ALTER TABLE projects ADD COLUMN caption_style TEXT NOT NULL DEFAULT '{}'")
            scene_columns = {row[1] for row in db.execute("PRAGMA table_info(scenes)")}
            if "caption_text" not in scene_columns:
                db.execute("ALTER TABLE scenes ADD COLUMN caption_text TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE scenes SET caption_text = narration WHERE caption_text = ''")
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (3, ?)",
                (utc_now(),),
            )

    def schema_version(self) -> int:
        with self.connection() as db:
            row = db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for key in ("timeline_actions", "metadata", "settings", "caption_style"):
            if key in data and isinstance(data[key], str):
                try:
                    data[key] = json.loads(data[key])
                except json.JSONDecodeError:
                    data[key] = [] if key == "timeline_actions" else {}
        return data

    def create_project(self, name: str, theme_id: str) -> dict[str, Any]:
        project_id = str(uuid.uuid4())
        now = utc_now()
        with self.connection() as db:
            db.execute(
                "INSERT INTO projects (id, name, theme_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, name.strip() or "Untitled project", theme_id, now, now),
            )
        return self.get_project(project_id) or {}

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._dict(row) or {} for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._dict(row)

    def update_project(self, project_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "name", "theme_id", "script", "voiceover_path", "duration_seconds", "target_scene_count",
            "requested_scene_count", "status", "estimated_cost", "actual_cost", "default_provider", "default_model_role",
            "default_candidate_count", "default_motion", "default_transition", "caption_style"
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if "caption_style" in values:
            values["caption_style"] = json.dumps(values["caption_style"])
        if values:
            values["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in values)
            with self.connection() as db:
                db.execute(
                    f"UPDATE projects SET {assignments} WHERE id = ?",
                    (*values.values(), project_id),
                )
        project = self.get_project(project_id)
        if not project:
            raise KeyError("Project not found")
        return project

    def replace_scenes(self, project_id: str, drafts: list[SceneDraft]) -> list[dict[str, Any]]:
        now = utc_now()
        project = self.get_project(project_id)
        if not project:
            raise KeyError("Project not found")
        with self.connection() as db:
            db.execute("DELETE FROM scenes WHERE project_id = ?", (project_id,))
            for draft in drafts:
                data = draft.to_dict()
                db.execute(
                    """
                    INSERT INTO scenes (
                        id, project_id, position, start_seconds, end_seconds, narration, caption_text, visual_subject,
                        emotion, narrative_role, importance, prompt, negative_prompt, media_kind,
                        provider, model_role, candidate_count, timeline_actions, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), project_id, data["position"], data["start_seconds"], data["end_seconds"],
                        data["narration"], data["narration"], data["visual_subject"], data["emotion"], data["narrative_role"],
                        data["importance"], data["prompt"], data["negative_prompt"], data["media_kind"],
                        project["default_provider"], project["default_model_role"],
                        int(project["default_candidate_count"]),
                        json.dumps([
                            {"type": "motion", "params": {"preset": project["default_motion"], "strength": 0.55}},
                            {"type": "transition", "params": {"preset": project["default_transition"], "duration": 0.32}},
                        ]), now, now,
                    ),
                )
            db.execute(
                "UPDATE projects SET target_scene_count = ?, status = 'planned', updated_at = ? WHERE id = ?",
                (len(drafts), now, project_id),
            )
        return self.list_scenes(project_id)

    def list_scenes(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM scenes WHERE project_id = ? ORDER BY position", (project_id,)
            ).fetchall()
        return [self._dict(row) or {} for row in rows]

    def get_scene(self, scene_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone()
        return self._dict(row)

    def update_scene(self, scene_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "start_seconds", "end_seconds", "narration", "caption_text", "visual_subject", "emotion", "narrative_role",
            "importance", "prompt", "negative_prompt", "media_kind", "provider", "model_role",
            "candidate_count", "timeline_actions", "generation_status", "selected_asset_id"
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if "timeline_actions" in values:
            values["timeline_actions"] = json.dumps(values["timeline_actions"])
        if values:
            values["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in values)
            with self.connection() as db:
                db.execute(f"UPDATE scenes SET {assignments} WHERE id = ?", (*values.values(), scene_id))
        scene = self.get_scene(scene_id)
        if not scene:
            raise KeyError("Scene not found")
        return scene

    def set_scene_duration(self, scene_id: str, duration_seconds: float) -> list[dict[str, Any]]:
        scene = self.get_scene(scene_id)
        if not scene:
            raise KeyError("Scene not found")
        duration = float(duration_seconds)
        if not 0.5 <= duration <= 600:
            raise ValueError("Scene duration must be between 0.5 and 600 seconds")
        scenes = self.list_scenes(str(scene["project_id"]))
        cursor = 0.0
        now = utc_now()
        with self.connection() as db:
            for item in scenes:
                item_duration = duration if item["id"] == scene_id else max(
                    0.5, float(item["end_seconds"]) - float(item["start_seconds"])
                )
                db.execute(
                    "UPDATE scenes SET start_seconds = ?, end_seconds = ?, updated_at = ? WHERE id = ?",
                    (cursor, cursor + item_duration, now, item["id"]),
                )
                cursor += item_duration
            db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, scene["project_id"]))
        return self.list_scenes(str(scene["project_id"]))

    def reorder_scenes(self, project_id: str, ordered_scene_ids: list[str]) -> list[dict[str, Any]]:
        scenes = self.list_scenes(project_id)
        current_ids = [str(scene["id"]) for scene in scenes]
        if len(ordered_scene_ids) != len(current_ids) or set(ordered_scene_ids) != set(current_ids):
            raise ValueError("The reordered timeline must contain every project scene exactly once")
        by_id = {str(scene["id"]): scene for scene in scenes}
        cursor = 0.0
        now = utc_now()
        with self.connection() as db:
            for temporary_position, scene_id in enumerate(ordered_scene_ids, start=1):
                db.execute(
                    "UPDATE scenes SET position = ?, updated_at = ? WHERE id = ?",
                    (-temporary_position, now, scene_id),
                )
            for position, scene_id in enumerate(ordered_scene_ids, start=1):
                scene = by_id[scene_id]
                duration = max(0.5, float(scene["end_seconds"]) - float(scene["start_seconds"]))
                db.execute(
                    "UPDATE scenes SET position = ?, start_seconds = ?, end_seconds = ?, updated_at = ? WHERE id = ?",
                    (position, cursor, cursor + duration, now, scene_id),
                )
                cursor += duration
            db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        return self.list_scenes(project_id)

    def bulk_update_scenes(self, project_id: str, scene_ids: list[str] | None,
                           changes: dict[str, Any]) -> list[dict[str, Any]]:
        scenes = self.list_scenes(project_id)
        selected = set(scene_ids) if scene_ids is not None else None
        chosen = [scene for scene in scenes if selected is None or scene["id"] in selected]
        if scene_ids is not None and len(chosen) != len(selected):
            raise KeyError("One or more selected scenes do not belong to this project")

        provider = changes.get("provider")
        model_role = changes.get("model_role")
        candidate_count = changes.get("candidate_count")
        motion = changes.get("motion")
        transition = changes.get("transition")
        prompt_find = str(changes.get("prompt_find") or "")
        prompt_replace = str(changes.get("prompt_replace") or "")
        if provider is not None and provider not in {"runware", "together", "mock"}:
            raise ValueError("Unknown image provider")
        if model_role is not None and model_role not in {"photoreal", "precise", "premium"}:
            raise ValueError("Unknown model route")
        if candidate_count is not None and int(candidate_count) not in {1, 2, 3}:
            raise ValueError("Image options must be 1, 2, or 3")
        if not any(value is not None for value in (provider, model_role, candidate_count, motion, transition)) and not prompt_find:
            raise ValueError("Choose at least one bulk change")

        with self.connection() as db:
            for scene in chosen:
                patch: dict[str, Any] = {}
                if provider is not None:
                    patch["provider"] = provider
                if model_role is not None:
                    patch["model_role"] = model_role
                if candidate_count is not None:
                    patch["candidate_count"] = int(candidate_count)
                if prompt_find:
                    patch["prompt"] = str(scene["prompt"]).replace(prompt_find, prompt_replace)
                if motion is not None or transition is not None:
                    actions = scene.get("timeline_actions") or []
                    current_motion = next((item for item in actions if item.get("type") == "motion"), None)
                    current_transition = next((item for item in actions if item.get("type") == "transition"), None)
                    patch["timeline_actions"] = [
                        {"type": "motion", "params": {
                            "preset": motion or (current_motion or {}).get("params", {}).get("preset", "slow_push"),
                            "strength": (current_motion or {}).get("params", {}).get("strength", 0.55),
                        }},
                        {"type": "transition", "params": {
                            "preset": transition or (current_transition or {}).get("params", {}).get("preset", "fade"),
                            "duration": (current_transition or {}).get("params", {}).get("duration", 0.32),
                        }},
                    ]
                if "timeline_actions" in patch:
                    patch["timeline_actions"] = json.dumps(patch["timeline_actions"])
                patch["updated_at"] = utc_now()
                assignments = ", ".join(f"{key} = ?" for key in patch)
                db.execute(f"UPDATE scenes SET {assignments} WHERE id = ?", (*patch.values(), scene["id"]))
        updated_scenes = self.list_scenes(project_id)
        return [scene for scene in updated_scenes if selected is None or scene["id"] in selected]

    def list_assets(self, project_id: str, scene_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM assets WHERE project_id = ?"
        params: list[Any] = [project_id]
        if scene_id:
            query += " AND scene_id = ?"
            params.append(scene_id)
        query += " ORDER BY scene_id, candidate_index, created_at"
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self._dict(row) or {} for row in rows]

    def next_asset_candidate_index(self, scene_id: str) -> int:
        with self.connection() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(candidate_index), -1) + 1 FROM assets WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def add_asset(self, *, project_id: str, scene_id: str, candidate_index: int, media_kind: str,
                  provider: str, model: str, local_path: str, remote_url: str | None,
                  provider_asset_id: str | None, cost: float, metadata: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO assets (
                    id, project_id, scene_id, candidate_index, media_kind, provider, model, local_path,
                    remote_url, provider_asset_id, cost, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (asset_id, project_id, scene_id, candidate_index, media_kind, provider, model, local_path,
                 remote_url, provider_asset_id, cost, json.dumps(metadata), utc_now()),
            )
            db.execute(
                "UPDATE projects SET actual_cost = actual_cost + ?, updated_at = ? WHERE id = ?",
                (cost, utc_now(), project_id),
            )
            db.execute(
                "UPDATE scenes SET selected_asset_id = COALESCE(selected_asset_id, ?), generation_status = 'complete', updated_at = ? WHERE id = ?",
                (asset_id, utc_now(), scene_id),
            )
        assets = self.list_assets(project_id, scene_id)
        return next(asset for asset in assets if asset["id"] == asset_id)

    def select_asset(self, scene_id: str, asset_id: str) -> dict[str, Any]:
        with self.connection() as db:
            exists = db.execute(
                "SELECT 1 FROM assets WHERE id = ? AND scene_id = ?", (asset_id, scene_id)
            ).fetchone()
            if not exists:
                raise KeyError("Asset not found for scene")
        return self.update_scene(scene_id, {"selected_asset_id": asset_id})

    def queue_generation(self, project_id: str, scene_ids: list[str] | None = None,
                         force: bool = False) -> int:
        scenes = self.list_scenes(project_id)
        chosen = [scene for scene in scenes if scene_ids is None or scene["id"] in scene_ids]
        now = utc_now()
        queued = 0
        with self.connection() as db:
            for scene in chosen:
                existing_assets = db.execute(
                    "SELECT candidate_index FROM assets WHERE scene_id = ?", (scene["id"],)
                ).fetchall()
                existing = {row[0] for row in existing_assets}
                for candidate_index in range(max(1, int(scene["candidate_count"]))):
                    if candidate_index in existing and not force:
                        continue
                    db.execute(
                        """
                        INSERT INTO generation_jobs (
                            id, project_id, scene_id, candidate_index, status, provider, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                        ON CONFLICT(scene_id, candidate_index) DO UPDATE SET
                            status = 'pending', provider = excluded.provider, error = NULL, updated_at = excluded.updated_at
                        """,
                        (str(uuid.uuid4()), project_id, scene["id"], candidate_index, scene["provider"], now, now),
                    )
                    queued += 1
                db.execute(
                    "UPDATE scenes SET generation_status = 'queued', updated_at = ? WHERE id = ?",
                    (now, scene["id"]),
                )
        return queued

    def claim_generation_job(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM generation_jobs WHERE project_id = ? AND status = 'pending' ORDER BY created_at LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE generation_jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
        return self._dict(row)

    def finish_generation_job(self, job_id: str, status: str, error: str | None = None) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE generation_jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, utc_now(), job_id),
            )

    def generation_status(self, project_id: str) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM generation_jobs WHERE project_id = ? GROUP BY status",
                (project_id,),
            ).fetchall()
            failures = db.execute(
                """
                SELECT generation_jobs.id, generation_jobs.scene_id, generation_jobs.candidate_index,
                       generation_jobs.provider, generation_jobs.attempts, generation_jobs.error,
                       generation_jobs.updated_at, scenes.position
                FROM generation_jobs
                JOIN scenes ON scenes.id = generation_jobs.scene_id
                WHERE generation_jobs.project_id = ? AND generation_jobs.status = 'failed'
                ORDER BY generation_jobs.updated_at DESC
                LIMIT 50
                """,
                (project_id,),
            ).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        total = sum(counts.values())
        complete = counts.get("complete", 0)
        return {
            "total": total,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "complete": complete,
            "failed": counts.get("failed", 0),
            "progress": (complete / total * 100) if total else 0,
            "failures": [self._dict(row) or {} for row in failures],
        }

    def retry_failed_generation(self, project_id: str, scene_ids: list[str] | None = None) -> int:
        selected = set(scene_ids) if scene_ids is not None else None
        with self.connection() as db:
            rows = db.execute(
                "SELECT id, scene_id FROM generation_jobs WHERE project_id = ? AND status = 'failed'",
                (project_id,),
            ).fetchall()
            chosen = [row for row in rows if selected is None or row["scene_id"] in selected]
            now = utc_now()
            for row in chosen:
                db.execute(
                    "UPDATE generation_jobs SET status = 'pending', error = NULL, updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                db.execute(
                    "UPDATE scenes SET generation_status = 'queued', updated_at = ? WHERE id = ?",
                    (now, row["scene_id"]),
                )
        return len(chosen)

    def create_render_job(self, project_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self.connection() as db:
            db.execute(
                "INSERT INTO render_jobs (id, project_id, settings, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, project_id, json.dumps(settings), now, now),
            )
        return self.get_render_job(job_id) or {}

    def update_render_job(self, job_id: str, **changes: Any) -> None:
        allowed = {"status", "progress", "output_path", "error"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.connection() as db:
            db.execute(f"UPDATE render_jobs SET {assignments} WHERE id = ?", (*values.values(), job_id))

    def get_render_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._dict(row)

    def latest_render_job(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM render_jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)
            ).fetchone()
        return self._dict(row)
