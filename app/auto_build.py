"""One click from script + voice-over to a filled timeline.

Plan the scenes (Whisper Sync by default), source real footage (which also makes the
chapter cards and motion graphics), then fill any scene still empty with a realistic
aged still, so the Visual plan step is no longer needed.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .footage_match import detect_era, heading_subject, scene_subjects
from .vintage_still import generate_vintage_still


class AutoBuildManager:
    def __init__(self, application: Any):
        self.app = application
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._statuses: dict[str, dict[str, Any]] = {}

    def status(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._statuses.get(project_id, {"running": False, "stage": "", "error": ""}))

    def _update(self, project_id: str, **changes: Any) -> None:
        with self._lock:
            self._statuses.setdefault(project_id, {}).update(changes)

    def start(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            active = self._threads.get(project_id)
            if active and active.is_alive():
                return dict(self._statuses[project_id])
            self._statuses[project_id] = {
                "running": True, "stage": "Planning scenes", "step": 1, "steps": 3, "error": "",
                "started": time.time(), "sourcing": {}, "filled": [],
            }
            thread = threading.Thread(target=self._run, args=(project_id, dict(body)), daemon=True,
                                      name=f"auto-build-{project_id[:8]}")
            self._threads[project_id] = thread
            thread.start()
            return dict(self._statuses[project_id])

    def _run(self, project_id: str, body: dict[str, Any]) -> None:
        try:
            body.setdefault("planner", "whisper")
            plan = self.app.plan_project(project_id, body)
            self._update(project_id, stage="Finding footage", step=2, scenes=len(plan.get("scenes") or []),
                         warnings=plan.get("warnings") or [])
            self.app.youtube_auto.start(project_id, force=True)
            time.sleep(1)
            while self.app.youtube_auto.status(project_id).get("running"):
                self._update(project_id, sourcing=self.app.youtube_auto.status(project_id))
                time.sleep(2)
            self._update(project_id, sourcing=self.app.youtube_auto.status(project_id),
                         stage="Filling missing scenes", step=3)
            if self.app.youtube_auto.status(project_id).get("youtube_blocked"):
                # Empty scenes wait for real footage instead of turning into AI images.
                self._update(project_id, running=False, stage="Paused: YouTube is blocking this computer. Run it again later; "
                             "finished scenes are kept.", filled=[], fill_failed=[])
                return
            filled, failed = self._fill_missing(project_id)
            self._update(project_id, running=False, stage="Done", filled=filled, fill_failed=failed)
            # After each video the AI designs one new chapter look and one new ingredients look for the
            # channel's library (quietly, after the build, so it never slows the video down).
            threading.Thread(target=self._grow_designs, args=(project_id,), daemon=True, name="design-growth").start()
        except Exception as error:  # the UI shows the reason; the project keeps whatever was made
            self._update(project_id, running=False, stage="Stopped", error=str(error)[:500])
        finally:
            with self._lock:
                self._threads.pop(project_id, None)

    def _grow_designs(self, project_id: str) -> None:
        try:
            from .design_generator import generate_design, library_size
            from .motion_designs import web_engines_ready

            if not web_engines_ready():
                return
            settings = self.app.settings.load()
            project = self.app.db.get_project(project_id) or {}
            style_key = str((project.get("effects") or {}).get("channel_style") or "v3")
            sample = next((str(asset["local_path"]) for asset in self.app.db.list_assets(project_id)
                           if asset.get("provider") in ("photo", "generated") and Path(str(asset.get("local_path"))).is_file()), "")
            if not sample:
                return
            for kind in ("chapter", "ingredients"):
                if library_size(kind) < 15:
                    generate_design(kind, settings, style_key, topic=str(project.get("script") or "")[:400],
                                    sample_image=sample, ffmpeg_path=str(settings.ffmpeg_path or "ffmpeg"))
        except Exception:  # growing the library is a bonus; it must never disturb the tool
            pass

    def _fill_missing(self, project_id: str) -> tuple[list[int], list[dict[str, Any]]]:
        """A realistic period still for every scene that still has nothing (and is not a heading)."""
        db, settings = self.app.db, self.app.settings.load()
        project = db.get_project(project_id) or {}
        scenes = db.list_scenes(project_id)
        subjects = scene_subjects(scenes, "")
        era = detect_era(str(project.get("script") or ""))
        filled: list[int] = []
        failed: list[dict[str, Any]] = []
        lock = threading.Lock()
        todo = [(scene, subject) for scene, subject in zip(scenes, subjects)
                if not scene.get("selected_asset_id") and not heading_subject(str(scene.get("narration") or ""))]

        def fill(pair: tuple[dict[str, Any], str]) -> None:
            scene, subject = pair
            position = int(scene.get("position") or 0)
            destination = (self.app.paths.project_dir(project_id) / "assets" / "stills"
                           / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.jpg")
            try:
                metadata = generate_vintage_still(settings, str(scene.get("narration") or ""), subject, era, destination)
            except Exception as error:
                with lock:
                    failed.append({"scene": position, "error": str(error)[:200]})
                return
            asset = db.add_asset(
                project_id=project_id, scene_id=str(scene["id"]),
                candidate_index=db.next_asset_candidate_index(str(scene["id"])),
                media_kind="image", provider="generated", model=str(metadata["model"]), local_path=str(destination),
                remote_url=None, provider_asset_id=None, cost=float(metadata.get("cost") or 0), metadata=metadata,
            )
            db.select_asset(str(scene["id"]), str(asset["id"]))
            with lock:
                filled.append(position)

        # Images are made three at a time (one by one took most of the last step).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(fill, todo))
        return sorted(filled), failed
