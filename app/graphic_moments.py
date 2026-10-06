"""Where a script calls for one of the extra graphics: a place on a map, a price from back
then, a span of years, or a question for the comments. Found from the narration's own
words; numbers are shown exactly as the script says them (no inflation or other maths).
"""
from __future__ import annotations

import re
from typing import Any

# Approximate centres (longitude, latitude) of places these channels talk about.
PLACES: dict[str, tuple[str, float, float]] = {
    # Britain & Ireland
    "yorkshire": ("GB", -1.5, 54.0), "lancashire": ("GB", -2.6, 53.8), "the midlands": ("GB", -1.9, 52.6),
    "midlands": ("GB", -1.9, 52.6), "london": ("GB", -0.13, 51.5), "manchester": ("GB", -2.24, 53.48),
    "liverpool": ("GB", -2.98, 53.41), "birmingham": ("GB", -1.9, 52.48), "leeds": ("GB", -1.55, 53.8),
    "sheffield": ("GB", -1.47, 53.38), "newcastle": ("GB", -1.61, 54.97), "bristol": ("GB", -2.59, 51.45),
    "cornwall": ("GB", -4.9, 50.4), "devon": ("GB", -3.8, 50.7), "kent": ("GB", 0.7, 51.2),
    "norfolk": ("GB", 1.0, 52.65), "suffolk": ("GB", 1.0, 52.2), "essex": ("GB", 0.5, 51.8),
    "sussex": ("GB", -0.3, 50.95), "somerset": ("GB", -3.0, 51.1), "dorset": ("GB", -2.3, 50.8),
    "cumbria": ("GB", -3.0, 54.6), "northumberland": ("GB", -2.0, 55.2), "durham": ("GB", -1.57, 54.78),
    "lincolnshire": ("GB", -0.2, 53.1), "nottingham": ("GB", -1.15, 52.95), "derbyshire": ("GB", -1.6, 53.1),
    "scotland": ("GB", -4.2, 56.8), "scottish": ("GB", -4.2, 56.8), "edinburgh": ("GB", -3.19, 55.95),
    "glasgow": ("GB", -4.25, 55.86), "aberdeen": ("GB", -2.1, 57.15), "dundee": ("GB", -2.97, 56.46),
    "wales": ("GB", -3.7, 52.3), "welsh": ("GB", -3.7, 52.3), "cardiff": ("GB", -3.18, 51.48),
    "northern ireland": ("GB", -6.7, 54.6), "belfast": ("GB", -5.93, 54.6), "ireland": ("IE", -8.0, 53.3),
    "dublin": ("IE", -6.26, 53.35), "england": ("GB", -1.5, 52.8), "the north": ("GB", -2.0, 54.3),
    # United States (states)
    "alabama": ("US", -86.8, 32.8), "arizona": ("US", -111.7, 34.3), "arkansas": ("US", -92.4, 34.9),
    "california": ("US", -119.4, 37.2), "colorado": ("US", -105.5, 39.0), "connecticut": ("US", -72.7, 41.6),
    "delaware": ("US", -75.5, 39.0), "florida": ("US", -81.7, 28.6), "georgia": ("US", -83.4, 32.7),
    "idaho": ("US", -114.6, 44.4), "illinois": ("US", -89.2, 40.0), "indiana": ("US", -86.3, 39.9),
    "iowa": ("US", -93.5, 42.1), "kansas": ("US", -98.4, 38.5), "kentucky": ("US", -85.3, 37.5),
    "louisiana": ("US", -92.0, 31.0), "maine": ("US", -69.2, 45.4), "maryland": ("US", -76.8, 39.0),
    "massachusetts": ("US", -71.8, 42.3), "michigan": ("US", -84.7, 43.6), "minnesota": ("US", -94.3, 46.3),
    "mississippi": ("US", -89.7, 32.7), "missouri": ("US", -92.5, 38.4), "montana": ("US", -109.6, 47.0),
    "nebraska": ("US", -99.8, 41.5), "nevada": ("US", -116.9, 39.3), "new hampshire": ("US", -71.6, 43.7),
    "new jersey": ("US", -74.7, 40.1), "new mexico": ("US", -106.1, 34.4), "new york": ("US", -75.5, 42.9),
    "north carolina": ("US", -79.4, 35.5), "north dakota": ("US", -100.5, 47.5), "ohio": ("US", -82.8, 40.3),
    "oklahoma": ("US", -97.5, 35.6), "oregon": ("US", -120.5, 43.9), "pennsylvania": ("US", -77.6, 40.9),
    "rhode island": ("US", -71.5, 41.7), "south carolina": ("US", -80.9, 33.9), "south dakota": ("US", -100.2, 44.4),
    "tennessee": ("US", -86.3, 35.8), "texas": ("US", -99.3, 31.5), "utah": ("US", -111.7, 39.3),
    "vermont": ("US", -72.7, 44.1), "virginia": ("US", -78.8, 37.5), "washington state": ("US", -120.5, 47.4),
    "west virginia": ("US", -80.6, 38.6), "wisconsin": ("US", -89.8, 44.6), "wyoming": ("US", -107.5, 43.0),
    "the south": ("US", -86.0, 33.0), "the midwest": ("US", -90.0, 41.5), "new england": ("US", -71.5, 43.5),
    "chicago": ("US", -87.63, 41.88), "new orleans": ("US", -90.07, 29.95), "boston": ("US", -71.06, 42.36),
    # Canada (provinces)
    "ontario": ("CA", -85.0, 50.0), "quebec": ("CA", -71.5, 52.0), "british columbia": ("CA", -124.5, 54.0),
    "alberta": ("CA", -115.0, 55.0), "saskatchewan": ("CA", -106.0, 54.0), "manitoba": ("CA", -98.0, 55.0),
    "nova scotia": ("CA", -63.5, 45.0), "new brunswick": ("CA", -66.2, 46.5), "newfoundland": ("CA", -56.0, 49.0),
    "prince edward island": ("CA", -63.3, 46.4), "the maritimes": ("CA", -64.5, 46.0), "toronto": ("CA", -79.38, 43.65),
    "montreal": ("CA", -73.57, 45.5), "vancouver": ("CA", -123.12, 49.28),
}

