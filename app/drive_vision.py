"""A visual index of the team Drive's footage, so a clip is found by what it shows,
not by its file name ("FlexClip_12" can be a perfect pecan-pie shot).

Built once in the background (a frame every two seconds, CLIP-embedded, saved per file),
then searched instantly for any dish or sentence.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .archive_source import drive_id

FRAME_STEP = 2.0
MIN_SIMILARITY = 0.26  # CLIP cosine: below this a frame rarely shows the words


class DriveVisualIndex:
    def __init__(self, root: Path):
        self.folder = root / "drive_vectors"
        self.manifest_path = self.folder / "manifest.json"
        self._lock = threading.Lock()
        self.manifest: dict[str, dict[str, Any]] = {}
        self._matrix: np.ndarray | None = None
        self._owners: list[str] = []
        self._times: list[float] = []
        try:
            self.manifest = json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            self.manifest = {}

    # ------------------------------------------------------------ building
    def build(self, files: list[dict[str, Any]], embed_images: Callable[[list[Any]], Any], ffmpeg_path: str = "ffmpeg",
              max_bytes: int = 60_000_000, deadline: float | None = None,
              progress: Callable[[int, int], None] | None = None) -> int:
        """Index every not-yet-indexed file up to `max_bytes` (one copy per name). Returns files added."""
        self.folder.mkdir(parents=True, exist_ok=True)
        todo, seen = [], set()
        for item in files:
            key = item["name"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            identifier = drive_id(item["path"])
            if identifier in self.manifest:
                continue
            try:
                size = Path(item["path"]).stat().st_size
            except OSError:
                continue
            if 0 < size <= max_bytes:
                todo.append((size, identifier, item))
        todo.sort(key=lambda entry: entry[0])  # small clips first: most shots per minute of download
        added = 0
        for done, (_size, identifier, item) in enumerate(todo, 1):
            if deadline and time.time() > deadline:
                break
            entry = self._index_file(identifier, item, embed_images, ffmpeg_path)
            with self._lock:
                self.manifest[identifier] = entry
                if done % 10 == 0 or done == len(todo):
                    self._save_manifest()
            added += entry.get("frames", 0) > 0
            if progress:
                progress(done, len(todo))
        with self._lock:
            self._save_manifest()
        return added

    def _index_file(self, identifier: str, item: dict[str, Any], embed_images: Callable[[list[Any]], Any],
                    ffmpeg_path: str) -> dict[str, Any]:
        from PIL import Image

        entry: dict[str, Any] = {"path": item["path"], "name": item["name"], "folder": item.get("folder", ""), "frames": 0}
        with tempfile.TemporaryDirectory() as folder:
            try:
                subprocess.run(
                    [ffmpeg_path, "-v", "error", "-i", item["path"], "-an", "-vf", f"fps=1/{FRAME_STEP},scale=224:-2",
                     "-q:v", "5", f"{folder}/%05d.jpg"], capture_output=True, timeout=900,
                )
            except (OSError, subprocess.SubprocessError) as error:
                entry["error"] = str(error)[:200]
                return entry
            frames = sorted(Path(folder).glob("*.jpg"))[:900]
            images = []
            for frame in frames:
                with Image.open(frame) as image:
                    images.append(image.convert("RGB"))
        if not images:
            entry["error"] = "no readable video"
            return entry
        vectors = np.asarray(embed_images(images), dtype=np.float16)
        np.save(self.folder / f"{identifier}.npy", vectors)
        entry["frames"] = int(vectors.shape[0])
        entry["duration"] = round(len(images) * FRAME_STEP, 1)
        return entry

    def _save_manifest(self) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.manifest))
        temporary.replace(self.manifest_path)
        self._matrix = None  # searches reload

    # ------------------------------------------------------------ searching
    def _load(self) -> None:
        if self._matrix is not None:
            return
        blocks, owners, times = [], [], []
        for identifier, entry in self.manifest.items():
            if not entry.get("frames"):
                continue
            try:
                vectors = np.load(self.folder / f"{identifier}.npy").astype(np.float32)
            except (OSError, ValueError):
                continue
            blocks.append(vectors)
            owners += [identifier] * len(vectors)
            times += [index * FRAME_STEP for index in range(len(vectors))]
        self._matrix = np.concatenate(blocks) if blocks else np.zeros((0, 512), np.float32)
        self._owners, self._times = owners, times

    def search(self, text_vector: Any, maximum: int = 6, minimum: float = MIN_SIMILARITY) -> list[dict[str, Any]]:
        """Files with frames that look like the text, best first."""
        with self._lock:
            self._load()
            if not len(self._matrix):
                return []
            query = np.asarray(text_vector, dtype=np.float32).reshape(-1)
            scores = self._matrix @ query
        best: dict[str, float] = {}
        for index in np.argsort(-scores)[:400]:
            score = float(scores[index])
            if score < minimum:
                break
            owner = self._owners[index]
            best[owner] = max(best.get(owner, 0.0), score)
        ranked = sorted(best.items(), key=lambda pair: pair[1], reverse=True)[:maximum]
        return [{
            "video_id": owner, "title": self.manifest[owner]["name"], "description": self.manifest[owner].get("folder", ""),
            "channel": "Team Drive", "local_path": self.manifest[owner]["path"], "source": "drive",
            "visual_score": round(score, 3),
        } for owner, score in ranked]

    def __len__(self) -> int:
        return sum(1 for entry in self.manifest.values() if entry.get("frames"))
