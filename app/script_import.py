"""Script + reference images from one file or one Google Doc (Ishaq, 7 Oct).

Tab 1 (or everything before a "REFERENCES" heading) is the script; Tab 2 (or the lines after the heading) lists
reference images per item: "Abbey Crunch | https://...jpg, https://...jpg". Each name is matched to a heading of
the script; the pictures are downloaded into the project and used in the video (as real photos) and as the
reference for any AI image of that item. Only image links are read; anything that is not an image is reported.
"""
from __future__ import annotations

import html
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
    # The first tab is always "t.0" (too short for the pattern below); the others are listed in the page.
    tabs = list(dict.fromkeys(["t.0"] + re.findall(r"\b(t\.[a-z0-9]{6,})\b", page)))
    texts: list[str] = []
    for index, tab in enumerate(tabs):
        try:
            text = _get(f"{base}/export?format=txt&tab={tab}").decode("utf-8-sig", "replace")
            if index:  # reference tabs: links hide behind words ("pictures"), so read them from the HTML
                text = html_lines(_get(f"{base}/export?format=html&tab={tab}").decode("utf-8", "replace")) or text
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


def _real_link(href: str) -> str:
    """Google Docs wraps every link in google.com/url?q=...; unwrap it."""
    href = html.unescape(href)
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        return urllib.parse.parse_qs(parsed.query).get("q", [href])[0]
    return href


def html_lines(page: str) -> str:
    """A Docs HTML export as "Name | link link" lines: one per table row (the name is the row's first words that
    are not a number), and plain lines with their links written out elsewhere."""
    def text_of(part: str) -> list[str]:
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", part, re.S) or [part]
        lines = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", item)).split()) for item in paragraphs]
        return [line for line in lines if line]

    lines: list[str] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        links = [_real_link(href) for href in re.findall(r'href="([^"]+)"', row)]
        cells = [text_of(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        name = next((cell[0] for cell in cells if cell and re.search(r"[A-Za-z]", cell[0])
                     and not re.match(r"^\d+\.?$", cell[0])), "")
        if name and links:
            lines.append(f"{name} | {' '.join(dict.fromkeys(links))}")
    outside = re.sub(r"<table.*?</table>", "", page, flags=re.S)
    for paragraph in re.findall(r"<p[^>]*>(.*?)</p>", outside, re.S):
        links = [_real_link(href) for href in re.findall(r'href="([^"]+)"', paragraph)]
        words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", paragraph)).split())
        if links:
            lines.append(f"{words} | {' '.join(links)}")
        elif words:
            lines.append(words)
    return "\n".join(lines)


def image_search(link: str) -> str:
    """The words of a Google Images search link ("" when the link is not one)."""
    parsed = urllib.parse.urlparse(link)
    query = urllib.parse.parse_qs(parsed.query)
    if "google." in parsed.netloc and (parsed.path == "/search" and (query.get("tbm") == ["isch"] or query.get("udm") == ["2"])
                                       or parsed.netloc.startswith("images.")):
        return (query.get("q") or [""])[0].strip()
    return ""


def _looks_like_page(link: str) -> bool:
    """An article or source page (Wikipedia, a blog): not a picture, so it is skipped quietly."""
    path = urllib.parse.urlparse(link).path.lower()
    return "drive.google.com" not in link and not re.search(r"\.(jpe?g|png|webp|gif|avif|bmp)$", path) and (
        path.endswith((".html", ".htm", "/")) or "/wiki/" in path or "/pages/" in path or "/post/" in path
        or "/blog" in path or "?p=" in link)


def shows_item(settings: Any, data: bytes, item: str, about: str = "") -> bool | None:
    """Claude: is this the item itself, as the script describes it (not a look-alike from another country or a
    different product with a similar name)? None = no Claude key."""
    from .llm import _claude_key, claude_ask

    if settings is None or not _claude_key(settings):
        return None
    schema = {"type": "OBJECT", "properties": {"picture_shows": {"type": "STRING"}, "same_item": {"type": "BOOLEAN"}},
              "required": ["picture_shows", "same_item"]}
    prompt = (f'Item: "{item}".' + (f" The script describes it: {about[:400]}" if about else "") +
              "\nFirst say in a few words what the picture shows. same_item is true only if it is this exact item as "
              "described (its product, packet, wrapper, box or advert). It is false for a different product, a similar "
              "name from another country or with a different filling or shape, a modern remake, a different item from "
              "the same brand, or a picture covered by a shop's logo or watermark.")
    try:
        answer = json.loads(claude_ask(settings, prompt, images=[data], schema=schema, max_tokens=200))
    except (ValueError, TypeError):
        return False
    return bool(answer.get("same_item"))


def section_texts(script: str) -> dict[str, str]:
    """{heading subject: the sentences under it} - what the check compares a picture with."""
    from .footage_match import heading_subject

    sections: dict[str, str] = {}
    current = ""
    for line in script.splitlines():
        subject = heading_subject(line)
        if subject:
            current = subject
            sections.setdefault(current, "")
        elif current and len(sections[current]) < 600:
            sections[current] = (sections[current] + " " + line.strip()).strip()
    return sections


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


GENERIC = {"bar", "candy", "candie", "gum", "bottle", "original", "the", "and", "of"}
NOT_ITEMS = {"intro", "outro", "introduction", "conclusion", "hook", "ending"}


def _words(text: str) -> set[str]:
    """The telling words of a name: no "(Wonka)"/"(1976)" note, no plural s, no "bar/candy/gum"."""
    text = re.sub(r"\([^)]*\)", " ", text)
    words = {word[:-1] if len(word) > 3 and word.endswith("s") else word for word in _key(text).split()}
    return (words - GENERIC) or words


def match_items(script: str, entries: list[tuple[str, list[str]]]) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """({script heading subject: [links]}, script items with no reference, reference names matching no item).
    "Space Dust / Cosmic Candy" or "Wax Lips & Nik-L-Nip Bottles" are tried part by part."""
    from .footage_match import heading_subject

    headings = [heading_subject(line) for line in script.splitlines()]
    headings = list(dict.fromkeys(item for item in headings if item and _key(item) not in NOT_ITEMS))
    matched: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for name, links in entries:
        found: list[str] = []
        for part in [name] + [piece for piece in re.split(r"\s+(?:/|&|\+|and)\s+", name) if piece != name]:
            key = _key(part)
            best = next((item for item in headings if _key(item) == key), None)
            if best is None:  # "Super Skrunch Bar" for "SUPER SKRUNCH (WONKA)": one name's words hold the other's
                words = _words(part)
                best = next((item for item in headings if words and (words <= _words(item) or _words(item) <= words)), None)
            if best is not None and best not in found:
                found.append(best)
        if not found:
            unmatched.append(name)
        for best in found:
            matched.setdefault(best, []).extend(link for link in links if link not in matched.get(best, []))
    missing = [item for item in headings if item not in matched]
    return matched, missing, unmatched


def image_url(link: str) -> str:
    """A Google Drive share link becomes its direct download link."""
    found = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=\w+&)?id=)([A-Za-z0-9_-]{20,})", link)
    return f"https://drive.google.com/uc?export=download&id={found.group(1)}" if found else link


