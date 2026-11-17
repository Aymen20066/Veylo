"""Tests for country resolution.

Run with: python -m pytest
"""
import unittest

import pandas as pd

from veylo import transform


class AnnotateEffectiveCountryTests(unittest.TestCase):
    """Originally reproduced a bug where a car scraped from the French AutoScout24 domain
    ("Ruisbroek (Belgique), France") was physically in Belgium, per its own `country` field
    ("BE"), but was charged France's transport distance (€290) instead of Belgium's (€230).

    Fixed by preferring the listing's own ISO `country` field. Discovery now runs one
    combined search across all countries per price band (see build_search_url) instead of
    one search per country, so there is no second, independent "which domain was this
    scraped from" signal left to disagree with `country` in the first place —
    annotate_effective_country() derives both 'source_country' and 'effective_country'
    straight from `country`, always."""

    def test_derives_both_columns_from_the_listings_own_country_field(self):
        df = pd.DataFrame([
            {"country": "BE"},
            {"country": "DE"},
            {"country": "fr"},  # lowercase — must still resolve
        ])
        annotated = transform.annotate_effective_country(df)
        self.assertEqual(list(annotated["source_country"]), ["Belgium", "Germany", "France"])
        self.assertEqual(list(annotated["effective_country"]), ["Belgium", "Germany", "France"])

    def test_unrecognised_or_missing_country_becomes_unknown_not_a_guess(self):
        df = pd.DataFrame([{"country": "XX"}, {"country": None}])
        annotated = transform.annotate_effective_country(df)
        self.assertEqual(list(annotated["source_country"]), ["Unknown", "Unknown"])
        self.assertEqual(list(annotated["effective_country"]), ["Unknown", "Unknown"])

    def test_missing_country_column_defaults_to_unknown_everywhere(self):
        df = pd.DataFrame([{"price": 10000}, {"price": 20000}])
        annotated = transform.annotate_effective_country(df)
        self.assertEqual(list(annotated["effective_country"]), ["Unknown", "Unknown"])

    def test_transport_cost_actually_changes_with_the_correct_country(self):
        # Regression guard: the same listing costs a different amount to move depending
        # on which country it is really in — France 150 + 0.40 × 1450 = 730, Belgium
        # 150 + 0.40 × 1150 = 610 at Poland's average distances.
        distances, _ = transform.get_distances("Poland", "")
        cost_france, _ = transform.resolve_transport_cost("France", distances, 0.40, {})
        cost_belgium, _ = transform.resolve_transport_cost("Belgium", distances, 0.40, {})
        self.assertNotEqual(cost_france, cost_belgium)
        self.assertAlmostEqual(cost_france, 730.0, places=0)
        self.assertAlmostEqual(cost_belgium, 610.0, places=0)


if __name__ == "__main__":
    unittest.main()
