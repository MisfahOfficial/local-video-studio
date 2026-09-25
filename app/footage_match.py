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
    "remind", "reminds", "reminded", "subscribe", "subscribed", "answer", "answers", "bring", "bringing",
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
    r"sing[- ]?along)\b",
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
    return ""


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
    return found[:limit]


def topic_queries(scene: dict[str, Any], topic: str, era: str = "") -> list[str]:
    """Searches that always name the subject, narrowed by the scene's own nouns."""
    source = " ".join(filter(None, [str(scene.get("visual_subject") or ""), str(scene.get("narration") or "")]))
    keys = scene_keywords(source, topic)
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
NOSTALGIA_WORDS = re.compile(
    r"\b(forgotten|vanished|lost|grandma'?s?|grandmas|nobody|remember|golden age|back then|used to|"
    r"life in america|felt like|was like|why did we stop|nostalgi\w*|disappeared|gone forever|hacks)\b",
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
    if ERA.search(title) and NOSTALGIA_WORDS.search(title):
        return True
    duration = float(item.get("duration_seconds") or item.get("duration") or 0)
    if duration > 1800 and LISTICLE_TITLE.search(title):
        return True
    return bool(AI_DISCLOSURE.search(str(item.get("description") or "")))


# Whole-video average above this means the source looks like AI imagery.
SYNTHETIC_THRESHOLD = 0.6


# Frames that look like these are never usable B-roll for a topic.
NEGATIVE_PROMPTS = (
    "a person talking to the camera", "a news anchor in a studio", "a title card with text",
    "a logo on a plain background", "a cartoon", "a video game screenshot", "an explosion",
    "a black screen",
)


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
    stride = max(1, round(len(board["fragments"]) * per_sheet / max_frames))
    jobs: list[tuple[float, str, list[int]]] = []
    fragment_start = 0.0
    for number, fragment in enumerate(board["fragments"]):
        wanted = [cell for cell in range(per_sheet) if (number * per_sheet + cell) % stride == 0]
        if wanted:
            jobs.append((fragment_start, str(fragment["url"]), wanted))
        fragment_start += float(fragment.get("duration") or per_sheet / fps)

    def fetch(job: tuple[float, str, list[int]]) -> list[tuple[float, Any]]:
        sheet_start, url, wanted = job
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=30, context=verified_ssl_context()) as response:
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
    return tiles


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
        # One model on one GPU: scene threads take turns.
        self.lock = threading.RLock()

    def rank_photos(self, photos: list[Any], subject: str, scene_text: str) -> list[tuple[int, float]]:
        """(index, score) of photos that clearly show the subject and are not AI-looking, best first."""
        if not photos:
            return []
        with self.lock:
            features = self.scorer.embed_images(photos)
            core = core_subject(subject)
            keys = scene_keywords(scene_text, core, limit=3)
            detail = " ".join(filter(None, [core, *keys])).strip() or scene_text[:100]
            gate = self.scorer.probabilities(features, core or detail, self.negatives(core, scene_text))
            scene = self.scorer.probabilities(features, detail, (core,)) if core and keys else [0.5] * len(photos)
            render = self.scorer.probabilities(
                features, "a hyperrealistic AI render, overly perfect and saturated",
                ("an ordinary real photo", "real camera footage"),
            )
            drawing = self.scorer.probabilities(features, "a drawing, painting or illustration", ("a photograph",))
        ranked = [
            (index, 0.4 * gate[index] + 0.6 * scene[index])
            for index in range(len(photos))
            if gate[index] >= TOPIC_THRESHOLD and max(render[index], drawing[index]) < SYNTHETIC_THRESHOLD
        ]
        return sorted(ranked, key=lambda item: item[1], reverse=True)

    def has_frames(self, video_id: str) -> bool:
        return video_id in self._frames

    def add_frames(self, video_id: str, tiles: list[tuple[float, Any]]) -> None:
        with self.lock:
            self._add_frames(video_id, tiles)

    def _add_frames(self, video_id: str, tiles: list[tuple[float, Any]]) -> None:
        from .logo_guard import storyboard_stack

        self._frames[video_id] = ([time for time, _image in tiles], self.scorer.embed_images([image for _time, image in tiles]))
        # Greyscale frames from across the whole video let logo detection see what never moves.
        stack = storyboard_stack(tiles)
        self._grey[video_id] = None if stack is None else stack.astype("uint8")
        while len(self._grey) > 24:
            self._grey.pop(next(iter(self._grey)))

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
        avoid: list[float] | None = None,
    ) -> tuple[float, float, float] | None:
        """Return (start, subject_score, scene_score) for the best unused window, or None."""
        with self.lock:
            return self._best_moment(video_id, info, subject, scene_text, clip_duration, avoid)

    def _best_moment(
        self, video_id: str, info: dict[str, Any], subject: str, scene_text: str, clip_duration: float,
        avoid: list[float] | None = None,
    ) -> tuple[float, float, float] | None:
        times, features = self._video_frames(video_id, info)
        if not times:
            return None
        core = core_subject(subject)
        keys = scene_keywords(scene_text, core, limit=3)
        detail = " ".join(filter(None, [core, *keys])).strip() or scene_text[:100]
        # The subject itself must be visible ("cookies", "musk ox"); sentence details only
        # rank moments that pass. Scenes without a subject are gated on their details.
        gate_scores = self.scorer.probabilities(features, core or detail, self.negatives(core, scene_text))
        # How much better the frame fits this sentence ("musk ox calves playing")
        # than the subject in general ("musk ox"); ranks moments that passed the gate.
        scene_scores = (
            self.scorer.probabilities(features, detail, (core,)) if core and keys else [0.5] * len(times)
        )
        combined = [0.4 * gate + 0.6 * scene for gate, scene in zip(gate_scores, scene_scores)]
        best: tuple[float, float, float] | None = None
        for index, start in enumerate(times):
            if any(abs(start - used) < 15 for used in avoid or []):
                continue
            window = [position for position in range(index, len(times)) if times[position] < start + max(clip_duration, 0.1)]
            gate_average = sum(gate_scores[position] for position in window) / len(window)
            if gate_average < TOPIC_THRESHOLD:
                continue
            score = sum(combined[position] for position in window) / len(window)
            if best is None or score > best[2]:
                best = (start, gate_average, score)
        return best
