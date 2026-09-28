"""Topic-aware footage matching: detect the video topic, build topic-bound
searches, and verify candidate footage visually with a local CLIP model."""
from __future__ import annotations

import io
import re
import threading
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .providers.http import verified_ssl_context

_STOP = {
    "a", "about", "after", "again", "all", "also", "an", "and", "any", "are", "as", "at", "be", "because", "been",
    "before", "being", "but", "by", "can", "could", "did", "do", "does", "each", "even", "every", "few", "for",
    "from", "had", "has", "have", "he", "her", "here", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "like", "many", "more", "most", "much", "my", "no", "not", "now", "of", "on", "once", "one",
    "only", "or", "other", "our", "out", "over", "own", "same", "she", "so", "some", "still", "such", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "why",
    "will", "with", "would", "you", "your", "first", "new", "way", "ways", "time", "times", "day", "days",
    "year", "years", "life", "world", "thing", "things", "something", "nothing", "everything", "people",
    "today", "yet", "never", "always", "ever", "really", "almost", "around", "across", "against", "among",
    "during", "without", "within", "toward", "towards", "whole", "entire", "own", "let", "lets", "get", "got",
    "make", "made", "takes", "take", "took", "come", "came", "goes", "went", "gone", "see", "seen", "know",
    "knew", "think", "thought", "look", "looks", "say", "said", "tell", "told", "may", "might", "must", "should",
    # Abstract or descriptive words that never help find footage.
    "single", "deadly", "called", "keeps", "keep", "appear", "appears", "become", "becomes", "became", "begin",
    "begins", "began", "impact", "important", "incredible", "amazing", "huge", "small", "big", "little", "great",
    "real", "true", "truth", "fact", "reason", "reasons", "place", "part", "kind", "long", "short", "high", "low",
    "hard", "harsh", "harshest", "best", "worst", "less", "least", "able", "within", "hours", "hour", "minutes",
    "survive", "survived", "survives", "changed", "change", "changes", "happen", "happened", "happens",
    # Narration verbs and channel talk.
    "remind", "reminds", "reminded", "subscribe", "subscribed", "hit", "channel", "comment", "comments", "bell",
    "notification", "notifications", "share", "video", "videos", "watch", "watching", "answer", "answers", "bring", "bringing",
    "brought", "back", "forgotten", "fill", "filled", "proved", "prove", "appeared", "require", "required",
    "didn", "don", "doesn", "wasn", "weren", "isn", "aren", "won", "ll", "ve", "re", "determination",
    "delicious", "tight", "expensive", "cheap", "every", "everyone", "anyone", "between", "straight",
    "together", "finished", "staple", "along", "without", "any", "whatever", "left", "using", "used", "meant",
    "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "twelve", "twenty", "thirty",
    "forty", "fifty", "hundred", "thousand", "half", "quarter", "cup", "cups", "teaspoon", "tablespoon",
}

# Titles containing these are talk, music or reaction content, never B-roll.
NON_FOOTAGE_TITLE = re.compile(
    r"\b(lyrics?|lyric video|music video|official video|asmr|podcast|reacts?|reaction|prank(ed)?|karaoke|"
    r"trailer|unboxing|live ?stream|#shorts|audiobook|full album|playlist|roblox|minecraft|fortnite|gameplay|"
    r"let'?s play|video game|walkthrough|animation|animated|cartoon|no music|no talk|music|songs?|radio|"
    r"sing[- ]?along|saturday night live|snl|sketch|skit|comedy|comedian|stand[- ]?up|parody|sitcom|"
    r"full episode|full movie|film history|long takes?|best scenes|movie scenes?|movie clips?|film clips?|"
    r"hulu|netflix|prime video|late show|tonight show|jimmy (fallon|kimmel)|conan)\b",
    re.IGNORECASE,
)


def _words(text: str) -> list[str]:
    found = re.findall(r"[a-z][a-z'-]+", text.lower().replace("’", "'"))
    # "man's" -> "man": possessives must not become "man'" after singularizing.
    return [word for word in (re.sub(r"'s$|'$", "", item) for item in found) if word]


