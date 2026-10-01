from __future__ import annotations

import unittest
from pathlib import Path

from app.whisper_planner import WhisperScenePlanner, _group_by_target, align_sentences


def _segments(timed_words: list[tuple[str, float, float]]) -> list[dict]:
    return [{"words": [{"word": word, "start": start, "end": end} for word, start, end in timed_words]}]


class FakeTranscriber:
    def __init__(self, segments: list[dict]):
        self.segments = segments

    def transcribe(self, audio_path: Path) -> list[dict]:
        return self.segments


SCRIPT = "In the 1970s, families shopped at the supermarket. Mom served a frozen TV dinner. Teenagers met at the drive-in."
WORDS = [
    ("In", 0.2, 0.3), ("the", 0.3, 0.4), ("1970s,", 0.4, 1.0), ("families", 1.0, 1.5), ("shopped", 1.5, 1.9),
    ("at", 1.9, 2.0), ("the", 2.0, 2.1), ("supermarket.", 2.1, 2.9),
    ("Mom", 3.6, 3.9), ("served", 3.9, 4.3), ("a", 4.3, 4.4), ("frozen", 4.4, 4.8), ("TV", 4.8, 5.1), ("dinner.", 5.1, 5.6),
    ("Teenagers", 6.4, 7.0), ("met", 7.0, 7.2), ("at", 7.2, 7.3), ("the", 7.3, 7.4), ("drive-in.", 7.4, 8.1),
]


class AlignmentTests(unittest.TestCase):
    def test_sentences_get_their_spoken_times(self) -> None:
        sentences = SCRIPT.split(". ")
        spans = align_sentences(sentences, _segments(WORDS), 9.0)
        self.assertEqual(spans[0], (0.2, 2.9))
        self.assertEqual(spans[1], (3.6, 5.6))
        self.assertEqual(spans[2], (6.4, 8.1))

    def test_misheard_sentence_is_interpolated_between_neighbours(self) -> None:
        sentences = ["Families shopped at the supermarket.", "Zyxwv qwerty.", "Teenagers met at the drive-in."]
        words = [(w, s, e) for w, s, e in WORDS if s < 3.0 or s >= 6.4]
        words = [("Families", 1.0, 1.5)] + words[4:]
        spans = align_sentences(sentences, _segments(words), 9.0)
        self.assertGreaterEqual(spans[1][0], spans[0][1])
        self.assertLessEqual(spans[1][1], spans[2][0])

    def test_no_speech_is_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not hear"):
            align_sentences(["Hello there."], [], 5.0)

    def test_target_grouping_is_contiguous_and_exact(self) -> None:
        spans = [(index * 2.0, index * 2.0 + 1.5) for index in range(10)]
        groups = _group_by_target(spans, 4)
        self.assertEqual(len(groups), 4)
        self.assertEqual([index for group in groups for index in group], list(range(10)))


class WhisperPlannerTests(unittest.TestCase):
    def test_plan_is_gapless_and_cuts_inside_pauses(self) -> None:
        planner = WhisperScenePlanner(transcriber=FakeTranscriber(_segments(WORDS)))
        drafts = planner.plan(script=SCRIPT, voiceover_path=Path("vo.wav"), duration_seconds=9.0, theme_id="us_nostalgia")
        self.assertEqual(drafts[0].start_seconds, 0.0)
        self.assertEqual(drafts[-1].end_seconds, 9.0)
        for previous, following in zip(drafts, drafts[1:]):
            self.assertEqual(previous.end_seconds, following.start_seconds)
        # Sentences 1+2 would run past the 5-second pacing limit, so the first cut
        # falls in the pause between 2.9s (end of sentence 1) and 3.6s.
        self.assertTrue(drafts[1].narration.startswith("Mom served"))
        self.assertAlmostEqual(drafts[0].end_seconds, 3.25, places=2)

    def test_manual_target_is_respected(self) -> None:
        planner = WhisperScenePlanner(transcriber=FakeTranscriber(_segments(WORDS)))
        drafts = planner.plan(
            script=SCRIPT, voiceover_path=Path("vo.wav"), duration_seconds=9.0,
            theme_id="us_nostalgia", target_scene_count=2,
        )
        self.assertEqual(len(drafts), 2)


    def test_long_sentence_is_split_at_comma_under_seven_seconds(self) -> None:
        words = [(f"w{index}{',' if index == 9 else ''}", index * 0.6, index * 0.6 + 0.5) for index in range(20)]
        script = " ".join(word for word, _start, _end in words) + "."
        planner = WhisperScenePlanner(transcriber=FakeTranscriber(_segments(words)))
        drafts = planner.plan(script=script, voiceover_path=Path("vo.wav"), duration_seconds=12.5, theme_id="us_nostalgia")
        self.assertGreater(len(drafts), 1)
        self.assertTrue(all(draft.end_seconds - draft.start_seconds <= 7.05 for draft in drafts))
        self.assertTrue(drafts[0].narration.endswith("w9,"))


if __name__ == "__main__":
    unittest.main()


class HeadingSceneTests(unittest.TestCase):
    def test_heading_is_its_own_scene(self) -> None:
        from app.whisper_planner import _group_by_pacing

        sentences = ["Chicken and Rice Casserole", "Chicken and Rice Casserole was the staple."]
        groups = _group_by_pacing(sentences, [(10.0, 11.5), (11.6, 13.0)])
        self.assertEqual(groups, [[0], [1]])


class VoiceTimesTest(unittest.TestCase):
    def test_scenes_start_on_their_spoken_words(self):
        from app.whisper_planner import voice_times

        words = [("Custard", 0.0), ("slices", 0.4), ("were", 5.0), ("loved", 5.3), ("Treacle", 9.0), ("tart", 9.4)]
        segments = [{"words": [{"word": word, "start": start, "end": start + 0.3} for word, start in words]}]
        times = voice_times(["Custard slices", "were loved", "Treacle tart"], segments, 12.0)
        self.assertAlmostEqual(times[1][0], (0.7 + 5.0) / 2, places=2)  # cut in the pause before "were"
        self.assertAlmostEqual(times[2][0], (5.6 + 9.0) / 2, places=2)
        self.assertEqual(times[-1][1], 12.0)
        self.assertTrue(all(end > start for start, end in times))


class EarlyListenTest(unittest.TestCase):
    def test_the_voice_over_is_heard_once_and_reused(self):
        import tempfile
        import threading as threads

        from app.whisper_planner import CachedTranscriber

        calls = []

        class Slow:
            def transcribe(self, _path):
                calls.append(1)
                return [{"words": [{"word": "hello", "start": 0.0, "end": 0.4}]}]

        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "voiceover.mp3"
            audio.write_bytes(b"x" * 10)
            listener = CachedTranscriber(Path(folder) / "transcript.json", Slow())
            early = threads.Thread(target=listener.transcribe_quietly, args=(audio,))
            early.start()
            segments = CachedTranscriber(Path(folder) / "transcript.json", Slow()).transcribe(audio)
            early.join()
            self.assertEqual(segments[0]["words"][0]["word"], "hello")
            self.assertEqual(len(calls), 1)
