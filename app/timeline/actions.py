from __future__ import annotations

from collections.abc import Callable


MotionBuilder = Callable[[int, int, int, float], str]


class MotionRegistry:
    """Maps timeline action names to FFmpeg filters.

    New motion effects register here without changing projects, scenes or the renderer.
    """

    def __init__(self) -> None:
        self._builders: dict[str, MotionBuilder] = {}

    def register(self, name: str, builder: MotionBuilder) -> None:
        self._builders[name] = builder

    def build(self, name: str, width: int, height: int, fps: int, duration: float) -> str:
        builder = self._builders.get(name, self._builders["static"])
        return builder(width, height, fps, duration)

    def names(self) -> list[str]:
        return sorted(self._builders)


def _base_scale(width: int, height: int) -> str:
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def build_default_motion_registry() -> MotionRegistry:
    registry = MotionRegistry()
    registry.register("static", lambda w, h, fps, d: f"{_base_scale(w, h)},fps={fps}")
    registry.register(
        "slow_push",
        lambda w, h, fps, d: (
            f"{_base_scale(w, h)},zoompan=z='min(zoom+0.0007,1.08)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={max(1, round(fps*d))}:s={w}x{h}:fps={fps}"
        ),
    )
    registry.register(
        "detail_push",
        lambda w, h, fps, d: (
            f"{_base_scale(w, h)},zoompan=z='min(zoom+0.001,1.12)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={max(1, round(fps*d))}:s={w}x{h}:fps={fps}"
        ),
    )
    registry.register(
        "slow_pull",
        lambda w, h, fps, d: (
            f"{_base_scale(w, h)},zoompan=z='if(eq(on,1),1.08,max(1.0,zoom-0.0007))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={max(1, round(fps*d))}:s={w}x{h}:fps={fps}"
        ),
    )
    registry.register(
        "pan_left",
        lambda w, h, fps, d: (
            f"scale={round(w*1.08)}:{round(h*1.08)}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)*(1-t/{max(0.1,d)})':y='(ih-oh)/2',fps={fps}"
        ),
    )
    registry.register(
        "pan_right",
        lambda w, h, fps, d: (
            f"scale={round(w*1.08)}:{round(h*1.08)}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)*(t/{max(0.1,d)})':y='(ih-oh)/2',fps={fps}"
        ),
    )
    return registry

