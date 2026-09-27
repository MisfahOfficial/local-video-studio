"""Documentary-style captions: a few short, centred key points instead of subtitles."""
from __future__ import annotations

import json
import re
from typing import Any

from .footage_match import heading_subject
from .providers.base import ProviderError
from .providers.http import post_json

MIN_GAP_SECONDS = 12.0
MAX_WORDS = 7

NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "fifteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion", "half", "quarter", "dozen",
}
REVEAL_CUES = ("the truth", "turned out", "secret", "the real reason", "never", "only", "first time", "last")
CLAUSE_BREAKS = {"until", "when", "that", "which", "because", "while", "so", "but", "who", "where"}
EDGE_STOP = {"a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "at", "with", "by", "as", "was", "were"}
LEADING_KEEP = {"between", "from", "since", "in", "under", "over", "about", "nearly", "almost", "just", "only"}

KEY_POINT_STYLE = {
    "animation": "highlight",
    "position": "bottom", "alignment": "center", "case": "upper", "size": 84, "bold": True,
    "background_enabled": False, "stroke_enabled": False, "glow_enabled": False,
    "shadow_enabled": True, "shadow_color": "#000000", "shadow_blur": 12, "shadow_x": 0, "shadow_y": 3,
    "max_lines": 2, "words_per_line": 4, "position_x": 0, "position_y": 0,
}


def _clean(token: str) -> str:
    return token.strip(".,;:!?\"'()—–-“”")


def _is_number(token: str) -> bool:
    word = _clean(token).lower()
    return bool(re.fullmatch(r"\$?\d[\d,.]*(%|s|°f?|th|st|nd|rd)?", word)) or word in NUMBER_WORDS


def key_phrase(sentence: str, hook: bool = False) -> str:
    """Shorten a sentence to its caption-worthy core ('350 degrees for ten to twelve minutes')."""
    tokens = sentence.split()
    if hook and sentence.strip().endswith("?") and len(tokens) <= 10:
        return sentence.strip()
    index = next((position for position, token in enumerate(tokens) if _is_number(token)), None)
    if index is None:
        return ""
    if index > 0 and _clean(tokens[index - 1]).lower() in LEADING_KEEP:
        index -= 1
    phrase: list[str] = []
    for token in tokens[index:index + MAX_WORDS]:
        word = _clean(token)
        if phrase and word.lower() in CLAUSE_BREAKS:
            break
        if word:
            phrase.append(word)
        if token.rstrip().endswith((",", ".", ";", ":", "—", "?", "!")):
            break
    # "...oats with one" is cut mid-measure: drop dangling number words and linkers.
    while len(phrase) > 2 and (phrase[-1].lower() in EDGE_STOP or phrase[-1].lower() in NUMBER_WORDS):
        phrase.pop()
    while phrase and phrase[-1].lower() in EDGE_STOP:
        phrase.pop()
    return " ".join(phrase) if len(phrase) >= 2 else ""


def _score(scene: dict[str, Any], index: int, total: int) -> tuple[int, str]:
    narration = str(scene.get("narration") or "")
    hook = index < max(2, total // 8)
    phrase = key_phrase(narration, hook=hook)
    if not phrase:
        return 0, ""
    score = 3 if hook else 2
    if any(cue in narration.lower() for cue in REVEAL_CUES):
        score += 1
    return score, phrase


def _space(picks: dict[int, tuple[int, str]], scenes: list[dict[str, Any]], limit: int) -> dict[int, str]:
    """Keep the best-scoring captions at least MIN_GAP_SECONDS apart, never on neighbours."""
    chosen: list[int] = []
    for index in sorted(picks, key=lambda item: (-picks[item][0], item)):
        start = float(scenes[index]["start_seconds"])
        if any(abs(start - float(scenes[other]["start_seconds"])) < MIN_GAP_SECONDS or abs(index - other) < 2
               for other in chosen):
            continue
        chosen.append(index)
        if len(chosen) >= limit:
            break
    return {index: picks[index][1] for index in chosen}


def local_key_captions(scenes: list[dict[str, Any]]) -> dict[int, str]:
    """Free, offline choice of key points: hook question, numbers, dates and prices."""
    picks: dict[int, tuple[int, str]] = {}
    for index, scene in enumerate(scenes):
        if heading_subject(str(scene.get("narration") or "")):
            continue  # chapter cards carry the heading
        score, phrase = _score(scene, index, len(scenes))
        if score:
            picks[index] = (score, phrase)
    duration = float(scenes[-1]["end_seconds"]) if scenes else 0
    return _space(picks, scenes, max(1, int(duration // MIN_GAP_SECONDS)))


def gemini_key_captions(scenes: list[dict[str, Any]], api_key: str, model: str) -> dict[int, str]:
    """One Gemini request for the whole video; raises ProviderError when unavailable."""
    if not api_key.strip():
        raise ProviderError("No Gemini key")
    items = [
        {"index": index, "start": round(float(scene["start_seconds"]), 1), "text": str(scene.get("narration") or "")}
        for index, scene in enumerate(scenes) if not heading_subject(str(scene.get("narration") or ""))
    ]
    prompt = (
        "You are the editor of a faceless documentary YouTube channel. Pick only the moments that deserve "
        "on-screen text: the opening hook, key numbers, dates, prices, names, and surprising facts. At most one "
        f"every {int(MIN_GAP_SECONDS)} seconds, never on two neighbouring scenes, usually 10 to 25 percent of "
        f"scenes. For each pick write a punchy caption of 2 to {MAX_WORDS} words (not a full sentence), e.g. "
        "\"$1 CHRISTMAS TABLE\" or \"350°F FOR 12 MINUTES\".\n\nSCENES\n" + json.dumps(items, ensure_ascii=False)
    )
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "index": {"type": "INTEGER"}, "text": {"type": "STRING"}}, "required": ["index", "text"]}}
    response = post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key.strip()}",
        {"contents": [{"parts": [{"text": prompt}]}],
         "generationConfig": {"response_mime_type": "application/json", "response_schema": schema, "temperature": 0.3}},
        {}, timeout=120,
    )
    try:
        chosen = json.loads(response["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ProviderError("Gemini returned no captions") from error
    picks: dict[int, tuple[int, str]] = {}
    for item in chosen if isinstance(chosen, list) else []:
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        text = " ".join(str(item.get("text") or "").split()[:MAX_WORDS])
        if 0 <= index < len(scenes) and text:
            picks[index] = (1, text)
    if not picks:
        raise ProviderError("Gemini returned no captions")
    duration = float(scenes[-1]["end_seconds"])
    return _space(picks, scenes, max(1, int(duration // MIN_GAP_SECONDS)))


def key_captions(scenes: list[dict[str, Any]], api_key: str = "", model: str = "") -> tuple[dict[int, str], str]:
    """Return ({scene index: caption}, source) using Gemini when possible, else the local rules."""
    try:
        return gemini_key_captions(scenes, api_key, model), "gemini"
    except ProviderError:
        return local_key_captions(scenes), "local"
