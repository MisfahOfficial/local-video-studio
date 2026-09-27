"""Encode frames from a template function into a clip.

A template is `frame(t, duration) -> PIL.Image` (RGB for full-screen graphics,
RGBA for overlays). Opaque clips become H.264 MP4; overlays become QuickTime
Animation (qtrle) MOVs, which keep the alpha channel for compositing.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageFont

Frame = Callable[[float, float], Image.Image]
SIZE = (1920, 1080)

SANS_FONTS = (
    "/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
)
SANS_BOLD_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/HelveticaNeue.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
)
SERIF_ITALIC_FONTS = (
    "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf", "C:/Windows/Fonts/georgiaz.ttf",
)


def font(candidates: tuple[str, ...], size: int, index: int = 0) -> Any:
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def ease_out_cubic(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return 1 - (1 - value) ** 3


def ease_out_back(value: float, overshoot: float = 1.4) -> float:
    value = min(1.0, max(0.0, value))
    return 1 + (overshoot + 1) * (value - 1) ** 3 + overshoot * (value - 1) ** 2


def ease_in_out(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return 0.5 - 0.5 * math.cos(math.pi * value)


def encode(frame: Frame, duration: float, destination: Path, *, fps: int = 30, alpha: bool = False,
           ffmpeg_path: str = "ffmpeg", size: tuple[int, int] = SIZE) -> Path:
    """Render every frame of `frame` over `duration` seconds into `destination`."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = max(1, round(duration * fps))
    command = [
        ffmpeg_path, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba" if alpha else "rgb24",
        "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-",
    ]
    if alpha:
        command += ["-c:v", "qtrle", str(destination)]
    else:
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(destination)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    mode = "RGBA" if alpha else "RGB"
    try:
        for index in range(total):
            image = frame(index / fps, duration)
            if image.size != size:
                image = image.resize(size)
            process.stdin.write(image.convert(mode).tobytes())
        process.stdin.close()
    except BrokenPipeError:
        pass
    error = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
    if process.wait() != 0 or not destination.is_file():
        raise RuntimeError(f"Motion graphic encode failed: {error[-600:]}")
    return destination
