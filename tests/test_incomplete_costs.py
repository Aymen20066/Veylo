"""Incomplete costs: a listing whose acquisition cost could not be fully calculated must
rank after every complete one, and a missing amount must never be displayed as 'nan'.

Run with: python -m pytest
"""
import re
import unittest

from streamlit.testing.v1 import AppTest

from veylo import transform
from veylo.config import CARDS_SHOWN

from test_app_smoke import APP_PATH, SEARCH_PARAMS, make_listing, make_results_df

UNCERTAIN_AGE = {
    "status": "unknown", "applied": False, "vat_rate": 0.23, "vat_eur": 15603.0,
    "reason": "First registered roughly 6 months ago.", "note": None,
}


def mixed_results():
    """Three complete listings and two cheap incomplete ones (no engine size, so Poland
    excise can't be calculated). The incomplete ones have the lowest price + transport."""
    listings = [make_listing(i, price=20000 + i * 1000) for i in range(3)]
    listings += [make_listing(10, price=9000, engineDisplacementCcm=None),
                 make_listing(11, price=8000, engineDisplacementCcm=None)]
    return make_results_df(listings)


class RankingTests(unittest.TestCase):
    def test_complete_costs_rank_above_incomplete_ones(self):
        df = mixed_results()
        self.assertEqual(int(df["total_cost"].isna().sum()), 2)
        ranked = df.assign(_k=transform.acquisition_cost_sort_key(df)).sort_values("_k")
        complete_flags = ranked["total_cost"].notna().tolist()
        self.assertEqual(complete_flags, [True, True, True, False, False])
        self.assertTrue(ranked["total_cost"].iloc[:3].is_monotonic_increasing)
        self.assertEqual(ranked["price"].iloc[3:].tolist(), [8000, 9000])

    def test_same_order_in_cards_and_table(self):
        df = mixed_results()
        key = transform.acquisition_cost_sort_key(df)
        cards, remaining = transform.split_cards_with_damage_filter(df, key, 2, True)
        self.assertTrue(cards["total_cost"].notna().all())
        table = remaining.assign(
            _k=transform.acquisition_cost_sort_key(remaining)).sort_values("_k")
        self.assertEqual(table["total_cost"].notna().tolist(), [True, False, False])

    def test_summary_lowest_acquisition_cost_uses_complete_costs_only(self):
        df = mixed_results()
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        at.session_state["results_df"] = df
        at.session_state["search_done"] = True
        at.session_state["search_params"] = dict(SEARCH_PARAMS)
        at.run()
        self.assertFalse(at.exception)
        metric = [m for m in at.metric if m.label == "Lowest Acquisition Cost"][0]
        self.assertEqual(metric.value, f"€{df['total_cost'].min():,.0f}")
        # First card on the page is a complete listing, not the cheap incomplete one.
        cards = [m.value for m in at.markdown if 'class="listing-card"' in m.value]
        self.assertEqual(len(cards), min(len(df), CARDS_SHOWN))
        self.assertIn("Est. Acquisition Cost", cards[0])
        self.assertIn("Price + Transport Only", cards[-1])


class NeverDisplayNanTests(unittest.TestCase):
    def test_format_eur(self):
        self.assertEqual(transform.format_eur(12345.4), "€12,345")
        self.assertEqual(transform.format_eur(0), "€0")
        self.assertEqual(transform.format_eur(None), "Not calculated")
        self.assertEqual(transform.format_eur(float("nan")), "Not calculated")

    def test_uncertain_age_card_without_tax_shows_reason_not_nan(self):
        df = make_results_df([make_listing(0, engineDisplacementCcm=None)])
        row = df.iloc[0].copy()
        row["new_vehicle"] = dict(UNCERTAIN_AGE)
        html = transform.build_listing_card_html(row, SEARCH_PARAMS)
        self.assertNotIn("nan", html.lower())
        reason = transform.escape_html(row["import_unavailable_reason"])
        self.assertEqual(html.count(f"Not calculated — {reason}"), 2)
        self.assertIn("Includes €15,603 Poland VAT", html)
        self.assertNotIn("Ranked using", html)

    def test_uncertain_age_card_with_tax_shows_both_totals(self):
        df = make_results_df([make_listing(0)])
        row = df.iloc[0].copy()
        row["new_vehicle"] = dict(UNCERTAIN_AGE)
        html = transform.build_listing_card_html(row, SEARCH_PARAMS)
        self.assertNotIn("nan", html.lower())
        self.assertIn(transform.format_eur(row["total_cost"]), html)
        self.assertIn(transform.format_eur(row["total_cost"] + 15603.0), html)
        self.assertIn('Ranked using the "used" figure', html)

    def test_no_rendered_card_contains_an_unfilled_placeholder(self):
        # A "{word}" in the output means a string lost its f-prefix or format call.
        placeholder = re.compile(r"\{\w+\}")
        complete = make_results_df([make_listing(0)]).iloc[0]
        incomplete = make_results_df([make_listing(0, engineDisplacementCcm=None)]).iloc[0]
        new_vehicle = dict(UNCERTAIN_AGE, status="new", applied=True)
        for base in (complete, incomplete):
            for nv in (None, UNCERTAIN_AGE, new_vehicle):
                row = base.copy()
                if nv is not None:
                    row["new_vehicle"] = dict(nv)
                for flagged in (False, True):
                    row["is_flagged"] = flagged
                    row["damage_category"] = "DAMAGE" if flagged else "CLEAN"
                    html = transform.build_listing_card_html(row, SEARCH_PARAMS)
                    with self.subTest(calculable=bool(row["import_calculable"]),
                                      new_vehicle=(nv or {}).get("status"), flagged=flagged):
                        self.assertIsNone(placeholder.search(html))

    def test_no_card_or_page_text_contains_nan(self):
        df = mixed_results()
        for _, row in df.iterrows():
            self.assertNotIn("€nan", transform.build_listing_card_html(row, SEARCH_PARAMS))
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        at.session_state["results_df"] = df
        at.session_state["search_done"] = True
        at.session_state["search_params"] = dict(SEARCH_PARAMS)
        at.run()
        text = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption]
                         + [str(m.value) for m in at.metric])
        self.assertNotIn("€nan", text)


if __name__ == "__main__":
    unittest.main()
