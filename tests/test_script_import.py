from __future__ import annotations

import unittest


SCRIPT = """ABBEY CRUNCH
Crunchy oat biscuits.

GYPSY CREAMS
Dark chocolate sandwich biscuits.

ROYAL SCOT
Pure butter shortbread fingers.
"""


class ScriptImportTest(unittest.TestCase):
    def test_one_text_splits_at_the_references_heading(self):
        from app.script_import import split_script

        script, refs = split_script([SCRIPT + "\nREFERENCES\nAbbey Crunch | https://x.com/a.jpg\n"])
        self.assertTrue(script.startswith("ABBEY CRUNCH"))
        self.assertIn("https://x.com/a.jpg", refs)

    def test_tabs_first_is_script_rest_with_links_are_references(self):
        from app.script_import import split_script

        script, refs = split_script([SCRIPT, "Notes without links", "Gypsy Creams: https://x.com/g.png"])
        self.assertEqual(script, SCRIPT.strip())
        self.assertNotIn("Notes", refs)

    def test_names_are_matched_to_headings_and_problems_reported(self):
        from app.script_import import match_items, parse_references

        entries = parse_references("Item | Reference links\nAbbey Crunch | https://x/a.jpg, https://x/b.jpg\n"
                                   "gypsy creams\nhttps://x/g.jpg\nCustard Creams | https://x/c.jpg\n")
        matched, missing, unmatched = match_items(SCRIPT, entries)
        self.assertEqual(matched["abbey crunch"], ["https://x/a.jpg", "https://x/b.jpg"])
        self.assertEqual(matched["gypsy creams"], ["https://x/g.jpg"])
        self.assertEqual(missing, ["royal scot"])
        self.assertEqual(unmatched, ["Custard Creams"])

    def test_drive_share_links_become_downloads(self):
        from app.script_import import doc_id, image_url

        self.assertEqual(image_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx/view?usp=sharing"),
                         "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOpQrStUvWx")
        self.assertEqual(doc_id("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit?tab=t.0"),
                         "1AbCdEfGhIjKlMnOpQrStUvWxYz")

    def test_doc_tables_give_names_and_hidden_links(self):
        from app.script_import import html_lines, image_search, match_items, parse_references

        row = ('<table><tr><td><p><span>1</span></p></td><td><p><span>Space Dust / Cosmic Candy</span></p>'
               '<p><span>1978</span></p></td><td><p><a href="https://www.google.com/url?q=https://site.com/post/space'
               '&amp;sa=D">Article</a></p></td><td><p><a href="https://www.google.com/url?q=https://www.google.com/'
               'search?tbm%3Disch%26q%3Dspace%2Bdust%2Bcandy&amp;sa=D">pictures</a></p></td></tr></table>')
        lines = html_lines(row)
        self.assertTrue(lines.startswith("Space Dust / Cosmic Candy | https://site.com/post/space"))
        entries = parse_references(lines)
        self.assertEqual(image_search(entries[0][1][-1]), "space dust candy")
        matched, _missing, unmatched = match_items("SPACE DUST\nIt fizzed.\nSUPER SKRUNCH (WONKA)\nCrispy.\nOUTRO\nBye.",
                                                   entries + [("Super Skrunch Bar", ["https://x/s.jpg"])])
        self.assertEqual(set(matched), {"space dust", "super skrunch (wonka)"})
        self.assertEqual(unmatched, [])


if __name__ == "__main__":
    unittest.main()
