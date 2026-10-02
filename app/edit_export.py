"""Hand a finished timeline to Premiere Pro (FCP7 XML) and CapCut desktop (a native draft).

Both editors get the tool's own 1080p per-clip renders (logo zooms, motion and fades as in
the export) with no words baked in: captions, chapter titles and ingredient names come as
separate, editable text (CapCut text layers; Premiere caption tracks). Scenes with no
footage yet are black and marked, and AI images are marked for replacement.
"""
from __future__ import annotations

import copy
import json
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .timeline.renderer import caption_segments
from .transcription import probe_duration

CAPCUT_TEMPLATE = Path(__file__).with_name("capcut_template.json")
CAPCUT_DRAFTS = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def _safe(name: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in " -_()" else "-" for character in name)
    return cleaned.strip(" .-")[:80] or "Video"


def build_edit_package(
    *, project: dict[str, Any], scenes: list[dict[str, Any]], timeline_clips: list[dict[str, Any]],
    assets: list[dict[str, Any]], project_dir: Path, destination: Path, caption_style: dict[str, Any] | None,
    fps: int = 30, width: int = 1920, height: int = 1080, capcut: bool = False,
    capcut_root: Path = CAPCUT_DRAFTS, ffprobe_path: str = "ffprobe", missing_positions: list[int] | None = None,
    library: Path | None = None,
) -> dict[str, str]:
    """Copy the rendered clips, voice-over and captions into a folder with a Premiere XML."""
    name = _safe(str(project.get("name") or "Video"))
    package = destination / f"{name} - Edit Package"
    suffix = 2
    while package.exists():
        package = destination / f"{name} - Edit Package {suffix}"
        suffix += 1
    media_dir, originals_dir = package / "media", package / "originals"
    media_dir.mkdir(parents=True)
    originals_dir.mkdir()
    scenes_by_id = {scene["id"]: scene for scene in scenes}
    assets_by_id = {asset["id"]: asset for asset in assets}

    clips: list[tuple[Path, float, float]] = []
    editable_dir = project_dir / "cache" / "clips-editable"
    for clip in timeline_clips:
        rendered = editable_dir / f"timeline-{int(clip['position']):04d}.mp4"
        if not rendered.is_file():
            rendered = project_dir / "cache" / "clips" / f"timeline-{int(clip['position']):04d}.mp4"
        if not rendered.is_file():
            raise RuntimeError("Render the video once before exporting an edit package")
        target = media_dir / f"clip-{int(clip['position']):04d}.mp4"
        shutil.copy2(rendered, target)
        clips.append((target, float(clip["start_seconds"]), float(clip["end_seconds"])))
        scene = scenes_by_id.get(clip["scene_id"]) or {}
        asset = assets_by_id.get(str(scene.get("selected_asset_id") or ""))
        source = Path(str((asset or {}).get("local_path") or ""))
        if source.is_file():
            shutil.copy2(source, originals_dir / f"scene-{int(scene.get('position') or 0):04d}{source.suffix}")

    voiceover = None
    original_vo = Path(str(project.get("voiceover_path") or ""))
    if original_vo.is_file():
        voiceover = package / f"voiceover{original_vo.suffix}"
        shutil.copy2(original_vo, voiceover)
    # Every word is editable: captions always travel as text, never burned into the clips.
    captions = [segment for scene in scenes for segment in caption_segments(scene, caption_style)]
    on_screen = on_screen_text(project, scenes, assets_by_id, library)
    markers = review_markers(timeline_clips, scenes_by_id, assets_by_id, set(missing_positions or []))
    write_srt(package / "captions.srt", captions)
    write_srt(package / "on-screen text (chapters, ingredients).srt",
              [(layer["start"], layer["end"], layer["text"]) for layer in on_screen])
    if markers:
        (package / "MISSING FOOTAGE.txt").write_text(markers_text(markers), encoding="utf-8")

    (package / "CREDITS.txt").write_text(credits_text(scenes, assets_by_id), encoding="utf-8")
    vo_duration = probe_duration(voiceover, ffprobe_path) if voiceover else clips[-1][2]
    xml_path = package / f"{name} - Premiere.xml"
    write_premiere_xml(xml_path, name, clips, voiceover, vo_duration, fps, width, height, markers)
    (package / "HOW TO OPEN.txt").write_text(
        "Premiere Pro: File > Import > choose the .xml file. A sequence with every clip and the voice-over opens.\n"
        "  Words: File > Import > captions.srt and 'on-screen text (chapters, ingredients).srt', drag each onto the\n"
        "  sequence. They are editable caption tracks (right-click > Upgrade caption to graphic for full styling).\n"
        "  Red markers and red clips = scenes with no footage yet; orange = AI images to replace (see MISSING FOOTAGE.txt).\n"
        "CapCut: open CapCut; the project appears in your project list (quit CapCut before exporting).\n"
        "  Captions, chapter titles and ingredient names are separate text layers; red/orange text on the top\n"
        "  track marks missing footage and AI images (delete those notes once replaced).\n"
        "media/ holds the edited 1080p clips (no words on them); originals/ holds the untouched source excerpts.\n",
        encoding="utf-8",
    )
    result = {"package": str(package), "premiere_xml": str(xml_path)}
    if capcut:
        result["capcut_draft"] = str(write_capcut_draft(
            capcut_root, name, clips, voiceover, vo_duration, captions, caption_style or {}, width, height, fps,
            on_screen=on_screen, markers=markers,
        ))
    return result


