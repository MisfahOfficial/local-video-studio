"""What a video needs from its footage, kept out of the sourcing engine.

The engine (search, read whole sources, match each sentence, face/caption checks, cutting) is the
same for every niche; a profile says what to search for, which titles fit and whether period
footage is required. "vintage_recipe" is exactly the behaviour the V channels were tuned on.
Every project also names at least one example video it should look like.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

MAX_REFERENCES = 5
_YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


@dataclass(frozen=True)
class ContentProfile:
    kind: str
    name: str
    # "period": old footage preferred and required in the hook; "any": modern footage is fine.
    footage_era: str
    # Searches for each item/section; {item}, {era} and {theme} are filled in.
    section_queries: tuple[str, ...]
    hook_queries: tuple[str, ...]
    # Ranking bonus for titles like these (a whole classic recipe beats a quick hack).
    good_title_words: tuple[str, ...] = ()
    # A title must also contain one of these (so "Red Gold" is the tomato brand, not a shower faucet).
    context_words: tuple[str, ...] = ()
    # Titles with any of these are never used.
    blocked_words: tuple[str, ...] = ()
    # Ingredient lists become ingredient cards.
    recipe_cards: bool = True
    references: tuple[str, ...] = field(default_factory=tuple)

    @property
    def period(self) -> bool:
        return self.footage_era == "period"

    def queries(self, templates: tuple[str, ...], item: str = "", era: str = "", theme: str = "") -> list[str]:
        filled = [" ".join(template.format(item=item, era=era, theme=theme).split()) for template in templates]
        return list(dict.fromkeys(query for query in filled if query))

    def title_allowed(self, title: str) -> bool:
        lowered = f" {title.lower()} "
        if any(_has_phrase(lowered, word) for word in self.blocked_words):
            return False
        return not self.context_words or any(_has_phrase(lowered, word) for word in self.context_words)

    def good_title(self, title: str) -> bool:
        lowered = title.lower()
        return any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lowered) for word in self.good_title_words)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: list(value) if isinstance(value, tuple) else value for key, value in data.items()}


def _has_phrase(padded_lower_text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}s?(?![a-z0-9])", padded_lower_text) is not None


BUILTIN: dict[str, ContentProfile] = {
    "vintage_recipe": ContentProfile(
        kind="vintage_recipe", name="Vintage recipes (V channels)", footage_era="period",
        section_queries=("{item} recipe", "old fashioned {item}", "{era} {item}", "{item}"),
        hook_queries=("{era} {theme} footage", "vintage {item} home movie", "{era} {item}", "{item} {era} film"),
        good_title_words=("old fashioned", "old-fashioned", "vintage", "classic", "grandma", "grandma's", "grandmas",
                          "homemade", "from scratch", "original", "1930s", "1940s", "1950s", "1960s", "1970s",
                          "retro", "church", "potluck"),
    ),
    "modern_brand": ContentProfile(
        kind="modern_brand", name="Food brands today (exposé, C channels)", footage_era="any",
        section_queries=("{item} commercial", "{item} factory", "{item} product review", "{item} history", "{item}"),
        hook_queries=("{theme} commercial", "{theme} factory production", "{theme} grocery store shelf", "{theme}"),
        good_title_words=("commercial", "factory", "how it's made", "how its made", "production", "documentary",
                          "history", "review", "taste test", "ad"),
        context_words=("food", "ketchup", "sauce", "tomato", "tomatoes", "mustard", "mayo", "mayonnaise", "condiment",
                       "commercial", "ad", "advert", "factory", "production", "brand", "bottle", "jar", "can",
                       "grocery", "supermarket", "product", "recipe", "taste", "review", "company", "history",
                       "documentary", "ingredients", "label", "organic", "snack", "cereal", "soup", "juice"),
        blocked_words=("faucet", "shower", "gold rush", "fertilizer", "fertiliser", "garden", "plant care",
                       "pest control", "weed killer", "walking tour", "real estate", "gaming", "minecraft"),
        recipe_cards=False,
    ),
    "history": ContentProfile(
        kind="history", name="History documentary", footage_era="period",
        section_queries=("{item} archival footage", "{item} documentary", "{item} newsreel", "{item} history", "{item}"),
        hook_queries=("{theme} archival footage", "{theme} documentary", "{theme} newsreel", "{theme}"),
        good_title_words=("archival", "archive", "newsreel", "documentary", "rare footage", "historical", "pathé",
                          "british pathe", "1900s", "1910s", "1920s", "1930s", "1940s"),
        blocked_words=("reaction", "reacts", "gameplay", "minecraft", "fortnite", "hearts of iron", "civilization",
                       "total war", "roblox"),
        recipe_cards=False,
    ),
}
DEFAULT_KIND = "vintage_recipe"


def youtube_id(url: str) -> str:
    match = _YOUTUBE_ID.search(str(url or ""))
    return match.group(1) if match else ""


def clean_references(values: Any) -> list[str]:
    """Up to five distinct YouTube example links, in the order given; anything else is dropped."""
    seen: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        video_id = youtube_id(str(value))
        if video_id and video_id not in seen:
            seen.append(video_id)
    return [f"https://www.youtube.com/watch?v={video_id}" for video_id in seen[:MAX_REFERENCES]]


def normalize_profile(data: Any) -> dict[str, Any]:
    """What a project stores: its kind, example videos and any per-video word lists."""
    data = data if isinstance(data, dict) else {}
    kind = str(data.get("kind") or DEFAULT_KIND)
    if kind not in BUILTIN:
        kind = DEFAULT_KIND
    stored: dict[str, Any] = {"kind": kind, "references": clean_references(data.get("references"))}
    for key in ("context_words", "blocked_words"):
        if isinstance(data.get(key), list):
            stored[key] = [str(word).strip().lower()[:40] for word in data[key] if str(word).strip()][:60]
    return stored


def profile_for(project: dict[str, Any] | None) -> ContentProfile:
    stored = normalize_profile((project or {}).get("content_profile"))
    base = BUILTIN[stored["kind"]]
    changes: dict[str, Any] = {"references": tuple(stored["references"])}
    for key in ("context_words", "blocked_words"):
        if key in stored:
            changes[key] = tuple(stored[key])
    return replace(base, **changes)


def missing_reference_message(project: dict[str, Any] | None) -> str:
    if profile_for(project).references:
        return ""
    return "Add at least one example video (a YouTube link of a video this one should look like) before creating the video"
