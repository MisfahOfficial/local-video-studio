from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from app.edit_export import write_capcut_draft, write_premiere_xml


class EditExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clips = []
        for index, (start, end) in enumerate(((0.0, 4.2), (4.2, 10.0), (10.0, 13.5)), start=1):
            path = self.root / f"clip-{index:04d}.mp4"
            path.write_bytes(b"video")
            self.clips.append((path, start, end))
        self.voiceover = self.root / "voiceover.mp3"
        self.voiceover.write_bytes(b"audio")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_premiere_xml_has_contiguous_clips_and_voiceover(self) -> None:
        path = self.root / "edit.xml"
        write_premiere_xml(path, "My Video", self.clips, self.voiceover, 13.5, 30, 1920, 1080)
        tree = ElementTree.parse(path)
        items = tree.findall(".//video/track/clipitem")
        self.assertEqual([(int(item.find("start").text), int(item.find("end").text)) for item in items],
                         [(0, 126), (126, 300), (300, 405)])
        self.assertTrue(items[0].find("file/pathurl").text.startswith("file:///"))
        self.assertIsNotNone(tree.find(".//audio/track/clipitem/file/pathurl"))

    def test_capcut_draft_is_consistent_and_registered(self) -> None:
        drafts = self.root / "drafts"
        drafts.mkdir()
        (drafts / "root_meta_info.json").write_text(json.dumps({"all_draft_store": [], "draft_ids": 3, "root_path": str(drafts)}))
        folder = write_capcut_draft(
            drafts, "My Video", self.clips, self.voiceover, 13.5, [(0.0, 4.2, "HOW DID ONE DOLLAR")],
            {"position": "middle"}, 1920, 1080, 30,
        )
        draft = json.loads((folder / "draft_info.json").read_text())
        material_ids = {item["id"] for items in draft["materials"].values() for item in items}
        segments = [segment for track in draft["tracks"] for segment in track["segments"]]
        self.assertEqual([track["type"] for track in draft["tracks"]], ["video", "text", "audio"])
        for segment in segments:
            self.assertIn(segment["material_id"], material_ids)
            self.assertTrue(set(segment["extra_material_refs"]) <= material_ids)
        videos = draft["tracks"][0]["segments"]
        self.assertEqual([segment["target_timerange"]["start"] for segment in videos], [0, 4_200_000, 10_000_000])
        paths = {item["path"] for item in draft["materials"]["videos"]}
        # Media is copied inside the draft folder because CapCut is sandboxed to ~/Movies.
        self.assertEqual(paths, {str((folder / "Resources" / "local_media" / path.name).resolve()) for path, _s, _e in self.clips})
        self.assertTrue(all(Path(path).is_file() for path in paths))
        self.assertTrue(draft["materials"]["audios"][0]["path"].startswith(str(folder.resolve())))
        text = json.loads(draft["materials"]["texts"][0]["content"])
        self.assertEqual(text["text"], "HOW DID ONE DOLLAR")
        index = json.loads((drafts / "root_meta_info.json").read_text())
        self.assertEqual(index["all_draft_store"][0]["draft_name"], "My Video")
        self.assertEqual(index["draft_ids"], 4)
        self.assertTrue(list(drafts.glob("root_meta_info.backup-*.json")))


if __name__ == "__main__":
    unittest.main()
