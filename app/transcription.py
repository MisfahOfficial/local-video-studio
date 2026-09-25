from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def probe_duration(path: Path, ffprobe_path: str = "ffprobe") -> float:
    ffprobe_error: Exception | None = None
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(json.loads(result.stdout)["format"]["duration"])
        if duration > 0:
            return duration
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        ffprobe_error = error

    # macOS ships afinfo independently of Homebrew. It keeps VO measurement
    # working when an ffprobe binary exists but one of its dylibs is broken.
    afinfo = shutil.which("afinfo")
    if afinfo:
        try:
            result = subprocess.run(
                [afinfo, str(path)], capture_output=True, text=True, check=True
            )
            match = re.search(r"estimated duration:\s*([0-9]+(?:\.[0-9]+)?)\s*sec", result.stdout)
            if match and float(match.group(1)) > 0:
                return float(match.group(1))
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass

    raise RuntimeError(
        "The voice-over duration could not be measured. Repair FFprobe or re-upload the audio."
    ) from ffprobe_error


class FasterWhisperTranscriber:
    def __init__(self, model_size: str = "small", compute_type: str = "int8"):
        self.model_size = model_size
        self.compute_type = compute_type

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError("Faster-Whisper is not installed. Run: pip install '.[transcription]'") from error
        model = WhisperModel(self.model_size, device="cpu", compute_type=self.compute_type)
        segments, _info = model.transcribe(str(audio_path), word_timestamps=True, vad_filter=True)
        return [
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": segment.text.strip(),
                "words": [
                    {"start": float(word.start or 0), "end": float(word.end or 0), "word": word.word}
                    for word in (segment.words or [])
                ],
            }
            for segment in segments
        ]