def on_screen_text(project: dict[str, Any], scenes: list[dict[str, Any]],
                   assets_by_id: dict[str, dict[str, Any]], library: Path | None = None) -> list[dict[str, Any]]:
    """Chapter titles and ingredient names, with where and when each appears."""
    from .channel_styles import get_style
    from .chapter_cards import card_text_layout
    from .ingredient_library import redrawable_items
    from .motion.templates import ingredient_label_layout

    style = get_style((project.get("effects") or {}).get("channel_style"))
    layers: list[dict[str, Any]] = []
    for scene in scenes:
        asset = assets_by_id.get(str(scene.get("selected_asset_id") or "")) or {}
        metadata = asset.get("metadata") or {}
        start, end = float(scene["start_seconds"]), float(scene["end_seconds"])
        # Only cards that were redrawn without words get text layers (else the words would show twice).
        from .motion_designs import is_web

        from .name_label import label_layers

        for layer in label_layers(scene):  # the orange item-name label (reference V2 style)
            layers.append({**layer, "start": min(end - 0.1, start + float(layer.get("appear") or 0)), "end": end})
        if is_web(metadata.get("design")):
            from .motion_designs import text_layers

            found = text_layers(metadata, style)
        elif (asset.get("provider") == "chapter" and metadata.get("title")
                and Path(str(metadata.get("plain_path") or "")).is_file()):
            found = card_text_layout(str(metadata["title"]), int(metadata.get("chapter") or 1), style)
        elif asset.get("provider") == "graphic" and metadata.get("graphic") == "ingredients":
            items = redrawable_items(metadata, library)
            found = ingredient_label_layout([item["label"] for item in items], style) if items else []
        else:
            continue
        for layer in found:
            layers.append({**layer, "start": min(end - 0.1, start + float(layer.get("appear") or 0)), "end": end})
    return layers


MISSING, AI_IMAGE = "MISSING FOOTAGE", "AI IMAGE - replace with real footage"


def review_markers(timeline_clips: list[dict[str, Any]], scenes_by_id: dict[str, dict[str, Any]],
                   assets_by_id: dict[str, dict[str, Any]], missing: set[int]) -> list[dict[str, Any]]:
    """Scenes an editor must fill: no footage at all, or only an AI image."""
    markers = []
    for clip in timeline_clips:
        scene = scenes_by_id.get(clip["scene_id"]) or {}
        asset = assets_by_id.get(str(scene.get("selected_asset_id") or ""))
        position = int(clip["position"])
        kind = MISSING if position in missing or not asset else (
            AI_IMAGE if (asset or {}).get("provider") in {"generated", "runware"} else "")
        if kind:
            markers.append({
                "kind": kind, "position": position, "start": float(clip["start_seconds"]),
                "end": float(clip["end_seconds"]), "narration": str(scene.get("narration") or "").strip(),
            })
    return markers


