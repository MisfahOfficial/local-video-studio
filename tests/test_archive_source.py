import unittest
from pathlib import Path
from unittest import mock

from app.archive_source import MultiSourceService, _is_footage, drive_id, search_drive
from app.footage_match import topic_queries, vague_heading
from app.providers.base import ProviderError


class DriveFiles(unittest.TestCase):
    def test_only_source_footage_is_indexed(self):
        self.assertTrue(_is_footage(Path("Vintage video - Black American Neighborhood in 1950's Los Angeles.mp4")))
        self.assertTrue(_is_footage(Path("Easy No Bake Woolworth Icebox Cheesecake.mp4")))
        for name in ("taha x2 -V2--30 Genius Big Mama Soul Kitchen Tricks.mp3_x264.mp4", "Outro.mp4",
                     "like-and-subscribe-05-SBV-347596325-HD.mov", "Enhance Your Videos with 4K Film Burn Transition.mp4",
                     "WhatsApp Video 2026-05-17 at 5.17.35 AM.mp4", "notes.txt",
                     "grok-video-0d293979-1044-4911-b879-dbd93.mp4", "kling_20260411_pie.mp4"):
            self.assertFalse(_is_footage(Path(name)), name)

    def test_search_matches_file_names(self):
        files = [{"path": "/d/a.mp4", "name": "1950s USA Family Meal, Dinner", "folder": "x"},
                 {"path": "/d/b.mp4", "name": "Chocolate cake recipe", "folder": "x"}]
        found = search_drive(files, "1950s family dinner")
        self.assertEqual([item["title"] for item in found], ["1950s USA Family Meal, Dinner"])
        self.assertEqual(found[0]["video_id"], drive_id("/d/a.mp4"))  # stable across runs


    def test_copies_of_one_download_count_once(self):
        files = [{"path": "/a/Divinity Candy.mp4", "name": "Old Fashioned Divinity Candy", "folder": "V1"},
                 {"path": "/b/Divinity Candy.mp4", "name": "Old Fashioned Divinity Candy", "folder": "V2"}]
        self.assertEqual(len(search_drive(files, "divinity candy")), 1)


class BlockedYouTube(unittest.TestCase):
    def test_one_bot_check_stops_every_later_youtube_request(self):
        service = MultiSourceService("", "ffmpeg", "fair_use")
        blocked = ProviderError("YouTube is temporarily blocking this computer (\"confirm you're not a bot\").")
        with mock.patch("app.youtube_source.YouTubeSourceService.inspect", side_effect=blocked) as inspect:
            with self.assertRaises(ProviderError):
                service.inspect("dQw4w9WgXcQ")
            with self.assertRaises(ProviderError):
                service.inspect("abcdefghijk")
        self.assertTrue(service.youtube_blocked)
        self.assertEqual(inspect.call_count, 1)
        with mock.patch("app.archive_source.search_archive", return_value=[]), \
                mock.patch("app.youtube_source.YouTubeSourceService.search") as youtube_search:
            service.search("pumpkin roll")
        youtube_search.assert_not_called()


class DishNames(unittest.TestCase):
    def test_named_dishes_are_searched_by_name(self):
        self.assertFalse(vague_heading("magic cookie bars"))
        self.assertTrue(vague_heading("poor man's cookies"))
        queries = topic_queries({"narration": "Layered bars made in a 9x13 pan"}, "magic cookie bars", "1970s",
                                recipe="condensed graham bars")
        self.assertTrue(queries[0].startswith("magic cookie bars"))


if __name__ == "__main__":
    unittest.main()
