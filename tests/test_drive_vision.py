import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.archive_source import drive_id
from app.drive_vision import DriveVisualIndex


class DriveVisualSearch(unittest.TestCase):
    def test_files_are_found_by_what_they_show(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            index = DriveVisualIndex(root)
            index.folder.mkdir(parents=True)
            pie, kitchen = np.eye(512, dtype=np.float16)[:2]
            for name, vectors in (("FlexClip_12", [pie, kitchen]), ("FlexClip_13", [kitchen])):
                identifier = drive_id(f"/drive/{name}.mp4")
                np.save(index.folder / f"{identifier}.npy", np.asarray(vectors))
                index.manifest[identifier] = {"path": f"/drive/{name}.mp4", "name": name, "folder": "V2", "frames": len(vectors)}
            index._save_manifest()
            found = DriveVisualIndex(root).search(pie.astype(np.float32), minimum=0.5)
            self.assertEqual([item["title"] for item in found], ["FlexClip_12"])
            self.assertEqual(found[0]["source"], "drive")
            self.assertEqual(found[0]["local_path"], "/drive/FlexClip_12.mp4")


if __name__ == "__main__":
    unittest.main()