def _timecode(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def markers_text(markers: list[dict[str, Any]]) -> str:
    lines = ["Scenes to fill before publishing", ""]
    for marker in markers:
        lines.append(f"{_timecode(marker['start'])}  scene {marker['position']}  {marker['kind']}")
        lines.append(f"    \"{marker['narration'][:160]}\"")
    return "\n".join(lines) + "\n"


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    return (f"{milliseconds // 3_600_000:02d}:{milliseconds % 3_600_000 // 60_000:02d}:"
            f"{milliseconds % 60_000 // 1000:02d},{milliseconds % 1000:03d}")


def write_srt(path: Path, segments: list[tuple[float, float, str]]) -> None:
    blocks = [f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n"
              for index, (start, end, text) in enumerate(sorted(segments), start=1) if str(text).strip()]
    path.write_text("\n".join(blocks), encoding="utf-8")


def credits_text(scenes: list[dict[str, Any]], assets_by_id: dict[str, dict[str, Any]]) -> str:
    """Source list for the video description: YouTube excerpts and CC photos need credit."""
    lines: list[str] = []
    for scene in scenes:
        asset = assets_by_id.get(str(scene.get("selected_asset_id") or "")) or {}
        metadata = asset.get("metadata") or {}
        if asset.get("provider") == "photo":
            line = f"{metadata.get('attribution')} {metadata.get('source_url') or ''}".strip()
        elif asset.get("provider") == "youtube":
            line = f"\"{metadata.get('title')}\" by {metadata.get('channel')} {asset.get('remote_url') or ''}".strip()
        else:
            continue
        if line not in lines:
            lines.append(line)
    return "Footage and photo credits\n\n" + "\n".join(lines) + "\n"


def _frames(seconds: float, fps: int) -> int:
    return int(round(seconds * fps))


def _rate(fps: int) -> str:
    return f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"


def _url(path: Path) -> str:
    return "file://" + urllib.parse.quote(str(path.resolve()))


def write_premiere_xml(
    path: Path, name: str, clips: list[tuple[Path, float, float]], voiceover: Path | None,
    vo_duration: float, fps: int, width: int, height: int, markers: list[dict[str, Any]] | None = None,
) -> None:
    """Final Cut Pro 7 XML (xmeml v4), which Premiere Pro imports as a sequence.
    Scenes to fill get a sequence marker and a coloured, renamed clip (red: missing, orange: AI image)."""
    total = max(_frames(clips[-1][2], fps), _frames(vo_duration, fps)) if clips else _frames(vo_duration, fps)
    flagged = {round(marker["start"], 3): marker for marker in markers or []}
    video_items = []
    for index, (file, start, end) in enumerate(clips, start=1):
        first, last = _frames(start, fps), _frames(end, fps)
        length = max(1, last - first)
        marker = flagged.get(round(start, 3))
        label = ""
        clip_name = file.name
        if marker:
            clip_name = f"{marker['kind']} - scene {marker['position']}"
            label = "<labels><label2>{}</label2></labels>".format("Rose" if marker["kind"] == MISSING else "Mango")
        video_items.append(
            f'<clipitem id="clipitem-{index}"><name>{escape(clip_name)}</name><enabled>TRUE</enabled>{label}'
            f"<duration>{length}</duration>{_rate(fps)}<start>{first}</start><end>{first + length}</end>"
            f"<in>0</in><out>{length}</out>"
            f'<file id="file-{index}"><name>{escape(file.name)}</name><pathurl>{escape(_url(file))}</pathurl>'
            f"{_rate(fps)}<duration>{length}</duration><media><video><samplecharacteristics>{_rate(fps)}"
            f"<width>{width}</width><height>{height}</height></samplecharacteristics></video></media></file>"
            "</clipitem>"
        )
    audio = ""
    if voiceover is not None:
        vo_frames = _frames(vo_duration, fps)
        audio = (
            '<track><clipitem id="clipitem-voiceover"><name>Voice-over</name><enabled>TRUE</enabled>'
            f"<duration>{vo_frames}</duration>{_rate(fps)}<start>0</start><end>{vo_frames}</end>"
            f"<in>0</in><out>{vo_frames}</out>"
            f'<file id="file-voiceover"><name>{escape(voiceover.name)}</name><pathurl>{escape(_url(voiceover))}</pathurl>'
            f"{_rate(fps)}<duration>{vo_frames}</duration><media><audio><samplecharacteristics><depth>16</depth>"
            "<samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio></media></file>"
            "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem></track>"
        )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{total}</duration>{_rate(fps)}'
        "<media><video><format><samplecharacteristics>"
        f"{_rate(fps)}<width>{width}</width><height>{height}</height><pixelaspectratio>square</pixelaspectratio>"
        "<fielddominance>none</fielddominance></samplecharacteristics></format>"
        f"<track>{''.join(video_items)}</track></video>"
        f"<audio><numOutputChannels>2</numOutputChannels>{audio}</audio></media>"
        + "".join(
            f"<marker><name>{escape(marker['kind'])}</name><comment>{escape('scene ' + str(marker['position']) + ': ' + marker['narration'][:200])}</comment>"
            f"<in>{_frames(marker['start'], fps)}</in><out>{_frames(marker['end'], fps)}</out></marker>"
            for marker in markers or []
        )
        + f"<timecode>{_rate(fps)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
        "</sequence></xmeml>\n",
        encoding="utf-8",
    )


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def _us(seconds: float) -> int:
    return int(round(seconds * 1_000_000))


