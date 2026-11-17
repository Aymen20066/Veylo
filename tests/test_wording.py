"""Wording and naming consistency: retired names must not come back, and Romania's amount
is a first-year ownership tax, never an "import tax".

Run with: python -m pytest
"""
import re
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from veylo import costs, transform

from test_app_smoke import APP_PATH, SEARCH_PARAMS, make_listing, make_results_df

ROOT = Path(__file__).resolve().parent.parent
RETIRED_STRINGS = ["Car" + "Data", "Best " + "Deals", "Best " + "Opportunities", "use_container" + "_width"]


class RetiredStringsTests(unittest.TestCase):
    def test_retired_strings_do_not_appear_anywhere_in_the_code(self):
        files = [ROOT / "app.py", *sorted((ROOT / "veylo").glob("*.py")),
                 *sorted((ROOT / "tests").glob("*.py"))]
        for path in files:
            source = path.read_text(encoding="utf-8")
            for retired in RETIRED_STRINGS:
                with self.subTest(file=path.name, retired=retired):
                    self.assertNotIn(retired, source)


class RomaniaLabelTests(unittest.TestCase):
    def test_tax_label_is_first_year_ownership_tax(self):
        result = costs.calculate_import_costs("Romania", 15000, 1995, "Diesel", "Euro 6", 2019)
        self.assertEqual(result["tax_label"], "First-year ownership tax")
        self.assertEqual(costs.destination_tax_name("Romania"), "First-year ownership tax")
        self.assertEqual(costs.destination_tax_name("Poland"), "Import tax")

    def test_romania_card_never_says_import_tax(self):
        params = dict(SEARCH_PARAMS, importer_country="Romania")
        calculable = make_results_df([make_listing(0)], destination="Romania")
        not_calculable = make_results_df(
            [make_listing(0, engineDisplacementCcm=None)], destination="Romania")
        for df in (calculable, not_calculable):
            html = transform.build_listing_card_html(df.iloc[0], params)
            self.assertNotIn("import tax", html.lower())
            self.assertIn("First-year ownership tax", html)

    def test_romania_results_page_never_says_import_tax(self):
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        listings = [make_listing(i) for i in range(6)] + [make_listing(9, engineDisplacementCcm=None)]
        at.session_state["results_df"] = make_results_df(listings, destination="Romania")
        at.session_state["search_done"] = True
        at.session_state["search_params"] = dict(SEARCH_PARAMS, importer_country="Romania")
        at.run()
        self.assertFalse(at.exception)
        text = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption])
        self.assertNotIn("import tax", text.lower())
        self.assertIn("first-year ownership tax", text.lower())


class HeaderTests(unittest.TestCase):
    def test_one_name_and_subtitle(self):
        at = AppTest.from_file(APP_PATH, default_timeout=60).run()
        text = "\n".join(m.value for m in at.markdown)
        self.assertIn('class="veylo-logo"', text)   # the Veylo logo replaces the text title
        self.assertIn("Cross-border used-car sourcing: estimated total acquisition cost.", text)
        self.assertEqual([b.label for b in at.button], ["Search"])


class NoEmojiTests(unittest.TestCase):
    def test_interface_code_contains_no_emoji(self):
        emoji = re.compile("[ℹ⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF]")
        for path in [ROOT / "app.py", *sorted((ROOT / "veylo").glob("*.py"))]:
            with self.subTest(file=path.name):
                self.assertIsNone(emoji.search(path.read_text(encoding="utf-8")))


class ProgressWordingTests(unittest.TestCase):
    def test_search_progress_never_shows_price_bands_or_provider_names(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        progress_calls = re.findall(r"progress_bar\.progress\((.*?)\)\n", source, flags=re.S)
        progress_calls += re.findall(r"st\.progress\((.*?)\)\n", source, flags=re.S)
        self.assertTrue(progress_calls)
        for call in progress_calls:
            for internal in ("label", "band[", "€", "Apify", "run_id", "Gemini"):
                with self.subTest(call=call[:40], internal=internal):
                    self.assertNotIn(internal, call)


if __name__ == "__main__":
    unittest.main()
