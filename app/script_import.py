"""Script + reference images from one file or one Google Doc (Ishaq, 7 Oct).

Tab 1 (or everything before a "REFERENCES" heading) is the script; Tab 2 (or the lines after the heading) lists
reference images per item: "Abbey Crunch | https://...jpg, https://...jpg". Each name is matched to a heading of
the script; the pictures are downloaded into the project and used in the video (as real photos) and as the
reference for any AI image of that item. Only image links are read; anything that is not an image is reported.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

URL = re.compile(r"https?://[^\s,|<>\"']+")
HEADING = re.compile(r"^\s*(?:tab\s*2\b.*|references?|reference images?|refs?)\s*:?\s*$", re.IGNORECASE)
REFERENCE_FILE = "references.json"
AGENT = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"}


# ------------------------------------------------------------------ reading the source
def _get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=timeout) as response:
        return response.read()


def doc_id(link: str) -> str:
    found = re.search(r"/document/d/([A-Za-z0-9_-]{20,})", link)
    return found.group(1) if found else ""


def google_doc_tabs(link: str) -> list[str]:
    """The text of every tab of a Google Doc shared as "anyone with the link can view", in order."""
    document = doc_id(link)
    if not document:
        raise ValueError("This is not a Google Docs link")
    base = f"https://docs.google.com/document/d/{document}"
    try:
        page = _get(f"{base}/edit").decode("utf-8", "replace")
    except Exception as error:
        raise ValueError(f"The Google Doc could not be opened (is it shared as 'Anyone with the link'?): {error}") from error
    tabs = list(dict.fromkeys(re.findall(r"\b(t\.[a-z0-9]{6,})\b", page)))
    linked = re.search(r"[?&]tab=(t\.[a-z0-9]+)", link)
    if linked and linked.group(1) not in tabs:
        tabs.insert(0, linked.group(1))
    texts: list[str] = []
    for tab in tabs:
        try:
            text = _get(f"{base}/export?format=txt&tab={tab}").decode("utf-8-sig", "replace")
        except Exception:
            continue
        if text.strip() and text not in texts:
            texts.append(text)
    if not texts:  # a document without tabs
        texts = [_get(f"{base}/export?format=txt").decode("utf-8-sig", "replace")]
    return texts


def pdf_text(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "script.pdf"
        source.write_bytes(data)
        for tool in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext", "pdftotext"):
            try:
                result = subprocess.run([tool, "-layout", str(source), "-"], capture_output=True, timeout=120)
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return result.stdout.decode("utf-8", "replace")
    raise ValueError("The PDF could not be read")


def split_script(texts: list[str]) -> tuple[str, str]:
    """(script, references). Tabs: the first is the script, the tabs holding links are the references. One text:
    the part after a "REFERENCES" heading is the references."""
    if len(texts) > 1:
        script = texts[0]
        references = "\n".join(text for text in texts[1:] if URL.search(text))
        return script.strip(), references
    lines = texts[0].splitlines() if texts else []
    for index, line in enumerate(lines):
        if HEADING.match(line):
            return "\n".join(lines[:index]).strip(), "\n".join(lines[index + 1:])
    return "\n".join(lines).strip(), ""


# ------------------------------------------------------------------ references
def parse_references(text: str) -> list[tuple[str, list[str]]]:
    """[(item name, [links])]: "Name | link, link", "Name: link", or a name line followed by link lines."""
    entries: list[tuple[str, list[str]]] = []
    last_name = ""
    for raw in text.splitlines():
        line = raw.strip().strip("•*-").strip()
        if not line:
            continue
        links = URL.findall(line)
        name = URL.sub("", line).strip(" |:,;-\t")
        if links:
            name = name or last_name
            if not name:
                continue
            if entries and entries[-1][0] == name:
                entries[-1][1].extend(links)
            else:
                entries.append((name, list(links)))
        elif not re.match(r"^(item|name|reference links?|links?)\s*\|?", line, re.IGNORECASE):
            last_name = name
    return entries


def _key(text: str) -> str:
    from .footage_match import _NUMBERING

    return " ".join(re.findall(r"[a-z0-9]+", _NUMBERING.sub("", text).lower()))


def match_items(script: str, entries: list[tuple[str, list[str]]]) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """({script heading subject: [links]}, script items with no reference, reference names matching no item)."""
    from .footage_match import heading_subject

    headings = [heading_subject(line) for line in script.splitlines()]
    headings = list(dict.fromkeys(item for item in headings if item))
    matched: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for name, links in entries:
        key = _key(name)
        best = next((item for item in headings if _key(item) == key), None)
        if best is None:  # "Abbey Crunch" for "ABBEY CRUNCH (MCVITIE'S)": every word of the name in the heading
            words = set(key.split())
            best = next((item for item in headings if words and words <= set(_key(item).split())), None)
        if best is None:
            unmatched.append(name)
            continue
        matched.setdefault(best, []).extend(link for link in links if link not in matched.get(best, []))
    missing = [item for item in headings if item not in matched]
    return matched, missing, unmatched


def image_url(link: str) -> str:
    """A Google Drive share link becomes its direct download link."""
    found = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=\w+&)?id=)([A-Za-z0-9_-]{20,})", link)
    return f"https://drive.google.com/uc?export=download&id={found.group(1)}" if found else link


def fetch_references(matched: dict[str, list[str]], folder: Path) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """Download every link; keep only real pictures. ({item: [saved files]}, [{item, link, problem}])."""
    from PIL import Image

    saved: dict[str, list[str]] = {}
    failed: list[dict[str, str]] = []
    folder.mkdir(parents=True, exist_ok=True)
    for item, links in matched.items():
        for index, link in enumerate(links):
            try:
                data = _get(image_url(link))
                picture = Image.open(io.BytesIO(data))
                picture.load()
                if min(picture.size) < 200:
                    raise ValueError(f"too small ({picture.size[0]}x{picture.size[1]})")
            except Exception as error:
                problem = "not an image (only image links are read)" if "cannot identify image" in str(error) else str(error)[:120]
                failed.append({"item": item, "link": link, "problem": problem})
                continue
            name = re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-")[:40] or "item"
            destination = folder / f"{name}-{index + 1}.jpg"
            picture.convert("RGB").save(destination, quality=92)
            saved.setdefault(item, []).append(str(destination))
    return saved, failed


def save_references(project_dir: Path, saved: dict[str, list[str]]) -> None:
    (project_dir / REFERENCE_FILE).write_text(json.dumps(saved, indent=1))


def load_references(project_dir: Path) -> dict[str, list[str]]:
    """{item subject: [image files]} imported for this project ({} when none)."""
    try:
        data = json.loads((project_dir / REFERENCE_FILE).read_text())
        return {str(key): [path for path in value if Path(path).is_file()] for key, value in data.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def references_for(project_dir: Path, subject: str) -> list[str]:
    """The imported pictures of one item (matched like headings are)."""
    key = _key(subject)
    return next((paths for item, paths in load_references(project_dir).items() if _key(item) == key), [])


def import_source(project_dir: Path, *, link: str = "", filename: str = "", data: bytes = b"") -> dict[str, Any]:
    """Read a Google Doc link or a .txt/.pdf file; save its references. Returns the script and a report."""
    if link:
        texts = google_doc_tabs(link)
    elif filename.lower().endswith(".pdf"):
        texts = [pdf_text(data)]
    else:
        texts = [data.decode("utf-8-sig", "replace")]
    script, reference_text = split_script(texts)
    entries = parse_references(reference_text)
    matched, missing, unmatched = match_items(script, entries)
    saved, failed = fetch_references(matched, project_dir / "assets" / "references")
    save_references(project_dir, saved)
    return {
        "script": script,
        "tabs": len(texts),
        "references": [{"item": item, "images": len(paths)} for item, paths in saved.items()],
        "items_without_reference": missing,
        "names_not_in_script": unmatched,
        "failed_links": failed,
    }
