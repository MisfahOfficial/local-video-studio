from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.config import StudioSettings
from app.domain import GeneratedAsset
from app.providers.base import ProviderError
from app.vintage_still import film_finish, generate_vintage_still, still_prompt


def _png(width: int = 1344, height: int = 768) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (220, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class VintageStillTests(unittest.TestCase):
    def test_prompt_names_scene_subject_and_era(self) -> None:
        prompt = still_prompt("Grandmas mixed rolled oats with sugar.", "poor man's cookies", "1950s")
        self.assertIn("Grandmas mixed rolled oats", prompt)
        self.assertIn("poor man's cookies", prompt)
        self.assertIn("1950s", prompt)

    def test_finish_is_full_hd_and_faded(self) -> None:
        image = film_finish(_png(), seed=3)
        self.assertEqual(image.size, (1920, 1080))
        red, green, _blue = image.getpixel((960, 540))
        self.assertLess(red - green, 180)  # saturation pulled back from the pure red input

    def test_missing_key_is_reported(self) -> None:
        with self.assertRaisesRegex(ProviderError, "Runware"):
            generate_vintage_still(StudioSettings(), "text", "", "1950s", Path("unused.jpg"))

    def test_generated_still_is_saved(self) -> None:
        settings = StudioSettings(runware_api_key="key")
        fake = GeneratedAsset(content=_png(), extension="png", provider="runware", model="m", cost=0.0008)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("app.vintage_still.RunwareImageProvider.generate", return_value=fake):
            destination = Path(temporary) / "still.jpg"
            metadata = generate_vintage_still(settings, "A 1950s kitchen.", "", "1950s", destination)
            self.assertTrue(destination.is_file())
        self.assertTrue(metadata["generated_still"])
        self.assertAlmostEqual(metadata["cost"], 0.0008)


if __name__ == "__main__":
    unittest.main()


class CountryHomeTest(unittest.TestCase):
    def test_britain_channel_stills_show_a_british_home(self):
        from app.vintage_still import still_prompt

        self.assertIn("British home", still_prompt("Mum baked on Sundays", "jam tarts", "1970s", country="GB"))
        self.assertIn("Canadian home", still_prompt("Mum baked on Sundays", "butter tarts", "1970s", country="CA"))
        self.assertIn("American home", still_prompt("Mum baked on Sundays", "pie", "1950s"))


class PoolSizeTest(unittest.TestCase):
    def test_long_sections_read_more_sources(self):
        from app.youtube_auto import pool_size

        self.assertEqual(pool_size(10), 2)
        self.assertEqual(pool_size(24), 2)
        self.assertEqual(pool_size(34), 3)
