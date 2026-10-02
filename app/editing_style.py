"""Each channel's editing style (Ishaq's JSON file), stored with the tool and read on every video.

The file is written in plain words for people (and GPT) to fill. This module turns the parts the tool can
follow into settings: footage rules, repetition, what the checker refuses, AI image rules, the quality gates
before export, and a per-video report of which rules were met. Look rules (captions, chapter labels, pacing)
live in the channel kit; a style file never changes a channel's look by itself.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

FOLDER = "editing_styles"
_lock = threading.Lock()


def _path(root: Path, channel: str) -> Path:
    safe = re.sub(r"[^a-z0-9_-]", "_", str(channel or "").lower())
    return Path(root) / FOLDER / f"{safe}.json"


def save_style(root: Path, channel: str, style: dict[str, Any]) -> Path:
    if not isinstance(style, dict) or not style:
        raise ValueError("An editing style must be a JSON object")
    path = _path(root, channel)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(style, indent=2, ensure_ascii=False))
        temporary.replace(path)
    return path


def load_style(root: Path, channel: Any) -> dict[str, Any]:
    """The channel's style file, or {} (then every rule keeps the tool's own default)."""
    try:
        data = json.loads(_path(root, str(channel or "")).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _section(style: dict[str, Any], name: str) -> dict[str, Any]:
    value = style.get(name)
    return value if isinstance(value, dict) else {}


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _words(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def footage_rules(style: dict[str, Any]) -> dict[str, Any]:
    footage = _section(style, "footage")
    repetition = _section(style, "repetition")
    return {
        "max_ai_share": _number(footage.get("max_ai_image_share"), 1.0),
        "min_real_video_share": _number(footage.get("minimum_real_video_share_per_item"), 0.0),
        "max_source_minutes": _number(footage.get("max_source_minutes"), 20.0),
        "sources_per_item": int(_number(footage.get("sources_per_item"), 2)),
        "avoid_titles": [word.lower() for word in _words(footage.get("avoid_titles_with"))],
        "prefer_titles": [word.lower() for word in _words(footage.get("prefer_titles_with"))],
        "no_neighbour_repeat": repetition.get("same_source_in_neighbouring_scenes") is False,
    }


def checker_rules(style: dict[str, Any]) -> str:
    """Extra lines for the AI checker: the style's never-show list and its reject/accept rules."""
    never = _section(style, "never_show")
    checker = _section(style, "checker")
    refuse = [item for key in ("people", "on_screen", "places", "other") for item in _words(never.get(key))]
    refuse += _words(checker.get("reject_if"))
    accept = _words(checker.get("accept_if"))
    lines = []
    if refuse:
        lines.append("This channel's editing style also refuses (fits = false): " + "; ".join(refuse[:20]) + ".")
    if accept:
        lines.append("It accepts: " + "; ".join(accept[:10]) + ".")
    return ("\n".join(lines) + "\n") if lines else ""


def ai_image_rules(style: dict[str, Any]) -> str:
    """Extra words for AI still prompts (what must never be in them)."""
    never = _words(_section(style, "ai_images").get("never"))
    return (" Never show: " + ", ".join(never[:10]) + ".") if never else ""


# ------------------------------------------------------------------ report and gates
def style_report(style: dict[str, Any], scenes: list[dict[str, Any]], assets: dict[str, dict[str, Any]],
                 sections: list[tuple[str, list[dict[str, Any]]]], hook_ids: set[str]) -> dict[str, Any]:
    """Each measurable rule as met / not met, from the finished timeline. `sections` lists (item, scenes)."""
    rules = footage_rules(style)
    checks: list[dict[str, Any]] = []

    def provider(scene: dict[str, Any]) -> str:
        return str((assets.get(str(scene.get("selected_asset_id") or "")) or {}).get("provider") or "")

    def seconds(scene: dict[str, Any]) -> float:
        return max(0.0, float(scene["end_seconds"]) - float(scene["start_seconds"]))

    filmable = [scene for scene in scenes if provider(scene) != "chapter"]
    total = sum(seconds(scene) for scene in filmable) or 1.0
    ai = sum(seconds(scene) for scene in filmable if provider(scene) == "generated")
    checks.append({"rule": f"AI images at most {rules['max_ai_share']:.0%} of the video",
                   "value": f"{ai / total:.1%}", "ok": ai / total <= rules["max_ai_share"] + 1e-9})
    empty = [int(scene.get("position") or 0) for scene in scenes if not scene.get("selected_asset_id")]
    checks.append({"rule": "Every scene has media", "value": f"{len(empty)} empty" + (f" (scenes {empty[:12]})" if empty else ""),
                   "ok": not empty, "gate": True})
    hook_ai = [int(scene.get("position") or 0) for scene in scenes
               if str(scene["id"]) in hook_ids and provider(scene) == "generated"]
    checks.append({"rule": "No AI image in the hook", "value": f"{len(hook_ai)} AI images", "ok": not hook_ai, "gate": True})
    if rules["min_real_video_share"] > 0:
        for name, members in sections:
            length = sum(seconds(scene) for scene in members) or 1.0
            real = sum(seconds(scene) for scene in members if provider(scene) in ("youtube", "stock"))
            checks.append({"rule": f"'{name}': real video at least {rules['min_real_video_share']:.0%}",
                           "value": f"{real / length:.0%}", "ok": real / length >= rules["min_real_video_share"] - 1e-9,
                           "gate": True, "item": name})
    if rules["no_neighbour_repeat"]:
        repeats = []
        previous = ""
        for scene in scenes:
            asset = assets.get(str(scene.get("selected_asset_id") or "")) or {}
            source = str(asset.get("provider_asset_id") or "") if asset.get("provider") == "youtube" else ""
            if source and source == previous:
                repeats.append(int(scene.get("position") or 0))
            previous = source
        checks.append({"rule": "Same source never in neighbouring scenes", "value": f"{len(repeats)} times",
                       "ok": not repeats})
    met = sum(1 for check in checks if check["ok"])
    return {"checks": checks, "met": met, "total": len(checks),
            "blocking": [check for check in checks if check.get("gate") and not check["ok"]]}


def gate_message(report: dict[str, Any]) -> str:
    """'' when export may go ahead; otherwise what to fix (the user can still export on purpose)."""
    blocking = report.get("blocking") or []
    if not blocking:
        return ""
    return "Quality check: " + "; ".join(f"{check['rule']} ({check['value']})" for check in blocking[:6])
