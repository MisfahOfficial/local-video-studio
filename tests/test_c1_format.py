"""C1 (restaurant/brand list) scripts: the rules that left a 277-scene video with 4 real clips."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.content_profile import BUILTIN
from app.footage_match import core_subject, heading_subject
from app.providers.base import ProviderError


class ListHeadingTest(unittest.TestCase):
    def test_numbering_is_not_part_of_the_subject(self):
        cases = {
            "Number Twelve — Red Robin": "red robin", "Number Twenty-One — Sbarro": "sbarro", "No. 3: IHOP": "ihop",
            "#12 Red Robin": "red robin", "12 — Red Lobster": "red lobster", "Part Two - Hooters": "hooters",
            "2. Vinegar Pie": "vinegar pie", "CUSTARD SLICES": "custard slices",
        }
        for heading, subject in cases.items():
            self.assertEqual(heading_subject(heading), subject, heading)

    def test_spoken_sentences_stay_sentences(self):
        for sentence in ("IHOP.", "One survival plan.", "Twenty three closed in 2025.", "Ten years later, it closed."):
            self.assertEqual(heading_subject(sentence), "", sentence)

    def test_brand_possessive_keeps_the_brand(self):
        self.assertEqual(core_subject("peet's coffee"), "peet's coffee")
        self.assertEqual(core_subject("annie's organic"), "annie's organic")
        self.assertEqual(core_subject("poor man's cookies"), "cookies")
        self.assertEqual(core_subject("grandma's pie"), "pie")


class BrandTitleTest(unittest.TestCase):
    def test_restaurant_titles_pass_the_brand_profile(self):
        profile = BUILTIN["modern_brand"]
        self.assertTrue(profile.title_allowed("Why Red Robin Is Closing Restaurants"))
        self.assertTrue(profile.title_allowed("IHOP Pancakes Commercial"))
        self.assertFalse(profile.title_allowed("Red Gold shower faucet install"))


class ChannelChoiceTest(unittest.TestCase):
    def test_example_made_channel_is_kept(self):
        from app import server

        with patch.object(server, "_kit_keys", return_value={"c-c1-inside-the-era"}):
            self.assertEqual(server.normalize_effects({"channel_style": "c-c1-inside-the-era"})["channel_style"],
                             "c-c1-inside-the-era")
            self.assertEqual(server.normalize_effects({"channel_style": "nonsense"})["channel_style"], "v3")


class RunwareQueueTest(unittest.TestCase):
    def test_busy_account_is_retried(self):
        from app import vintage_still

        calls = []

        def make():
            calls.append(1)
            if len(calls) < 3:
                raise ProviderError('HTTP 400: "concurrentRequestLimitExceeded"')
            return "image"

        with patch.object(vintage_still.time, "sleep"):
            self.assertEqual(vintage_still._runware_one_at_a_time(make), "image")
        self.assertEqual(len(calls), 3)

    def test_other_errors_are_not_retried(self):
        from app import vintage_still

        with self.assertRaises(ProviderError):
            vintage_still._runware_one_at_a_time(lambda: (_ for _ in ()).throw(ProviderError("bad key")))


if __name__ == "__main__":
    unittest.main()
