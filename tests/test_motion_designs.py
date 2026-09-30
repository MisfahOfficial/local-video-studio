import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import motion_designs
from app.channel_styles import get_style


class DesignChoice(unittest.TestCase):
    def test_designs_vary_and_avoid_the_channels_recent_looks(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(motion_designs, "web_engines_ready", return_value=True):
            root = Path(folder)
            motion_designs.remember_designs(root, "v2", ["recipe_book", "recipe_book", "recipe_book"])
            plan = motion_designs.plan_designs("ingredients", 6, "desserts", "v2", root)
            self.assertEqual(len(plan), 6)
            self.assertTrue(all(first != second for first, second in zip(plan, plan[1:])))
            self.assertNotEqual(plan[0], "recipe_book")  # the look used lately does not lead
            self.assertEqual(set(plan), {"cards", "recipe_book", "carousel"})

    def test_without_node_only_the_python_designs_are_used(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(motion_designs, "web_engines_ready", return_value=False):
            self.assertEqual(set(motion_designs.plan_designs("chapter", 4, "", "v3", Path(folder))), {"classic"})


class SharedPositions(unittest.TestCase):
    def test_export_text_sits_where_the_design_draws_it(self):
        style = get_style("v3")
        items = [{"label": "pineapple", "image": "/x/a.jpg"}, {"label": "coconut", "image": "/x/b.jpg"}]
        for design in ("recipe_book", "carousel"):
            payload = motion_designs.ingredient_payload(design, items, style, 4.0)
            layers = motion_designs.text_layers({"design": design, "payload": payload}, style)
            drawn = {(item["label"], item["text_x"], item["text_y"]) for item in payload["items"]}
            self.assertTrue(drawn <= {(layer["text"], layer["x"], layer["y"]) for layer in layers})
        payload = motion_designs.chapter_payload("newspaper", "Watergate Salad", 5, style, 4.0)
        layers = motion_designs.text_layers({"design": "newspaper", "payload": payload}, style)
        self.assertIn("WATERGATE SALAD", [layer["text"] for layer in layers])


if __name__ == "__main__":
    unittest.main()
