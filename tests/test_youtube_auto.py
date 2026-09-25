from __future__ import annotations

import unittest

from app.youtube_auto import candidate_relevance, scene_search_query


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


if __name__ == "__main__":
    unittest.main()
