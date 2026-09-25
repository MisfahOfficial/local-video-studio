from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.chapter_cards import build_chapter_cards, render_card
from app.database import Database
from app.paths import AppPaths
from app.scene_planner import RuleBasedScenePlanner


class ChapterCardTests(unittest.TestCase):
    def test_card_is_full_hd(self) -> None:
        card = render_card(None, "POOR MAN'S COOKIES", 1, "history_documentary")
        self.assertEqual(card.size, (1920, 1080))

    def test_heading_scenes_get_selected_cards_without_captions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = AppPaths.resolve(temporary)
            db = Database(paths.database)
            project = db.create_project("Cards", "us_nostalgia")
            drafts = RuleBasedScenePlanner().plan(
                "We made thirty desserts.\nPOOR MAN'S COOKIES\nThey used oats and sugar.\n2. Vinegar Pie\nIt used vinegar.",
                theme_id="us_nostalgia", duration_seconds=20.0,
            )
            db.replace_scenes(project["id"], drafts)
            self.assertEqual(build_chapter_cards(db, paths, project["id"]), 2)
            scenes = db.list_scenes(project["id"])
            assets = {asset["id"]: asset for asset in db.list_assets(project["id"])}
            cards = [scene for scene in scenes if assets.get(scene["selected_asset_id"], {}).get("provider") == "chapter"]
            self.assertEqual([scene["caption_text"] for scene in cards], ["", ""])
            numbers = [assets[scene["selected_asset_id"]]["metadata"]["chapter"] for scene in cards]
            self.assertEqual(numbers, [1, 2])
            self.assertTrue(all(Path(assets[scene["selected_asset_id"]]["local_path"]).is_file() for scene in cards))


if __name__ == "__main__":
    unittest.main()
