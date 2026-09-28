"""Motion graphics stay editable: each graphic keeps its items (label + picture), so the
timeline can change the words, order or style and draw it again."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .channel_styles import get_style

EDITABLE = {"ingredients", "gallery"}


def draw_graphic(kind: str, items: list[dict[str, str]], style_key: str, duration: float, destination: Path,
                 ffmpeg_path: str = "ffmpeg") -> Path:
    from PIL import Image

    from .motion.engine import encode
    from .motion.templates import gallery_stack, ingredient_cards

    style = get_style(style_key)
    pictures = [(str(item.get("label") or ""), Image.open(str(item["image"])).convert("RGB")) for item in items]
    if kind == "ingredients":
        frame = ingredient_cards(pictures, style)
    else:
        frame = gallery_stack([image for _label, image in pictures], style)
    return encode(frame, duration, destination, ffmpeg_path=ffmpeg_path)


def redraw_scene_graphic(db: Any, paths: Any, scene_id: str, items: list[dict[str, Any]], ffmpeg_path: str,
                         style_key: str | None = None) -> dict[str, Any]:
    """Draw the scene's graphic again with edited items; the new version becomes the selected asset."""
    scene = db.get_scene(scene_id)
    if not scene:
        raise ValueError("Scene not found")
    current = next((asset for asset in db.list_assets(str(scene["project_id"]))
                    if str(asset["id"]) == str(scene.get("selected_asset_id") or "")), None)
    metadata = dict((current or {}).get("metadata") or {})
    kind = str(metadata.get("graphic") or "")
    if kind not in EDITABLE:
        raise ValueError("This scene's picture is not an editable graphic")
    allowed = {str(item.get("image")) for item in metadata.get("items") or [] if isinstance(item, dict)}
    cleaned = []
    for item in items[:5]:
        image = str(item.get("image") or "")
        if image not in allowed or not Path(image).is_file():
            raise ValueError("Pictures can only be reordered or removed, not replaced from outside the project")
        cleaned.append({"label": str(item.get("label") or "")[:40], "image": image})
    if len(cleaned) < (2 if kind == "ingredients" else 3):
        raise ValueError("Keep at least two ingredients (three pictures for a gallery)")
    project = db.get_project(str(scene["project_id"])) or {}
    style_key = style_key or (project.get("effects") or {}).get("channel_style")
    duration = max(0.25, float(scene["end_seconds"]) - float(scene["start_seconds"]))
    destination = (paths.project_dir(str(scene["project_id"])) / "assets" / "graphics"
                   / f"scene-{int(scene['position']):04d}-{uuid.uuid4().hex[:8]}.mp4")
    destination.parent.mkdir(parents=True, exist_ok=True)
    draw_graphic(kind, cleaned, str(style_key or ""), duration, destination, ffmpeg_path)
    asset = db.add_asset(
        project_id=str(scene["project_id"]), scene_id=scene_id,
        candidate_index=db.next_asset_candidate_index(scene_id),
        media_kind="video", provider="graphic", model=kind, local_path=str(destination),
        remote_url=None, provider_asset_id=None, cost=0.0,
        metadata={"graphic": kind, "items": cleaned, "channel_style": style_key, "edited": True},
    )
    db.select_asset(scene_id, str(asset["id"]))
    return asset
