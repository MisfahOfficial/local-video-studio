from __future__ import annotations

import unittest

from app.footage_match import (
    AI_LIKE_TITLE, NON_FOOTAGE_TITLE, auto_topic, looks_like_ai_slideshow, best_window, core_subject, detect_era, detect_topic, heading_subject,
    mentions_topic, scene_keywords, scene_subjects, topic_queries,
)

MUSK = (
    "Musk ox calves are born in the harshest place on Earth. Within hours, a musk ox calf must stand. "
    "The impact of a single blizzard can be deadly. When wolves appear, the adults circle the calves. "
    "Musk oxen have survived since the Ice Age."
)


class FootageMatchTests(unittest.TestCase):
    def test_topic_prefers_two_word_subject(self) -> None:
        self.assertEqual(detect_topic(MUSK), "musk ox")
        self.assertEqual(detect_topic(MUSK, "Musk Ox Calves"), "musk ox")

    def test_topic_spellings_and_lookalikes(self) -> None:
        self.assertTrue(mentions_topic("Newborn Muskox's First Day | PBS", "musk ox"))
        self.assertTrue(mentions_topic("Musk Oxen in Greenland", "musk ox"))
        self.assertFalse(mentions_topic("Baby buffalo calves playing", "musk ox"))
        self.assertTrue(mentions_topic("anything at all", ""))

    def test_queries_always_name_topic_and_drop_figurative_words(self) -> None:
        queries = topic_queries({"narration": "The impact of a single blizzard can be deadly."}, "musk ox")
        self.assertTrue(all(query.startswith("musk ox") for query in queries))
        self.assertFalse(any("impact" in query for query in queries))
        self.assertIn("blizzard", queries[0])

    def test_best_window_averages_over_clip_length(self) -> None:
        frames = [(0.0, 0.9), (2.0, 0.1), (4.0, 0.1), (10.0, 0.8), (12.0, 0.8), (14.0, 0.8)]
        start, score = best_window(frames, 5.0)
        self.assertEqual(start, 10.0)
        self.assertAlmostEqual(score, 0.8)


LIST_SCRIPT = (
    "How did one dollar fill a Christmas table in 1955? We're bringing back forgotten dollar desserts.\n"
    "POOR MAN'S COOKIES\nPoor Man's Cookies were a Depression-era staple. The dough came together without eggs.\n"
    "2. Vinegar Pie\nVinegar pie used pantry staples."
)


class ListVideoTests(unittest.TestCase):
    def test_headings_define_sections(self) -> None:
        self.assertEqual(heading_subject("POOR MAN'S COOKIES"), "poor man's cookies")
        self.assertEqual(heading_subject("2. Vinegar Pie"), "vinegar pie")
        self.assertEqual(heading_subject("The dough came together without eggs."), "")
        scenes = [{"narration": text} for text in (
            "How did one dollar fill a Christmas table in 1955?", "POOR MAN'S COOKIES",
            "The dough came together without eggs.", "2. Vinegar Pie", "Vinegar pie used pantry staples.",
        )]
        self.assertEqual(scene_subjects(scenes, ""), ["", "poor man's cookies", "poor man's cookies", "vinegar pie", "vinegar pie"])
        self.assertEqual(scene_subjects(scenes, "musk ox"), ["musk ox"] * 5)

    def test_list_videos_get_no_single_topic(self) -> None:
        self.assertEqual(auto_topic(LIST_SCRIPT, "testing 6"), "")
        self.assertEqual(auto_topic(MUSK), "musk ox")

    def test_possessives_and_core_subject(self) -> None:
        self.assertEqual(core_subject("poor man's cookies"), "cookies")
        self.assertNotIn("man'", " ".join(scene_keywords("The poor man's cookies at Christmas", "")))
        self.assertIn("christmas", scene_keywords("The poor man's cookies at Christmas", ""))
        self.assertTrue(mentions_topic("Chewy Oatmeal Cookies Recipe", "cookies"))
        self.assertFalse(mentions_topic("POOR MAN'S LASAGNA MELTDOWN!", "cookies"))

    def test_era_and_intro_queries(self) -> None:
        self.assertEqual(detect_era(LIST_SCRIPT), "1950s")
        query = topic_queries({"narration": "If this reminds you of your grandma's kitchen, subscribe."}, "", "1950s")[0]
        self.assertTrue(query.startswith("1950s grandma kitchen"))

    def test_non_footage_titles(self) -> None:
        self.assertTrue(NON_FOOTAGE_TITLE.search("Eraserheads - Poorman's Grave [Lyric Video]"))
        self.assertTrue(NON_FOOTAGE_TITLE.search("Poor Man's Cookies Soft Spoken ASMR"))
        self.assertIsNone(NON_FOOTAGE_TITLE.search("Old Fashioned Hermit Cookies"))
        self.assertTrue(NON_FOOTAGE_TITLE.search("Roblox Steal Cookies From Grandma"))

    def test_ai_channel_titles(self) -> None:
        self.assertTrue(AI_LIKE_TITLE.search("Cozy 1950s Holiday Kitchen Ambience | Retro Christmas"))
        self.assertTrue(AI_LIKE_TITLE.search("Vintage Jazz Playing Softly in Grandma's Cottage Kitchen"))
        self.assertTrue(AI_LIKE_TITLE.search("1950s America - AI generated"))
        self.assertIsNone(AI_LIKE_TITLE.search("Poor Man's Cookies From The 1930's"))
        self.assertIsNone(AI_LIKE_TITLE.search("Chair repair and painting"))

    def test_nostalgia_slideshow_sources_are_skipped(self) -> None:
        ai = [
            {"title": "FORGOTTEN Objects in EVERY 1950s Kitchen - Life in America", "channel": "Recollection Road"},
            {"title": "20 Forgotten Poor Man Desserts 1970s Grandmas Made", "channel": "Someone"},
            {"title": "Anything", "channel": "Vintage Life of USA"},
            {"title": "Cookie recipe", "channel": "Kitchen", "description": "Narrated with a synthetic (AI) voice."},
        ]
        real = [
            {"title": "Supermarket Shoppers, 1970s - Film 61577", "channel": "Kinolibrary"},
            {"title": "We Ate 1950's Recipes For A Week", "channel": "BuzzFeedVideo"},
            {"title": "POOR MANS OATMEAL COOKIES", "channel": "Alabama Prepper", "duration_seconds": 868},
        ]
        blocked = "Vintage Life of USA, Forgotten Flavors of USA"
        self.assertTrue(all(looks_like_ai_slideshow(item, blocked) for item in ai))
        self.assertFalse(any(looks_like_ai_slideshow(item, blocked) for item in real))


if __name__ == "__main__":
    unittest.main()
