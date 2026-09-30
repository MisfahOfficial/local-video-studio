from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Protocol

from .domain import SceneDraft
from .footage_match import heading_subject
from .scene_planner import (
    _classify_emotion, _compose_prompt, _importance, _is_pop_insert, _narrative_role, _sentences,
    _timeline_actions, _visual_subject, _word_count, scene_duration_limit,
)
from .themes import get_theme
from .transcription import FasterWhisperTranscriber


# No visual stays on screen longer than this; long sentences get several clips.
MAX_SCENE_SECONDS = 7.0


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]: ...


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def _timed_words(segments: list[dict[str, Any]]) -> list[tuple[str, float, float]]:
    """Flatten Whisper segments into normalized (token, start, end) triples."""
    timed: list[tuple[str, float, float]] = []
    for segment in segments:
        for word in segment.get("words") or []:
            start, end = float(word.get("start") or 0), float(word.get("end") or 0)
            for token in _tokens(str(word.get("word") or "")):
                timed.append((token, start, max(start, end)))
    return timed


def _match(script_tokens: list[str], words: list[tuple[str, float, float]]) -> dict[int, tuple[float, float]]:
    matched: dict[int, tuple[float, float]] = {}
    matcher = difflib.SequenceMatcher(None, script_tokens, [word[0] for word in words], autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            _token, start, end = words[block.b + offset]
            matched[block.a + offset] = (start, end)
    return matched


def split_long_sentences(
    sentences: list[str], spans: list[tuple[float, float]], segments: list[dict[str, Any]], max_seconds: float,
) -> list[tuple[str, float, float]]:
    """Units of at most `max_seconds`: long sentences are cut at a comma or the
    longest spoken pause, so every visual stays short and cuts land between words."""
    words = _timed_words(segments)
    script_tokens = [token for sentence in sentences for token in _tokens(sentence)]
    matched = _match(script_tokens, words)
    units: list[tuple[str, float, float]] = []
    cursor = 0
    for sentence, (sentence_start, sentence_end) in zip(sentences, spans):
        pieces = sentence.split()
        times: list[tuple[float | None, float | None]] = []
        for piece in pieces:
            count = len(_tokens(piece))
            hits = [matched[index] for index in range(cursor, cursor + count) if index in matched]
            cursor += count
            times.append((hits[0][0], hits[-1][1]) if hits else (None, None))
        if sentence_end - sentence_start <= max_seconds or len(pieces) < 4:
            units.append((sentence, sentence_start, sentence_end))
            continue
        # Words Whisper missed get times spread evenly across the sentence.
        filled: list[tuple[float, float]] = []
        step = (sentence_end - sentence_start) / len(pieces)
        for index, (start, end) in enumerate(times):
            guess = sentence_start + index * step
            filled.append((start if start is not None else guess, end if end is not None else guess + step))
        first = 0
        while first < len(pieces):
            chunk_start = sentence_start if first == 0 else filled[first][0]
            last = first
            while last + 1 < len(pieces) and filled[last + 1][1] - chunk_start <= max_seconds:
                last += 1
            if last == len(pieces) - 1:
                cut = len(pieces)
            else:
                # Prefer a comma/dash, then the longest pause, in the chunk's back half.
                options = range(max(first + 2, first + (last - first) // 2), last + 1)
                cut = max(
                    options or [last + 1],
                    key=lambda index: (
                        pieces[index - 1].endswith((",", ";", ":", "\u2014", "-")),
                        filled[index][0] - filled[index - 1][1],
                    ),
                )
            chunk_end = sentence_end if cut == len(pieces) else (filled[cut - 1][1] + filled[cut][0]) / 2
            units.append((" ".join(pieces[first:cut]), chunk_start, chunk_end))
            first = cut
    return units


def align_sentences(sentences: list[str], segments: list[dict[str, Any]], duration: float) -> list[tuple[float, float]]:
    """Return the spoken (start, end) of every script sentence.

    Script words are matched to Whisper's timed words; sentences with no matched
    word (misheard names, numbers read aloud) are interpolated from neighbours.
    """
    words = _timed_words(segments)
    if not words:
        raise ValueError("Whisper did not hear any speech in the voice-over.")
    script_tokens: list[str] = []
    owner: list[int] = []
    for index, sentence in enumerate(sentences):
        for token in _tokens(sentence):
            script_tokens.append(token)
            owner.append(index)

    matched = _match(script_tokens, words)

    spans: list[tuple[float, float] | None] = [None] * len(sentences)
    for position, index in enumerate(owner):
        if position in matched:
            start, end = matched[position]
            spans[index] = (spans[index][0], end) if spans[index] else (start, end)

    _fill_unmatched(spans, sentences, duration)
    result: list[tuple[float, float]] = []
    previous_end = 0.0
    for span in spans:
        assert span is not None
        start = min(max(span[0], previous_end), duration)
        end = min(max(span[1], start), duration)
        result.append((start, end))
        previous_end = end
    return result


def _fill_unmatched(spans: list[tuple[float, float] | None], sentences: list[str], duration: float) -> None:
    index = 0
    while index < len(spans):
        if spans[index] is not None:
            index += 1
            continue
        run_end = index
        while run_end < len(spans) and spans[run_end] is None:
            run_end += 1
        left = spans[index - 1][1] if index > 0 and spans[index - 1] else 0.0
        right = spans[run_end][0] if run_end < len(spans) and spans[run_end] else duration
        counts = [_word_count(sentences[item]) for item in range(index, run_end)]
        total = sum(counts)
        cursor = left
        for item, count in zip(range(index, run_end), counts):
            length = max(0.0, right - left) * count / total
            spans[item] = (cursor, cursor + length)
            cursor += length
        index = run_end


def _group_by_pacing(sentences: list[str], spans: list[tuple[float, float]]) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for index, (start, end) in enumerate(spans):
        # A list heading is always its own scene: it becomes the chapter card.
        if _is_pop_insert(sentences[index], end - start) or heading_subject(sentences[index]):
            if current:
                groups.append(current)
                current = []
            groups.append([index])
            continue
        if current and end - spans[current[0]][0] > min(MAX_SCENE_SECONDS, scene_duration_limit(spans[current[0]][0])):
            groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)
    return groups


def _group_by_target(spans: list[tuple[float, float]], target: int) -> list[list[int]]:
    """Split sentences into `target` contiguous groups of roughly equal spoken time."""
    if target >= len(spans):
        return [[index] for index in range(len(spans))]
    origin = spans[0][0]
    total = (spans[-1][1] - origin) or 1.0
    groups: list[list[int]] = []
    current: list[int] = []
    for index, (_start, end) in enumerate(spans):
        current.append(index)
        groups_after = target - len(groups) - 1
        remaining = len(spans) - index - 1
        boundary = origin + total * (len(groups) + 1) / target
        if groups_after > 0 and (end >= boundary or remaining == groups_after):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


class WhisperScenePlanner:
    """Free, offline VO sync: Whisper word timings plus the local scene director."""

    def __init__(self, transcriber: Transcriber | None = None, model_size: str = "small"):
        self.transcriber = transcriber or FasterWhisperTranscriber(model_size=model_size)

    def plan(
        self,
        *,
        script: str,
        voiceover_path: Path,
        duration_seconds: float,
        theme_id: str,
        target_scene_count: int | None = None,
    ) -> list[SceneDraft]:
        sentences = _sentences(script)
        if not sentences:
            raise ValueError("The script is empty")
        segments = self.transcriber.transcribe(voiceover_path)
        sentence_spans = align_sentences(sentences, segments, duration_seconds)
        units = split_long_sentences(sentences, sentence_spans, segments, MAX_SCENE_SECONDS)
        sentences = [text for text, _start, _end in units]
        spans = [(start, end) for _text, start, end in units]
        groups = _group_by_target(spans, target_scene_count) if target_scene_count else _group_by_pacing(sentences, spans)

        # Cut between scenes in the middle of the pause, so every visual change
        # lands between spoken sentences and the timeline stays gapless.
        boundaries = [0.0]
        for previous, following in zip(groups, groups[1:]):
            boundaries.append((spans[previous[-1]][1] + spans[following[0]][0]) / 2)
        boundaries.append(duration_seconds)

        theme = get_theme(theme_id)
        drafts: list[SceneDraft] = []
        for position, group in enumerate(groups, start=1):
            chunk = " ".join(sentences[index] for index in group)
            start, end = boundaries[position - 1], boundaries[position]
            emotion = _classify_emotion(chunk)
            role = _narrative_role(chunk, position, len(groups), emotion)
            pop_insert = len(group) == 1 and _is_pop_insert(chunk, spans[group[0]][1] - spans[group[0]][0])
            drafts.append(SceneDraft(
                position=position,
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                narration=chunk,
                visual_subject=_visual_subject(chunk),
                emotion=emotion,
                narrative_role=role,
                importance=_importance(role, emotion),
                prompt=_compose_prompt(chunk, emotion, theme_id, position, pop_insert=pop_insert),
                negative_prompt=theme.negative_prompt,
                model_role="photoreal",
                candidate_count=1,
                timeline_actions=_timeline_actions(emotion, pop_insert=pop_insert),
            ))
        return drafts


def voice_times(narrations: list[str], segments: list[dict[str, Any]], duration: float,
                minimum: float = 0.25) -> list[tuple[float, float]]:
    """(start, end) for existing scenes from the spoken words, footage untouched: each scene starts where
    its narration is heard and cuts land in the middle of the pause. Stretching a whole timeline evenly
    to the voice-over drifted 10-13 s by the middle of a 45-minute video (V3 test)."""
    spans = align_sentences(narrations, segments, duration)
    bounds = [0.0] + [(left[1] + right[0]) / 2 for left, right in zip(spans, spans[1:])] + [duration]
    times: list[tuple[float, float]] = []
    for index in range(len(narrations)):
        start = times[-1][1] if times else 0.0
        room = duration - minimum * (len(narrations) - index - 1)  # leave the rest their minimum
        end = min(max(bounds[index + 1], start + minimum), room) if index < len(narrations) - 1 else duration
        times.append((round(start, 3), round(max(end, start + minimum), 3)))
    return times


class CachedTranscriber:
    """Whisper once per voice-over: the words are saved next to it, so re-syncing is instant."""

    def __init__(self, cache: Path, inner: Transcriber | None = None):
        self.cache = cache
        self.inner = inner or FasterWhisperTranscriber()

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        import json

        stat = audio_path.stat()
        stamp = f"{audio_path.name}|{stat.st_size}|{int(stat.st_mtime)}"
        try:
            saved = json.loads(self.cache.read_text())
            if saved.get("stamp") == stamp:
                return saved["segments"]
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        segments = self.inner.transcribe(audio_path)
        try:
            self.cache.write_text(json.dumps({"stamp": stamp, "segments": segments}))
        except OSError:
            pass
        return segments
