from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ..config import SettingsStore
from ..database import Database
from ..paths import AppPaths
from ..transcription import probe_duration
from .actions import MotionRegistry, build_default_motion_registry


ProgressCallback = Callable[[float], None]


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def caption_segments(scene: dict[str, Any], style: dict[str, Any] | None = None) -> list[tuple[float, float, str]]:
    value = style or {}
    caption = scene["caption_text"] if "caption_text" in scene else scene["narration"]
    text = str(caption).strip()
    if not text:
        return []
    start = float(scene["start_seconds"])
    end = float(scene["end_seconds"])
    max_lines = int(value.get("max_lines", 0))
    words_per_line = max(2, min(20, int(value.get("words_per_line", 7))))
    if max_lines <= 0:
        return [(start, end, text)]
    words = text.replace("\n", " ").split()
    words_per_card = max_lines * words_per_line
    cards = [words[index:index + words_per_card] for index in range(0, len(words), words_per_card)]
    segments: list[tuple[float, float, str]] = []
    cursor_words = 0
    for card in cards:
        lines = [card[index:index + words_per_line] for index in range(0, len(card), words_per_line)]
        card_text = "\n".join(" ".join(line) for line in lines)
        segment_start = start + (end - start) * cursor_words / len(words)
        cursor_words += len(card)
        segment_end = start + (end - start) * cursor_words / len(words)
        segments.append((segment_start, segment_end, card_text))
    return segments


def write_scene_srt(
    scenes: list[dict[str, Any]], destination: Path, style: dict[str, Any] | None = None
) -> None:
    blocks: list[str] = []
    index = 1
    for scene in scenes:
        for start, end, caption in caption_segments(scene, style):
            text = caption.replace("--> ", "→ ").strip()
            blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
            index += 1
    destination.write_text("\n".join(blocks), encoding="utf-8")


def _motion_name(scene: dict[str, Any]) -> str:
    for action in scene.get("timeline_actions") or []:
        if action.get("type") == "motion":
            return str(action.get("params", {}).get("preset", "slow_push"))
    return "slow_push"


def _fade_duration(scene: dict[str, Any]) -> float:
    for action in scene.get("timeline_actions") or []:
        if action.get("type") == "transition" and action.get("params", {}).get("preset") == "fade":
            return max(0.0, min(1.5, float(action.get("params", {}).get("duration", 0.32))))
    return 0.0


def _escape_subtitle_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    value = str(hex_color).lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha = round((1 - max(0.0, min(1.0, opacity))) * 255)
    return f"&H{alpha:02X}{blue}{green}{red}"


def _caption_case(text: str, style: dict[str, Any]) -> str:
    case = str(style.get("case") or "normal")
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return text.title()
    return text


def _ass_alignment(style: dict[str, Any]) -> int:
    row = {"bottom": 0, "middle": 3, "top": 6}.get(str(style.get("position") or "bottom"), 0)
    column = {"left": 1, "center": 2, "right": 3}.get(str(style.get("alignment") or "center"), 2)
    return row + column


