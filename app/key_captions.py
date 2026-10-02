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
LEADING_KEEP = {"between", "from", "since", "in", "under", "over", "about", "nearly", "almost", "just", "only", "chapter"}

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


def hook_phrase(sentence: str, limit: int = MAX_WORDS) -> str:
    """The opening line as big on-screen words ('Christmas baking used to start weeks before' ->
    'CHRISTMAS BAKING USED TO START WEEKS BEFORE'): its first clause, at most MAX_WORDS words."""
    words: list[str] = []
    for token in sentence.split():
        word = _clean(token)
        if words and word.lower() in CLAUSE_BREAKS:
            break
        if word:
            words.append(word)
        if len(words) >= limit or token.rstrip().endswith((",", ".", ";", ":", "—", "?", "!")):
            break
    while len(words) > 2 and words[-1].lower() in EDGE_STOP:
        words.pop()
    return " ".join(words) if len(words) >= 2 else ""


_TAIL_STOP = EDGE_STOP | {"across", "into", "from", "its", "their", "his", "her", "is", "are", "be", "been", "being",
                         "against", "under", "over", "after", "before", "about", "through", "this", "that", "these",
                         "those", "it", "they", "we", "you", "our", "my", "kept", "later", "running", "cracking",
                         "looks", "nothing", "like", "single"}
_YEAR = re.compile(r"^(?:1[89]|20)\d\d$")


def body_phrase(sentence: str) -> str:
    """A short caption around the sentence's first number, ending on a full word ('FOUNDED IN 1958',
    'OVER 2,000 LOCATIONS', '23 CLOSED IN 2025'); '' when the sentence has no number written with digits."""
    tokens = [token for token in re.split(r"\s+", sentence.strip()) if token]
    index = next((position for position, token in enumerate(tokens)
                  if re.search(r"\d", token) or _clean(token).lower() in NUMBER_WORDS), None)
    if index is None or not any(re.search(r"\d", token) for token in tokens):
        return ""
    words = [_clean(token) for token in tokens]
    if re.fullmatch(r"\d+(st|nd|rd|th)", words[index].lower()):
        # A day ("3rd") belongs to its date: build the caption around the year that follows.
        index = next((position for position in range(index, min(len(words), index + 3)) if _YEAR.match(words[position])), index)
    if _YEAR.match(words[index]):
        # A year reads best with the verb before it: "founded in 1958", "closed in 2025".
        begin = index
        while begin > 0 and index - begin < 2 and not tokens[begin - 1].endswith((",", ".", ";", ":")):
            begin -= 1
        phrase = words[begin:index + 1]
    else:
        begin = index - 1 if index > 0 and words[index - 1].lower() in LEADING_KEEP else index
        phrase = []
        for position in range(begin, min(len(tokens), index + 4)):
            if phrase and words[position].lower() in CLAUSE_BREAKS:
                break
            phrase.append(words[position])
            if tokens[position].endswith((",", ".", ";", ":", "?", "!")):
                break
    while len(phrase) > 1 and phrase[-1].lower() in _TAIL_STOP:
        phrase.pop()
    while len(phrase) > 1 and phrase[0].lower() in EDGE_STOP | {"by", "in"} and not _YEAR.match(phrase[-1]):
        phrase.pop(0)
    text = " ".join(word for word in phrase if word)
    return text if len(text.split()) >= 2 and re.search(r"\d", text) else ""


def sentence_groups(scenes: list[dict[str, Any]]) -> list[list[int]]:
    """Scene indexes grouped into whole spoken sentences (short shots split one sentence over several scenes)."""
    groups: list[list[int]] = []
    current: list[int] = []
    for index, scene in enumerate(scenes):
        text = str(scene.get("narration") or "").strip()
        if heading_subject(text):
            if current:
                groups.append(current)
                current = []
            continue
        current.append(index)
        if text.endswith((".", "?", "!", "\u201d", '"')):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


_REMEMBER = re.compile(r"^\s*(do|did|can|could)\s+you\s+remember\s+(when\s+|the\s+)?", re.IGNORECASE)


def local_key_captions(scenes: list[dict[str, Any]]) -> dict[int, str]:
    """Free, offline choice of key points, read from whole sentences (never a broken piece like '1970s Bacardi'):
    the opening line in at most 6 words, then numbers, dates and prices written with digits."""
    picks: dict[int, tuple[int, str]] = {}
    used_decades: set[str] = set()
    groups = sentence_groups(scenes)
    for number, group in enumerate(groups):
        sentence = " ".join(str(scenes[index].get("narration") or "").strip() for index in group)
        hook = number == 0
        sentence = re.sub(r"\s*[—–]\s*", " — ", sentence)  # "version—homemade": the dash ends a phrase
        if hook:
            # A number makes the punchiest opener; otherwise the line's first clause, at most 6 words.
            phrase = body_phrase(sentence) or hook_phrase(_REMEMBER.sub("", sentence), limit=8)
            score = 4
        else:
            phrase = body_phrase(sentence)
            score = 3 if any(cue in sentence.lower() for cue in REVEAL_CUES) else 2
            if not re.search(r"[\d$£]", phrase):
                phrase = ""  # "half into each piece" was cut from "press a pecan half into each piece"
            decade = re.fullmatch(r"(?:the\s+)?(1[89]\d0s)\b.*", phrase, flags=re.IGNORECASE)
            if decade and not re.search(r"[$£]|\d{3}|\d+\s*(?:%|degrees|cents|dollars|cups|minutes|hours)", phrase[len(decade.group(1)):]):
                if decade.group(1).lower() in used_decades or len(phrase.split()) < 3:
                    continue  # also too thin alone ("1970s version")  # "1970s ..." five times in one video says nothing new: once is enough
                used_decades.add(decade.group(1).lower())
        if not phrase:
            continue
        first = _clean(phrase.split()[0]).lower()
        # The scene where the phrase is actually spoken.
        target = next((index for index in group if first in [_clean(word).lower()
                       for word in str(scenes[index].get("narration") or "").split()]), group[0])
        picks[target] = (score, phrase)
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
