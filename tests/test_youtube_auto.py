from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.providers.base import ProviderError
from app.youtube_auto import candidate_relevance, gemini_footage_queries, scene_search_queries, scene_search_query


class AutoYouTubeTests(unittest.TestCase):
    def test_query_keeps_named_places_and_uses_archival_footage_for_history(self) -> None:
        query = scene_search_query({
            "visual_subject": "British warships crossing the Dardanelles toward Istanbul during the Ottoman campaign, cinematic wide shot",
            "narration": "Across the Dardanelles, the Allied fleet aimed for Istanbul.",
        })
        self.assertIn("Dardanelles", query)
        self.assertIn("Istanbul", query)
        self.assertIn("archival footage", query)
        self.assertNotIn("cinematic", query.lower())

    def test_relevance_prefers_candidate_that_mentions_scene_entities(self) -> None:
        scene = {
            "visual_subject": "Allied warships cross the Dardanelles toward Istanbul",
            "narration": "The fleet tried to reach Istanbul through the Dardanelles.",
            "start_seconds": 0,
            "end_seconds": 5,
        }
        relevant = {
            "title": "Dardanelles Campaign: Allied warships toward Istanbul",
            "description": "Historic naval footage", "channel": "Archive", "duration_seconds": 120,
        }
        generic = {
            "title": "Beautiful ocean waves", "description": "Relaxing sea", "channel": "Nature", "duration_seconds": 120,
        }
        self.assertGreater(candidate_relevance(relevant, scene), candidate_relevance(generic, scene))


    def test_sentence_narration_becomes_short_keyword_queries(self) -> None:
        queries = scene_search_queries({
            "visual_subject": "",
            "narration": "At home, mom served a frozen TV dinner in front of the television.",
        })
        first, fallback = queries
        self.assertLessEqual(len(first.split()), 8)
        self.assertIn("TV", first)
        for word in ("the", "front", "At"):
            self.assertNotIn(f" {word} ", f" {first} ")
        self.assertLessEqual(len(fallback.split()), 5)

    def test_fallback_query_keeps_decade_and_names_first(self) -> None:
        queries = scene_search_queries({
            "narration": "In the 1970s, families pushed shopping carts through bright Kroger supermarket aisles.",
        })
        self.assertTrue(queries[-1].startswith("1970s Kroger"))

    def test_talk_and_reaction_videos_rank_below_footage(self) -> None:
        scene = {"narration": "Teenagers met at the drive-in diner for burgers.", "start_seconds": 0, "end_seconds": 5}
        footage = {"title": "1960s drive-in diner burgers archive footage", "duration_seconds": 90}
        reaction = {"title": "Drive-in diner burgers reaction podcast", "duration_seconds": 90}
        self.assertGreater(candidate_relevance(footage, scene), candidate_relevance(reaction, scene))

    def test_gemini_queries_are_parsed_and_cleaned(self) -> None:
        reply = [{"position": 1, "queries": ['"1970s supermarket aisle"', "shoppers carts 1970s", "x", "a " * 20]}]
        response = {"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]}
        with patch("app.youtube_auto.post_json", return_value=response) as post:
            found = gemini_footage_queries([{"position": 1, "narration": "Shoppers in 1970s"}], "key", "gemini-test")
        self.assertEqual(found, {1: ["1970s supermarket aisle", "shoppers carts 1970s"]})
        self.assertIn("gemini-test", post.call_args.args[0])

    def test_gemini_queries_are_optional(self) -> None:
        self.assertEqual(gemini_footage_queries([{"position": 1}], "", "model"), {})
        problems: list[str] = []
        with patch("app.youtube_auto.post_json", side_effect=ProviderError("Provider returned HTTP 429: quota")):
            self.assertEqual(gemini_footage_queries([{"position": 1}], "key", "model", problems=problems), {})
        self.assertIn("quota", problems[0])


if __name__ == "__main__":
    unittest.main()