def build_subtitle_style(style: dict[str, Any] | None) -> str:
    value = style or {}
    font = str(value.get("font") or "Arial")
    size = max(12, min(160, int(value.get("size", 54))))
    opacity = float(value.get("opacity", 1))
    text_color = _ass_color(str(value.get("text_color") or "#FFFFFF"), opacity)
    background_enabled = bool(value.get("background_enabled", True))
    stroke_enabled = bool(value.get("stroke_enabled", False))
    if background_enabled:
        border_style = 3
        outline_color = _ass_color(
            str(value.get("background_color") or "#000000"),
            float(value.get("background_opacity", 0.72)) * opacity,
        )
        outline = max(1, min(20, float(value.get("stroke_width", 3))))
    else:
        border_style = 1
        outline_color = _ass_color(str(value.get("stroke_color") or "#000000"), opacity)
        outline = max(0, min(20, float(value.get("stroke_width", 3)))) if stroke_enabled else 0
    shadow = max(abs(float(value.get("shadow_x", 2))), abs(float(value.get("shadow_y", 3)))) if value.get("shadow_enabled") else 0
    return (
        f"FontName={font},FontSize={size},PrimaryColour={text_color},"
        f"OutlineColour={outline_color},BackColour={outline_color},"
        f"Bold={-1 if value.get('bold', True) else 0},Italic={-1 if value.get('italic') else 0},"
        f"Underline={-1 if value.get('underline') else 0},Spacing={float(value.get('character_spacing', 0)):.2f},"
        f"ScaleX={float(value.get('scale', 100)):.2f},ScaleY={float(value.get('scale', 100)):.2f},"
        f"Angle={float(value.get('rotation', 0)):.2f},BorderStyle={border_style},"
        f"Outline={outline:.2f},Shadow={shadow:.2f},Alignment={_ass_alignment(value)},MarginV=55"
    )


def write_scene_ass(
    scenes: list[dict[str, Any]], destination: Path, width: int, height: int,
    style: dict[str, Any] | None = None,
) -> None:
    value = style or {}
    position = str(value.get("position") or "bottom")
    alignment = str(value.get("alignment") or "center")
    base_x = {"left": width * 0.08, "center": width * 0.5, "right": width * 0.92}.get(alignment, width * 0.5)
    base_y = {"top": height * 0.1, "middle": height * 0.5, "bottom": height * 0.9}.get(position, height * 0.9)
    x = max(0, min(width, base_x + float(value.get("position_x", 0)) * width / 200))
    y = max(0, min(height, base_y + float(value.get("position_y", 0)) * height / 200))
    # Rebuild the ASS style in the strict field order required by libass.
    font = str(value.get("font") or "Arial").replace(",", " ")
    size = max(12, min(160, int(value.get("size", 54))))
    primary = _ass_color(str(value.get("text_color") or "#FFFFFF"), float(value.get("opacity", 1)))
    background_enabled = bool(value.get("background_enabled", True))
    border_style = 3 if background_enabled else 1
    outline_color = _ass_color(
        str((value.get("background_color") or "#000000") if background_enabled else (value.get("stroke_color") or "#000000")),
        float(value.get("background_opacity", 0.72)) * float(value.get("opacity", 1)) if background_enabled else float(value.get("opacity", 1)),
    )
    back_color = _ass_color(
        str(value.get("shadow_color") or "#000000"), float(value.get("opacity", 1))
    ) if value.get("shadow_enabled") else outline_color
    outline = max(1, float(value.get("stroke_width", 3))) if background_enabled else (
        max(0, float(value.get("stroke_width", 3))) if value.get("stroke_enabled") else 0
    )
    shadow = max(abs(float(value.get("shadow_x", 2))), abs(float(value.get("shadow_y", 3)))) if value.get("shadow_enabled") else 0
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{primary},{primary},{outline_color},{back_color},"
        f"{-1 if value.get('bold', True) else 0},{-1 if value.get('italic') else 0},"
        f"{-1 if value.get('underline') else 0},0,{float(value.get('scale', 100)):.2f},"
        f"{float(value.get('scale', 100)):.2f},{float(value.get('character_spacing', 0)):.2f},"
        f"{float(value.get('rotation', 0)):.2f},{border_style},{outline:.2f},{shadow:.2f},"
        f"{_ass_alignment(value)},55,55,55,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    for scene in scenes:
        for segment_start, segment_end, caption in caption_segments(scene, value):
            text = _caption_case(caption, value)
            text = text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")
            start = _srt_time(segment_start).replace(",", ".")[:-1]
            end = _srt_time(segment_end).replace(",", ".")[:-1]
            if value.get("glow_enabled"):
                glow_style_color = _ass_color(str(value.get("glow_color") or "#FFFFFF"), float(value.get("opacity", 1)))
                glow = f"&H{glow_style_color[-6:]}&"
                glow_radius = max(0, min(40, float(value.get("glow_radius", 8))))
                events.append(
                    f"Dialogue: 0,{start},{end},Default,,0,0,0,,"
                    f"{{\\pos({x:.1f},{y:.1f})\\blur{glow_radius:.1f}\\bord{max(1, glow_radius / 2):.1f}"
                    f"\\1a&HFF&\\3c{glow}}}{text}"
                )
            events.append(f"Dialogue: 1,{start},{end},Default,,0,0,0,,{{\\pos({x:.1f},{y:.1f})}}{text}")
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