def _singular(word: str) -> str:
    if word.endswith(("ss", "us", "is", "as")) or word in {"molasses", "series", "species"}:
        return word  # christmas, molasses, bus: not plurals
    for suffix, replacement in (("ies", "y"), ("oxen", "ox"), ("ves", "f"), ("es", "e"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + replacement
    return word


def detect_topic(script: str, project_name: str = "") -> str:
    """Guess the main filmed subject: the most repeated one- or two-word noun phrase."""
    words = _words(script)
    counts: Counter[str] = Counter()
    for index, word in enumerate(words):
        if word in _STOP or len(word) < 3:
            continue
        counts[_singular(word)] += 1
        following = words[index + 1] if index + 1 < len(words) else ""
        if following and following not in _STOP:
            # Two-word names ("musk ox", "tv dinner") outrank their parts.
            counts[f"{_singular(word)} {_singular(following)}"] += 2.5
    title = " ".join(_singular(word) for word in _words(project_name))
    for phrase in list(counts):
        if f" {phrase} " in f" {title} ":
            counts[phrase] += 6 if " " in phrase else 2
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def topic_forms(topic: str) -> set[str]:
    """Spellings to accept in titles: 'musk ox' also matches 'muskox' and 'musk oxen'."""
    base = " ".join(_singular(word) for word in _words(topic))
    if not base:
        return set()
    return {base, base.replace(" ", ""), base.replace("-", " "), base.replace("-", "")}


def mentions_topic(text: str, topic: str) -> bool:
    if not topic.strip():
        return True
    normalized = " ".join(_singular(word) for word in _words(text))
    squeezed = normalized.replace(" ", "")
    return any(form in normalized or form.replace(" ", "") in squeezed for form in topic_forms(topic))


_NUMBERING = re.compile(r"^\s*(?:#?\d+\s*[.):\-]|number\s+\d+\s*[.:\-]?)\s*", re.IGNORECASE)


def heading_subject(sentence: str) -> str:
    """'POOR MAN'S COOKIES' or '2. Vinegar Pie' -> the section's subject; '' for normal sentences."""
    text = sentence.strip().strip('"\u201c\u201d')
    numbered = bool(_NUMBERING.match(text))
    text = _NUMBERING.sub("", text).strip(" .:!-")
    words = re.findall(r"[A-Za-z][A-Za-z'\u2019-]*", text)
    if not 1 <= len(words) <= 7:
        return ""
    letters = "".join(words)
    if (letters.isupper() and len(letters) > 3) or (numbered and not sentence.strip().endswith("?")):
        return text.lower().replace("\u2019", "'")
    # A Title Case line with no closing punctuation ("Chicken and Rice Casserole").
    capitalised = [word for word in words if word[0].isupper()]
    if (len(words) >= 2 and not sentence.strip().endswith((".", "!", "?", ",", ";", ":", "\u2014", "-"))
            and len(capitalised) >= 2 and all(word[0].isupper() or word.lower() in _TITLE_SMALL for word in words)):
        return text.lower().replace("\u2019", "'")
    return ""


_TITLE_SMALL = {"a", "an", "and", "or", "of", "the", "in", "on", "with", "for", "to", "de", "au"}


def core_subject(subject: str) -> str:
    """The filmable thing: 'poor man's cookies' -> 'cookies', 'musk ox' -> 'musk ox'."""
    subject = subject.lower().replace("\u2019", "'").strip()
    if "'s " in subject:
        subject = subject.split("'s ", 1)[1]
    return subject.strip()


def detect_era(script: str) -> str:
    """Most mentioned decade ('1950s'), or '' when the script names none."""
    decades = [f"{match[:3]}0s" for match in re.findall(r"\b(1[89]\d\d|20[0-2]\d)s?\b", script)]
    return Counter(decades).most_common(1)[0][0] if decades else ""


def _script_sentences(script: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", script) if part.strip()]
    merged: list[str] = []
    for part in parts:
        # "2." split away from "Vinegar Pie" is list numbering, not a sentence.
        if merged and re.fullmatch(r"#?\d+[.)]", merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def auto_topic(script: str, project_name: str = "") -> str:
    """A single topic only for single-subject films; '' for list videos with section headings."""
    sentences = _script_sentences(script)
    if any(heading_subject(sentence) for sentence in sentences):
        return ""
    topic = detect_topic(script, project_name)
    if not topic or not sentences:
        return ""
    share = sum(mentions_topic(sentence, topic) for sentence in sentences) / len(sentences)
    return topic if share >= 0.3 else ""


def hook_theme(hook_text: str) -> str:
    """What the opening is about: the promised list ('thirty church potluck casseroles'), else its two
    most repeated nouns in spoken order."""
    from .key_captions import NUMBER_WORDS

    tokens = re.findall(r"[a-z0-9'-]+", hook_text.lower().replace("\u2019", "'"))
    phrases: list[tuple[bool, str]] = []
    for index, token in enumerate(tokens):
        if token in PLURAL_FOODS and index:
            before = tokens[max(0, index - 5):index]
            counted = next((position for position in range(len(before) - 1, -1, -1)
                            if (before[position].isdigit() and not re.fullmatch(r"1[89]\d\d|20\d\d", before[position]))
                            or before[position] in NUMBER_WORDS), None)
            words = [word for word in before[(counted + 1) if counted is not None else -2:] if word not in _STOP]
            if words:
                phrases.append((counted is not None, " ".join([*words[-3:], token])))
    if phrases:
        # "thirty church potluck casseroles" is the video's promise; prefer a counted list.
        return max(phrases, key=lambda item: item[0])[1]
    words = [word for word in _words(hook_text) if word not in _STOP and len(word) >= 3 and word not in NUMBER_LIKE]
    counts = Counter(_singular(word) for word in words)
    top = [word for word, count in counts.most_common(2) if count >= 2]
    if not top:
        return ""
    order = [_singular(word) for word in words]
    return " ".join(sorted(top, key=order.index))


NUMBER_LIKE = {"hundred", "thousand", "million"}

# Other cuisines' home-cooking videos (pulao, karahi, fried rice) never fit an American,
# British or Canadian vintage story unless the script itself names the dish.
FOREIGN_CUISINE = {
    "pulao", "pulav", "biryani", "karahi", "tandoori", "tikka", "masala", "curry", "dal", "daal", "paneer",
    "haleem", "nihari", "korma", "handi", "chaat", "roti", "paratha", "naan", "desi", "pakistani", "indian",
    "hindi", "urdu", "bengali", "punjabi", "tadka", "tarka", "achar", "kebab", "kabab", "qorma", "sabzi",
    "khana", "banaye", "banane", "tarika", "itna", "jise", "wala", "wali", "recipe by", "food fusion",
    "hainanese", "fried rice", "kimchi", "korean", "chinese", "thai", "vietnamese", "japanese", "filipino",
    "adobo", "jollof", "nigerian", "mexican", "arroz", "bibimbap", "ramen", "sushi", "dim sum", "pho",
    "shawarma", "arabic", "arabian", "mandi", "kabsa", "turkish", "persian", "afghani", "sri lankan",
    "bollywood", "tollywood", "tamil", "telugu", "hindi movie", "shemaroo",
}


def off_cuisine(title: str, script: str) -> bool:
    """True for another cuisine's video (or a non-Latin title) that the script never mentions."""
    if re.search(r"[\u0590-\u08ff\u0900-\u0dff\u0e00-\u0eff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]", title):
        return True
    lowered = f" {' '.join(_words(title))} "
    story = f" {' '.join(_words(script))} "
    return any(f" {word} " in lowered and f" {word} " not in story for word in FOREIGN_CUISINE)


INGREDIENTS = {
    "oats", "oat", "oatmeal", "molasses", "shortening", "lard", "vinegar", "jello", "jell-o", "gelatin", "raisins",
    "cinnamon", "nutmeg", "ginger", "cloves", "peanut", "chocolate", "cocoa", "coconut", "banana", "bananas",
    "apple", "apples", "applesauce", "pumpkin", "cornmeal", "corn", "mayonnaise", "potato", "potatoes", "rice",
    "bread", "breadcrumbs", "crackers", "graham", "ritz", "marshmallow", "marshmallows", "cornflakes", "walnuts",
    "walnut", "pecans", "pecan", "almonds", "dates", "prunes", "honey", "syrup", "maple", "lemon", "lime", "orange",
    "pineapple", "cherries", "cherry", "strawberries", "blueberries", "cranberries", "cranberry", "rhubarb",
    "buttermilk", "cream", "cheese", "cottage", "sour", "evaporated", "condensed", "spam", "bologna", "beans",
    "noodles", "macaroni", "tuna", "mushroom", "chicken", "beef", "pork", "ham", "bacon", "sausage", "hotdogs", "tomato",
    "soup", "cabbage", "carrot", "carrots", "onion", "onions", "zucchini", "yeast", "biscuit", "biscuits",
    "pudding", "custard", "caramel", "toffee", "fudge", "butterscotch", "vanilla", "nuts", "sprinkles", "icing",
    "frosting", "jam", "jelly", "preserves", "figs", "persimmon", "sweet", "yams", "hominy", "grits", "sorghum",
}
# Present in almost every recipe, so they say nothing about which dish it is.
GENERIC_INGREDIENTS = {"sugar", "flour", "butter", "egg", "eggs", "milk", "water", "salt", "oil", "sweet", "sour", "cream"}
PROCESS_WORDS = {
    "mix", "mixed", "mixing", "stir", "stirred", "stirring", "whisk", "whisked", "beat", "beaten", "fold", "folded",
    "bake", "baked", "baking", "oven", "dough", "batter", "bowl", "spoon", "spoonful", "spoonfuls", "pour", "poured",
    "knead", "kneaded", "roll", "rolled", "rolling", "drop", "dropped", "sheet", "sheets", "tray", "pan", "skillet",
    "melt", "melted", "dissolve", "dissolved", "boil", "boiled", "simmer", "fry", "fried", "cool", "cooled", "slice",
    "sliced", "cut", "chop", "chopped", "flatten", "flattened", "fork", "measure", "measured", "grease", "greased",
    "sift", "sifted", "spread", "cream", "creamed", "frost", "frosted", "glaze", "shape", "shaped",
}
ADJECTIVE_FORM = {"oats": "oatmeal", "oat": "oatmeal", "bananas": "banana", "apples": "apple", "walnuts": "walnut",
                  "pecans": "pecan", "raisins": "raisin", "potatoes": "potato", "carrots": "carrot", "cherries": "cherry"}


def recipe_phrase(section_text: str, heading: str) -> str:
    """'Poor Man's Cookies' + its text -> 'oatmeal molasses cookies': the dish as a camera sees it."""
    words = _words(section_text)
    counts: Counter[str] = Counter()
    negated_until = -1
    for index, word in enumerate(words):
        if word in {"without", "no", "instead", "skip", "skipped"}:
            negated_until = index + 5  # "without any eggs or butter"
        if index <= negated_until:
            continue
        if word in INGREDIENTS and word not in GENERIC_INGREDIENTS:
            counts[ADJECTIVE_FORM.get(word, word)] += 1
    core = core_subject(heading) if heading else ""
    if any(word in INGREDIENTS for word in _words(core)):
        return core  # the name already says what it is: "chicken and rice casserole"
    kind = core.split()[-1] if core else ""
    signature = [word for word, _count in counts.most_common(2) if word not in core.split()]
    return " ".join([*signature, kind]).strip() if signature and kind else ""


def signature_words(recipe: str, heading: str) -> set[str]:
    """Words a matching video should mention: the dish name or its signature ingredients."""
    kind = core_subject(heading).split()[-1:] if heading else []
    # The full dish name ("poor man's cookies"), never just its kind ("cookies").
    return ({word for word in _words(recipe) if word not in kind and word not in _STOP}
            | ({heading} if heading else set()))


BASIC_INGREDIENTS = {"milk", "butter", "egg", "eggs", "flour", "sugar", "oil", "salt", "cream"}
DISPLAY = {"egg": "eggs", "oat": "oats", "oatmeal": "oats"}
PLURAL_FOODS = {
    "desserts", "recipes", "cookies", "cakes", "pies", "dishes", "dinners", "treats", "candies", "meals",
    "snacks", "breads", "casseroles", "salads", "soups", "puddings", "sweets", "foods",
}


MEASURES = {
    "cup", "cups", "tablespoon", "tablespoons", "teaspoon", "teaspoons", "can", "cans", "pound", "pounds", "ounce",
    "ounces", "stick", "sticks", "pinch", "dash", "quart", "quarts", "pint", "pints", "handful", "spoonful", "packet",
    "package", "jar", "box", "tbsp", "tsp", "lb", "oz",
}
# Words that describe the finished dish rather than what goes into it.
DISH_TALK = {"finished", "served", "tasted", "taste", "texture", "flavor", "flavour", "bite"}


def ingredient_list(scene_text: str) -> list[str]:
    """Ingredients a sentence talks about: two or more named ("butter and eggs were too expensive",
    "mixed oats with sugar"), or one with a measure ("an entire can of cream of mushroom").
    Every such scene becomes an ingredient card; sentences about the finished dish do not."""
    words = _words(scene_text)
    if set(words) & DISH_TALK:
        return []
    found: list[str] = []
    negated_until = -1
    for index, word in enumerate(words):
        if word in {"without", "no", "instead"}:
            negated_until = index + 5
        if index <= negated_until:
            continue
        if word in (INGREDIENTS | BASIC_INGREDIENTS) - {"sweet", "sour", "cream", "cheese", "cottage", "soup"}:
            name = DISPLAY.get(word, word)
            if name not in found:
                found.append(name)
    parts = {part for word in words for part in word.split("-")}  # "quarter-cup"
    if len(found) >= 2 or (found and parts & (MEASURES | {"ingredient", "ingredients"})):
        return found
    return []


def plural_items(scene_text: str) -> str:
    """'thirty forgotten dollar desserts' -> 'desserts': a count of many dishes deserves many pictures."""
    from .key_captions import NUMBER_WORDS

    words = _words(scene_text) + re.findall(r"\d+", scene_text)
    tokens = re.findall(r"[a-z0-9']+", scene_text.lower())
    for index, token in enumerate(tokens):
        if token.isdigit() or token in NUMBER_WORDS:
            for following in tokens[index + 1:index + 5]:
                if following in PLURAL_FOODS:
                    return following
    return ""


def is_process_scene(scene_text: str) -> bool:
    """A recipe step shows an action ("mixed", "stirred", "dropped onto sheets").

    A bare ingredient list ("Just oats, sugar, and determination") is a summary and
    shows the finished dish, so the story never jumps back to raw ingredients.
    """
    words = {_singular(word) for word in _words(scene_text)} | set(_words(scene_text))
    return bool(words & PROCESS_WORDS)


def section_recipes(scenes: list[dict[str, Any]], subjects: list[str]) -> list[str]:
    """Recipe phrase per scene, computed from all text of that scene's list section."""
    text_by_subject: dict[str, list[str]] = {}
    for scene, subject in zip(scenes, subjects):
        text_by_subject.setdefault(subject, []).append(str(scene.get("narration") or ""))
    phrases = {subject: recipe_phrase(" ".join(texts), subject) for subject, texts in text_by_subject.items() if subject}
    return [phrases.get(subject, "") for subject in subjects]


def scene_subjects(scenes: list[dict[str, Any]], topic: str) -> list[str]:
    """Subject for each scene: the user's topic, else the current list section's heading."""
    if topic.strip():
        return [topic.strip()] * len(scenes)
    current = ""
    subjects: list[str] = []
    for scene in scenes:
        for sentence in _script_sentences(str(scene.get("narration") or "")):
            current = heading_subject(sentence) or current
        subjects.append(current)
    return subjects


def scene_keywords(text: str, topic: str, limit: int = 4) -> list[str]:
    """Filmable words from the sentence, in spoken order, minus the topic's own words."""
    topic_words = {_singular(word) for word in _words(topic)}
    roots: set[str] = set()
    found: list[str] = []
    for word in _words(text):
        root = _singular(word)
        parts = set(re.split(r"[-']", word))
        if (word in _STOP or parts & _STOP or "'" in word or root in topic_words
                or len(word) < 3 or root in roots):
            continue
        roots.add(root)
        found.append(word)
    visual = [word for word in found if _singular(word) in PROCESS_WORDS or word in PROCESS_WORDS
              or word in INGREDIENTS or _singular(word) in INGREDIENTS]
    return (visual + [word for word in found if word not in visual])[:limit]


def topic_queries(scene: dict[str, Any], topic: str, era: str = "", recipe: str = "") -> list[str]:
    """Searches that always name the subject, narrowed by the scene's own nouns."""
    source = " ".join(filter(None, [str(scene.get("visual_subject") or ""), str(scene.get("narration") or "")]))
    keys = scene_keywords(source, topic)
    if recipe and topic.strip():
        # The exact dish first ("oatmeal molasses cookies mixed oats"), then its name.
        recipe_keys = [key for key in keys if key not in _words(recipe)]
        queries = [
            f"{recipe} {' '.join(recipe_keys[:2])}".strip(), f"{topic} {' '.join(keys[:1])}".strip(),
            f"{recipe} recipe", topic,
        ]
        return list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    if not topic.strip():
        prefix = f"{era} " if era else ""
        queries = []
        if keys:
            queries.append(f"{prefix}{' '.join(keys[:3])} footage")
            queries.append(f"{prefix}{' '.join(keys[:2])}")
        return list(dict.fromkeys(query.strip() for query in queries))
    queries = []
    if keys:
        queries.append(f"{topic} {' '.join(keys[:2])}")
        queries.append(f"{topic} {keys[0]}")
    queries.append(topic)
    return list(dict.fromkeys(query.strip() for query in queries))


# Titles typical of AI-generated slideshow/"ambience" channels; real footage is preferred.
AI_LIKE_TITLE = re.compile(
    r"\b(ai|a\.i\.|ai[- ]?generated|ai[- ]?art|midjourney|sora|veo\s?\d?|kling|runway ?ml|stable diffusion|"
    r"dall-?e|ambience|ambiance|ambient|aesthetic|vibes|cozy|cosy|dreamcore|liminal|lo-?fi|relaxing|sleep|jazz)\b",
    re.IGNORECASE,
)
# Faceless nostalgia narrators publish AI slideshows; their footage is never real.
NARRATOR_CHANNEL = re.compile(
    r"nostalgi|vintage life|sleepy|we lived in|forgotten flavou?rs|old american|history with|bedtime|"
    r"memories|memory lane|retro|recollection|lost pantry|golden age|dreams|yesteryear|bygone|olden|"
    r"remember when|throwback|time capsule",
    re.IGNORECASE,
)
# Long "25 Forgotten Desserts Grandma Made" / "What Life Was Like" listicles are narrated slideshows.
LISTICLE_TITLE = re.compile(
    r"(\b\d{2,3}\s+(forgotten|vintage|classic|old|retro|lost|nostalgic|things|foods?|recipes|desserts|dinners|"
    r"meals|snacks|treats|dishes)\b)|forgotten|what (life|an? \w+) (was|felt) like|felt like|you could only|"
    r"grandma (made|brought|used)|nobody (makes|remembers)",
    re.IGNORECASE,
)
AI_DISCLOSURE = re.compile(
    r"\b(ai[- ]generated|generated (with|by|using) ai|ai (images?|visuals|voice|narration|art)|synthetic (\(ai\) )?voice|"
    r"artificial intelligence|midjourney|for illustrative purposes|visuali[sz]ations?|recreations?)\b",
    re.IGNORECASE,
)


ERA = re.compile(r"\b(1[89]\d0s|\d0s|'\d0s|fifties|sixties|seventies|eighties|forties|thirties)\b", re.IGNORECASE)
# "From Old America", "Back in the Day": a period named without a decade.
PERIOD_WORDS = re.compile(
    r"\b(old america|old[- ]time|olden days|back in the day|bygone|yesteryear|the past|old days|"
    r"depression[- ]era|war[- ]?time|post[- ]war|mid[- ]century|grandma'?s era)\b",
    re.IGNORECASE,
)
NOSTALGIA_WORDS = re.compile(
    r"\b(forgotten|vanished|lost|grandma'?s?|grandmas|nobody|remember\w*|golden age|back then|used to|"
    r"life in america|felt like|was like|looked like|really looked|why did we stop|nostalgi\w*|disappeared|gone forever|hacks)\b",
    re.IGNORECASE,
)


COUNTED_DISHES = re.compile(
    r"\b\d{1,3}\s+(?:[\w'&-]+\s+){0,4}(dishes|recipes|foods|desserts|dinners|meals|casseroles|snacks|treats|"
    r"cookies|cakes|pies|sides|suppers|salads|breakfasts|lunches)\b",
    re.IGNORECASE,
)
MEMORY_WORDS = re.compile(
    r"\b(grandma\w*|granny|seniors?|boomers?|depression|southern|every(one|body)?|loved|knew|remembered|"
    r"church|mom|mother'?s|old[- ]fashioned|vintage|classic|retro|stories)\b",
    re.IGNORECASE,
)


# Present-day content: recent years, hauls and store tours never open a period story.
MODERN_TITLE = re.compile(
    r"\b(20[0-3]\d|haul|shop with me|store tour|walkthrough|what'?s new at|new at|decor(ate|ating)? with me|"
    r"vlog|day in my life|grwm|tiktok|trend(ing)?)\b",
    re.IGNORECASE,
)


def looks_like_ai_slideshow(item: dict[str, Any], blocked_channels: str = "") -> bool:
    """True for AI-slideshow sources: blocked/narrator channels, long nostalgia listicles, AI disclosures."""
    title = str(item.get("title") or "")
    channel = str(item.get("channel") or item.get("uploader") or "")
    blocked = [name.strip().lower() for name in blocked_channels.split(",") if name.strip()]
    if channel.lower() in blocked or NARRATOR_CHANNEL.search(channel) or AI_LIKE_TITLE.search(title):
        return True
    # "FORGOTTEN Objects in EVERY 1950s Kitchen": era + nostalgia hook is the AI-slideshow formula.
    if (ERA.search(title) or PERIOD_WORDS.search(title)) and NOSTALGIA_WORDS.search(title):
        return True
    duration = float(item.get("duration_seconds") or item.get("duration") or 0)
    if duration > 1800 and LISTICLE_TITLE.search(title):
        return True
    # "10 Church Potluck Dishes Every Southern Grandma Knew": a counted list of dishes told as memories.
    if COUNTED_DISHES.search(title) and (ERA.search(title) or NOSTALGIA_WORDS.search(title) or MEMORY_WORDS.search(title)):
        return True
    return bool(AI_DISCLOSURE.search(str(item.get("description") or "")))


# Whole-video average above this means the source looks like AI imagery.
SYNTHETIC_THRESHOLD = 0.6


# Frames that look like these are never usable B-roll for a topic.
NEGATIVE_PROMPTS = (
    "a person talking to the camera", "a news anchor in a studio", "a title card with text",
    "a logo on a plain background", "a cartoon", "a video game screenshot", "an explosion",
    "a black screen", "a nutrition facts label", "text printed on a food package",
    "a channel logo intro animation",
)
# Photos with the subject's name written on them (a "Chicken Basket" diner sign) are not the subject.
PHOTO_NEGATIVES = ("a shop sign or neon sign", "a building exterior or storefront", "a street with parked cars")
VINTAGE = ("old vintage film footage", ("modern digital video", "a modern smartphone video"))
# Faceless channels never show a present-day person's face (often another creator).
FACE = ("a close-up of a person's face", ("hands preparing food", "food on a table", "an empty kitchen"))
PERSON = ("a person standing in a kitchen, face visible", ("only hands and food", "food with no people", "an empty kitchen"))
FACE_LIMIT = 0.45
# A host speaking to camera is another creator even in a vintage dress and set.
PRESENTER = ("a person looking into the camera and talking", (
    "people in an old home movie", "hands preparing food", "food on a table", "an empty kitchen",
))
PRESENTER_LIMIT = 0.6
HOST = ("a TV cooking show host speaking to the audience", (
    "people in an old home movie", "hands preparing food", "food on a table", "an empty kitchen", "a crowd of people",
))
HOST_LIMIT = 0.7
# Only footage that truly looks like old film may show period people (the hook's home movies).
OLD_FILM = ("an old black and white or faded film photograph", ("a modern colour digital photo",))
OLD_FILM_LIMIT = 0.7
VINTAGE_LIMIT = 0.5


def modern_face(face: float, vintage: float) -> bool:
    """A clear face in modern footage; archival people from the period are fine."""
    return face > FACE_LIMIT and vintage < 0.5
# Channel intros and end screens live here; skip them in longer sources.
EDGE_SKIP_SECONDS = 8.0


class ClipScorer:
    """Zero-shot image-text matching with OpenCLIP, loaded once and shared."""

    _lock = threading.Lock()
    _model: Any = None

    @classmethod
    def available(cls) -> bool:
        try:
            import open_clip  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    @classmethod
    def _load(cls) -> tuple[Any, Any, Any, Any]:
        with cls._lock:
            if cls._model is None:
                import open_clip
                import torch

                device = "mps" if torch.backends.mps.is_available() else "cpu"
                model, _, preprocess = open_clip.create_model_and_transforms(
                    "ViT-B-32", pretrained="laion2b_s34b_b79k", device=device,
                )
                model.eval()
                cls._model = (model, preprocess, open_clip.get_tokenizer("ViT-B-32"), device)
            return cls._model

    def embed_images(self, images: list[Any]) -> Any:
        import torch

        model, preprocess, _tokenizer, device = self._load()
        chunks = []
        with torch.no_grad():
            for start in range(0, len(images), 64):
                batch = torch.stack([preprocess(image) for image in images[start:start + 64]]).to(device)
                features = model.encode_image(batch)
                chunks.append((features / features.norm(dim=-1, keepdim=True)).cpu())
        return torch.cat(chunks) if chunks else torch.empty(0)

    def embed_texts(self, texts: list[str]) -> Any:
        import torch

        model, _preprocess, tokenizer, device = self._load()
        with torch.no_grad():
            features = model.encode_text(tokenizer(texts).to(device))
            return (features / features.norm(dim=-1, keepdim=True)).cpu()

    def probabilities(self, image_features: Any, positive: str, negatives: tuple[str, ...]) -> list[float]:
        """Probability (0-1) that each image shows `positive` rather than any negative."""
        if len(image_features) == 0:
            return []
        text = self.embed_texts([f"a photo of {positive}", *(f"a photo of {item}" for item in negatives)])
        probabilities = (100.0 * image_features @ text.T).softmax(dim=-1)
        return [float(value) for value in probabilities[:, 0]]

    def score(self, images: list[Any], positive: str, negatives: tuple[str, ...] = NEGATIVE_PROMPTS) -> list[float]:
        return self.probabilities(self.embed_images(images), positive, negatives)

    def lookalikes(self, topic: str, count: int = 5) -> tuple[str, ...]:
        """Visually similar things CLIP could confuse with the topic (bison for musk ox)."""
        from open_clip.zero_shot_metadata import IMAGENET_CLASSNAMES

        topic_words = {_singular(word) for word in _words(topic)}
        names = [re.sub(r"\s*\(.*?\)", "", name).strip() for name in IMAGENET_CLASSNAMES]
        names = [name for name in dict.fromkeys(names) if not {_singular(word) for word in _words(name)} & topic_words]
        topic_feature = self.embed_texts([f"a photo of a {topic}"])
        name_features = self.embed_texts([f"a photo of a {name}" for name in names])
        similarity = (topic_feature @ name_features.T)[0]
        ranked = [names[int(index)] for index in similarity.argsort(descending=True)]
        return tuple(f"a {name}" for name in ranked[:count])


MAX_SHEETS = 40


def storyboard_frames(info: dict[str, Any], max_frames: int = 80) -> list[tuple[float, Any]]:
    """Return (timestamp, PIL image) tiles from YouTube's storyboard sprites."""
    from PIL import Image

    boards = [
        item for item in info.get("formats") or []
        if str(item.get("format_id", "")).startswith("sb") and item.get("fragments") and item.get("rows")
    ]
    if not boards:
        return []
    board = max(boards, key=lambda item: int(item.get("width") or 0))
    rows, columns = int(board["rows"]), int(board["columns"])
    fps = float(board.get("fps") or 0) or 0.5
    duration = float(info.get("duration") or 0)
    per_sheet = rows * columns
    fragments = board["fragments"]
    starts: list[float] = []
    cursor = 0.0
    for fragment in fragments:
        starts.append(cursor)
        cursor += float(fragment.get("duration") or per_sheet / fps)
    # Frames evenly spread over the WHOLE video (a moment anywhere can be found), but never
    # more than MAX_SHEETS sprite downloads: long sources get fewer, still evenly spaced frames.
    cells = [(number, cell) for number in range(len(fragments)) for cell in range(per_sheet)
             if not duration or starts[number] + cell / fps < duration]
    if not cells:
        return []
    count = min(len(cells), max_frames)
    picked = [cells[int((index + 0.5) * len(cells) / count)] for index in range(count)]
    sheets = sorted({number for number, _cell in picked})
    if len(sheets) > MAX_SHEETS:
        keep = {sheets[round(index * (len(sheets) - 1) / (MAX_SHEETS - 1))] for index in range(MAX_SHEETS)}
        picked = [item for item in picked if item[0] in keep]
    wanted_by_sheet: dict[int, list[int]] = {}
    for number, cell in picked:
        wanted_by_sheet.setdefault(number, []).append(cell)
    jobs = [(starts[number], str(fragments[number]["url"]), cells_wanted) for number, cells_wanted in sorted(wanted_by_sheet.items())]

    def fetch(job: tuple[float, str, list[int]]) -> list[tuple[float, Any]]:
        sheet_start, url, wanted = job
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=10, context=verified_ssl_context()) as response:
                sheet = Image.open(io.BytesIO(response.read())).convert("RGB")
        except (OSError, ValueError):
            return []
        width, height = sheet.width // columns, sheet.height // rows
        found = []
        for cell in wanted:
            timestamp = sheet_start + cell / fps
            if duration and timestamp >= duration:
                break
            row, column = divmod(cell, columns)
            found.append((timestamp, sheet.crop((column * width, row * height, (column + 1) * width, (row + 1) * height))))
        return found

    with ThreadPoolExecutor(max_workers=6) as pool:
        tiles = [tile for sheet in pool.map(fetch, jobs) for tile in sheet]
    return sorted(tiles, key=lambda tile: tile[0])


def best_window(frames: list[tuple[float, float]], clip_duration: float) -> tuple[float, float]:
    """Pick the start time whose frames over `clip_duration` score highest on average."""
    if not frames:
        return 0.0, 0.0
    best_start, best_score = frames[0][0], -1.0
    for index, (start, _score) in enumerate(frames):
        window = [score for time, score in frames[index:] if time < start + max(clip_duration, 0.1)]
        average = sum(window) / len(window)
        if average > best_score:
            best_start, best_score = start, average
    return best_start, best_score


# A window must look like the topic at least this confidently to be used.
TOPIC_THRESHOLD = 0.6


class FootageVerifier:
    """Checks candidate YouTube videos frame by frame before any excerpt is used."""

    def __init__(self, scorer: ClipScorer | None = None):
        self.scorer = scorer or ClipScorer()
        self._frames: dict[str, tuple[list[float], Any]] = {}
        self._lookalikes: dict[str, tuple[str, ...]] = {}
        self._grey: dict[str, Any] = {}
        self._synthetic: dict[str, float] = {}
        # Small storyboard tiles, kept so the final judge can see the chosen moment.
        self._tiles: dict[str, list[tuple[float, Any]]] = {}
        # Storyboard times where macOS Vision sees a clear face (someone on camera).
        self._face_times: dict[str, set[float]] = {}
        # Hard cuts of sources read in full (whole-video check); windows never span one.
        self._cuts: dict[str, list[float]] = {}
        # One model on one GPU: scene threads take turns.
        self.lock = threading.RLock()

    def rank_photos(
        self, photos: list[Any], subject: str, scene_text: str, recipe: str = "",
    ) -> list[tuple[int, float]]:
        """(index, score) of photos that clearly show the subject and are not AI-looking, best first."""
        if not photos:
            return []
        with self.lock:
            features = self.scorer.embed_images(photos)
            core = core_subject(recipe or subject)
            keys = scene_keywords(scene_text, f"{subject} {recipe}", limit=3)
            detail = " ".join(filter(None, [core, *keys])).strip() or scene_text[:100]
            # Same rule as for footage: recipe steps must show the step, not a finished dish.
            process = is_process_scene(scene_text) and bool(keys)
            gate = self.scorer.probabilities(
                features, " ".join(keys) if process else (core or detail),
                self.negatives(core, scene_text) + PHOTO_NEGATIVES,
            )
            scene = self.scorer.probabilities(features, detail, (core,)) if core and keys else [0.5] * len(photos)
            render = self.scorer.probabilities(
                features, "a hyperrealistic AI render, overly perfect and saturated",
                ("an ordinary real photo", "real camera footage"),
            )
            drawing = self.scorer.probabilities(features, "a drawing, painting or illustration", ("a photograph",))
            faces = [max(a, b) for a, b in zip(self.scorer.probabilities(features, *FACE),
                                               self.scorer.probabilities(features, *PERSON))]
            vintage = self.scorer.probabilities(features, *VINTAGE)
        ranked = [
            (index, 0.4 * gate[index] + 0.6 * scene[index])
            for index in range(len(photos))
            if gate[index] >= TOPIC_THRESHOLD and max(render[index], drawing[index]) < SYNTHETIC_THRESHOLD
            and not modern_face(faces[index], vintage[index])
        ]
        return sorted(ranked, key=lambda item: item[1], reverse=True)

    def rank_ingredient_photos(
        self, photos: list[Any], name: str, kind: str = "a cooking ingredient",
    ) -> list[tuple[int, float]]:
        """Photos that plainly show the item itself, not a factory, field, poster or unrelated dish."""
        if not photos:
            return []
        with self.lock:
            features = self.scorer.embed_images(photos)
            plain = self.scorer.probabilities(features, f"a close-up photo of {name}, {kind}", (
                "a factory or industrial building", "a farm field or growing plants", "an advertisement or poster",
                *(("a decorated plate of finished food",) if "ingredient" in kind else ()),
                "a landscape", "a page of text", "a person",
            ))
            render = self.scorer.probabilities(
                features, "a hyperrealistic AI render, overly perfect and saturated",
                ("an ordinary real photo", "real camera footage"),
            )
            # An ingredient card shows the food, never a cook holding it.
            people = [max(a, b) for a, b in zip(self.scorer.probabilities(features, *FACE),
                                                self.scorer.probabilities(features, *PERSON))]
        ranked = [(index, plain[index]) for index in range(len(photos))
                  if plain[index] >= 0.6 and render[index] < SYNTHETIC_THRESHOLD and people[index] < FACE_LIMIT]
        return sorted(ranked, key=lambda item: item[1], reverse=True)

    def shows_creator(self, frames: list[Any], face_areas: list[list[float]] | None = None, archival_ok: bool = False) -> bool:
        """True when full-size frames of a downloaded clip show a present-day person or a host on camera.

        `face_areas` (macOS Vision) is trusted first: any clear face means someone is on camera. Genuinely
        old film may keep its people only where `archival_ok` (the hook)."""
        if not frames:
            return False
        with self.lock:
            features = self.scorer.embed_images(frames)
            faces = [max(a, b) for a, b in zip(self.scorer.probabilities(features, *FACE),
                                                self.scorer.probabilities(features, *PERSON))]
            vintage = self.scorer.probabilities(features, *VINTAGE)
            host = self.scorer.probabilities(features, *HOST)
            old_film = self.scorer.probabilities(features, *OLD_FILM)
        from .text_guard import MIN_FACE_AREA

        for index, (face, old, on_camera) in enumerate(zip(faces, vintage, host)):
            if on_camera > HOST_LIMIT:
                return True
            seen = face_areas[index] if face_areas and index < len(face_areas) else None
            clear_face = any(area >= MIN_FACE_AREA for area in seen) if seen is not None else modern_face(face, old)
            if clear_face and not (archival_ok and old_film[index] >= OLD_FILM_LIMIT):
                return True
        return False

    def has_frames(self, video_id: str) -> bool:
        return video_id in self._frames

    def add_frames(self, video_id: str, tiles: list[tuple[float, Any]]) -> None:
        with self.lock:
            self._add_frames(video_id, tiles)

    def _add_frames(self, video_id: str, tiles: list[tuple[float, Any]]) -> None:
        from .logo_guard import storyboard_stack

        self._frames[video_id] = ([time for time, _image in tiles], self.scorer.embed_images([image for _time, image in tiles]))
        self._tiles[video_id] = [(time, image) for time, image in tiles]
        while len(self._tiles) > 60:
            self._tiles.pop(next(iter(self._tiles)))
        # Greyscale frames from across the whole video let logo detection see what never moves.
        stack = storyboard_stack(tiles)
        self._grey[video_id] = None if stack is None else stack.astype("uint8")
        while len(self._grey) > 24:
            self._grey.pop(next(iter(self._grey)))

    def set_whole_video(self, video_id: str, frames: list[tuple[float, Any]], cuts: list[float]) -> None:
        """Replace storyboard frames with a whole-video read (one frame per second) and its cuts."""
        with self.lock:
            self._add_frames(video_id, frames)
            self._cuts[video_id] = sorted(cuts)
            self._face_times.pop(video_id, None)
            self._synthetic.pop(video_id, None)

    def has_whole_video(self, video_id: str) -> bool:
        return video_id in self._cuts

    def mark_faces(self, video_id: str) -> None:
        """Find storyboard moments with a clear face once per source, so they are never picked."""
        if video_id in self._face_times:
            return
        import tempfile
        from pathlib import Path

        from .text_guard import MIN_FACE_AREA, available, face_areas

        tiles = self._tiles.get(video_id) or []
        found: set[float] = set()
        if available() and tiles:
            with tempfile.TemporaryDirectory() as folder:
                for index, (time, image) in enumerate(tiles):
                    path = Path(folder) / f"tile-{index}.jpg"
                    image.convert("RGB").save(path, quality=85)
                    if any(area >= MIN_FACE_AREA for area in face_areas(path)):
                        found.add(time)
        self._face_times[video_id] = found

    def moment_frames(self, video_id: str, start: float, duration: float, count: int = 3) -> list[Any]:
        """Storyboard tiles nearest the start, middle and end of a chosen moment."""
        tiles = self._tiles.get(video_id) or []
        if not tiles:
            return []
        wanted = [start + duration * index / max(1, count - 1) for index in range(count)]
        chosen = [min(tiles, key=lambda tile: abs(tile[0] - moment))[1] for moment in wanted]
        unique: list[Any] = []
        for image in chosen:
            if all(image is not other for other in unique):
                unique.append(image)
        return unique

    def grey_frames(self, video_id: str) -> Any:
        stack = self._grey.get(video_id)
        return None if stack is None else stack.astype("float32")

    def _video_frames(self, video_id: str, info: dict[str, Any]) -> tuple[list[float], Any]:
        if video_id not in self._frames:
            self.add_frames(video_id, storyboard_frames(info))
        return self._frames[video_id]

    def synthetic_score(self, video_id: str) -> float:
        """How much the whole source looks like AI art rather than camera footage (0-1)."""
        with self.lock:
            return self._synthetic_score(video_id)

    def _synthetic_score(self, video_id: str) -> float:
        if video_id not in self._synthetic:
            _times, features = self._frames.get(video_id, ([], []))
            if len(features):
                render = self.scorer.probabilities(
                    features, "a hyperrealistic AI render, overly perfect and saturated",
                    ("an ordinary real photo", "real camera footage"),
                )
                drawing = self.scorer.probabilities(
                    features, "a drawing, painting or illustration", ("a photograph", "real camera footage"),
                )
                self._synthetic[video_id] = max(sum(render) / len(render), sum(drawing) / len(drawing))
            else:
                self._synthetic[video_id] = 0.0
        return self._synthetic[video_id]

    def negatives(self, subject: str, scene_text: str) -> tuple[str, ...]:
        if subject and subject not in self._lookalikes:
            self._lookalikes[subject] = self.scorer.lookalikes(subject)
        scene_words = {_singular(word) for word in _words(scene_text)}
        # "dough" must not count against a scene that is about cookie dough.
        similar = tuple(
            item for item in self._lookalikes.get(subject, ())
            if not {_singular(word) for word in _words(item)} & scene_words
        )
        return similar + NEGATIVE_PROMPTS

    def best_moment(
        self, video_id: str, info: dict[str, Any], subject: str, scene_text: str, clip_duration: float,
        avoid: list[float] | None = None, recipe: str = "", prefer_vintage: bool = False,
        require_vintage: bool = False,
    ) -> tuple[float, float, float] | None:
        """Return (start, subject_score, scene_score) for the best unused window, or None."""
        with self.lock:
            return self._best_moment(
                video_id, info, subject, scene_text, clip_duration, avoid, recipe, prefer_vintage, require_vintage,
            )

    def _best_moment(
        self, video_id: str, info: dict[str, Any], subject: str, scene_text: str, clip_duration: float,
        avoid: list[float] | None = None, recipe: str = "", prefer_vintage: bool = False,
        require_vintage: bool = False,
    ) -> tuple[float, float, float] | None:
        times, features = self._video_frames(video_id, info)
        if not times:
            return None
        core = core_subject(recipe or subject)
        keys = scene_keywords(scene_text, f"{subject} {recipe}", limit=3)
        detail = " ".join(filter(None, [core, *keys])).strip() or scene_text[:100]
        # Normally the dish itself must be visible ("oatmeal molasses cookies", "musk ox").
        # Recipe steps ("mixed rolled oats in a bowl") show the action instead, before any
        # cookie exists, so they are gated on the action and its ingredients.
        process = is_process_scene(scene_text) and bool(keys)
        gate_text = " ".join(keys) if process else (core or detail)
        gate_scores = self.scorer.probabilities(features, gate_text, self.negatives(core, scene_text))
        # How much better the frame fits this sentence ("musk ox calves playing")
        # than the subject in general ("musk ox"); ranks moments that passed the gate.
        scene_scores = (
            self.scorer.probabilities(features, detail, (core,)) if core and keys else [0.5] * len(times)
        )
        combined = [0.4 * gate + 0.6 * scene for gate, scene in zip(gate_scores, scene_scores)]
        vintage = self.scorer.probabilities(features, *VINTAGE)
        close = self.scorer.probabilities(features, *FACE)
        people = self.scorer.probabilities(features, *PERSON)
        # Close-ups and medium shots both show whose kitchen it is: either counts as a face.
        faces = [max(a, b) for a, b in zip(close, people)]
        presenter = self.scorer.probabilities(features, *PRESENTER)
        if prefer_vintage:
            # A period story reads best on footage that already looks old.
            combined = [value + 0.25 * old for value, old in zip(combined, vintage)]
        source_length = float(info.get("duration") or 0)
        best: tuple[float, float, float] | None = None
        for index, start in enumerate(times):
            if any(abs(start - used) < 15 for used in avoid or []):
                continue
            if source_length > 60 and (
                start < EDGE_SKIP_SECONDS or start + clip_duration > source_length - EDGE_SKIP_SECONDS
            ):
                continue
            window = [position for position in range(index, len(times)) if times[position] < start + max(clip_duration, 0.1)]
            gate_average = sum(gate_scores[position] for position in window) / len(window)
            if gate_average < TOPIC_THRESHOLD:
                continue
            if any(modern_face(faces[position], vintage[position]) or presenter[position] > PRESENTER_LIMIT
                   for position in window):
                continue
            if not require_vintage and any(times[position] in self._face_times.get(video_id, ()) for position in window):
                continue  # someone on camera (Vision saw a clear face in the storyboard)
            if any(start + 0.15 < cut < start + clip_duration - 0.15 for cut in self._cuts.get(video_id, ())):
                continue  # a hard cut inside the window would be a jump cut
            if require_vintage and sum(vintage[position] for position in window) / len(window) < VINTAGE_LIMIT:
                continue  # the opening of a period story is always old footage
            score = sum(combined[position] for position in window) / len(window)
            if best is None or score > best[2]:
                best = (start, gate_average, score)
        return best
