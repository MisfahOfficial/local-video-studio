from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    database: Path
    projects: Path
    settings: Path
    static: Path

    @classmethod
    def resolve(cls, override: str | None = None) -> "AppPaths":
        if override:
            root = Path(override).expanduser().resolve()
        elif os.getenv("LOCAL_VIDEO_STUDIO_HOME"):
            root = Path(os.environ["LOCAL_VIDEO_STUDIO_HOME"]).expanduser().resolve()
        elif sys.platform == "win32":
            root = Path(os.getenv("LOCALAPPDATA", Path.home())) / "LocalVideoStudio"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "LocalVideoStudio"
        else:
            root = Path.home() / ".local" / "share" / "local-video-studio"

        package_root = Path(__file__).resolve().parent
        paths = cls(
            root=root,
            database=root / "studio.sqlite3",
            projects=root / "projects",
            settings=root / "settings.json",
            static=package_root / "static",
        )
        root.mkdir(parents=True, exist_ok=True)
        paths.projects.mkdir(parents=True, exist_ok=True)
        return paths

    def project_dir(self, project_id: str) -> Path:
        safe_id = "".join(char for char in project_id if char.isalnum() or char in "-_")
        path = (self.projects / safe_id).resolve()
        if self.projects.resolve() not in path.parents:
            raise ValueError("Invalid project identifier")
        path.mkdir(parents=True, exist_ok=True)
        return path

