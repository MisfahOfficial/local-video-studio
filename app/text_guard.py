"""Burned-in text check: another creator's captions ("BAKE 350 FOR 90 MINUTES") must not
appear under our own. Uses the free, on-device macOS Vision text reader; elsewhere it is skipped."""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

# A sentence-like line, or this much readable writing in one frame, means an overlay.
MIN_LINE_WORDS = 3
MIN_LETTERS = 24
MIN_CONFIDENCE = 0.8

# Vision objects are freed only when an autorelease pool drains; worker threads have none, so
# hundreds of frames leaked memory until the server was killed. One call at a time, each in its own pool.
_vision_lock = threading.Lock()


def available() -> bool:
    try:
        import Vision  # noqa: F401
    except ImportError:
        return False
    return True


def _handler(Vision: Any, image_path: Path) -> Any:
    """Vision reads the picture from memory: a handler opened on the file's URL kept the file open, and a
    277-scene run reached 7,000 open files, after which the tool could open nothing (1 Oct, C1 video)."""
    from Foundation import NSData

    data = Path(image_path).read_bytes()
    return Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(data, len(data)), None)


def read_text(image_path: Path) -> list[tuple[str, float]]:
    """(line, confidence) for every line of text macOS Vision finds in the image."""
    import objc
    import Vision

    with _vision_lock, objc.autorelease_pool():
        handler = _handler(Vision, image_path)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(0)  # accurate; still ~50 ms per frame
        request.setUsesLanguageCorrection_(False)
        handler.performRequests_error_([request], None)
        lines = []
        for result in request.results() or []:
            candidate = result.topCandidates_(1)[0]
            lines.append((str(candidate.string()), float(candidate.confidence())))
    return lines


# A face this big (share of the frame) is someone on camera, not a distant crowd.
MIN_FACE_AREA = 0.008
MIN_FACE_CONFIDENCE = 0.6


def face_areas(image_path: Path) -> list[float]:
    """Frame share of every face macOS Vision finds (reliable where CLIP misses a cook at the stove)."""
    if not available():
        return []
    import objc
    import Vision

    with _vision_lock, objc.autorelease_pool():
        handler = _handler(Vision, image_path)
        request = Vision.VNDetectFaceRectanglesRequest.alloc().init()
        handler.performRequests_error_([request], None)
        return [
            float(face.boundingBox().size.width * face.boundingBox().size.height)
            for face in request.results() or [] if float(face.confidence()) >= MIN_FACE_CONFIDENCE
        ]


def is_overlay(lines: list[tuple[str, float]]) -> bool:
    readable = [text for text, confidence in lines if confidence >= MIN_CONFIDENCE]
    if any(len(re.findall(r"[A-Za-z]{2,}", text)) >= MIN_LINE_WORDS for text in readable):
        return True
    return sum(len(re.findall(r"[A-Za-z]", text)) for text in readable) >= MIN_LETTERS


def sample_frames(clip: Path, folder: Path, ffmpeg_path: str = "ffmpeg", samples: int = 3) -> list[Path]:
    """Full-resolution stills from across the clip (storyboards are too small to show a host or a caption)."""
    probe = subprocess.run([ffmpeg_path, "-i", str(clip)], capture_output=True, text=True, timeout=30).stderr
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
    duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else 0.0
    frames = []
    for index in range(samples):
        frame = folder / f"frame-{index}.jpg"
        subprocess.run(
            [ffmpeg_path, "-v", "error", "-y", "-ss", f"{duration * (index + 0.5) / samples:.2f}", "-i", str(clip),
             "-frames:v", "1", "-vf", "scale=1280:-2", str(frame)],
            capture_output=True, timeout=30, check=True,
        )
        frames.append(frame)
    return frames


def has_burned_in_text(clip: Path, ffmpeg_path: str = "ffmpeg", samples: int = 3) -> bool:
    """True when frames across the clip carry written captions; False if unsure or unsupported."""
    if not available():
        return False
    try:
        with tempfile.TemporaryDirectory() as folder:
            return any(is_overlay(read_text(frame)) for frame in sample_frames(clip, Path(folder), ffmpeg_path, samples))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
