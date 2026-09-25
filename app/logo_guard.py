"""Find burned-in channel logos/watermarks and pick a zoomed crop that hides them.

A logo is detail that never moves while the picture around it does, so it shows
up as strong edges with almost no change over time. Only the border zones are
searched, where broadcasters and channels place their bugs.
"""
from __future__ import annotations

import subprocess
from itertools import combinations
from pathlib import Path
from typing import Any

WIDTH, HEIGHT = 320, 180
MAX_ZOOM = 1.35


def _frames(path: Path, ffmpeg_path: str, count: int = 12) -> Any:
    import numpy as np

    try:
        result = subprocess.run(
            [ffmpeg_path, "-v", "error", "-i", str(path), "-vf",
             f"fps=4,scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=disable,format=gray",
             "-frames:v", str(count * 3), "-f", "rawvideo", "-"],
            capture_output=True, timeout=120, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    frames = np.frombuffer(result.stdout, dtype=np.uint8)
    total = frames.size // (WIDTH * HEIGHT)
    if total < 4:
        return None
    frames = frames[: total * WIDTH * HEIGHT].reshape(total, HEIGHT, WIDTH).astype(np.float32)
    step = max(1, total // count)
    return frames[::step][:count]


# Border zones as (x0, y0, x1, y1) fractions: four corners plus top/bottom centre strips.
ZONES = (
    (0.0, 0.0, 0.32, 0.26), (0.68, 0.0, 1.0, 0.26), (0.0, 0.74, 0.32, 1.0), (0.68, 0.74, 1.0, 1.0),
    (0.32, 0.0, 0.68, 0.16), (0.32, 0.84, 0.68, 1.0),
)


def static_boxes(
    frames: Any, *, edge_threshold: float, motion_threshold: float, min_motion: float = 0.0,
    max_area: float = 1.0, min_density: float = 0.0, max_static_share: float = 1.0,
) -> list[tuple[float, float, float, float]]:
    """Boxes of sharp detail that stays put while the rest of the picture changes."""
    import numpy as np

    if frames is None or len(frames) < 4:
        return []
    frames = frames[frames.std(axis=(1, 2)) > 8]  # drop black/blank padding frames
    if len(frames) < 4:
        return []
    motion = frames.std(axis=0)
    if float(np.median(motion)) < min_motion:
        return []
    median = np.median(frames, axis=0)
    gradient_y, gradient_x = np.gradient(median)
    candidate = (np.hypot(gradient_x, gradient_y) > edge_threshold) & (motion < motion_threshold)
    height, width = candidate.shape
    boxes: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1 in ZONES:
        left, top, right, bottom = int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)
        zone = candidate[top:bottom, left:right]
        if zone.sum() < max(10, zone.size * 0.004) or zone.mean() > max_static_share:
            continue  # nothing there, or a static camera's background rather than a logo
        # Trim sparse tails so the box hugs the dense logo, not stray static pixels.
        row_counts, column_counts = zone.sum(axis=1), zone.sum(axis=0)
        rows = np.where(row_counts >= max(2, 0.2 * row_counts.max()))[0]
        columns = np.where(column_counts >= max(2, 0.2 * column_counts.max()))[0]
        if rows.size < 3 or columns.size < 3:
            continue
        area = (rows[-1] - rows[0] + 1) * (columns[-1] - columns[0] + 1)
        if area / (width * height) > max_area:
            continue
        if zone[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1].mean() < min_density:
            continue
        box = (
            (left + columns[0] - 2) / width, (top + rows[0] - 2) / height,
            (left + columns[-1] + 3) / width, (top + rows[-1] + 3) / height,
        )
        boxes.append(tuple(min(1.0, max(0.0, value)) for value in box))
    return boxes


def storyboard_stack(tiles: list[tuple[float, Any]]) -> Any:
    """Greyscale 320x180 frames from storyboard tiles spread over the whole source video."""
    import numpy as np

    if not tiles:
        return None
    return np.stack([np.asarray(image.convert("L").resize((WIDTH, HEIGHT)), dtype=np.float32) for _time, image in tiles])


def find_logos(
    path: Path, ffmpeg_path: str = "ffmpeg", source_frames: Any = None,
) -> list[tuple[float, float, float, float]]:
    """Normalized (x0, y0, x1, y1) logo boxes for a clip.

    Frames from across the whole source video (its storyboard) catch translucent
    logos over slow footage; the clip's own frames catch logos added only there.
    """
    boxes = static_boxes(
        source_frames, edge_threshold=10, motion_threshold=35, max_area=0.06,
        min_density=0.08, max_static_share=0.3,
    )
    boxes += static_boxes(_frames(path, ffmpeg_path), edge_threshold=18, motion_threshold=3, min_motion=4.0)
    merged: list[tuple[float, float, float, float]] = []
    for box in boxes:
        for index, other in enumerate(merged):
            if box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]:
                merged[index] = (min(box[0], other[0]), min(box[1], other[1]), max(box[2], other[2]), max(box[3], other[3]))
                break
        else:
            merged.append(box)
    return merged


