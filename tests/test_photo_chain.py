import unittest
from unittest import mock

from app import photo_source
from app.providers.base import ProviderError


class FreeImageChain(unittest.TestCase):
    def setUp(self):
        photo_source._resting.clear()

    def test_next_source_takes_over_when_one_fails(self):
        calls = []

        def failing(query, count):
            calls.append("a")
            raise ProviderError("HTTP 429")

        def working(query, count):
            calls.append("b")
            return [{"url": f"https://x/{index}.jpg", "width": 900} for index in range(count)]

        with mock.patch.object(photo_source, "image_sources", return_value=[("a", failing), ("b", working)]):
            found = photo_source.search_photos("pumpkin pie", count=3)
            self.assertEqual(len(found), 3)
            photo_source.search_photos("pecan pie", count=3)
        self.assertEqual(calls, ["a", "b", "b"])  # the failed source rests instead of being asked again

    def test_sources_fill_up_until_enough(self):
        first = lambda query, count: [{"url": "https://x/1.jpg", "width": 900}]
        second = lambda query, count: [{"url": "https://x/1.jpg", "width": 900}, {"url": "https://x/2.jpg", "width": 900}]
        with mock.patch.object(photo_source, "image_sources", return_value=[("a", first), ("b", second)]):
            self.assertEqual([item["url"] for item in photo_source.search_photos("pie", count=5)],
                             ["https://x/1.jpg", "https://x/2.jpg"])


if __name__ == "__main__":
    unittest.main()