# Adjectives shown by the place's own name on the map ("Scottish bakeries" -> Scotland).
DISPLAY = {"scottish": "Scotland", "welsh": "Wales", "the midlands": "The Midlands", "midlands": "The Midlands",
           "the north": "The North", "the south": "The South", "the midwest": "The Midwest", "new england": "New England",
           "washington state": "Washington"}

_PRICE = re.compile(
    r"(?:[£$]\s?\d+(?:[.,]\d{1,2})?|\b\d+(?:\.\d+)?\s?(?:cents?|pence|p|shillings?|dollars?|pounds?)\b|"
    r"\b(?:a |one )?(?:penny|ha'?penny|halfpenny|farthing|nickel|dime|quarter|sixpence|threepence|thruppence|"
    r"shilling|half a crown|half-crown|two bob|a bob)\b)", re.IGNORECASE)
_COST_WORDS = re.compile(r"\b(cost|costs|priced|price|sold for|paid|for just|only)\b", re.IGNORECASE)
_DECADE = re.compile(r"\b(1[89]\d0s|(?:19|20)\d\d)\b")
_RANGE = re.compile(r"\b(?:from\s+|between\s+)?(?:the\s+)?(1[89]\d0s|(?:19|20)\d\d)\s+(?:to|until|and|through|-|–)\s+(?:the\s+)?"
                    r"(1[89]\d0s|(?:19|20)\d\d)\b", re.IGNORECASE)
_QUESTION = re.compile(r"\b(comment(?:s)? below|in the comments|let us know|tell us|drop a comment|comment and|"
                       r"what do you think|do you remember)\b", re.IGNORECASE)


def find_places(text: str) -> list[tuple[str, str, float, float]]:
    lowered = f" {text.lower()} "
    found: list[tuple[str, str, float, float]] = []
    for name in sorted(PLACES, key=len, reverse=True):  # "new york" before "york"
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", lowered) and not any(name in other[0] for other in found):
            country, lon, lat = PLACES[name]
            found.append((name, country, lon, lat))
    return found


_FACT = re.compile(
    r"(?:\b(over|nearly|about|almost|more than|up to|under)\s+)?(\$?\d[\d,]*(?:\.\d+)?)\s*"
    r"(%|percent|degrees(?:\s+fahrenheit)?|°f|locations|stores|restaurants|outlets|million|billion|thousand|cents|"
    r"calories|miles|employees|workers|people|bars|pounds|ounces|boxes|cans|copies|units|times)\b",
    re.IGNORECASE)


