from __future__ import annotations

import math
import re
from collections.abc import Iterable

from .domain import Emotion, NarrativeRole, SceneDraft, TimelineAction
from .themes import get_theme


EMOTION_CUES: dict[Emotion, tuple[str, ...]] = {
    Emotion.NOSTALGIA: (
        "remember", "forgotten", "childhood", "once", "used to", "back then", "old-fashioned",
        "tradition", "grandmother", "grandma", "school days", "years ago",
    ),
    Emotion.JOY: (
        "joy", "smile", "laughed", "celebrate", "delicious", "comfort", "warm", "favorite",
        "favourite", "loved", "treat", "happy",
    ),
    Emotion.LOSS: (
        "vanished", "disappeared", "last", "closed", "lost", "never again", "gone", "forgotten",
        "ended", "final", "empty",
    ),
    Emotion.SUSPENSE: (
        "but", "however", "secret", "quietly", "unknown", "mystery", "what happened", "hidden",
        "until", "strange", "warning",
    ),
    Emotion.REVEAL: (
        "truth", "actually", "revealed", "turned out", "real reason", "in fact", "never was",
        "surprisingly", "discovered",
    ),
    Emotion.URGENCY: (
        "danger", "rushed", "immediately", "before it was too late", "threat", "panic", "urgent",
        "suddenly", "crisis",
    ),
}


EMOTION_DIRECTION: dict[Emotion, str] = {
    Emotion.NOSTALGIA: "warm practical light, gentle memory-like atmosphere, intimate human detail",
    Emotion.JOY: "open composition, candid warmth, brighter natural colour, inviting expressions",
    Emotion.LOSS: "restrained melancholy, negative space, cooler shadows against fading warm light",
    Emotion.SUSPENSE: "controlled shadows, isolated focal detail, cooler contrast, unanswered visual tension",
    Emotion.REVEAL: "decisive composition, visual contrast between expectation and evidence, crisp focal hierarchy",
    Emotion.URGENCY: "dynamic diagonals, tighter framing, directional light, believable motion and pressure",
    Emotion.NEUTRAL: "clear observational composition, balanced natural light, direct visual explanation",
}


MOTION_BY_EMOTION: dict[Emotion, str] = {
    Emotion.NOSTALGIA: "slow_push",
    Emotion.JOY: "pan_right",
    Emotion.LOSS: "slow_pull",
    Emotion.SUSPENSE: "detail_push",
    Emotion.REVEAL: "slow_push",
    Emotion.URGENCY: "pan_left",
    Emotion.NEUTRAL: "slow_push",
}


def _sentences(script: str) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", script.replace("\r", "\n"))
    normalized = re.sub(r"\n{2,}", "\n", normalized).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _word_count(text: str) -> int:
    return max(1, len(re.findall(r"\b[\w'-]+\b", text)))


def _split_longest(units: list[str], desired: int) -> list[str]:
    result = units[:]
    while len(result) < desired:
        candidate_index = max(range(len(result)), key=lambda index: _word_count(result[index]))
        words = result[candidate_index].split()
        if len(words) < 4:
            break
        middle = len(words) // 2
        result[candidate_index:candidate_index + 1] = [" ".join(words[:middle]), " ".join(words[middle:])]
    return result


def _group_units(units: list[str], desired: int) -> list[str]:
    if desired <= 0 or len(units) <= desired:
        return _split_longest(units, desired) if desired > len(units) else units

    total_words = sum(_word_count(unit) for unit in units)
    groups: list[str] = []
    cursor = 0
    used_words = 0
    for group_index in range(desired):
        groups_left = desired - group_index
        units_left = len(units) - cursor
        if groups_left == 1:
            groups.append(" ".join(units[cursor:]))
            break

        target_cumulative = total_words * (group_index + 1) / desired
        selected: list[str] = []
        while cursor < len(units) and units_left > groups_left:
            unit = units[cursor]
            projected = used_words + _word_count(unit)
            if selected and projected > target_cumulative:
                break
            selected.append(unit)
            used_words = projected
            cursor += 1
            units_left = len(units) - cursor
        if not selected and cursor < len(units):
            selected.append(units[cursor])
            used_words += _word_count(units[cursor])
            cursor += 1
        groups.append(" ".join(selected))
    return [group for group in groups if group]