def _clone_segment(part: dict[str, Any], materials: dict[str, list[Any]], start: float, duration: float,
                   material_changes: dict[str, Any], source: bool = True) -> dict[str, Any]:
    material = copy.deepcopy(part["material"])
    material.update(material_changes, id=_new_id())
    materials.setdefault(part["material_kind"], []).append(material)
    refs = []
    for kind, extra in part["extras"]:
        clone = copy.deepcopy(extra)
        clone["id"] = _new_id()
        materials.setdefault(kind, []).append(clone)
        refs.append(clone["id"])
    segment = copy.deepcopy(part["segment"])
    segment.update(id=_new_id(), material_id=material["id"], extra_material_refs=refs)
    segment["target_timerange"] = {"start": _us(start), "duration": _us(duration)}
    segment["source_timerange"] = {"start": 0, "duration": _us(duration)} if source else None
    return segment


def _text_segment(template: dict[str, Any], materials: dict[str, list[Any]], start: float, end: float, text: str,
                  x: float, y: float, size: float | None = None, color: tuple[int, int, int] | None = None,
                  font_path: str = "", letter_spacing: float = 0.0) -> dict[str, Any]:
    """One editable CapCut text layer; x/y are CapCut's -1..1 canvas coordinates (y up)."""
    content = json.loads(template["text"]["material"]["content"])
    content["text"] = text
    for style in content.get("styles", []):
        style["range"] = [0, len(text)]
        if size:
            style["size"] = size
        if color:
            style["fill"]["content"]["solid"]["color"] = [round(channel / 255, 4) for channel in color]
        if font_path:
            style["font"] = {"id": "", "path": font_path}
    segment = _clone_segment(template["text"], materials, start, max(0.1, end - start), {
        "content": json.dumps(content, ensure_ascii=False), "letter_spacing": letter_spacing,
    }, source=False)
    segment["clip"]["transform"] = {"x": round(x, 4), "y": round(y, 4)}
    return segment


def _canvas(x_px: float, y_px: float, width: int, height: int) -> tuple[float, float]:
    return (x_px - width / 2) / (width / 2), -(y_px - height / 2) / (height / 2)


