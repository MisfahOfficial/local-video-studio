from __future__ import annotations

import json

from .domain import Emotion, NarrativeRole, SceneDraft, TimelineAction
from .providers.base import ProviderError
from .providers.http import post_json
from .themes import ThemePreset


SCENE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "position": {"type": "INTEGER"},
            "visual_subject": {"type": "STRING"},
            "emotion": {"type": "STRING", "enum": [item.value for item in Emotion]},
            "narrative_role": {"type": "STRING", "enum": [item.value for item in NarrativeRole]},
            "importance": {"type": "INTEGER"},
            "prompt": {"type": "STRING"},
            "motion": {"type": "STRING", "enum": ["static", "slow_push", "slow_pull", "pan_left", "pan_right", "detail_push", "pop_in"]},
            "model_role": {"type": "STRING", "enum": ["photoreal", "precise", "premium"]},
        },
        "required": ["position", "visual_subject", "emotion", "narrative_role", "importance", "prompt", "motion", "model_role"],
    },
}


class GeminiSceneEnhancer:
    endpoint_template = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key.strip()
        self.model = model

    def enhance(self, drafts: list[SceneDraft], theme: ThemePreset, batch_size: int = 25) -> list[SceneDraft]:
        if not self.api_key:
            raise ProviderError("Gemini API key is missing. Add it in Settings or use Local Free planning.")
        by_position = {draft.position: draft for draft in drafts}
        for start in range(0, len(drafts), batch_size):
            batch = drafts[start:start + batch_size]
            prompt = self._prompt(batch, theme)
            response = post_json(
                self.endpoint_template.format(model=self.model) + f"?key={self.api_key}",
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "response_mime_type": "application/json",
                        "response_schema": SCENE_SCHEMA,
                        "temperature": 0.35,
                    },
                },
                {},
                timeout=180,
            )
            try:
                text = response["candidates"][0]["content"]["parts"][0]["text"]
                enhanced = json.loads(text)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                raise ProviderError(f"Gemini returned an invalid scene plan: {response}") from error
            for item in enhanced:
                draft = by_position.get(int(item.get("position", 0)))
                if not draft:
                    continue
                draft.visual_subject = str(item["visual_subject"])
                draft.emotion = Emotion(item["emotion"])
                draft.narrative_role = NarrativeRole(item["narrative_role"])
                draft.importance = min(3, max(1, int(item["importance"])))
                draft.prompt = str(item["prompt"])
                # Smart direction must not silently increase spend. The editor may
                # opt into extra candidates or premium routing after review.
                draft.model_role = "photoreal"
                draft.candidate_count = 1
                draft.timeline_actions = [
                    TimelineAction(type="motion", params={"preset": item["motion"], "strength": 0.55}),
                    TimelineAction(type="transition", params={"preset": "fade", "duration": 0.32}),
                ]
        return drafts

    @staticmethod
    def _prompt(batch: list[SceneDraft], theme: ThemePreset) -> str:
        source = [
            {
                "position": draft.position,
                "start": draft.start_seconds,
                "end": draft.end_seconds,
                "narration": draft.narration,
                "current_prompt": draft.prompt,
            }
            for draft in batch
        ]
        return (
            "Act as a rigorous documentary visual director. Return one JSON item for every supplied scene, preserving positions. "
            "Do not change the narration or invent factual claims. Identify the visible subject, emotion and narrative role. "
            "Write a production-ready image prompt that shows the narration directly, stays historically plausible, and contains "
            "one coherent moment. Never request visible captions, logos or watermarks. Use importance 1 for normal explanation, "
            "2 for an emotional or important beat, and 3 only for hook, major reveal or climax. Use model_role photoreal normally, "
            "precise for complex layouts or exact object relationships, and premium only when cheaper models are likely to fail.\n\n"
            f"CHANNEL THEME\nStyle: {theme.visual_style}\nPalette: {theme.palette}\nCamera: {theme.camera_language}\n"
            f"Avoid: {theme.negative_prompt}\n\nSCENES\n{json.dumps(source, ensure_ascii=False)}"
        )