def fact_moment(text: str) -> dict[str, Any] | None:
    """A key number with its unit ('260 degrees Fahrenheit', 'over 2,000 locations', '4.7 percent')."""
    match = _FACT.search(text)
    if not match:
        return None
    qualifier, number, unit = (match.group(1) or "").lower(), match.group(2), match.group(3).lower()
    digits = number.lstrip("$").replace(",", "")
    try:
        amount = float(digits)
    except ValueError:
        return None
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    prefix = "$" if number.startswith("$") else ""
    if unit in ("%", "percent"):
        suffix, label = "%", "percent"
    elif unit.startswith("degrees") or unit == "°f":
        suffix, label = ("°F", "degrees fahrenheit") if "fahrenheit" in unit or unit == "°f" else ("°", "degrees")
    else:
        suffix, label = "", unit
    if unit in ("%", "percent"):
        label = "percent"
    value = prefix + (f"{amount:,.{decimals}f}") + suffix
    return {"type": "fact", "value": value, "label": label.strip(), "qualifier": qualifier,
            "count": {"to": amount, "decimals": decimals, "prefix": prefix, "suffix": suffix}}


def moment_for(text: str, era: str = "") -> dict[str, Any] | None:
    """The one extra graphic a sentence calls for, or None."""
    places = find_places(text)
    # A map only when the places matter: two or more of them, or one place with movement across it ("spread
    # across Scotland"). "Scottish families bought..." alone made two Scotland maps in one item (6 Oct).
    moving = re.search(r"\b(across|all over|throughout|spread|travel\w*|route|from \w+ to)\b", text, re.IGNORECASE)
    if len({(place[2], place[3]) for place in places}) >= 2 or (places and moving):  # "Scottish" + "Scotland" is one place
        country = max({place[1] for place in places}, key=lambda code: sum(place[1] == code for place in places))
        return {"type": "map", "country": country,
                "places": [{"name": DISPLAY.get(name, name.title()), "lon": lon, "lat": lat}
                            for name, code, lon, lat in places if code == country][:5]}
    price = _PRICE.search(text)
    if price and _COST_WORDS.search(text):
        decade = _DECADE.search(text)
        return {"type": "price", "price": price.group(0).strip(), "when": decade.group(1) if decade else era}
    span = _RANGE.search(text)
    if span:
        return {"type": "years", "start": span.group(1), "end": span.group(2)}
    fact = fact_moment(text)
    if fact:
        return fact
    if _QUESTION.search(text) and "?" in text:
        question = [part.strip() for part in re.split(r"(?<=\?)", text) if part.strip().endswith("?")]
        return {"type": "comment", "question": (question[-1] if question else text)[:140]}
    return None


MAX_MAPS = 3  # maps per video: a map only when it is needed


def plan_moments(scenes: list[dict[str, Any]], era: str = "", skip: set[str] | None = None,
                 comment_gap_seconds: float = 240.0) -> dict[str, dict[str, Any]]:
    """Scene id -> extra graphic. At most one of each kind per section, and question cards spaced out,
    so real footage stays the main thing on screen."""
    from .footage_match import heading_subject

    skip = skip or set()
    chosen: dict[str, dict[str, Any]] = {}
    section_used: set[str] = set()
    last_comment = -1e9
    maps_shown: set[tuple[str, ...]] = set()
    # Whole sentences, not scenes: with short shots "throughout the 1950s / to the 1990s" sits in two scenes
    # and neither alone shows a span of years (the V2 test got one graphic in five minutes).
    sentences: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for scene in scenes:
        text = str(scene.get("narration") or "").strip()
        if heading_subject(text):
            if current:
                sentences.append(current)
                current = []
            sentences.append([scene])  # a heading starts a new section
            continue
        current.append(scene)
        if text.endswith((".", "?", "!", "\u201d", '"')):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    for group in sentences:
        text = " ".join(str(scene.get("narration") or "").strip() for scene in group)
        if heading_subject(text):
            section_used = set()
            continue
        usable = [scene for scene in group if str(scene["id"]) not in skip]
        if not usable:
            continue
        moment = moment_for(text, era)
        if not moment or moment["type"] in section_used:
            continue
        if moment["type"] == "map":
            shown = tuple(sorted(place["name"] for place in moment.get("places") or []))
            if shown in maps_shown or len(maps_shown) >= MAX_MAPS:
                continue  # the same map again, or enough maps in this video
            maps_shown.add(shown)
        # The graphic takes the sentence's longest shot (a short one would cut its animation off).
        scene = max(usable, key=lambda item: float(item.get("end_seconds") or 0) - float(item.get("start_seconds") or 0))
        start = float(scene.get("start_seconds") or 0)
        if moment["type"] == "comment":
            if start - last_comment < comment_gap_seconds:
                continue
            last_comment = start
        section_used.add(moment["type"])
        chosen[str(scene["id"])] = moment
    return chosen
