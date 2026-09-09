from __future__ import annotations

import hashlib
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from .config import SettingsStore, StudioSettings
from .database import Database
from .domain import GenerationRequest, MediaKind
from .paths import AppPaths
from .providers import MockImageProvider, ProviderError, ProviderRegistry, RunwareImageProvider, TogetherImageProvider


class GenerationManager:
    def __init__(self, db: Database, paths: AppPaths, settings_store: SettingsStore):
        self.db = db
        self.paths = paths
        self.settings_store = settings_store
        self._threads: dict[str, threading.Thread] = {}
        self._pause: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _registry(settings: StudioSettings) -> ProviderRegistry:
        return ProviderRegistry(
            [
                MockImageProvider(),
                RunwareImageProvider(settings.runware_api_key),
                TogetherImageProvider(settings.together_api_key),
            ]
        )

    def start(self, project_id: str) -> None:
        with self._lock:
            thread = self._threads.get(project_id)
            if thread and thread.is_alive():
                self._pause.setdefault(project_id, threading.Event()).clear()
                return
            pause = self._pause.setdefault(project_id, threading.Event())
            pause.clear()
            thread = threading.Thread(target=self._run_project, args=(project_id,), daemon=True)
            self._threads[project_id] = thread
            thread.start()

    def pause(self, project_id: str) -> None:
        self._pause.setdefault(project_id, threading.Event()).set()

    def resume(self, project_id: str) -> None:
        self._pause.setdefault(project_id, threading.Event()).clear()
        self.start(project_id)

    def is_running(self, project_id: str) -> bool:
        thread = self._threads.get(project_id)
        return bool(thread and thread.is_alive())

    def _run_project(self, project_id: str) -> None:
        settings = self.settings_store.load()
        registry = self._registry(settings)
        pause = self._pause.setdefault(project_id, threading.Event())
        futures: set[Future[None]] = set()
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(12, settings.generation_concurrency))) as executor:
                while not pause.is_set():
                    project = self.db.get_project(project_id)
                    if not project or float(project["actual_cost"]) >= settings.max_project_cost:
                        break
                    job = self.db.claim_generation_job(project_id)
                    if not job:
                        break
                    futures.add(executor.submit(self._process_job, job, registry, settings))
                    if len(futures) >= settings.generation_concurrency:
                        completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                        for future in completed:
                            future.result()
                if futures:
                    for future in futures:
                        future.result()
        finally:
            with self._lock:
                self._threads.pop(project_id, None)

    def _process_job(self, job: dict[str, object], registry: ProviderRegistry, settings: StudioSettings) -> None:
        scene_id = str(job["scene_id"])
        scene = self.db.get_scene(scene_id)
        if not scene:
            self.db.finish_generation_job(str(job["id"]), "failed", "Scene no longer exists")
            return
        try:
            media_kind = MediaKind(scene["media_kind"])
            provider_name = str(scene["provider"])
            request = GenerationRequest(
                prompt=str(scene["prompt"]),
                negative_prompt=str(scene["negative_prompt"]),
                model=self._model_for_scene(provider_name, str(scene["model_role"]), settings),
                width=settings.width,
                height=settings.height,
                seed=self._seed(scene_id, int(job["candidate_index"])),
                steps=4,
                media_kind=media_kind,
                metadata={"scene_id": scene_id, "candidate_index": int(job["candidate_index"])},
            )
            asset = self._generate_with_fallback(provider_name, request, registry, settings)
            project_dir = self.paths.project_dir(str(job["project_id"]))
            asset_dir = project_dir / "assets"
            asset_dir.mkdir(parents=True, exist_ok=True)
            filename = f"scene-{int(scene['position']):04d}-candidate-{int(job['candidate_index']) + 1}.{asset.extension}"
            destination = asset_dir / filename
            destination.write_bytes(asset.content)
            self.db.add_asset(
                project_id=str(job["project_id"]),
                scene_id=scene_id,
                candidate_index=int(job["candidate_index"]),
                media_kind=media_kind.value,
                provider=asset.provider,
                model=asset.model,
                local_path=str(destination),
                remote_url=asset.remote_url,
                provider_asset_id=asset.provider_asset_id,
                cost=asset.cost,
                metadata=asset.metadata,
            )
            self.db.finish_generation_job(str(job["id"]), "complete")
        except Exception as error:
            self.db.finish_generation_job(str(job["id"]), "failed", str(error))
            self.db.update_scene(scene_id, {"generation_status": "failed"})

    @staticmethod
    def _model_for_scene(provider: str, role: str, settings: StudioSettings) -> str:
        if provider == "mock":
            return "offline-placeholder"
        if provider == "together":
            return settings.together_default_model
        if role == "premium":
            return settings.runware_premium_model
        if role == "precise":
            return settings.runware_precise_model
        return settings.runware_default_model

    @staticmethod
    def _seed(scene_id: str, candidate_index: int) -> int:
        digest = hashlib.sha256(f"{scene_id}:{candidate_index}".encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF

    @staticmethod
    def _generate_with_fallback(provider_name: str, request: GenerationRequest,
                                registry: ProviderRegistry, settings: StudioSettings):
        primary = registry.get(provider_name, request.media_kind)
        try:
            return primary.generate(request)
        except ProviderError:
            if provider_name != "runware" or not settings.together_api_key or request.media_kind != MediaKind.IMAGE:
                raise
            fallback_request = GenerationRequest(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                model=settings.together_default_model,
                width=request.width,
                height=request.height,
                seed=request.seed,
                steps=request.steps,
                media_kind=request.media_kind,
                metadata={**request.metadata, "fallback_from": "runware"},
            )
            return registry.get("together", request.media_kind).generate(fallback_request)