def write_capcut_draft(
    root: Path, name: str, clips: list[tuple[Path, float, float]], voiceover: Path | None, vo_duration: float,
    captions: list[tuple[float, float, str]], caption_style: dict[str, Any], width: int, height: int, fps: int,
    on_screen: list[dict[str, Any]] | None = None, markers: list[dict[str, Any]] | None = None,
) -> Path:
    """A CapCut desktop draft that shows up in CapCut's project list."""
    if not root.is_dir():
        raise RuntimeError("CapCut desktop was not found on this computer")
    template = json.loads(CAPCUT_TEMPLATE.read_text(encoding="utf-8"))
    folder_name = name
    folder = root / folder_name
    suffix = 2
    while folder.exists():
        folder_name = f"{name} ({suffix})"
        folder = root / folder_name
        suffix += 1
    folder.mkdir(parents=True)

    # CapCut is sandboxed to ~/Movies, so media outside it shows as offline:
    # keep a copy of every clip and the voice-over inside the draft folder.
    media_dir = folder / "Resources" / "local_media"
    media_dir.mkdir(parents=True)
    local: list[tuple[Path, float, float]] = []
    for file, start, end in clips:
        target = media_dir / file.name
        shutil.copy2(file, target)
        local.append((target, start, end))
    clips = local
    if voiceover is not None:
        target = media_dir / voiceover.name
        shutil.copy2(voiceover, target)
        voiceover = target

    draft = copy.deepcopy(template["draft"])
    materials: dict[str, list[Any]] = {key: [] for key in draft["materials"]}
    total = max([end for _file, _start, end in clips] + [vo_duration])

    video_track = copy.deepcopy(template["video"]["track"])
    video_track.update(id=_new_id(), segments=[])
    for file, start, end in clips:
        video_track["segments"].append(_clone_segment(template["video"], materials, start, end - start, {
            "path": str(file.resolve()), "material_name": file.name, "duration": _us(end - start),
            "width": width, "height": height, "has_audio": False, "local_material_id": str(uuid.uuid4()),
        }))

    def new_text_track() -> dict[str, Any]:
        track = copy.deepcopy(template["text"]["track"])
        track.update(id=_new_id(), segments=[])
        return track

    middle = str(caption_style.get("position") or "bottom") == "middle"
    caption_track = new_text_track()
    for start, end, text in captions:
        caption_track["segments"].append(_text_segment(template, materials, start, end, text, 0.0, 0.0 if middle else -0.78))

    # Chapter titles and ingredient names at the exact place the tool drew them; one layer each.
    # CapCut keeps overlapping text on separate tracks, so each word block gets the first free track.
    screen_tracks: list[dict[str, Any]] = []
    for layer in sorted(on_screen or [], key=lambda item: item["start"]):
        x, y = _canvas(float(layer["x"]), float(layer["y"]), width, height)
        segment = _text_segment(template, materials, layer["start"], layer["end"], layer["text"], x, y,
                                size=round(float(layer["size"]) / 5.6, 1), color=tuple(layer["color"]),
                                font_path=str(layer.get("font") or ""),
                                letter_spacing=float(layer.get("letter_spacing") or 0.0))
        for track in screen_tracks:
            last = track["segments"][-1]["target_timerange"]
            if last["start"] + last["duration"] <= segment["target_timerange"]["start"]:
                track["segments"].append(segment)
                break
        else:
            track = new_text_track()
            track["segments"].append(segment)
            screen_tracks.append(track)

    # Notes for the editor at the top of the frame: red = no footage, orange = AI image.
    marker_track = new_text_track()
    for marker in markers or []:
        colour = (230, 40, 40) if marker["kind"] == MISSING else (245, 150, 30)
        marker_track["segments"].append(_text_segment(
            template, materials, marker["start"], marker["end"], f"⚠ {marker['kind']} (scene {marker['position']})",
            0.0, 0.85, size=9.0, color=colour))

    tracks = [video_track]
    tracks += [track for track in (caption_track, *screen_tracks, marker_track) if track["segments"]]
    if voiceover is not None:
        audio_track = copy.deepcopy(template["audio"]["track"])
        audio_track.update(id=_new_id(), segments=[_clone_segment(template["audio"], materials, 0.0, vo_duration, {
            "path": str(voiceover.resolve()), "name": voiceover.stem, "duration": _us(vo_duration),
        })])
        tracks.append(audio_track)

    draft_id = _new_id()
    draft.update(
        id=draft_id, name=folder_name, duration=_us(total), fps=float(fps), materials=materials, tracks=tracks,
        canvas_config={**draft.get("canvas_config", {}), "width": width, "height": height, "ratio": "original"},
    )
    draft_json = folder / "draft_info.json"
    draft_json.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    now = int(time.time() * 1_000_000)
    meta = copy.deepcopy(template["meta"])
    meta.update(
        draft_fold_path=str(folder), draft_id=draft_id, draft_name=folder_name, draft_root_path=str(root),
        tm_draft_create=now, tm_draft_modified=now, tm_duration=_us(total), draft_cover="",
    )
    (folder / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    index_path = root / "root_meta_info.json"
    if index_path.is_file():
        shutil.copy2(index_path, root / f"root_meta_info.backup-{int(time.time())}.json")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        entry = copy.deepcopy(template["root_entry"])
        entry.update(
            draft_fold_path=str(folder), draft_id=draft_id, draft_name=folder_name, draft_root_path=str(root),
            draft_json_file=str(draft_json), draft_cover="", tm_draft_create=now, tm_draft_modified=now,
            tm_duration=_us(total),
        )
        index.setdefault("all_draft_store", []).insert(0, entry)
        index["draft_ids"] = int(index.get("draft_ids") or 0) + 1
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return folder
