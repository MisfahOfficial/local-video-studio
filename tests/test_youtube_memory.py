from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class YouTubeMemoryTest(unittest.TestCase):
    def setUp(self):
        from app import youtube_memory

        self.folder = Path(tempfile.mkdtemp())
        youtube_memory.configure(self.folder)
        self.memory = youtube_memory

    def tearDown(self):
        self.memory._folder.clear()
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_a_search_and_a_page_read_are_asked_once(self):
        from app.youtube_source import YouTubeSourceService

        service = YouTubeSourceService("", "ffmpeg", "fair_use")
        with mock.patch.object(service, "_search_live", return_value=[{"video_id": "abcdefghijk"}]) as live:
            first = service.search("heinz kidney soup", 10)
            second = service.search("  Heinz  kidney soup ", 10)
        self.assertEqual(first, second)
        self.assertEqual(live.call_count, 1)
        with mock.patch.object(service, "_inspect_live", return_value={"id": "abcdefghijk", "duration": 61}) as live:
            service.inspect("abcdefghijk")
            info = service.inspect("abcdefghijk")
        self.assertEqual((live.call_count, info["duration"]), (1, 61))

    def test_empty_results_and_old_details_are_asked_again(self):
        self.memory.remember_search("x", 10, [])
        self.assertIsNone(self.memory.cached_search("x", 10))
        self.memory.remember_info("vid", {"id": "vid"})
        with mock.patch("time.time", return_value=10 ** 12):
            self.assertIsNone(self.memory.cached_info("vid"))


if __name__ == "__main__":
    unittest.main()
