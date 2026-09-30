"""The AI writes brand-new HyperFrames motion-graphic designs, so the library keeps growing.

Every new design follows the same contract as the built-in ones (words and pictures at the
positions the tool gives, timings that fit short scenes, a word-free mode for editable
exports). A design only joins the library after it renders cleanly at a short and a normal
length, with and without words, and does not come out blank.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .channel_styles import get_style
from .motion_designs import GENERATED, WEB_ROOT, all_designs, chapter_payload, ingredient_payload, render_design

CONTRACT = """
Write ONE complete HTML file for HyperFrames (HTML + GSAP rendered to video, 1920x1080).

Hard rules (the tool depends on them):
- Load GSAP with exactly: <script src="gsap.min.js"></script>  (no other external files, no web fonts, no CDNs).
- Root element: <div id="stage" data-composition-id="__ID__" data-start="0" data-width="1920" data-height="1080"
  data-duration="4" data-composition-variables='{"payload":{"type":"string","default":"{}"}}'> ... </div>
  with style position:relative;width:1920px;height:1080px;overflow:hidden.
- Every visible child of #stage is a layer: class="clip" data-start="0" data-duration="600" data-track-index="N" (N = 0,1,2...).
- Read the data with: const vars = (window.__hyperframes && window.__hyperframes.getVariables) ? window.__hyperframes.getVariables() : {};
  const p = JSON.parse(vars.payload || "{}");
- Build elements from p in JavaScript. One paused timeline: const tl = gsap.timeline({paused: true});
  and register it: window.__timelines = window.__timelines || {}; window.__timelines["__ID__"] = tl;
- Scenes can be as short as 1 second: let seconds = p.seconds || 4; const k = Math.min(1, seconds / 4);
  multiply every duration and start time by k so everything has appeared by 0.8 * seconds.
- If p.show_text is false, draw NO words at all (only backgrounds, shapes, pictures). The tool adds the words separately.
- Words must sit exactly where the data says: position:absolute; left:Xpx; top:Ypx; transform:translate(-50%,-50%);
  white-space:nowrap; font-size, font-family and color from the data. Never move words away from that point
  (you may fade, clip-reveal or scale them in place).
