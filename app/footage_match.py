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
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]+", text.lower().replace("’", "'"))


def _singular(word: str) -> str:
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


def scene_keywords(text: str, topic: str, limit: int = 4) -> list[str]:
    topic_words = {_singular(word) for word in _words(topic)}
    found: list[str] = []
    for word in _words(text):
        root = _singular(word)
        if word in _STOP or root in topic_words or len(word) < 3 or root in found:
            continue
        found.append(root)
    return found[:limit]


def topic_queries(scene: dict[str, Any], topic: str) -> list[str]:
    """Searches that always name the topic, narrowed by the scene's own nouns."""
    source = " ".join(filter(None, [str(scene.get("visual_subject") or ""), str(scene.get("narration") or "")]))
    keys = scene_keywords(source, topic)
    if not topic.strip():
        return [" ".join(keys + ["footage"])] if keys else []
    queries = []
    if keys:
        queries.append(f"{topic} {' '.join(keys[:2])} footage")
        queries.append(f"{topic} {keys[0]}")
    queries.append(f"{topic} footage")
    return list(dict.fromkeys(query.strip() for query in queries))


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


def storyboard_frames(info: dict[str, Any], max_frames: int = 120) -> list[tuple[float, Any]]:
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

    def __init__(self, topic: str, scorer: ClipScorer | None = None):
        self.topic = topic.strip()
        self.scorer = scorer or ClipScorer()
        self.negatives = (self.scorer.lookalikes(self.topic) if self.topic else ()) + NEGATIVE_PROMPTS
        self._frames: dict[str, tuple[list[float], Any]] = {}

    def has_frames(self, video_id: str) -> bool:
        return video_id in self._frames

    def add_frames(self, video_id: str, tiles: list[tuple[float, Any]]) -> None:
        self._frames[video_id] = ([time for time, _image in tiles], self.scorer.embed_images([image for _time, image in tiles]))

    def _video_frames(self, video_id: str, info: dict[str, Any]) -> tuple[list[float], Any]:
        if video_id not in self._frames:
            tiles = storyboard_frames(info)
            self._frames[video_id] = ([time for time, _image in tiles], self.scorer.embed_images([image for _time, image in tiles]))
        return self._frames[video_id]

    def best_moment(
        self, video_id: str, info: dict[str, Any], scene_text: str, clip_duration: float,
        avoid: list[float] | None = None,
    ) -> tuple[float, float, float] | None:
        """Return (start, topic_score, scene_score) for the best unused window, or None."""
        times, features = self._video_frames(video_id, info)
        if not times:
            return None
        subject = self.topic or scene_text[:120]
        topic_scores = self.scorer.probabilities(features, subject, self.negatives)
        keys = scene_keywords(scene_text, self.topic, limit=3)
        detail = f"{subject} {' '.join(keys)}".strip()
        # How much better the frame fits this sentence ("musk ox calves playing")
        # than the topic in general ("musk ox"); ranks moments that passed the gate.
        scene_scores = (
            self.scorer.probabilities(features, detail, (subject,)) if keys else [0.5] * len(times)
        )
        combined = [0.4 * topic + 0.6 * scene for topic, scene in zip(topic_scores, scene_scores)]
        best: tuple[float, float, float] | None = None
        for index, start in enumerate(times):
            if any(abs(start - used) < 15 for used in avoid or []):
                continue
            window = [position for position in range(index, len(times)) if times[position] < start + max(clip_duration, 0.1)]
            topic_average = sum(topic_scores[position] for position in window) / len(window)
            if topic_average < TOPIC_THRESHOLD:
                continue
            score = sum(combined[position] for position in window) / len(window)
            if best is None or score > best[2]:
                best = (start, topic_average, score)
        return best
