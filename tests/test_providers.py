from __future__ import annotations

import unittest

from app.domain import GenerationRequest
from app.providers.mock import MockImageProvider


class ProviderTests(unittest.TestCase):
    def test_mock_provider_returns_valid_png_without_cost(self) -> None:
        asset = MockImageProvider().generate(
            GenerationRequest(
                prompt="A warm kitchen",
                negative_prompt="text",
                model="offline-placeholder",
                width=640,
                height=360,
            )
        )
        self.assertTrue(asset.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(asset.cost, 0.0)
        self.assertEqual(asset.provider, "mock")


if __name__ == "__main__":
    unittest.main()

