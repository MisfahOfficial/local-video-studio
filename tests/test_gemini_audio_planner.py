from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.gemini_audio_planner import GeminiAudioScenePlanner, GeminiFilesClient
from app.providers.base import ProviderError
from app.scene_planner import scene_duration_limit
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
        transcript = [
            {
                "start_seconds": index * 8.94,
                "end_seconds": (index + 1) * 8.94 - 0.2,
                "text": f"Complete sentence {index + 1}.",
            }
            for index in range(1000)
        ]
        batches = GeminiAudioScenePlanner._planning_batches(transcript, 8940, 715)
        self.assertEqual(sum(batch["count"] for batch in batches), 715)
        self.assertTrue(all(batch["count"] <= 80 for batch in batches))
        self.assertEqual(batches[0]["start_position"], 1)
        self.assertEqual(batches[-1]["start_position"] + batches[-1]["count"] - 1, 715)
        self.assertEqual(batches[-1]["window_end"], 8940)
        self.assertEqual(sum(len(batch["transcript"]) for batch in batches), len(transcript))
        self.assertTrue(
            all(
                left["transcript"][-1]["text"] != right["transcript"][0]["text"]
                for left, right in zip(batches, batches[1:])
            )
        )

    def test_image_target_cannot_force_mid_sentence_scene_changes(self) -> None:
        transcript = [
            {"start_seconds": 0, "end_seconds": 5, "text": "One complete spoken sentence."},
        ]
        with self.assertRaisesRegex(ProviderError, "1 complete spoken sentences or pauses, but 2 images"):
            GeminiAudioScenePlanner._planning_batches(transcript, 5, 2)

    def test_transcript_does_not_split_a_long_unfinished_sentence_by_word_count(self) -> None:
        words = [f"word{index}" for index in range(45)]
        annotations = [
            {
                "type": "word_info",
                "text": word + ("." if index == len(words) - 1 else ""),
                "start_offset": f"{index * 0.2:.1f}s",
                "end_offset": f"{index * 0.2 + 0.15:.2f}s",
            }
            for index, word in enumerate(words)
        ]
        response = {"steps": [{"content": [{"annotations": annotations}]}]}

        segments = GeminiAudioScenePlanner._extract_timed_transcript(response)

        self.assertEqual(len(segments), 1)
        self.assertIn("word44.", segments[0]["text"])

    def test_script_punctuation_restores_sentence_boundaries_missing_from_asr(self) -> None:
        spoken_words = "This is the first sentence This is the second sentence".split()
        annotations = [
            {
                "type": "word_info",
                "text": word,
                "start_offset": f"{index * 0.3:.1f}s",
                "end_offset": f"{index * 0.3 + 0.2:.1f}s",
            }
            for index, word in enumerate(spoken_words)
        ]
        response = {"steps": [{"content": [{"annotations": annotations}]}]}

        segments = GeminiAudioScenePlanner._extract_timed_transcript(
            response,
            script="This is the first sentence. This is the second sentence.",
        )

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["text"], "This is the first sentence.")
        self.assertEqual(segments[1]["text"], "This is the second sentence.")

    def test_mid_sentence_visual_boundary_is_rejected(self) -> None:
        transcript = [
            {"start_seconds": 0.0, "end_seconds": 5.0, "text": "The first sentence completes here."},
            {"start_seconds": 5.1, "end_seconds": 10.0, "text": "The second sentence completes here."},
        ]
        planned = sample_items()
        planned[0] = {**planned[0], "start_seconds": 0.0, "end_seconds": 2.0}
        planned[1] = {**planned[1], "start_seconds": 2.1, "end_seconds": 10.0}
        with self.assertRaisesRegex(ProviderError, "before a sentence was complete"):
            GeminiAudioScenePlanner._validate_batch_alignment(planned, transcript, 0.0, 10.0, 1)

    def test_small_timing_drift_snaps_to_complete_sentence_boundary(self) -> None:
        transcript = [
            {"start_seconds": 0.2, "end_seconds": 4.8, "text": "The old lunch counter opened before sunrise."},
            {"start_seconds": 5.0, "end_seconds": 10.0, "text": "Then the first steaming bowl reached the counter."},
        ]
        planned = sample_items()
        planned[0] = {**planned[0], "end_seconds": 4.6}
        planned[1] = {**planned[1], "start_seconds": 5.2}

        GeminiAudioScenePlanner._validate_batch_alignment(planned, transcript, 0.0, 10.0, 1)

        self.assertEqual(planned[0]["end_seconds"], 4.9)
        self.assertEqual(planned[1]["start_seconds"], 4.9)
        self.assertEqual(planned[0]["narration"], transcript[0]["text"])

    def test_visual_change_anywhere_in_a_long_silent_pause_is_valid(self) -> None:
        transcript = [
            {"start_seconds": 0.0, "end_seconds": 4.0, "text": "The first complete sentence ends."},
            {"start_seconds": 7.0, "end_seconds": 10.0, "text": "The second complete sentence begins."},
        ]
        planned = sample_items()
        planned[0] = {
            **planned[0],
            "start_seconds": 0.0,
            "end_seconds": 4.0,
            "narration": transcript[0]["text"],
        }
        planned[1] = {
            **planned[1],
            "start_seconds": 4.0,
            "end_seconds": 10.0,
            "narration": transcript[1]["text"],
        }

        GeminiAudioScenePlanner._validate_batch_alignment(planned, transcript, 0.0, 10.0, 1)

        self.assertEqual(planned[0]["end_seconds"], 5.5)
        self.assertEqual(planned[1]["start_seconds"], 5.5)

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
        self.assertIn("Exact spoken moment", scenes[0].prompt)
        self.assertNotIn("Visualize this narration", scenes[0].prompt)
        self.assertEqual(client.deleted, ["files/test-vo"])
        self.assertIn("Never distribute time evenly", client.created["prompt"])
        self.assertIn("TIMED VO TRANSCRIPT", client.created["prompt"])
        self.assertEqual(client.transcribed["mime_type"], "audio/mpeg")

    def test_invalid_provider_range_is_repaired_without_discarding_the_plan(self) -> None:
        items = sample_items()
        items[0] = {**items[0], "end_seconds": float("nan")}
        items[1] = {**items[1], "start_seconds": 7.0, "end_seconds": 6.0}

        scenes = GeminiAudioScenePlanner._build_drafts(
            items,
            duration=10.0,
            target=2,
            theme=get_theme("us_nostalgia"),
        )

        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0].start_seconds, 0.0)
        self.assertEqual(scenes[-1].end_seconds, 10.0)
        self.assertGreater(scenes[0].end_seconds, scenes[0].start_seconds)
        self.assertGreater(scenes[1].end_seconds, scenes[1].start_seconds)
        self.assertEqual(scenes[0].end_seconds, scenes[1].start_seconds)

    def test_auto_pacing_supplies_fixed_sentence_safe_scene_guides(self) -> None:
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
                target_scene_count=None,
                theme=get_theme("us_nostalgia"),
            )

        self.assertEqual(len(scenes), 2)
        self.assertIn("AUTO-PACED SCENE GUIDE", client.created["prompt"])
        self.assertIn("maximum of 5 seconds through minute 20", client.created["prompt"])

    def test_auto_pacing_isolates_a_two_second_pop_insert(self) -> None:
        transcript = [
            {"start_seconds": 0.0, "end_seconds": 4.0, "text": "The workshop opened before dawn."},
            {"start_seconds": 4.1, "end_seconds": 5.5, "text": "Look!"},
            {"start_seconds": 5.6, "end_seconds": 10.0, "text": "Every restored object waited on the table."},
        ]

        guides = GeminiAudioScenePlanner._adaptive_scene_guides(transcript, 10.0)

        self.assertEqual(len(guides), 3)
        self.assertTrue(guides[1]["pop_insert"])
        self.assertLessEqual(guides[1]["end_seconds"] - guides[1]["start_seconds"], 2.0)

    def test_auto_pacing_enforces_5_8_10_second_bands(self) -> None:
        transcript = [
            {
                "start_seconds": index * 2.0,
                "end_seconds": index * 2.0 + 1.8,
                "text": f"Complete documentary sentence number {index}.",
            }
            for index in range(1500)
        ]

        guides = GeminiAudioScenePlanner._adaptive_scene_guides(transcript, 50 * 60)

        self.assertEqual(len(guides), 510)
        self.assertTrue(all(
            guide["end_seconds"] - guide["start_seconds"]
            <= scene_duration_limit(guide["start_seconds"]) + 0.001
            for guide in guides
        ))
        self.assertEqual(sum(guide["start_seconds"] < 20 * 60 for guide in guides), 300)
        self.assertEqual(sum(20 * 60 <= guide["start_seconds"] < 40 * 60 for guide in guides), 150)
        self.assertEqual(sum(guide["start_seconds"] >= 40 * 60 for guide in guides), 60)

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

    def test_wrong_spoken_passage_is_rejected(self) -> None:
        transcript = [
            {"start_seconds": 0.0, "end_seconds": 4.8, "text": "The old lunch counter opened before sunrise."},
            {"start_seconds": 4.9, "end_seconds": 10.0, "text": "Then the first steaming bowl reached the counter."},
        ]
        wrong = sample_items()
        wrong[0] = {**wrong[0], "narration": "A spaceship crossed the distant moon at midnight."}
        with self.assertRaisesRegex(ProviderError, "wrong spoken passage"):
            GeminiAudioScenePlanner._validate_batch_alignment(wrong, transcript, 0.0, 10.0, 1)

    def test_incomplete_sentence_narration_is_rejected(self) -> None:
        transcript = [
            {
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "text": "The neighborhood baker opened the shop before sunrise every winter morning.",
            }
        ]
        incomplete = [{
            **sample_items()[0],
            "start_seconds": 0.0,
            "end_seconds": 5.0,
            "narration": "The neighborhood baker opened",
        }]
        with self.assertRaisesRegex(ProviderError, "incomplete spoken sentence"):
            GeminiAudioScenePlanner._validate_batch_alignment(incomplete, transcript, 0.0, 5.0, 1)

    def test_unrelated_image_subject_is_reanchored_to_timed_narration(self) -> None:
        transcript = [
            {"start_seconds": 0.0, "end_seconds": 4.8, "text": "The old lunch counter opened before sunrise."},
            {"start_seconds": 4.9, "end_seconds": 10.0, "text": "Then the first steaming bowl reached the counter."},
        ]
        wrong = sample_items()
        wrong[0] = {**wrong[0], "visual_subject": "A rocket orbiting a bright alien planet"}
        GeminiAudioScenePlanner._validate_batch_alignment(wrong, transcript, 0.0, 10.0, 1)
        self.assertEqual(wrong[0]["visual_subject"], wrong[0]["narration"])


if __name__ == "__main__":
    unittest.main()
