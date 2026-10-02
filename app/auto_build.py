"""One click from script + voice-over to a filled timeline.

Plan the scenes (Whisper Sync by default), source real footage (which also makes the
chapter cards and motion graphics), then fill any scene still empty with a realistic
aged still, so the Visual plan step is no longer needed.
"""
from __future__ import annotations

import json
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
            checked = self._check(project_id)
            filled, failed = self._fill_missing(project_id)
            self._update(project_id, running=False, stage="Done", filled=filled, fill_failed=failed, checker=checked)
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

            settings = self.app.settings.load()
            if not web_engines_ready() or not str(getattr(settings, "anthropic_api_key", "") or "").strip():
                return  # without Claude the free models' designs do not pass review; don't spend the Mac on it
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

    def _check(self, project_id: str) -> dict[str, Any]:
        """The AI checker looks at every real clip next to its sentence; the ones that do not fit are
        sourced again once (their source excluded) before anything becomes an AI image."""
        from .scene_checker import check_scenes

        settings = self.app.settings.load()
        ffmpeg = str(settings.ffmpeg_path or "ffmpeg")
        self._update(project_id, stage="AI checker: looking at every clip", step=3)

        def progress(done: int, total: int) -> None:
            self._update(project_id, stage=f"AI checker: {done}/{total} clips looked at")

        result = check_scenes(self.app.db, self.app.paths.root, settings, project_id, ffmpeg, progress)
        rejected = result.get("rejected") or []
        kept = 0
        if rejected:
            self._update(project_id, stage=f"AI checker: finding new footage for {len(rejected)} wrong clips")
            before = {str(scene["id"]): str(scene.get("selected_asset_id") or "") for scene in self.app.db.list_scenes(project_id)}
            self.app.youtube_auto.start(project_id, rejected, force=True, exclude_current=True)
            time.sleep(1)
            while self.app.youtube_auto.status(project_id).get("running"):
                time.sleep(2)
            # Never worse than before: a scene that found no other REAL footage keeps its first clip
            # (an AI image or an empty scene would be a bigger miss than a near-fit clip).
            assets = {str(asset["id"]): asset for asset in self.app.db.list_assets(project_id)}
            for scene in self.app.db.list_scenes(project_id):
                scene_id = str(scene["id"])
                if scene_id not in rejected or not before.get(scene_id):
                    continue
                now = assets.get(str(scene.get("selected_asset_id") or ""), {})
                if now.get("provider") not in ("youtube", "photo") and before[scene_id] in assets:
                    self.app.db.select_asset(scene_id, before[scene_id])
                    kept += 1
        result["kept_first_clip"] = kept
        self._update(project_id, stage="Filling missing scenes")
        try:
            logs = self.app.paths.root / "logs"
            logs.mkdir(exist_ok=True)
            (logs / f"checker-{project_id}.json").write_text(json.dumps(result, indent=1))
        except OSError:
            pass
        return {"checked": result.get("checked", 0), "rejected": len(rejected), "kept_first_clip": kept,
                "error": result.get("error", "")}

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
        from .channel_kits import kit_for

        country = str(kit_for(self.app.paths.root, (project.get("effects") or {}).get("channel_style") or "v3").get("country") or "US")
        kit = kit_for(self.app.paths.root, (project.get("effects") or {}).get("channel_style") or "v3")
        labels = kit.get("chapter_style") == "name_label"  # headings are ordinary shots there
        # The opening (everything before the first heading) never gets an AI image: a hook scene that found no
        # real footage stays empty and is listed (the V2 test got one AI image in its hook here).
        first_heading = next((index for index, scene in enumerate(scenes)
                              if heading_subject(str(scene.get("narration") or ""))), 0)
        hook = {str(scene["id"]) for scene in scenes[:first_heading]}
        todo = [(scene, subject) for scene, subject in zip(scenes, subjects)
                if not scene.get("selected_asset_id") and str(scene["id"]) not in hook
                and (labels or not heading_subject(str(scene.get("narration") or "")))]

        def fill(pair: tuple[dict[str, Any], str]) -> None:
            scene, subject = pair
            position = int(scene.get("position") or 0)
            destination = (self.app.paths.project_dir(project_id) / "assets" / "stills"
                           / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.jpg")
            try:
                metadata = generate_vintage_still(settings, str(scene.get("narration") or ""), subject, era, destination,
                                                  country=country)
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