class FFmpegRenderer:
    def __init__(self, ffmpeg_path: str = "ffmpeg", motion_registry: MotionRegistry | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.motion_registry = motion_registry or build_default_motion_registry()

    def render(
        self,
        *,
        project: dict[str, Any],
        scenes: list[dict[str, Any]],
        assets: list[dict[str, Any]],
        project_dir: Path,
        timeline_clips: list[dict[str, Any]] | None = None,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        burn_captions: bool = True,
        caption_style: dict[str, Any] | None = None,
        fonts_dir: Path | None = None,
        output_directory: Path | None = None,
        output_name: str | None = None,
        video_bitrate_kbps: int = 12_000,
        audio_bitrate_kbps: int = 192,
        progress: ProgressCallback | None = None,
    ) -> Path:
        selected = {scene["id"]: scene.get("selected_asset_id") for scene in scenes}
        scenes_by_id = {scene["id"]: scene for scene in scenes}
        by_id = {asset["id"]: asset for asset in assets}
        clips = timeline_clips or [
            {
                "id": f"clip-{scene['id']}", "scene_id": scene["id"], "position": scene["position"],
                "start_seconds": scene["start_seconds"], "end_seconds": scene["end_seconds"],
                "source_in_seconds": 0,
            }
            for scene in scenes
        ]
        missing = [
            clip["position"] for clip in clips
            if clip.get("scene_id") not in scenes_by_id
            or not selected.get(clip["scene_id"])
            or selected[clip["scene_id"]] not in by_id
        ]
        if missing:
            preview = ", ".join(str(item) for item in missing[:12])
            raise RuntimeError(f"Scenes without selected assets: {preview}{'…' if len(missing) > 12 else ''}")

        render_dir = project_dir / "renders"
        clip_dir = project_dir / "cache" / "clips"
        render_dir.mkdir(parents=True, exist_ok=True)
        clip_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = project_dir / "captions.srt"
        write_scene_srt(scenes, subtitle_path, caption_style)
        ass_path = project_dir / "captions.ass"
        write_scene_ass(scenes, ass_path, width, height, caption_style)

        clip_paths: list[Path] = []
        total = max(1, len(clips))
        encoder = self._choose_encoder()
        for index, timeline_clip in enumerate(clips):
            scene = scenes_by_id[timeline_clip["scene_id"]]
            asset = by_id[selected[scene["id"]]]
            source = Path(asset["local_path"])
            duration = max(0.1, float(timeline_clip["end_seconds"]) - float(timeline_clip["start_seconds"]))
            clip = clip_dir / f"timeline-{int(timeline_clip['position']):04d}.mp4"
            self._render_clip(
                source, clip, str(asset["media_kind"]), duration, scene, width, height, fps, encoder,
                source_in_seconds=float(timeline_clip.get("source_in_seconds", 0)),
            )
            clip_paths.append(clip)
            if progress:
                progress(0.82 * (index + 1) / total)

        concat_file = project_dir / "cache" / "concat.txt"
        concat_file.parent.mkdir(parents=True, exist_ok=True)
        concat_file.write_text(
            "\n".join(f"file '{str(path.resolve()).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for path in clip_paths),
            encoding="utf-8",
        )
        silent_video = project_dir / "cache" / "assembled.mp4"
        self._run([self.ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent_video)])
        if progress:
            progress(0.88)

        chosen_dir = (output_directory or render_dir).expanduser().resolve()
        chosen_dir.mkdir(parents=True, exist_ok=True)
        base_name = _safe_output_name(output_name or str(project["name"]))
        output = chosen_dir / f"{base_name}.mp4"
        duplicate = 2
        while output.exists():
            output = chosen_dir / f"{base_name}-{duplicate}.mp4"
            duplicate += 1
        voiceover = Path(project["voiceover_path"]) if project.get("voiceover_path") else None
        command = [self.ffmpeg_path, "-y", "-i", str(silent_video)]
        if voiceover and voiceover.exists():
            command += ["-i", str(voiceover)]

        final_filters: list[str] = []
        if voiceover and voiceover.exists():
            # Preserve the full voice-over even if a user trims the visual track
            # shorter; the last rendered frame is held until audio completes.
            final_filters.append("tpad=stop_mode=clone:stop_duration=86400")
        if burn_captions:
            subtitle_filter = f"subtitles='{_escape_subtitle_path(ass_path)}'"
            if fonts_dir and fonts_dir.exists():
                subtitle_filter += f":fontsdir='{_escape_subtitle_path(fonts_dir)}'"
            final_filters.append(subtitle_filter)
        if final_filters:
            command += ["-vf", ",".join(final_filters)]
        command += ["-c:v", encoder]
        if encoder == "libx264":
            command += ["-preset", "veryfast"]
        bitrate = max(500, min(100_000, int(video_bitrate_kbps)))
        command += ["-b:v", f"{bitrate}k", "-maxrate", f"{round(bitrate * 1.25)}k", "-bufsize", f"{bitrate * 2}k"]
        if voiceover and voiceover.exists():
            audio_bitrate = max(96, min(320, int(audio_bitrate_kbps)))
            command += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", f"{audio_bitrate}k", "-shortest"]
        else:
            command += ["-an"]
        command += ["-movflags", "+faststart", str(output)]
        self._run(command)
        if progress:
            progress(1.0)
        return output

    def _render_clip(self, source: Path, destination: Path, media_kind: str, duration: float,
                     scene: dict[str, Any], width: int, height: int, fps: int, encoder: str,
                     source_in_seconds: float = 0) -> None:
        motion = self.motion_registry.build(_motion_name(scene), width, height, fps, duration)
        fade = _fade_duration(scene)
        filters = [motion]
        if fade > 0 and duration > fade * 2:
            filters.extend([f"fade=t=in:st=0:d={fade}", f"fade=t=out:st={duration-fade}:d={fade}"])
        if media_kind == "video":
            command = [self.ffmpeg_path, "-y", "-stream_loop", "-1"]
            if source_in_seconds > 0:
                command += ["-ss", str(source_in_seconds)]
            command += ["-i", str(source), "-t", str(duration)]
        else:
            command = [self.ffmpeg_path, "-y", "-loop", "1", "-i", str(source), "-t", str(duration)]
        command += ["-vf", ",".join(filters), "-r", str(fps), "-pix_fmt", "yuv420p", "-c:v", encoder]
        if encoder == "libx264":
            command += ["-preset", "veryfast", "-crf", "20"]
        command += ["-an", str(destination)]
        self._run(command)

    def _choose_encoder(self) -> str:
        try:
            result = subprocess.run([self.ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True)
            if sys.platform == "darwin" and "h264_videotoolbox" in result.stdout:
                return "h264_videotoolbox"
            if "h264_amf" in result.stdout:
                return "h264_amf"
        except (OSError, subprocess.CalledProcessError):
            pass
        return "libx264"

    @staticmethod
    def _run(command: list[str]) -> None:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr[-1500:]}")


def _safe_name(name: str) -> str:
    safe = "".join(character.lower() if character.isalnum() else "-" for character in name)
    return "-".join(part for part in safe.split("-") if part)[:80] or "video"


def _safe_output_name(name: str) -> str:
    stem = Path(str(name)).stem.strip()
    safe = "".join(character if character.isalnum() or character in " -_()." else "-" for character in stem)
    return safe.strip(" .")[:140] or "video"


class RenderManager:
    def __init__(self, db: Database, paths: AppPaths, settings_store: SettingsStore):
        self.db = db
        self.paths = paths
        self.settings_store = settings_store
        self._threads: dict[str, threading.Thread] = {}

    def start(self, project_id: str, options: dict[str, Any]) -> str:
        existing = self._threads.get(project_id)
        if existing and existing.is_alive():
            raise RuntimeError("This project is already rendering")
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.db.connection() as db:
            db.execute(
                "INSERT INTO render_jobs (id, project_id, status, settings, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                (job_id, project_id, json.dumps(options), now, now),
            )
        thread = threading.Thread(target=self._run, args=(job_id, project_id, options), daemon=True)
        self._threads[project_id] = thread
        thread.start()
        return job_id

    def _run(self, job_id: str, project_id: str, options: dict[str, Any]) -> None:
        try:
            self._update(job_id, status="running", progress=0.01)
            project = self.db.get_project(project_id)
            if not project:
                raise RuntimeError("Project not found")
            settings = self.settings_store.load()
            voiceover_duration = float(project.get("duration_seconds") or 0)
            voiceover_path = Path(str(project.get("voiceover_path") or ""))
            if voiceover_path.is_file():
                measured_duration = probe_duration(voiceover_path, settings.ffprobe_path)
                if measured_duration > 0:
                    voiceover_duration = measured_duration
                    if abs(measured_duration - float(project.get("duration_seconds") or 0)) > 0.05:
                        project = self.db.update_project(project_id, duration_seconds=measured_duration)
                sync = self.db.timeline_sync_status(project_id)
                if sync["status"] != "synced":
                    self.db.fit_timeline_to_duration(project_id, voiceover_duration)
            renderer = FFmpegRenderer(settings.ffmpeg_path)
            output = renderer.render(
                project=project,
                scenes=self.db.list_scenes(project_id),
                timeline_clips=self.db.list_timeline_clips(project_id),
                assets=self.db.list_assets(project_id),
                project_dir=self.paths.project_dir(project_id),
                width=int(options.get("width", 1920)),
                height=int(options.get("height", 1080)),
                fps=int(options.get("fps", 30)),
                burn_captions=bool(options.get("burn_captions", True)),
                caption_style=options.get("caption_style"),
                fonts_dir=self.paths.root / "fonts",
                output_directory=Path(options["output_directory"]) if options.get("output_directory") else None,
                output_name=str(options.get("output_name") or project["name"]),
                video_bitrate_kbps=int(options.get("video_bitrate_kbps", 12_000)),
                audio_bitrate_kbps=int(options.get("audio_bitrate_kbps", 192)),
                progress=lambda value: self._update(job_id, progress=value),
            )
            rendered_duration = probe_duration(output, settings.ffprobe_path)
            if voiceover_path.is_file() and rendered_duration + 0.25 < voiceover_duration:
                raise RuntimeError(
                    f"Export verification failed: video is {rendered_duration:.2f}s but the voice-over is "
                    f"{voiceover_duration:.2f}s. The incomplete export was not marked complete."
                )
            self._update(job_id, status="complete", progress=1.0, output_path=str(output))
            self.db.update_project(project_id, status="rendered")
        except Exception as error:
            self._update(job_id, status="failed", error=str(error))
        finally:
            self._threads.pop(project_id, None)

    def _update(self, job_id: str, **changes: Any) -> None:
        allowed = {"status", "progress", "output_path", "error"}
        values = {key: value for key, value in changes.items() if key in allowed}
        values["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.db.connection() as db:
            db.execute(
                f"UPDATE render_jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
