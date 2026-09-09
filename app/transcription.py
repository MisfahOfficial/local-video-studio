from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def probe_duration(path: Path, ffprobe_path: str = "ffprobe") -> float:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


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

