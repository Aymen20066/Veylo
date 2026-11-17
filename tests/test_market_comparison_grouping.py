"""Tests for Market Comparison grouping by effective_country.

Run with: python -m pytest
"""
import unittest

import pandas as pd

from veylo import transform


class MarketComparisonEffectiveCountryTests(unittest.TestCase):
    """Confirms a cross-listed car (mismatched source vs. effective country, the bug #4
    scenario) is counted under its effective country in the Market Comparison table —
    not the market it was scraped from — consistent with the transport cost fix."""

    def test_cross_listed_car_counted_under_effective_country_not_source(self):
        # 2 genuine Belgium listings + 1 France listing that's actually in Belgium.
        rows = (
            [{"source_country": "Belgium", "effective_country": "Belgium",
              "price": 16000, "shipping_est": 230, "import_costs_total": 500,
              "total_cost": 16730}] * 2
            + [{"source_country": "France", "effective_country": "Belgium",
                "price": 16250, "shipping_est": 230, "import_costs_total": 500,
                "total_cost": 16980}]
        )
        df = pd.DataFrame(rows)
        stats = transform.build_market_comparison(df)

        belgium_row = stats[stats["Market"] == "Belgium"].iloc[0]
        self.assertEqual(belgium_row["Vehicles"], 3)
        self.assertNotIn("France", list(stats["Market"]))

    def test_correctly_labeled_cars_still_group_normally(self):
        df = pd.DataFrame([
            {"source_country": "Germany", "effective_country": "Germany",
             "price": 20000, "shipping_est": 300, "import_costs_total": 400,
             "total_cost": 20700},
            {"source_country": "Italy", "effective_country": "Italy",
             "price": 18000, "shipping_est": 350, "import_costs_total": 350,
             "total_cost": 18700},
        ])
        stats = transform.build_market_comparison(df)
        self.assertEqual(set(stats["Market"]), {"Germany", "Italy"})
        self.assertEqual(stats[stats["Market"] == "Germany"].iloc[0]["Vehicles"], 1)
        self.assertEqual(stats[stats["Market"] == "Italy"].iloc[0]["Vehicles"], 1)

    def test_median_columns_are_computed_from_the_effective_country_group(self):
        # The France-sourced-but-Belgium car's price must feed Belgium's median, not France's.
        rows = [
            {"source_country": "Belgium", "effective_country": "Belgium",
             "price": 10000, "shipping_est": 200, "import_costs_total": 100,
             "total_cost": 10300},
            {"source_country": "France", "effective_country": "Belgium",
             "price": 20000, "shipping_est": 200, "import_costs_total": 100,
             "total_cost": 20300},
        ]
        df = pd.DataFrame(rows)
        stats = transform.build_market_comparison(df)
        belgium_row = stats[stats["Market"] == "Belgium"].iloc[0]
        self.assertEqual(belgium_row["Sample Median Price (€)"], 15000)


if __name__ == "__main__":
    unittest.main()
