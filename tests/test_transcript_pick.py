from __future__ import annotations

import unittest
from unittest import mock


class TranscriptPickTest(unittest.TestCase):
    def test_caption_events_become_plain_words(self):
        from app.transcript_pick import transcript_text

        events = [{"segs": [{"utf8": "Today we make"}, {"utf8": " Heinz soup\n"}]}, {}, {"segs": [{"utf8": "at home"}]}]
        self.assertEqual(transcript_text(events), "Today we make Heinz soup at home")
        self.assertEqual(len(transcript_text(events, limit=10)), 10)

    def test_videos_about_the_item_come_first_and_unknown_ids_are_ignored(self):
        from app.transcript_pick import rank_videos

        answer = {"videos": [{"id": "b", "about_item": False, "score": 2, "reason": "tomato soup"},
                             {"id": "a", "about_item": True, "score": 6, "reason": "heinz advert"},
                             {"id": "c", "about_item": True, "score": 9, "reason": "tasting the tin"},
                             {"id": "zz", "about_item": True, "score": 10, "reason": "not offered"}]}
        videos = [{"id": key, "title": key, "transcript": ""} for key in "abc"]
        with mock.patch("app.llm.gemini_json", return_value=answer) as ask:
            ranked = rank_videos(object(), "heinz golden vegetable soup", videos, "1970s")
        self.assertEqual([entry[0] for entry in ranked], ["c", "a", "b"])
        self.assertIn("heinz golden vegetable soup", ask.call_args[0][1])


if __name__ == "__main__":
    unittest.main()


class GoogleImagesTest(unittest.TestCase):
    def test_google_results_come_first_when_configured(self):
        from app import photo_source

        answer = {"items": [{"link": "https://x/soup.jpg", "title": "Batchelors Cup-a-Soup 1978",
                             "displayLink": "ebay.co.uk", "image": {"width": 900, "height": 700,
                                                                    "thumbnailLink": "https://t/s.jpg", "contextLink": "https://ebay"}},
                            {"link": "https://x/tiny.jpg", "image": {"width": 120}}]}
        photo_source.configure_google("key", "cx")
        try:
            with mock.patch.object(photo_source, "request_json", return_value=answer), \
                 mock.patch.object(photo_source, "_openverse", return_value=[]), \
                 mock.patch.object(photo_source, "_archive_images", return_value=[]):
                found = photo_source.search_photos("batchelors cup-a-soup", count=5)
            self.assertEqual([item["url"] for item in found], ["https://x/soup.jpg"])  # the tiny one is skipped
            self.assertIn("Google Images", photo_source.attribution(found[0]))
        finally:
            photo_source.configure_google("", "")
        self.assertEqual(photo_source.image_sources()[0][0], "openverse")  # without a key nothing changes


class SerperTest(unittest.TestCase):
    def test_serper_results_are_real_photos_first(self):
        import io
        import json
        from app import photo_source

        answer = {"images": [{"title": "Heinz soup tin 1975", "imageUrl": "https://x/tin.jpg", "imageWidth": 800,
                              "imageHeight": 600, "domain": "worthpoint.com", "link": "https://worthpoint.com/x"},
                             {"title": "tiny", "imageUrl": "https://x/t.jpg", "imageWidth": 100}]}
        photo_source.configure_serper("key")
        try:
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(answer).encode()
            with mock.patch("urllib.request.urlopen", return_value=response), \
                 mock.patch.object(photo_source, "_openverse", return_value=[]), \
                 mock.patch.object(photo_source, "_archive_images", return_value=[]):
                found = photo_source.search_photos("heinz soup", count=5)
            self.assertEqual([item["url"] for item in found], ["https://x/tin.jpg"])
        finally:
            photo_source.configure_serper("")

    def test_pasted_embed_code_gives_the_engine_id(self):
        from app import photo_source

        photo_source.configure_google("k", '<script async src="https://cse.google.com/cse.js?cx=abc123:xyz"></script>')
        self.assertEqual(photo_source._google["cx"], "abc123:xyz")
        photo_source.configure_google("", "")


class ClaudeProviderTest(unittest.TestCase):
    def test_gemini_schema_becomes_json_schema_and_wrong_keys_are_ignored(self):
        from types import SimpleNamespace
        from app.llm import _claude_key, _json_schema

        converted = _json_schema({"type": "OBJECT", "properties": {"a": {"type": "ARRAY", "items": {"type": "STRING"}}},
                                  "required": ["a"]})
        self.assertEqual(converted["type"], "object")
        self.assertFalse(converted["additionalProperties"])
        self.assertEqual(converted["properties"]["a"]["items"]["type"], "string")
        self.assertEqual(_claude_key(SimpleNamespace(anthropic_api_key="AIzaSyWrongField")), "")
        self.assertEqual(_claude_key(SimpleNamespace(anthropic_api_key=" sk-ant-abc ")), "sk-ant-abc")


class ItemKindTest(unittest.TestCase):
    def test_ambiguous_names_get_the_videos_kind(self):
        from app.footage_match import item_kind, names_a_kind

        self.assertEqual(item_kind("V3-tea time biscuits"), "biscuits")
        self.assertEqual(item_kind("25 CHEAP British Vintage 10-Minute Soups"), "soups")
        self.assertFalse(names_a_kind("Royal Scot"))
        self.assertTrue(names_a_kind("Heinz Kidney Soup"))
        self.assertTrue(names_a_kind("Yo-Yo Biscuits"))


class BiscuitsReviewTest(unittest.TestCase):
    def test_text_cards_never_end_on_a_dangling_word(self):
        from app.text_card import card_phrase

        self.assertEqual(card_phrase("The texture was so delicate the biscuits crumbled if"),
                         "The texture was so delicate the biscuits crumbled")
        self.assertEqual(card_phrase("Then McVities reduced the butter content and changed the"),
                         "Then McVities reduced the butter content and changed")

    def test_a_map_only_when_places_matter(self):
        from app.graphic_moments import moment_for

        self.assertIsNone((moment_for("Scottish families bought Royal Scot in Scotland every week.") or {}).get("type") == "map" or None)
        found = moment_for("It spread from Scotland to Wales within a year.")
        self.assertEqual((found or {}).get("type"), "map")

    def test_ingredient_cards_only_for_how_it_is_made(self):
        from app.youtube_auto import RECIPE_VERB

        self.assertIsNone(RECIPE_VERB.search("crunchier oat biscuits with more butter and less sugar"))
        self.assertIsNotNone(RECIPE_VERB.search("She would mix flour, butter and sugar"))
