"""Burned-in text check: another creator's captions ("BAKE 350 FOR 90 MINUTES") must not
appear under our own. Uses the free, on-device macOS Vision text reader; elsewhere it is skipped."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

# A sentence-like line, or this much readable writing in one frame, means an overlay.
MIN_LINE_WORDS = 3
MIN_LETTERS = 24
MIN_CONFIDENCE = 0.8


def available() -> bool:
    try:
        import Vision  # noqa: F401
    except ImportError:
        return False
    return True


def read_text(image_path: Path) -> list[tuple[str, float]]:
    """(line, confidence) for every line of text macOS Vision finds in the image."""
    import Vision
    from Foundation import NSURL

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(image_path)), None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)  # accurate; still ~50 ms per frame
    request.setUsesLanguageCorrection_(False)
    handler.performRequests_error_([request], None)
    lines = []
    for result in request.results() or []:
        candidate = result.topCandidates_(1)[0]
        lines.append((str(candidate.string()), float(candidate.confidence())))
    return lines


def is_overlay(lines: list[tuple[str, float]]) -> bool:
    readable = [text for text, confidence in lines if confidence >= MIN_CONFIDENCE]
    if any(len(re.findall(r"[A-Za-z]{2,}", text)) >= MIN_LINE_WORDS for text in readable):
        return True
    return sum(len(re.findall(r"[A-Za-z]", text)) for text in readable) >= MIN_LETTERS


def has_burned_in_text(clip: Path, ffmpeg_path: str = "ffmpeg", samples: int = 3) -> bool:
    """True when frames across the clip carry written captions; False if unsure or unsupported."""
    if not available():
        return False
    try:
        probe = subprocess.run(
            [ffmpeg_path, "-i", str(clip)], capture_output=True, text=True, timeout=30,
        ).stderr
        match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else 0.0
        with tempfile.TemporaryDirectory() as folder:
            for index in range(samples):
                moment = duration * (index + 0.5) / samples
                frame = Path(folder) / f"frame-{index}.jpg"
                subprocess.run(
                    [ffmpeg_path, "-v", "error", "-y", "-ss", f"{moment:.2f}", "-i", str(clip),
                     "-frames:v", "1", "-vf", "scale=1280:-2", str(frame)],
                    capture_output=True, timeout=30, check=True,
                )
                if is_overlay(read_text(frame)):
                    return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return False
