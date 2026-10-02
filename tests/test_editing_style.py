from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.editing_style import (ai_image_rules, checker_rules, footage_rules, gate_message, load_style, save_style,
                               style_report)

STYLE = {
    "footage": {"max_ai_image_share": 0.05, "minimum_real_video_share_per_item": 0.65, "max_source_minutes": 20,
                "sources_per_item": 2, "avoid_titles_with": ["asmr", "AI generated"]},
    "repetition": {"same_source_in_neighbouring_scenes": False},
    "never_show": {"people": ["smartwatch or phone"], "other": ["generated brand logo"]},
    "checker": {"reject_if": ["shows a different dish"], "accept_if": ["a clean shot of the correct dish"]},
    "ai_images": {"never": ["text or logos", "modern objects"]},
}


def scene(position, start, end, asset=None, narration="line"):
    return {"id": f"s{position}", "position": position, "start_seconds": start, "end_seconds": end,
            "selected_asset_id": asset, "narration": narration}


class StyleFileTest(unittest.TestCase):
    def test_saved_style_is_read_back_and_turned_into_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            save_style(Path(folder), "v2", STYLE)
            rules = footage_rules(load_style(Path(folder), "v2"))
            self.assertEqual(load_style(Path(folder), "v4"), {})
        self.assertEqual(rules["max_ai_share"], 0.05)
        self.assertEqual(rules["avoid_titles"], ["asmr", "ai generated"])
        self.assertTrue(rules["no_neighbour_repeat"])
        self.assertIn("smartwatch or phone", checker_rules(STYLE))
        self.assertIn("text or logos", ai_image_rules(STYLE))
        self.assertEqual(checker_rules({}), "")


class ReportTest(unittest.TestCase):
    def test_rules_are_measured_and_gates_name_the_problem(self):
        assets = {"a": {"provider": "youtube", "provider_asset_id": "v1"}, "b": {"provider": "youtube", "provider_asset_id": "v1"},
                  "g": {"provider": "generated"}, "c": {"provider": "chapter"}}
        scenes = [scene(1, 0, 4, "g"), scene(2, 4, 8, "a"), scene(3, 8, 12, "b"), scene(4, 12, 16, None)]
        report = style_report(STYLE, scenes, assets, [("Fruitcake", scenes[1:])], {"s1"})
        by_rule = {check["rule"]: check for check in report["checks"]}
        self.assertFalse(by_rule["No AI image in the hook"]["ok"])
        self.assertFalse(by_rule["Every scene has media"]["ok"])
        self.assertFalse(by_rule["Same source never in neighbouring scenes"]["ok"])
        self.assertTrue(by_rule["'Fruitcake': real video at least 65%"]["ok"])  # 8 of 12 s
        self.assertTrue(gate_message(report).startswith("Quality check"))
        clean = style_report(STYLE, scenes[1:2], assets, [("Fruitcake", scenes[1:2])], set())
        self.assertEqual(gate_message(clean), "")


if __name__ == "__main__":
    unittest.main()