SEARCH_PICTURES = 3  # pictures kept from one Google Images search link


def _picture(data: bytes) -> Any:
    from PIL import Image

    picture = Image.open(io.BytesIO(data))
    picture.load()
    if min(picture.size) < 200:
        raise ValueError(f"too small ({picture.size[0]}x{picture.size[1]})")
    return picture


def fetch_references(matched: dict[str, list[str]], folder: Path, settings: Any = None,
                     about: dict[str, str] | None = None) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """Download every picture of every item. A direct image link is used as it is; a Google Images search link is
    searched again (Serper) and its first pictures that Claude confirms show the item are kept; article pages are
    skipped. ({item: [saved files]}, [{item, link, problem}])."""
    from .photo_source import _serper_images, configure_serper

    if settings is not None:
        configure_serper(str(getattr(settings, "serper_api_key", "") or ""))
    saved: dict[str, list[str]] = {}
    failed: list[dict[str, str]] = []
    folder.mkdir(parents=True, exist_ok=True)

    def keep(item: str, picture: Any) -> None:
        name = re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-")[:40] or "item"
        destination = folder / f"{name}-{len(saved.get(item, [])) + 1}.jpg"
        picture.convert("RGB").save(destination, quality=92)
        saved.setdefault(item, []).append(str(destination))

    for item, links in matched.items():
        for link in links:
            query = image_search(link)
            if query:
                try:
                    found = _serper_images(query, 40)
                except Exception as error:
                    found, problem = [], f"search failed: {str(error)[:80]}"
                else:
                    problem = "" if found else "image search needs the Serper key in Settings" if not getattr(
                        settings, "serper_api_key", "") else "the search found no pictures"
                kept = unclear = 0
                for result in found[:10]:
                    if kept >= SEARCH_PICTURES:
                        break
                    try:
                        data = _get(result["url"], timeout=20)
                        picture = _picture(data)
                        verdict = shows_item(settings, data, item, (about or {}).get(item, ""))
                    except Exception:
                        continue
                    if verdict is False:
                        unclear += 1
                        continue
                    keep(item, picture)
                    kept += 1
                if not kept:
                    failed.append({"item": item, "link": link, "problem": problem or
                                   f"none of the search's pictures clearly showed the item ({unclear} refused)"})
                continue
            if _looks_like_page(link):
                continue  # a source article, not a picture
            try:
                keep(item, _picture(_get(image_url(link))))
            except Exception as error:
                problem = "not an image (only image links are read)" if "cannot identify image" in str(error) else str(error)[:120]
                failed.append({"item": item, "link": link, "problem": problem})
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


def import_source(project_dir: Path, *, link: str = "", filename: str = "", data: bytes = b"",
                  settings: Any = None) -> dict[str, Any]:
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
    saved, failed = fetch_references(matched, project_dir / "assets" / "references", settings,
                                     section_texts(script))
    save_references(project_dir, saved)
    return {
        "script": script,
        "tabs": len(texts),
        "references": [{"item": item, "images": len(paths)} for item, paths in saved.items()],
        "items_without_reference": missing,
        "names_not_in_script": unmatched,
        "failed_links": failed,
    }