- Only system fonts from the data or: Georgia, Futura, Avenir Next, American Typewriter, Noteworthy, Snell Roundhand.
- No external images except the file names given in the data.
"""

CHAPTER_DATA = """Data p for a CHAPTER TITLE card:
p.seconds (number), p.show_text (bool), p.background (image file name or ""), p.bg_inner / p.bg_outer / p.accent / p.ink
(hex colours of the channel), p.title_font, p.label_font,
p.texts = [{text, x, y, size, color, font, spacing}]  (the chapter label then the title; draw each at its x,y).
Use p.background (if given) as a full-frame backdrop, treated in the design's own way.
YOU choose where the words go: after the HTML write one line
LAYOUT: {"label": {"x": .., "y": .., "size": .., "color": "#hex"}, "title": {"x": .., "y": .., "size": .., "color": "#hex"}}
(pixel centres on 1920x1080; the tool puts p.texts exactly there, so design the frame around those points).
Titles can be up to 26 characters: keep room for them at your size."""

INGREDIENT_DATA = """Data p for an INGREDIENTS card:
p.seconds, p.show_text, p.bg_inner, p.bg_outer, p.accent, p.ink, p.label_font, p.title_font, p.photo_size,
p.items = [{label, image, photo_x, photo_y, text_x, text_y}]:
draw each picture (file name in image) as a p.photo_size square box with its top-left corner at photo_x, photo_y,
and its label word centred at text_x, text_y. Pictures and labels appear one after another.
YOU choose the arrangement (any composition that fills the frame: staggered, scattered, circular, board, shelf...):
after the HTML write one line
LAYOUT: {"photo": <box size px>, "slots": {"2": [[photo_x, photo_y, text_x, text_y], ...2 entries], "3": [...3], "4": [...4], "5": [...5]}}
(the tool fills p.items from the slots for that many items)."""


def _sample_images(image: str) -> list[str]:
    """Three different pictures for the test renders (one repeated picture made reviews fail)."""
    library = Path(image).parent
    pictures = [str(path) for path in sorted(library.glob("*.jpg"))[:40:13]] if library.is_dir() else []
    return (pictures + [image] * 3)[:3]


def _sample_payload(kind: str, style_key: str, seconds: float, show_text: bool, image: str) -> dict[str, Any]:
    style = get_style(style_key)
    pictures = _sample_images(image)
    if kind == "chapter":
        return chapter_payload("generated", "Traffic Light Biscuits", 3, style, seconds, pictures[0], show_text)
    items = [{"label": name, "image": picture} for name, picture in zip(("sugar", "butter", "eggs"), pictures)]
    return ingredient_payload("generated", items, style, seconds, show_text)


def _clean_html(text: str) -> str:
    match = re.search(r"<!doctype html>.*</html>", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(0) if match else text.strip().strip("`")


def _frames_vary(video: Path, ffmpeg_path: str) -> bool:
    """A design that renders one flat colour (a broken script) is rejected."""
    import subprocess

    from PIL import Image, ImageStat

    frame = video.with_suffix(".png")
    subprocess.run([ffmpeg_path, "-v", "error", "-y", "-sseof", "-0.3", "-i", str(video), "-frames:v", "1", str(frame)],
                   capture_output=True, timeout=60)
    if not frame.is_file():
        return False
    with Image.open(frame) as image:
        spread = sum(ImageStat.Stat(image.convert("L")).stddev)
    frame.unlink(missing_ok=True)
    return spread > 8


def generate_design(kind: str, settings: Any, style_key: str, topic: str = "", reference_notes: str = "",
                    sample_image: str = "", ffmpeg_path: str = "ffmpeg", attempts: int = 3) -> str | None:
    """Ask the AI for a new design; keep it only if it passes the checks. Returns its key or None."""
    from .llm import gemini_text

    style = get_style(style_key)
    existing = [f"{item.name} ({item.mood})" for item in all_designs().values() if item.kind == kind]
    feedback = ""
    for _attempt in range(attempts):
        key = f"ai_{kind}_{uuid.uuid4().hex[:6]}"
        prompt = (
            f"You are a motion designer for a faceless nostalgic-food YouTube channel ({style.name}).\n"
            f"Video topic: {topic[:300]}\nReference video notes: {reference_notes[:600] or 'none'}\n"
            f"Designs the channel already has (make something clearly DIFFERENT): {existing}\n\n"
            + CONTRACT.replace("__ID__", key) + "\n" + (CHAPTER_DATA if kind == "chapter" else INGREDIENT_DATA)
            + "\n\nA working design that follows the contract (for structure only; do NOT copy its look):\n"
            + (WEB_ROOT / "hf" / ("film_slate" if kind == "chapter" else "recipe_book") / "index.html").read_text()[:6000]
            + "\n\nQuality bar: big, fully visible, high-contrast words (light text needs a dark panel behind it and dark "
              "text a light one; never put text in the channel colours straight on a similar background); a rich, "
              "finished layout filling the frame; clear motion (reveals, parallax, light sweeps, paper/film textures "
              "made with CSS); nothing clipped. You may override the text colour from the data for contrast."
            + (f"\n\nYour previous attempt was rejected by the art director: {feedback}\nFix every point." if feedback else "")
            + "\n\nAnswer with the HTML file, then the LAYOUT line, then on the very last line: NAME: <3-6 word name> | MOOD: <what it suits>"
        )
        try:
            # Claude when there is a key (the free Gemini models' designs rarely pass the art director).
            if str(getattr(settings, "anthropic_api_key", "") or "").strip():
                from .llm import claude_text

                answer = claude_text(settings, prompt)
            else:
                answer = gemini_text(settings, prompt, temperature=1.0, timeout=180)
        except Exception:
            return None
        html = _clean_html(answer)
        meta_line = re.search(r"NAME:\s*(.+?)\s*\|\s*MOOD:\s*(.+)", answer)
        layout_line = re.search(r"LAYOUT:\s*(\{.*\})", answer)
        try:
            layout = json.loads(layout_line.group(1)) if layout_line else {}
        except ValueError:
            layout = {}
        if 'data-composition-id="' + key not in html or "__timelines" not in html:
            continue
        folder = GENERATED / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(html)
        (folder / "meta.json").write_text(json.dumps({
            "kind": kind, "name": (meta_line.group(1) if meta_line else key)[:60],
            "mood": (meta_line.group(2) if meta_line else "")[:120], "channel": style_key, "created": time.time(),
            "layout": layout,
            "pending": True,
        }))
        passed, feedback = _passes(key, kind, style_key, sample_image, ffmpeg_path, settings, existing)
        if passed:
            meta = json.loads((folder / "meta.json").read_text())
            meta.pop("pending", None)
            (folder / "meta.json").write_text(json.dumps(meta))
            return key
        shutil.rmtree(folder, ignore_errors=True)
    return None


REVIEW = {"type": "OBJECT", "properties": {
    "score": {"type": "INTEGER"}, "problems": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["score", "problems"]}
PASS_SCORE = 7


def _passes(key: str, kind: str, style_key: str, image: str, ffmpeg_path: str, settings: Any,
            existing: list[str]) -> tuple[bool, str]:
    """Renders cleanly (short and normal, with and without words), is not blank, and an AI art
    director who looks at the frames scores it at least 7/10."""
    checks = GENERATED / key / "_checks"
    checks.mkdir(exist_ok=True)
    try:
        for seconds in (1.2, 4.0):
            for show_text in (True, False):
                video = checks / f"{seconds}-{show_text}.mp4"
                render_design(key, _sample_payload(kind, style_key, seconds, show_text, image), video, ffmpeg_path)
                if not _frames_vary(video, ffmpeg_path):
                    return False, "The render came out blank or one flat colour; the script probably failed."
        frames = [_frame(checks / "4.0-True.mp4", 3.6, ffmpeg_path), _frame(checks / "1.2-True.mp4", 1.1, ffmpeg_path)]
        from .llm import gemini_look

        review = gemini_look(settings, (
            f"You are a strict art director for a YouTube documentary channel. These are the last frames of a new "
            f"{'chapter title card' if kind == 'chapter' else 'ingredients card'} design (4-second and 1.2-second versions). "
            "The photos are random placeholders: do NOT judge what they show, only the design around them. "
            "Score 1-10. Deduct heavily if: any word is cut off, overlapping, too small or low-contrast/unreadable; "
            "the layout looks empty, broken or amateur; pictures are missing or badly placed; "
            f"{'the background photo is not used; ' if kind == 'chapter' else ''}"
            f"it looks like one of the channel's existing designs: {existing}. "
            "List the concrete problems to fix."), [item for item in frames if item], REVIEW)
        score = int(review.get("score") or 0)
        problems = "; ".join(str(item) for item in review.get("problems") or [])
        return score >= PASS_SCORE, f"score {score}/10: {problems}"
    except Exception as error:
        return False, f"Could not render or review: {str(error)[:200]}"
    finally:
        shutil.rmtree(checks, ignore_errors=True)


def _frame(video: Path, at: float, ffmpeg_path: str) -> bytes | None:
    import subprocess

    frame = video.with_name(video.stem + f"-{at}.png")
    subprocess.run([ffmpeg_path, "-v", "error", "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=960:-2", str(frame)], capture_output=True, timeout=60)
    return frame.read_bytes() if frame.is_file() else None


def library_size(kind: str) -> int:
    return sum(1 for item in all_designs().values() if item.kind == kind)


__all__ = ["generate_design", "library_size", "WEB_ROOT"]
