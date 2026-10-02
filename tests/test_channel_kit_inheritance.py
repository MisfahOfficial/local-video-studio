import json
import tempfile
import unittest
from pathlib import Path

from app.channel_kits import HOUSE, kit_style, load_kits
from app.channel_styles import STYLES


class HouseStyleInheritance(unittest.TestCase):
    def test_without_changes_every_channel_is_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as folder:
            kits = load_kits(Path(folder))
            self.assertEqual(kits["v3"]["captions"], {"animation": "highlight", "position": "bottom"})
            self.assertEqual(kits["v3"]["chapter_designs"], ["film_slate", "typewriter_card"])
            self.assertEqual(kits["v1"]["extras"], ["map", "price", "years", "comment"])
            for key in ("v1", "v3", "v4"):
                self.assertIs(kit_style(kits[key]), STYLES[key])
            # V2 took its viral video's look on Ishaq's approval (2 Oct); the old V2 look lives on as v2_classic.
            self.assertIs(kit_style(kits["v2_classic"]), STYLES["v2"])
            self.assertNotEqual(kit_style(kits["v2"]).bg_outer, STYLES["v2"].bg_outer)

    def test_one_house_change_reaches_every_channel_and_a_channel_change_only_that_one(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "channel_kits.json").write_text(json.dumps({
                HOUSE: {"captions": {"position": "middle"}, "style_overrides": {"subscribe": [10, 20, 30]}},
                "v2": {"captions": {"animation": "plain"}, "style_overrides": {"highlight": [1, 2, 3]}},
            }))
            kits = load_kits(root)
            self.assertEqual(kits["v1"]["captions"], {"animation": "highlight", "position": "middle"})
            self.assertEqual(kits["v2"]["captions"], {"animation": "plain", "position": "middle"})
            self.assertEqual(kit_style(kits["v4"]).subscribe, (10, 20, 30))  # house change, every channel
            self.assertEqual(kit_style(kits["v2"]).highlight, (1, 2, 3))    # only V2
            self.assertEqual(kit_style(kits["v3"]).highlight, STYLES["v3"].highlight)
            self.assertEqual(kit_style(kits["v3"]).bg_inner, STYLES["v3"].bg_inner)  # untouched fields inherited


if __name__ == "__main__":
    unittest.main()
