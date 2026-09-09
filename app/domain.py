from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class Emotion(StrEnum):
    NOSTALGIA = "nostalgia"
    JOY = "joy"
    LOSS = "loss"
    SUSPENSE = "suspense"
    REVEAL = "reveal"
    URGENCY = "urgency"
    NEUTRAL = "neutral"


class NarrativeRole(StrEnum):
    HOOK = "hook"
    SETUP = "setup"
    EXPLANATION = "explanation"
    REVEAL = "reveal"
    TRANSITION = "transition"
    CLIMAX = "climax"
    CLOSING = "closing"


@dataclass(slots=True)
class ThemePreset:
    id: str
    name: str
    description: str
    visual_style: str
    palette: str
    camera_language: str
    negative_prompt: str
    default_motion: str = "slow_push"
    default_model_role: str = "photoreal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TimelineAction:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "params": self.params}


@dataclass(slots=True)
class SceneDraft:
    position: int
    start_seconds: float
    end_seconds: float
    narration: str
    visual_subject: str
    emotion: Emotion
    narrative_role: NarrativeRole
    importance: int
    prompt: str
    negative_prompt: str
    media_kind: MediaKind = MediaKind.IMAGE
    provider: str = "runware"
    model_role: str = "photoreal"
    candidate_count: int = 1
    timeline_actions: list[TimelineAction] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0.1, self.end_seconds - self.start_seconds)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["emotion"] = self.emotion.value
        data["narrative_role"] = self.narrative_role.value
        data["media_kind"] = self.media_kind.value
        data["timeline_actions"] = [item.to_dict() for item in self.timeline_actions]
        data["duration_seconds"] = self.duration_seconds
        return data


@dataclass(slots=True)
class GenerationRequest:
    prompt: str
    negative_prompt: str
    model: str
    width: int
    height: int
    seed: int | None = None
    steps: int = 4
    media_kind: MediaKind = MediaKind.IMAGE
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GeneratedAsset:
    content: bytes
    extension: str
    provider: str
    model: str
    cost: float = 0.0
    remote_url: str | None = None
    provider_asset_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

