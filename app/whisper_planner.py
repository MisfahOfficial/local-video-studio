from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Protocol

from .domain import SceneDraft
from .scene_planner import (
    _classify_emotion, _compose_prompt, _importance, _is_pop_insert, _narrative_role, _sentences,
    _timeline_actions, _visual_subject, _word_count, scene_duration_limit,
)
from .themes import get_theme
from .transcription import FasterWhisperTranscriber


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

    matched: dict[int, tuple[float, float]] = {}
    matcher = difflib.SequenceMatcher(None, script_tokens, [word[0] for word in words], autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            _token, start, end = words[block.b + offset]
            matched[block.a + offset] = (start, end)

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
        if _is_pop_insert(sentences[index], end - start):
            if current:
                groups.append(current)
                current = []
            groups.append([index])
            continue
        if current and end - spans[current[0]][0] > scene_duration_limit(spans[current[0]][0]):
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
        spans = align_sentences(sentences, segments, duration_seconds)
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
