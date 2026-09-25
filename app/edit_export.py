"""Hand a finished timeline to Premiere Pro (FCP7 XML) and CapCut desktop (a native draft).

Both editors get the tool's own 1080p per-clip renders, so logo zooms, motion and
fades look exactly like the export; the untouched YouTube excerpts are copied
alongside for swapping.
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
    capcut_root: Path = CAPCUT_DRAFTS, ffprobe_path: str = "ffprobe",
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
    for clip in timeline_clips:
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
    captions = [segment for scene in scenes for segment in caption_segments(scene, caption_style)]
    srt = project_dir / "captions.srt"
    if srt.is_file():
        shutil.copy2(srt, package / "captions.srt")

    vo_duration = probe_duration(voiceover, ffprobe_path) if voiceover else clips[-1][2]
    xml_path = package / f"{name} - Premiere.xml"
    write_premiere_xml(xml_path, name, clips, voiceover, vo_duration, fps, width, height)
    (package / "HOW TO OPEN.txt").write_text(
        "Premiere Pro: File > Import > choose the .xml file. A sequence with every clip and the voice-over opens.\n"
        "Captions: File > Import > captions.srt, then drag it onto the sequence.\n"
        "CapCut: open CapCut; the project appears in your project list (quit CapCut before exporting).\n"
        "media/ holds the edited 1080p clips; originals/ holds the untouched YouTube excerpts.\n",
        encoding="utf-8",
    )
    result = {"package": str(package), "premiere_xml": str(xml_path)}
    if capcut:
        result["capcut_draft"] = str(write_capcut_draft(
            capcut_root, name, clips, voiceover, vo_duration, captions, caption_style or {}, width, height, fps,
        ))
    return result


def _frames(seconds: float, fps: int) -> int:
    return int(round(seconds * fps))


def _rate(fps: int) -> str:
    return f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"


def _url(path: Path) -> str:
    return "file://" + urllib.parse.quote(str(path.resolve()))


def write_premiere_xml(
    path: Path, name: str, clips: list[tuple[Path, float, float]], voiceover: Path | None,
    vo_duration: float, fps: int, width: int, height: int,
) -> None:
    """Final Cut Pro 7 XML (xmeml v4), which Premiere Pro imports as a sequence."""
    total = max(_frames(clips[-1][2], fps), _frames(vo_duration, fps)) if clips else _frames(vo_duration, fps)
    video_items = []
    for index, (file, start, end) in enumerate(clips, start=1):
        first, last = _frames(start, fps), _frames(end, fps)
        length = max(1, last - first)
        video_items.append(
            f'<clipitem id="clipitem-{index}"><name>{escape(file.name)}</name><enabled>TRUE</enabled>'
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
        f"<timecode>{_rate(fps)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
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


def write_capcut_draft(
    root: Path, name: str, clips: list[tuple[Path, float, float]], voiceover: Path | None, vo_duration: float,
    captions: list[tuple[float, float, str]], caption_style: dict[str, Any], width: int, height: int, fps: int,
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

    text_track = copy.deepcopy(template["text"]["track"])
    text_track.update(id=_new_id(), segments=[])
    middle = str(caption_style.get("position") or "bottom") == "middle"
    for start, end, text in captions:
        content = json.loads(template["text"]["material"]["content"])
        content["text"] = text
        for style in content.get("styles", []):
            style["range"] = [0, len(text)]
        segment = _clone_segment(template["text"], materials, start, end - start, {
            "content": json.dumps(content, ensure_ascii=False),
        }, source=False)
        segment["clip"]["transform"] = {"x": 0.0, "y": 0.0 if middle else -0.78}
        text_track["segments"].append(segment)

    tracks = [video_track]
    if text_track["segments"]:
        tracks.append(text_track)
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
