from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StudioSettings:
    runware_api_key: str = ""
    together_api_key: str = ""
    gemini_api_key: str = ""
    runware_default_model: str = "rundiffusion:110@101"
    runware_precise_model: str = "runware:400@2"
    runware_premium_model: str = "alibaba:qwen-image@3.0"
    together_default_model: str = "black-forest-labs/FLUX.1-schnell-Free"
    gemini_model: str = "gemini-3.7-flash"
    width: int = 1344
    height: int = 768
    generation_concurrency: int = 4
    estimated_unit_cost: float = 0.0013
    max_project_cost: float = 3.0
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("runware_api_key", "together_api_key", "gemini_api_key"):
            data[f"{key}_set"] = bool(data.pop(key))
        return data


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> StudioSettings:
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}

        allowed = {field.name for field in fields(StudioSettings)}
        filtered = {key: value for key, value in data.items() if key in allowed}
        settings = StudioSettings(**filtered)
        settings.runware_api_key = os.getenv("RUNWARE_API_KEY", settings.runware_api_key)
        settings.together_api_key = os.getenv("TOGETHER_API_KEY", settings.together_api_key)
        settings.gemini_api_key = os.getenv("GEMINI_API_KEY", settings.gemini_api_key)
        return settings

    def update(self, changes: dict[str, Any]) -> StudioSettings:
        current = asdict(self.load())
        allowed = {field.name for field in fields(StudioSettings)}
        for key, value in changes.items():
            if key in allowed and value is not None:
                current[key] = value
        settings = StudioSettings(**current)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return settings