def _classify_emotion(text: str) -> Emotion:
    lowered = text.casefold()
    scores = {
        emotion: sum(2 if " " in cue else 1 for cue in cues if cue in lowered)
        for emotion, cues in EMOTION_CUES.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else Emotion.NEUTRAL


def _narrative_role(text: str, position: int, count: int, emotion: Emotion) -> NarrativeRole:
    progress = position / max(1, count)
    lowered = text.casefold()
    if position <= max(2, math.ceil(count * 0.025)):
        return NarrativeRole.HOOK
    if position >= count - max(2, math.ceil(count * 0.025)):
        return NarrativeRole.CLOSING
    if emotion == Emotion.REVEAL or any(cue in lowered for cue in ("the truth", "turned out", "real reason")):
        return NarrativeRole.REVEAL
    if 0.72 <= progress <= 0.92 and emotion in {Emotion.REVEAL, Emotion.URGENCY, Emotion.LOSS}:
        return NarrativeRole.CLIMAX
    if any(lowered.startswith(cue) for cue in ("next", "meanwhile", "years later", "by then")):
        return NarrativeRole.TRANSITION
    return NarrativeRole.SETUP if progress < 0.15 else NarrativeRole.EXPLANATION


def _importance(role: NarrativeRole, emotion: Emotion) -> int:
    if role in {NarrativeRole.HOOK, NarrativeRole.REVEAL, NarrativeRole.CLIMAX}:
        return 3
    if emotion in {Emotion.LOSS, Emotion.SUSPENSE, Emotion.URGENCY, Emotion.JOY}:
        return 2
    return 1


def _visual_subject(text: str) -> str:
    cleaned = re.sub(r"^[#>*\-\d.\s]+", "", text).strip()
    words = cleaned.split()
    if len(words) > 32:
        cleaned = " ".join(words[:32]) + "…"
    return cleaned


def _era_hint(text: str) -> str:
    years = re.findall(r"\b(?:18|19|20)\d{2}s?\b", text)
    return f"historical period anchored to {years[0]}" if years else "period details inferred from the surrounding chapter"


def _compose_prompt(narration: str, emotion: Emotion, theme_id: str, position: int) -> str:
    theme = get_theme(theme_id)
    shot = "establishing wide shot" if position % 5 == 1 else "observational medium shot"
    if emotion in {Emotion.SUSPENSE, Emotion.REVEAL}:
        shot = "evidence-focused close detail with clear contextual background"
    elif emotion == Emotion.JOY:
        shot = "candid human medium-wide shot"
    elif emotion == Emotion.LOSS:
        shot = "quiet environmental composition with purposeful negative space"

    subject = _visual_subject(narration)
    return (
        f"Visualize this narration without adding unsupported facts: {subject}. "
        f"{_era_hint(narration)}. {EMOTION_DIRECTION[emotion]}. {shot}. "
        f"{theme.visual_style}. Palette: {theme.palette}. Camera: {theme.camera_language}. "
        "One coherent moment, realistic proportions, clean cinematic 16:9 composition, no captions inside the image."
    )


def _timeline_actions(emotion: Emotion) -> list[TimelineAction]:
    return [
        TimelineAction(type="motion", params={"preset": MOTION_BY_EMOTION[emotion], "strength": 0.55}),
        TimelineAction(type="transition", params={"preset": "fade", "duration": 0.32}),
    ]


class RuleBasedScenePlanner:
    """Free local planner. It is deterministic, fast and safe to use without an API key."""

    def plan(
        self,
        script: str,
        *,
        theme_id: str,
        duration_seconds: float,
        target_scene_count: int | None = None,
        seconds_per_scene: float = 12.5,
    ) -> list[SceneDraft]:
        units = _sentences(script)
        if not units:
            raise ValueError("The script is empty")

        if duration_seconds <= 0:
            duration_seconds = max(10.0, sum(_word_count(unit) for unit in units) / 2.35)
        desired = target_scene_count or max(1, round(duration_seconds / max(2.0, seconds_per_scene)))
        chunks = _group_units(units, desired)
        total_words = sum(_word_count(chunk) for chunk in chunks)

        drafts: list[SceneDraft] = []
        elapsed = 0.0
        for index, chunk in enumerate(chunks, start=1):
            proportion = _word_count(chunk) / total_words
            end = duration_seconds if index == len(chunks) else elapsed + duration_seconds * proportion
            emotion = _classify_emotion(chunk)
            role = _narrative_role(chunk, index, len(chunks), emotion)
            importance = _importance(role, emotion)
            drafts.append(
                SceneDraft(
                    position=index,
                    start_seconds=round(elapsed, 3),
                    end_seconds=round(end, 3),
                    narration=chunk,
                    visual_subject=_visual_subject(chunk),
                    emotion=emotion,
                    narrative_role=role,
                    importance=importance,
                    prompt=_compose_prompt(chunk, emotion, theme_id, index),
                    negative_prompt=get_theme(theme_id).negative_prompt,
                    # The economical default is one generation per scene. Important scenes are
                    # still labelled, so the editor can deliberately request extra candidates or
                    # a more expensive model only where it adds value.
                    model_role="photoreal",
                    candidate_count=1,
                    timeline_actions=_timeline_actions(emotion),
                )
            )
            elapsed = end
        return drafts


def estimate_generation_count(drafts: Iterable[SceneDraft]) -> int:
    return sum(max(1, draft.candidate_count) for draft in drafts)
