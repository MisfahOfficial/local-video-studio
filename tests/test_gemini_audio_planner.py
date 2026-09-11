from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.gemini_audio_planner import GeminiAudioScenePlanner, GeminiFilesClient
from app.providers.base import ProviderError
from app.themes import get_theme


class FakeGeminiAudioClient:
    def __init__(self, items: list[dict[str, Any]]):
        self.items = items
        self.deleted: list[str] = []
        self.created: dict[str, str] = {}

    def upload(self, path: Path) -> dict[str, Any]:
        self.uploaded = path
        return {"name": "files/test-vo", "uri": "https://example.test/vo", "mimeType": "audio/mpeg"}

    def transcribe(self, *, file_uri: str, mime_type: str) -> dict[str, Any]:
        self.transcribed = {"file_uri": file_uri, "mime_type": mime_type}
        annotations = []
        words = "The old lunch counter opened before sunrise. Then the first steaming bowl reached the counter.".split()
        for index, word in enumerate(words):
            annotations.append({
                "type": "word_info", "text": word,
                "start_offset": f"{index * 0.6:.1f}s", "end_offset": f"{index * 0.6 + 0.5:.1f}s",
            })
        return {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "transcript", "annotations": annotations}]}]}

    def create_scene_plan(self, *, model: str, prompt: str) -> dict[str, Any]:
        self.created = {"model": model, "prompt": prompt}
        return {
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(self.items)}]}],
        }

    def delete_file(self, name: str) -> None:
        self.deleted.append(name)


def sample_items() -> list[dict[str, Any]]:
    return [
        {
            "position": 1,
            "start_seconds": 0.2,
            "end_seconds": 4.8,
            "narration": "The old lunch counter opened before sunrise.",
            "visual_subject": "A 1950s lunch-counter cook unlocking the chrome diner before sunrise",
            "emotion": "nostalgia",
            "narrative_role": "hook",
        },
        {
            "position": 2,
            "start_seconds": 4.9,
            "end_seconds": 10.1,
            "narration": "Then the first steaming bowl reached the counter.",
            "visual_subject": "A steaming ceramic soup bowl being placed on a chrome lunch counter",
            "emotion": "reveal",
            "narrative_role": "explanation",
        },
    ]


class GeminiAudioPlannerTests(unittest.TestCase):
    def test_transient_capacity_error_retries_then_uses_stable_fallback(self) -> None:
        completed = {
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "[]"}]}],
        }
        busy = ProviderError(
            'Provider returned HTTP 500: {"error":{"message":"gemini-3.7-flash is currently experiencing high demand"}}'
        )
        client = GeminiFilesClient("test-key")
        with (
            patch(
                "app.gemini_audio_planner.request_json",
                side_effect=[busy, busy, busy, completed],
            ) as request,
            patch("app.gemini_audio_planner.time.sleep") as sleep,
            patch("app.gemini_audio_planner.random.uniform", return_value=0.0),
        ):
            result = client.create_scene_plan(model="gemini-3.7-flash", prompt="Plan this scene")

        self.assertEqual(result, completed)
        attempted_models = [call.kwargs["payload"]["model"] for call in request.call_args_list]
        self.assertEqual(
            attempted_models,
            ["gemini-3.7-flash", "gemini-3.7-flash", "gemini-3.7-flash", "gemini-3.8-flash"],
        )
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_non_transient_planning_error_is_not_retried(self) -> None:
        client = GeminiFilesClient("test-key")
        with patch(
            "app.gemini_audio_planner.request_json",
            side_effect=ProviderError("Provider returned HTTP 400: invalid request"),
        ) as request:
            with self.assertRaisesRegex(ProviderError, "HTTP 400"):
                client.create_scene_plan(model="gemini-3.7-flash", prompt="Plan this scene")
        self.assertEqual(request.call_count, 1)

    def test_long_projects_are_split_into_bounded_planning_batches(self) -> None:
        transcript = [{"start_seconds": 0, "end_seconds": 8940, "text": "complete transcript"}]
        batches = GeminiAudioScenePlanner._planning_batches(transcript, 8940, 715)
        self.assertEqual(sum(batch["count"] for batch in batches), 715)
        self.assertTrue(all(batch["count"] <= 80 for batch in batches))
        self.assertEqual(batches[0]["start_position"], 1)
        self.assertEqual(batches[-1]["start_position"] + batches[-1]["count"] - 1, 715)
        self.assertEqual(batches[-1]["window_end"], 8940)

    def test_real_audio_ranges_are_normalized_into_gapless_scenes(self) -> None:
        client = FakeGeminiAudioClient(sample_items())
        with tempfile.TemporaryDirectory() as temporary:
            voiceover = Path(temporary) / "voice.mp3"
            voiceover.write_bytes(b"test-audio")
            scenes = GeminiAudioScenePlanner("key", "gemini-3.7-flash", client=client).plan(
                script=(
                    "The old lunch counter opened before sunrise. "
                    "Then the first steaming bowl reached the counter."
                ),
                voiceover_path=voiceover,
                duration_seconds=10.0,
                target_scene_count=2,
                theme=get_theme("us_nostalgia"),
            )

        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0].start_seconds, 0.0)
        self.assertEqual(scenes[-1].end_seconds, 10.0)
        self.assertEqual(scenes[0].end_seconds, scenes[1].start_seconds)
        self.assertIn("lunch-counter cook", scenes[0].prompt)
        self.assertNotIn("Visualize this narration", scenes[0].prompt)
        self.assertEqual(client.deleted, ["files/test-vo"])
        self.assertIn("Never distribute time evenly", client.created["prompt"])
        self.assertIn("TIMED VO TRANSCRIPT", client.created["prompt"])
        self.assertEqual(client.transcribed["mime_type"], "audio/mpeg")

    def test_wrong_scene_count_is_rejected_without_losing_remote_cleanup(self) -> None:
        client = FakeGeminiAudioClient(sample_items()[:1])
        with tempfile.TemporaryDirectory() as temporary:
            voiceover = Path(temporary) / "voice.mp3"
            voiceover.write_bytes(b"test-audio")
            with self.assertRaisesRegex(ProviderError, "instead of 2"):
                GeminiAudioScenePlanner("key", "gemini-3.7-flash", client=client).plan(
                    script="A complete two-scene script.",
                    voiceover_path=voiceover,
                    duration_seconds=10.0,
                    target_scene_count=2,
                    theme=get_theme("us_nostalgia"),
                )
        self.assertEqual(client.deleted, ["files/test-vo"])


if __name__ == "__main__":
    unittest.main()
