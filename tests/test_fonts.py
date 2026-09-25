from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app.fonts import FontError, FontManager


class FontManagerTests(unittest.TestCase):
    def test_requested_caption_families_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = FontManager(Path(temporary))
            families = {font["family"] for font in manager.list_fonts()}
        self.assertTrue({"Poppins", "Impact", "Amsi Pro", "Montserrat"}.issubset(families))

    def test_upload_lists_and_resolves_local_font(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = FontManager(Path(temporary))
            payload = manager.save_upload("My_Title-Font.ttf", io.BytesIO(b"\x00\x01\x00\x00font-data"), 13)
            self.assertEqual(payload["family"], "My Title Font")
            self.assertTrue(payload["custom"])
            self.assertTrue(manager.resolve("My_Title-Font.ttf").is_file())
            self.assertIn("My Title Font", [font["family"] for font in manager.list_fonts()])

    def test_installed_font_replaces_matching_family_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = FontManager(Path(temporary))
            manager.save_upload("Poppins.ttf", io.BytesIO(b"\x00\x01\x00\x00font-data"), 13)
            matches = [font for font in manager.list_fonts() if font["family"] == "Poppins"]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["custom"])

    def test_invalid_upload_and_unsafe_download_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = FontManager(Path(temporary))
            with self.assertRaises(FontError):
                manager.save_upload("not-a-font.ttf", io.BytesIO(b"nope"), 4)
            with self.assertRaises(FontError):
                manager.download("http://127.0.0.1/private.ttf")


if __name__ == "__main__":
    unittest.main()