def _smallest_crop(boxes: list[tuple[float, float, float, float]]) -> dict[str, float] | None:
    steps = 24
    for zoom_step in range(0, int(round((MAX_ZOOM - 1.0) * 100)) + 1):
        size = 1.0 / (1.0 + zoom_step * 0.01)
        for ix in range(steps + 1):
            x = (1.0 - size) * ix / steps
            for iy in range(steps + 1):
                y = (1.0 - size) * iy / steps
                if all(bx1 <= x or bx0 >= x + size or by1 <= y or by0 >= y + size for bx0, by0, bx1, by1 in boxes):
                    return {"x": round(x, 4), "y": round(y, 4), "w": round(size, 4), "h": round(size, 4)}
    return None


def _in_corner(box: tuple[float, float, float, float]) -> bool:
    return (box[0] < 0.32 or box[2] > 0.68) and (box[1] < 0.26 or box[3] > 0.74)


def safe_crop(boxes: list[tuple[float, float, float, float]]) -> tuple[dict[str, float] | None, bool]:
    """Smallest same-aspect crop (x, y, w, h) that hides the logos.

    Corner boxes (where channel bugs live) are hidden first; other boxes are added
    only while a crop of at most MAX_ZOOM can still hide all chosen ones.
    Returns (crop, ok): crop is None when nothing needs hiding; ok is False when a
    corner logo could not be hidden.
    """
    if not boxes:
        return None, True
    corners = [box for box in boxes if _in_corner(box)][:6]
    others = [box for box in boxes if not _in_corner(box)]

    def edge_gap(box: tuple[float, float, float, float]) -> float:
        # Channel bugs hug the frame edge; stray static detail usually does not.
        return min(box[0], box[1], 1.0 - box[2], 1.0 - box[3])

    best: tuple[tuple[int, float], list[tuple[float, float, float, float]], dict[str, float]] | None = None
    for size in range(len(corners), 0, -1):
        for subset in combinations(corners, size):
            crop = _smallest_crop(list(subset))
            if crop is None:
                continue
            rank = (size, -sum(edge_gap(box) for box in subset))
            if best is None or rank > best[0]:
                best = (rank, list(subset), crop)
        if best is not None:
            break
    chosen, crop = (best[1], best[2]) if best else ([], None)
    for box in others:  # hide other static detail too when it costs no extra feasibility
        trial = _smallest_crop([*chosen, box])
        if trial is not None:
            chosen.append(box)
            crop = trial
    return crop, len(chosen) >= len(corners) or bool(corners and best and best[0][0] == len(corners))


def crop_filter(crop: dict[str, Any] | None) -> str:
    """FFmpeg crop for a normalized crop box; '' when there is nothing to hide."""
    if not crop:
        return ""
    return (
        f"crop=trunc(iw*{float(crop['w']):.4f}/2)*2:trunc(ih*{float(crop['h']):.4f}/2)*2:"
        f"iw*{float(crop['x']):.4f}:ih*{float(crop['y']):.4f}"
    )


def analyse_clip(path: Path, ffmpeg_path: str = "ffmpeg", source_frames: Any = None) -> dict[str, Any]:
    """Metadata to store on a sourced clip: logo boxes, crop, and whether it needs review."""
    boxes = find_logos(path, ffmpeg_path, source_frames)
    crop, ok = safe_crop(boxes)
    return {"logo_boxes": [list(box) for box in boxes], "safe_crop": crop, "logo_hidden": ok}
