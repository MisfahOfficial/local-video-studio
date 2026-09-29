import tempfile
import unittest
from pathlib import Path

from app.content_profile import BUILTIN, clean_references, missing_reference_message, normalize_profile, profile_for
from app.database import Database
from app.youtube_auto import _CLASSIC_TITLE


class VintageProfileKeepsOldBehaviour(unittest.TestCase):
    def test_queries_are_the_ones_the_v_channels_were_tuned_on(self):
        vintage = BUILTIN["vintage_recipe"]
        dish, era, theme, subject = "oatmeal cookies", "1950s", "church potluck casseroles", "church potluck"
        self.assertEqual(vintage.queries(vintage.section_queries, item=dish, era=era),
                         [f"{dish} recipe", f"old fashioned {dish}", f"{era} {dish}", dish])
        self.assertEqual(vintage.queries(vintage.hook_queries, item=subject, era=era, theme=theme),
                         [f"{era} {theme} footage", f"vintage {subject} home movie", f"{era} {subject}",
                          f"{subject} {era} film"])

    def test_title_bonus_matches_the_old_rule(self):
        vintage = BUILTIN["vintage_recipe"]
        for title in ("Grandma's Old-Fashioned Pound Cake", "1950s Jello Salad", "Classics of the diner",
                      "Quick 5 minute hack", "Retro church potluck", "Homemade from scratch bread", "1980s cake",
                      "grandmas pie", "The Original Recipe", "classical music"):
            self.assertEqual(vintage.good_title(title), bool(_CLASSIC_TITLE.search(title)), title)

    def test_vintage_allows_every_title_the_old_filters_allowed(self):
        self.assertTrue(BUILTIN["vintage_recipe"].title_allowed("Garden vegetable soup 1950s"))


class ModernBrandProfile(unittest.TestCase):
    def test_brand_homonyms_are_rejected(self):
        brand = BUILTIN["modern_brand"]
        self.assertFalse(brand.title_allowed("Kludi Red Gold Thermostatic Shower Faucet"))
        self.assertFalse(brand.title_allowed("Rick Takes His Biggest Gamble On Monster Red | Gold Rush"))
        self.assertFalse(brand.title_allowed("How to Make an Eggshell & Vinegar Fertilizer - Recipe & Use"))
        self.assertFalse(brand.title_allowed("Exploring FRISCO | SAN FRANCISCO DEL MONTE Quezon City Walking Tour"))
        self.assertTrue(brand.title_allowed("The Red Gold Fresh Pack Factory Tour"))
        self.assertTrue(brand.title_allowed("Del Monte Commercial 1984"))
        self.assertFalse(brand.period)


class References(unittest.TestCase):
    def test_links_are_cleaned_and_limited(self):
        links = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "https://youtu.be/dQw4w9WgXcQ", "not a link",
                 "https://www.youtube.com/shorts/abcdefghijk"] + [f"https://youtu.be/{i:011d}" for i in range(6)]
        cleaned = clean_references(links)
        self.assertEqual(cleaned[0], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(cleaned[1], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(len(cleaned), 5)

    def test_at_least_one_example_is_required(self):
        self.assertTrue(missing_reference_message({"content_profile": {"kind": "modern_brand"}}))
        project = {"content_profile": {"kind": "modern_brand", "references": ["https://youtu.be/dQw4w9WgXcQ"]}}
        self.assertEqual(missing_reference_message(project), "")
        self.assertEqual(profile_for(project).kind, "modern_brand")

    def test_unknown_kind_falls_back_to_vintage(self):
        self.assertEqual(normalize_profile({"kind": "nonsense"})["kind"], "vintage_recipe")

    def test_profile_is_saved_with_the_project(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "studio.sqlite3")
            project = db.create_project("C1 test", "history")
            saved = db.update_project(project["id"], content_profile=normalize_profile(
                {"kind": "modern_brand", "references": ["https://youtu.be/dQw4w9WgXcQ"], "blocked_words": ["Faucet"]}))
            self.assertEqual(saved["content_profile"]["kind"], "modern_brand")
            self.assertEqual(profile_for(saved).blocked_words, ("faucet",))


if __name__ == "__main__":
    unittest.main()
