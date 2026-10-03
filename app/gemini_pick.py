"""Gemini chooses each sentence's clip among the planned options (Ishaq, 3 Oct: editors found ~40% wrong clips).

The local CLIP model ranks moments by how the food looks, not by what happens, so "dissolve gelatin in cold
water" could get any pie. Here Gemini sees every option of a sentence (2 frames each, labelled A-F) next to
the sentence and picks the one that shows it, or "none" when nothing fits (the scene then searches again or
takes a fallback instead of a wrong clip). Four sentences go in one request and five requests run at once,
while other items are still being downloaded, so it adds little time. A picked clip needs no second check.
"""
from __future__ import annotations

import io
import string
import threading
import time
from typing import Any

PER_REQUEST = 4  # sentences per request
MAX_OPTIONS = 6
PARALLEL = 5
_slots = threading.Semaphore(PARALLEL)  # all videos together: Gemini's free tier refuses bursts

SCHEMA = {"type": "OBJECT", "properties": {"picks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "scene": {"type": "STRING"}, "best": {"type": "STRING"}, "score": {"type": "INTEGER"},
    "reason": {"type": "STRING"}}, "required": ["scene", "best", "score", "reason"]}}}, "required": ["picks"]}


def _prompt(rows: list[dict[str, Any]], era: str, style_lines: str) -> str:
    lines = "\n".join(
        f"Scene {row['label']} (image {index + 1}, options {', '.join(string.ascii_uppercase[:len(row['options'])])}): "
        f"section \"{row['subject'] or 'general'}\" | sentence: \"{row['sentence'][:240]}\""
        for index, row in enumerate(rows))
    period = (f"The story is about the {era} past: a modern kitchen, phone, smartwatch or present-day presenter is "
              "wrong even when the food is right.\n") if era else ""
    return (
        "You are the video editor of a faceless food-history YouTube documentary. For each scene below you get one "
        "image: its candidate clips, one labelled row each (A, B, C ...; frames left to right are the clip's start "
        "and end). Pick the ONE option that best shows what the narrator says in that sentence:\n"
        "1. It must show the section's own dish, brand or place (not another pie, cake or dish).\n"
        "2. Prefer the option showing the exact action or thing in the sentence (dissolving gelatin, pouring into a "
        "crust, slicing, serving); for history or memory lines, a clean appetising shot of the dish.\n"
        "3. Never pick a person doing something unrelated (opening a drawer, walking, talking to camera), or a blank "
        "or very dark frame.\n"
        "IGNORE text, captions, logos and watermarks completely: the tool crops logos away and checks captions "
        "itself, so they are never a reason for \"none\".\n"
        + period + style_lines +
        "A clean shot of the RIGHT dish is acceptable (score about 5) when no option shows the exact action.\n"
        "best = the option letter, or \"none\" only when every option breaks rule 1 or 3 (better no clip than a "
        "wrong one).\n"
        "score 0-10 for how well the picked option fits. reason: at most 8 words.\n\n" + lines)


def pick_moments(settings: Any, rows: list[dict[str, Any]], era: str = "", style_lines: str = "",
                 keep_sheets: Any = None) -> dict[str, tuple[int | None, int]]:
    """rows: [{"label", "subject", "sentence", "options": [[frames], ...]}] (at most PER_REQUEST).
    Returns label -> (option index or None for "none", score). Raises when Gemini cannot be asked."""
    from .ai_judge import contact_sheet
    from .llm import gemini_look

    images = []
    for row in rows:
        buffer = io.BytesIO()
        sheet = contact_sheet(row["options"])
        sheet.save(buffer, format="PNG")
        images.append(buffer.getvalue())
        if keep_sheets is not None:
            keep_sheets.append(sheet)
    prompt = _prompt(rows, era, style_lines)
    answer = None
    for attempt in range(3):
        try:
            with _slots:
                answer = gemini_look(settings, prompt, images, SCHEMA)
            break
        except Exception as error:
            # "high demand" (503) and dropped connections pass in seconds; a used-up quota (429) does not.
            if attempt == 2 or not any(code in str(error) for code in (
                    "503", "UNAVAILABLE", "high demand", "Broken pipe", "timed out", "Connection reset", "urlopen error")):
                raise
            time.sleep(6 * (attempt + 1))
    picks: dict[str, tuple[int | None, int]] = {}
    for item in (answer or {}).get("picks") or []:
        label = "".join(character for character in str(item.get("scene") or "") if character.isdigit())  # "Scene 1"
        best = str(item.get("best") or "").strip().upper()
        row = next((row for row in rows if row["label"] == label), None)
        if row is None:
            continue
        score = int(item.get("score") or 0)
        row.setdefault("reason", str(item.get("reason") or "")[:80])
        if best in ("NONE", "") or score < 4:
            picks[label] = (None, score)
        elif len(best) == 1 and best in string.ascii_uppercase[:len(row["options"])]:
            picks[label] = (string.ascii_uppercase.index(best), score)
    return picks
