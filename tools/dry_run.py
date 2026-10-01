"""Dry run before calling a fix done: for each saved script, every section's subject, its searches and how
many results pass the tool's own source rule (search only, nothing downloaded). Usage:
  python tools/dry_run.py <code folder> "<project name>|<project name>" [max sections]"""
import sqlite3, os, sys, json
from types import SimpleNamespace
code, names = sys.argv[1], sys.argv[2].split("|")
sys.path.insert(0, code)
from pathlib import Path
from app.footage_match import heading_subject, _script_sentences, core_subject, vague_heading, signature_words, detect_era
from app.content_profile import profile_for
from app.config import SettingsStore
from app.archive_source import MultiSourceService, load_drive_index
from app.youtube_auto import usable_source
root = Path.home() / "Library/Application Support/LocalVideoStudio"
settings = SettingsStore(root / "settings.json").load()
service = MultiSourceService(settings.youtube_api_key, settings.ffmpeg_path, settings.youtube_license_mode,
                             drive_files=load_drive_index(root / "drive_index.json"))
db = sqlite3.connect(root / "studio.sqlite3"); db.row_factory = sqlite3.Row
cache = {}
for name in names:
    row = db.execute("select * from projects where name=?", (name,)).fetchone()
    project = dict(row); project["content_profile"] = json.loads(project["content_profile"] or "{}")
    profile = profile_for(project)
    script = project["script"]; era = detect_era(script) if profile.period else ""
    run = SimpleNamespace(script=script, exclude_videos=set(), settings=settings, profile=profile, era=era)
    headings = [h for h in (heading_subject(s) for s in _script_sentences(script)) if h and h.strip(" .") not in {"outro", "intro"}]
    print(f"\n=== {name} ({profile.kind}), {len(headings)} sections")
    total_ok = 0
    for subject in list(dict.fromkeys(headings))[:int(sys.argv[3]) if len(sys.argv) > 3 else 99]:
        dish = core_subject(subject)
        queries = profile.queries(profile.section_queries[:2], item=dish, era=era or ("vintage" if profile.period else ""), theme="")
        found = {}
        for q in queries:
            if q not in cache:
                try: cache[q] = service.search(q, maximum=10, archive=False)
                except Exception as e: cache[q] = []; print("  search failed", q, str(e)[:80])
            for item in cache[q]: found.setdefault(str(item.get("video_id")), item)
        ok = [i for i in found.values() if usable_source(run, i, False, dish, set())]
        total_ok += bool(ok)
        print(f"  {subject[:34]:34} -> topic '{dish}'  passed {len(ok)}/{len(found)}  e.g. {[str(i.get('title'))[:40] for i in ok[:2]]}")
    print(f"  sections with usable sources: {total_ok}/{len(set(headings))}")
