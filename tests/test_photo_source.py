from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from app.edit_export import credits_text
from app.photo_source import attribution, frame_photo, search_photos


class PhotoSourceTests(unittest.TestCase):
    def test_search_requests_commercial_modifiable_photos(self) -> None:
        response = {"results": [{"url": "https://x/a.jpg", "width": 1200}, {"url": "https://x/b.jpg", "width": 200}]}
        with patch("app.photo_source.request_json", return_value=response) as request:
            found = search_photos("1950s kitchen")
        self.assertIn("license_type=commercial%2Cmodification", request.call_args.args[0])
        self.assertIn("category=photograph", request.call_args.args[0])
        self.assertEqual([item["url"] for item in found], ["https://x/a.jpg"])

    def test_portrait_photo_is_framed_to_16_9(self) -> None:
        framed = frame_photo(Image.new("RGB", (600, 900), (200, 100, 50)))
        self.assertEqual(framed.size, (1920, 1080))

    def test_attribution_and_credits(self) -> None:
        item = {"title": "Cookie Company", "creator": "Jane", "license": "by", "license_version": "2.0", "source": "flickr"}
        self.assertEqual(attribution(item), '"Cookie Company" by Jane (CC BY 2.0) via flickr')
        scenes = [{"selected_asset_id": "a"}, {"selected_asset_id": "b"}, {"selected_asset_id": "c"}]
        assets = {
            "a": {"provider": "photo", "metadata": {"attribution": attribution(item), "source_url": "https://f"}},
            "b": {"provider": "youtube", "remote_url": "https://y", "metadata": {"title": "T", "channel": "C"}},
            "c": {"provider": "generated", "metadata": {}},
        }
        text = credits_text(scenes, assets)
        self.assertIn("Cookie Company", text)
        self.assertIn('"T" by C https://y', text)
        self.assertEqual(text.count("\n"), 4)


if __name__ == "__main__":
    unittest.main()
