"""Claude as the final judge of sourced media.

The free CLIP model narrows every scene to a few candidates; Claude then looks at
all of them in one labelled contact sheet (one request per scene) and says which
really shows the sentence, and which carry a present-day face, burned-in text or
an AI look. About 1,000 image tokens per scene keeps a 2-minute video near 10-15¢.
"""
from __future__ import annotations

import base64
import io
import json
import string
import threading
from dataclasses import dataclass
from typing import Any

# (input, output) US$ per million tokens.
PRICES = {"claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0), "claude-opus-5": (5.0, 25.0)}
TILE = (320, 180)
LABEL_WIDTH = 44
MAX_CANDIDATES = 4

SYSTEM = (
    "You choose B-roll for a faceless YouTube documentary channel about vintage American, British and "
    "Canadian home cooking and everyday life. The image holds labelled candidates, one per row: a video's "
    "frames from left to right, or a single photo. Judge every candidate against the NEED.\n"
    "- fits: true only when the pictures clearly show what the NEED describes: the right dish, ingredient, "
    "action or place. A different dish that merely looks similar is not a fit.\n"
    "- present_day_person: true when a present-day person's face is visible (a host, vlogger or cook "
    "looking at the camera, or any modern person whose face is clear). People in genuinely old film or "
    "photographs are fine; hands alone are fine.\n"
    "- text_overlay: true when captions, titles, subtitles or watermarks are burned into the picture. "
    "Printed product labels on packages do not count.\n"
    "- ai_generated: true when it looks AI-generated, rendered, or illustrated rather than photographed.\n"
    "- score: 0 to 10 for how well it fits the NEED and works as calm documentary B-roll.\n"
    "- reason: at most 12 words."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "fits": {"type": "boolean"},
                    "score": {"type": "integer"},
                    "present_day_person": {"type": "boolean"},
                    "text_overlay": {"type": "boolean"},
                    "ai_generated": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["label", "fits", "score", "present_day_person", "text_overlay", "ai_generated", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class Verdict:
    label: str
    fits: bool
    score: int
    present_day_person: bool
    text_overlay: bool
    ai_generated: bool
    reason: str

    @property
    def usable(self) -> bool:
        return self.fits and not (self.present_day_person or self.text_overlay or self.ai_generated)


def contact_sheet(candidates: list[list[Any]]) -> Any:
    """One labelled row per candidate (A, B, C ...), up to three frames each."""
    from PIL import Image, ImageDraw, ImageOps

    from .motion.engine import SANS_BOLD_FONTS, font as load_font

    columns = max(1, min(3, max((len(frames) for frames in candidates), default=1)))
    sheet = Image.new("RGB", (LABEL_WIDTH + columns * TILE[0], len(candidates) * TILE[1]), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    label_font = load_font(SANS_BOLD_FONTS, 30)
    for row, frames in enumerate(candidates):
        top = row * TILE[1]
        draw.text((10, top + TILE[1] // 2 - 18), string.ascii_uppercase[row], fill=(255, 220, 60), font=label_font)
        for column, frame in enumerate(frames[:columns]):
            tile = ImageOps.fit(frame.convert("RGB"), TILE) if len(frames) > 1 else ImageOps.pad(
                frame.convert("RGB"), (columns * TILE[0], TILE[1]), color=(18, 18, 18),
            )
            sheet.paste(tile, (LABEL_WIDTH + column * TILE[0], top))
            if len(frames) == 1:
                break
        draw.line([(0, top + TILE[1] - 1), (sheet.width, top + TILE[1] - 1)], fill=(60, 60, 60), width=2)
    return sheet


class ClaudeJudge:
    """Thread-safe; tracks what it spends. Any API trouble returns None so sourcing falls back to CLIP."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)
        self.model = model
        self.lock = threading.Lock()
        self.cost = 0.0
        self.calls = 0
        self.disabled = ""

    @classmethod
    def from_settings(cls, settings: Any) -> "ClaudeJudge | None":
        key = str(getattr(settings, "anthropic_api_key", "") or "").strip()
        if not key:
            return None
        try:
            return cls(key, str(getattr(settings, "judge_model", "") or "claude-sonnet-5"))
        except ImportError:
            return None

    def judge(self, need: str, candidates: list[list[Any]]) -> list[Verdict] | None:
        """Verdicts in candidate order, or None when Claude could not be asked."""
        import anthropic

        candidates = [frames for frames in candidates if frames][:MAX_CANDIDATES]
        if not candidates or self.disabled:
            return None
        buffer = io.BytesIO()
        contact_sheet(candidates).save(buffer, format="JPEG", quality=80)
        labels = ", ".join(string.ascii_uppercase[: len(candidates)])
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
                }},
                {"type": "text", "text": f"NEED: {need}\nCandidates: {labels}. Give one verdict per candidate."},
            ]}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        }
        if self.model.startswith("claude-sonnet"):
            request["thinking"] = {"type": "disabled"}  # a quick visual check; thinking only adds cost
        try:
            response = self.client.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            self.disabled = f"Claude judge off: {getattr(error, 'message', error)}"
            return None
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            return None
        price_in, price_out = PRICES.get(self.model, PRICES["claude-sonnet-5"])
        with self.lock:
            self.calls += 1
            self.cost += (response.usage.input_tokens * price_in + response.usage.output_tokens * price_out) / 1e6
        if response.stop_reason == "refusal":
            return None
        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            items = json.loads(text)["verdicts"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        by_label = {str(item.get("label", "")).strip().upper()[:1]: item for item in items if isinstance(item, dict)}
        verdicts = []
        for index in range(len(candidates)):
            item = by_label.get(string.ascii_uppercase[index])
            if item is None:
                verdicts.append(Verdict(string.ascii_uppercase[index], False, 0, False, False, False, "no verdict"))
                continue
            verdicts.append(Verdict(
                string.ascii_uppercase[index], bool(item.get("fits")), int(item.get("score") or 0),
                bool(item.get("present_day_person")), bool(item.get("text_overlay")),
                bool(item.get("ai_generated")), str(item.get("reason") or "")[:120],
            ))
        return verdicts


def best_usable(verdicts: list[Verdict] | None) -> list[int]:
    """Indexes of usable candidates, best first; [] when none passed."""
    if verdicts is None:
        return []
    order = [index for index, verdict in enumerate(verdicts) if verdict.usable]
    return sorted(order, key=lambda index: -verdicts[index].score)
