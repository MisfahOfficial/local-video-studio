"""Before any download, Gemini reads what each search result says (title, channel, transcript) and keeps the
videos that are really about the item (Ishaq, 4 Oct: "Heinz Golden Vegetable Soup" should get videos of that
soup, not any soup video that matched a word). Only text is fetched, so it costs seconds, not downloads.
"""
from __future__ import annotations

from typing import Any

TRANSCRIPT_CHARS = 1200
SCHEMA = {"type": "OBJECT", "properties": {"videos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "id": {"type": "STRING"}, "about_item": {"type": "BOOLEAN"}, "score": {"type": "INTEGER"},
    "reason": {"type": "STRING"}}, "required": ["id", "about_item", "score", "reason"]}}}, "required": ["videos"]}


def transcript_text(events: list[dict[str, Any]], limit: int = TRANSCRIPT_CHARS) -> str:
    """Plain words of YouTube json3 caption events, cut to `limit` characters."""
    words: list[str] = []
    size = 0
    for event in events:
        for segment in event.get("segs") or []:
            text = str(segment.get("utf8") or "").replace("\n", " ").strip()
            if text:
                words.append(text)
                size += len(text) + 1
        if size >= limit:
            break
    return " ".join(words)[:limit]


def rank_videos(settings: Any, item: str, videos: list[dict[str, Any]], era: str = "") -> list[tuple[str, bool, int]]:
    """videos: [{"id", "title", "channel", "minutes", "transcript"}]. Returns (id, about_item, score) best first.
    Raises when Gemini cannot be asked (the caller keeps its own order then)."""
    from .llm import gemini_json

    lines = "\n\n".join(
        f"ID {video['id']} | title: {video['title'][:120]} | channel: {video.get('channel', '')[:60]} | "
        f"{video.get('minutes', 0):.0f} min\ntranscript start: {video.get('transcript') or '(no transcript)'}"
        for video in videos)
    period = f" The video is about the {era}; archive footage, old adverts and classic versions are a plus." if era else ""
    prompt = (
        f"A faceless documentary needs B-roll footage of exactly this item: \"{item}\".{period}\n"
        "For each YouTube video below, decide from its title, channel and the start of its transcript whether its "
        "pictures will mostly show THIS item (being made, served, eaten, unpacked, advertised, reviewed). "
        "about_item = false for a different dish or product with a similar name, a general compilation where it is "
        "one of many, a podcast or talking-head video, a reaction or a song. "
        "score 0-10 for how useful its footage of this item is. reason: at most 8 words.\n\n" + lines)
    answer = gemini_json(settings, prompt, SCHEMA, temperature=0.2)
    known = {video["id"] for video in videos}
    ranked = [(str(entry.get("id") or "").strip(), bool(entry.get("about_item")), int(entry.get("score") or 0))
              for entry in (answer or {}).get("videos") or []]
    ranked = [entry for entry in ranked if entry[0] in known]
    return sorted(ranked, key=lambda entry: (entry[1], entry[2]), reverse=True)
