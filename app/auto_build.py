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


LAST_RESORT_STILLS = 10  # AI stills beyond the style's limit, at most, for scenes nothing real could fill


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
            self.app.youtube_auto.start(project_id, force=True, check_inline=True)
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
            # Empty scenes, in Ishaq's order: a shot of the same item (mirrored / old TV), AI stills within the
            # style's limit, a real shot of another item of this video, and only then a few AI stills (a soup
            # video with six items YouTube has no footage of made 270 AI stills before this).
            held = self._hold_empty(project_id)
            filled, failed = self._fill_missing(project_id)
            held += self._hold_empty(project_id, any_item=True)
            forced, forced_failed = self._fill_missing(project_id, beyond_limit=True)
            filled, failed = filled + forced, [item for item in failed if item["scene"] not in forced] + forced_failed
            report = self.app.style_report(project_id)  # the editing style's rules, met or not (None without a style)
            if report is not None and forced:
                report["checks"].append({"rule": "AI images beyond the limit (no footage exists for these scenes)",
                                         "value": f"{len(forced)} (scenes {forced[:12]})", "ok": False})
            if report is not None and held:
                report["checks"].append({"rule": "Scenes reusing a shot of their item, mirrored or in an old TV (no footage found)",
                                         "value": f"{len(held)} (scenes {held[:12]})", "ok": True})
            self._update(project_id, running=False, stage="Done", filled=filled, fill_failed=failed, checker=checked,
                         style_report=report)
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
        """Most clips were already checked item by item while sourcing; the AI checker now looks at the rest
        (the hook, scenes sourced outside an item). A refused clip is sourced again once (its source excluded),
        and if nothing else is found the scene is emptied for the fallbacks: a refused clip never stays."""
        from .scene_checker import check_scenes

        settings = self.app.settings.load()
        ffmpeg = str(settings.ffmpeg_path or "ffmpeg")
        sourcing = self.app.youtube_auto.status(project_id)
        done = set(sourcing.get("checked_ids") or [])
        inline = list(sourcing.get("check_rejected") or [])
        self._update(project_id, stage="AI checker: looking at the remaining clips", step=3)

        def progress(checked: int, total: int) -> None:
            self._update(project_id, stage=f"AI checker: {checked}/{total} remaining clips looked at")

        result = check_scenes(self.app.db, self.app.paths.root, settings, project_id, ffmpeg, progress, skip_ids=done)
        result["error"] = result.get("error") or str(sourcing.get("check_error") or "")
        rejected = result.get("rejected") or []
        emptied = 0
        if rejected:
            self._update(project_id, stage=f"AI checker: finding new footage for {len(rejected)} wrong clips")
            before = {str(scene["id"]): str(scene.get("selected_asset_id") or "") for scene in self.app.db.list_scenes(project_id)}
            self.app.youtube_auto.start(project_id, rejected, force=True, exclude_current=True)
            time.sleep(1)
            while self.app.youtube_auto.status(project_id).get("running"):
                time.sleep(2)
            for scene in self.app.db.list_scenes(project_id):
                scene_id = str(scene["id"])
                if scene_id in rejected and before.get(scene_id) and str(scene.get("selected_asset_id") or "") == before[scene_id]:
                    self.app.db.update_scene(scene_id, {"selected_asset_id": None})
                    emptied += 1
            # The replacements are looked at once more; one refused again leaves the scene for the fallbacks.
            again = check_scenes(self.app.db, self.app.paths.root, settings, project_id, ffmpeg, only_ids=set(rejected))
            for scene_id in again.get("rejected") or []:
                self.app.db.update_scene(scene_id, {"selected_asset_id": None})
                emptied += 1
        result["notes"] = {**{str(key): value for key, value in (sourcing.get("check_notes") or {}).items()},
                           **{str(key): value for key, value in (result.get("notes") or {}).items()}}
        result["checked"] = int(result.get("checked") or 0) + len(done)
        result["rejected_while_sourcing"] = inline
        result["emptied_for_fallbacks"] = emptied
        self._update(project_id, stage="Filling missing scenes")
        try:
            logs = self.app.paths.root / "logs"
            logs.mkdir(exist_ok=True)
            (logs / f"checker-{project_id}.json").write_text(json.dumps(result, indent=1))
        except OSError:
            pass
        return {"checked": result["checked"], "rejected": len(rejected) + len(inline), "emptied": emptied,
                "error": result.get("error", "")}

    def _hold_empty(self, project_id: str, any_item: bool = False) -> list[int]:
        """A scene with no footage of its own (and no room for an AI image) reuses an earlier shot of the SAME
        item, changed so it does not read as a repeat: mirrored the first time, inside an old TV the second
        time (Ishaq, 3 Oct). A shot never loops: one shorter than the scene plays slower (down to half speed)
        instead. Before this the previous 2-4 s clip ran on past its end and the same seconds repeated.
        Marked for review; returns the positions."""
        from .reuse_look import VARIANTS
        from .transcription import probe_duration

        db = self.app.db
        settings = self.app.settings.load()
        ffmpeg = str(settings.ffmpeg_path or "ffmpeg")
        ffprobe = str(Path(ffmpeg).with_name("ffprobe")) if "/" in ffmpeg else "ffprobe"
        scenes = db.list_scenes(project_id)
        subjects = scene_subjects(scenes, "")
        assets = {str(asset["id"]): asset for asset in db.list_assets(project_id)}
        clips = {str(clip["scene_id"]): clip for clip in db.list_timeline_clips(project_id)}
        lengths: dict[str, float] = {}
        reuses: dict[str, int] = {}  # donor asset -> times it already came back
        for asset in assets.values():
            donor = str((asset.get("metadata") or {}).get("reused_asset") or "")
            if donor:
                reuses[donor] = reuses.get(donor, 0) + 1

        def length(asset: dict[str, Any]) -> float:
            key = str(asset["id"])
            if key not in lengths:
                try:
                    lengths[key] = probe_duration(Path(str(asset["local_path"])), ffprobe)
                except Exception:
                    lengths[key] = 0.0
            return lengths[key]

        held: list[int] = []
        for index, scene in enumerate(scenes):
            if scene.get("selected_asset_id"):
                continue
            duration = float(scene["end_seconds"]) - float(scene["start_seconds"])
            options = []
            for other_index, other in enumerate(scenes):
                # any_item: a shot of another item of the same video (a different soup for a soup with no
                # footage at all) is still real footage, and better than an AI still.
                if other_index == index or (subjects[other_index] != subjects[index] and not any_item):
                    continue
                asset = assets.get(str(other.get("selected_asset_id") or "")) or {}
                metadata = asset.get("metadata") or {}
                # Only a shot the AI checker approved comes back (a refused or unchecked stock shot of another
                # pie spread to three scenes in the 3 Oct test); unchecked YouTube clips only when no checker ran.
                approved = (metadata.get("checker") == "ok" or metadata.get("chosen_by") == "gemini"
                            or (asset.get("provider") == "youtube" and "checker" not in metadata))
                if (asset.get("media_kind") != "video" or asset.get("provider") not in ("youtube", "stock")
                        or metadata.get("variant") or not approved or not Path(str(asset.get("local_path"))).is_file()):
                    continue
                count = reuses.get(str(asset["id"]), 0)
                if count >= len(VARIANTS):
                    continue
                start = float((clips.get(str(other["id"])) or {}).get("source_in_seconds") or 0)
                room = length(asset) - start
                if room < duration * 0.5:
                    continue
                neighbour = abs(other_index - index) == 1
                options.append(((neighbour, count, room < duration, -abs(other_index - index)), other, asset, start, room, neighbour))
            if not options:
                continue
            options = [option for option in options if not option[5]]  # never right next to its original
            if not options:
                continue
            _rank, other, asset, start, room, neighbour = min(options, key=lambda item: item[0])
            count = reuses.get(str(asset["id"]), 0)
            variant = VARIANTS[min(count, len(VARIANTS) - 1)]
            copy = db.add_asset(
                project_id=project_id, scene_id=str(scene["id"]),
                candidate_index=db.next_asset_candidate_index(str(scene["id"])),
                media_kind="video", provider=str(asset["provider"]), model=str(asset.get("model") or ""),
                local_path=str(asset["local_path"]), remote_url=asset.get("remote_url"),
                provider_asset_id=asset.get("provider_asset_id"), cost=0.0,
                metadata={**(asset.get("metadata") or {}), "held_from_scene": int(other.get("position") or 0),
                          "reused_asset": str(asset["id"]), "variant": variant,
                          "speed": round(min(1.0, room / max(duration, 0.1)), 3), "needs_review": True},
            )
            reuses[str(asset["id"])] = count + 1
            db.select_asset(str(scene["id"]), str(copy["id"]))
            mine = clips.get(str(scene["id"]))
            if mine:
                db.set_timeline_clip_duration(str(mine["id"]), float(mine["end_seconds"]) - float(mine["start_seconds"]), start)
            assets[str(copy["id"])] = copy
            scene["selected_asset_id"] = copy["id"]
            held.append(int(scene.get("position") or 0))
        return held

    def _fill_missing(self, project_id: str, beyond_limit: bool = False) -> tuple[list[int], list[dict[str, Any]]]:
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
        extra = ""
        try:  # the channel's editing style: its AI limit, and what AI images must never show
            from .editing_style import ai_image_rules, footage_rules, load_style

            style_doc = load_style(self.app.paths.root, (project.get("effects") or {}).get("channel_style"))
            if style_doc:
                extra = ai_image_rules(style_doc)
            if beyond_limit:
                for scene, _subject in todo[LAST_RESORT_STILLS:]:
                    failed.append({"scene": int(scene.get("position") or 0), "error": "Needs footage: no real shot exists"})
                todo = todo[:LAST_RESORT_STILLS]
            if style_doc and not beyond_limit:
                assets = {str(asset["id"]): asset for asset in db.list_assets(project_id)}
                filmable = [scene for scene in scenes if not heading_subject(str(scene.get("narration") or "")) or labels]
                made = sum(1 for scene in scenes
                           if (assets.get(str(scene.get("selected_asset_id") or "")) or {}).get("provider") == "generated")
                room = max(0, int(footage_rules(style_doc)["max_ai_share"] * len(filmable) + 1e-9) - made)
                for scene, _subject in todo[room:]:
                    failed.append({"scene": int(scene.get("position") or 0),
                                   "error": "Needs footage: the style's AI image limit is reached"})
                todo = todo[:room]
        except Exception:
            pass

        def fill(pair: tuple[dict[str, Any], str]) -> None:
            scene, subject = pair
            position = int(scene.get("position") or 0)
            destination = (self.app.paths.project_dir(project_id) / "assets" / "stills"
                           / f"scene-{position:04d}-{uuid.uuid4().hex[:8]}.jpg")
            try:
                metadata = generate_vintage_still(settings, str(scene.get("narration") or ""), subject, era, destination,
                                                  country=country, extra=extra)
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
