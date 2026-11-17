"""Smoke tests for the Streamlit interface, run headlessly with Streamlit's AppTest:
the page must render with no exception both before a search and with results on screen.
No network call is made — results are built locally and placed in session state."""
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from veylo import transform

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def make_listing(i, **overrides):
    listing = {
        "title": f"BMW 320d Touring #{i}",
        "price": 15000 + i * 500,
        "mileageKm": 60000 + i * 1000,
        "firstRegistration": "2019-03-01",
        "url": f"https://www.autoscout24.com/offers/test-{i}",
        "country": ["DE", "BE", "FR", "NL", "IT"][i % 5],
        "city": "Teststadt",
        "transmission": "Automatic",
        "fuelType": "Diesel",
        "bodyType": "Station Wagon",
        "engineDisplacementCcm": 1995,
        "emissionClass": "Euro 6d",
        "vatDeductible": True,
        "sellerType": "Dealer",
        "priceEvaluation": "good-price",
    }
    listing.update(overrides)
    return listing


def make_results_df(listings, destination="Poland"):
    df, missing = transform.clean_and_filter_listings(
        listings, (1000, 300000), (0, 300000), (2000, 2100), None)
    assert not missing
    df, _ = transform.deduplicate_listings(df)
    df["damage_category"] = "CLEAN"
    df["is_flagged"] = False
    distances, _ = transform.get_distances(destination, "")
    return transform.add_cost_columns(df, destination, distances, 0.40, {})


SEARCH_PARAMS = {
    "make": "BMW", "model": "320d", "model_clean": "320d", "body_type": "Any body type",
    "generation_suffix": None, "price_range": (1000, 300000), "mileage_range": (0, 300000),
    "year_range": (2000, 2100), "importer_country": "Poland", "city": "",
    "cost_per_km": 0.40, "matched_city": None, "shipping_overrides": {},
    "exclude_damaged": True, "truncated_bands": [], "duplicates_merged": 0,
}


class AppSmokeTests(unittest.TestCase):
    def test_page_renders_before_any_search(self):
        at = AppTest.from_file(APP_PATH, default_timeout=60).run()
        self.assertFalse(at.exception)

    def test_page_renders_results_cards_table_and_market_comparison(self):
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        at.session_state["results_df"] = make_results_df([make_listing(i) for i in range(30)])
        at.session_state["search_done"] = True
        at.session_state["search_params"] = dict(SEARCH_PARAMS)
        at.run()
        self.assertFalse(at.exception)
        page_text = "\n".join(m.value for m in at.markdown)
        self.assertIn("BMW 320d Touring #0", page_text)
        self.assertEqual(len(at.dataframe), 2)   # other-results table + market comparison


if __name__ == "__main__":
    unittest.main()
