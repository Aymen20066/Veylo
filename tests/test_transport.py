"""Transport estimate: max(minimum, fixed + per-km rate × distance), and a user-supplied
figure for a source country always replaces the estimate.

Run with: python -m pytest
"""
import unittest

from veylo import config, transform

from test_app_smoke import make_listing, make_results_df


class TransportFormulaTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(config.TRANSPORT_FIXED_EUR, 150)
        self.assertEqual(config.TRANSPORT_MINIMUM_EUR, 300)
        self.assertEqual(config.TRANSPORT_RATE_PER_KM_DEFAULT, 0.40)

    def test_germany_to_poland_default_is_490(self):
        distances, matched = transform.get_distances("Poland", "")
        self.assertIsNone(matched)
        self.assertEqual(distances["Germany"], 850)
        cost, is_user = transform.resolve_transport_cost(
            "Germany", distances, config.TRANSPORT_RATE_PER_KM_DEFAULT, {})
        self.assertAlmostEqual(cost, 490)      # 150 + 0.40 × 850
        self.assertFalse(is_user)

    def test_fixed_plus_per_km_above_the_minimum(self):
        cost, _ = transform.resolve_transport_cost("France", {"France": 1450}, 0.40, {})
        self.assertAlmostEqual(cost, 150 + 0.40 * 1450)
        cost, _ = transform.resolve_transport_cost("France", {"France": 1450}, 0.60, {})
        self.assertAlmostEqual(cost, 150 + 0.60 * 1450)

    def test_short_distance_hits_the_minimum(self):
        # 150 + 0.40 × 350 = 290, below the €300 minimum.
        distances, _ = transform.get_distances("Czech Republic", "Prague")
        cost, is_user = transform.resolve_transport_cost("Germany", distances, 0.40, {})
        self.assertEqual(cost, 300)
        self.assertFalse(is_user)
        cost, _ = transform.resolve_transport_cost("Germany", {"Germany": 10}, 0.30, {})
        self.assertEqual(cost, 300)

    def test_override_wins_even_below_the_minimum(self):
        distances, _ = transform.get_distances("Poland", "")
        cost, is_user = transform.resolve_transport_cost(
            "Germany", distances, 0.40, {"Germany": 120.0})
        self.assertEqual(cost, 120.0)
        self.assertTrue(is_user)
        # A zero or missing override keeps the estimate.
        cost, is_user = transform.resolve_transport_cost(
            "Germany", distances, 0.40, {"Germany": 0, "France": 999.0})
        self.assertAlmostEqual(cost, 490)
        self.assertFalse(is_user)

    def test_unknown_source_country_has_no_estimate(self):
        cost, is_user = transform.resolve_transport_cost("Spain", {"Germany": 850}, 0.40, {})
        self.assertIsNone(cost)
        self.assertFalse(is_user)

    def test_results_use_the_new_formula(self):
        df = make_results_df([make_listing(0)])      # a car in Germany, going to Poland
        distances, _ = transform.get_distances("Poland", "")
        df = transform.add_cost_columns(df, "Poland", distances, 0.40, {})
        self.assertAlmostEqual(df["shipping_est"].iloc[0], 490)
        df = transform.add_cost_columns(df, "Poland", distances, 0.40, {"Germany": 275.0})
        self.assertEqual(df["shipping_est"].iloc[0], 275.0)


if __name__ == "__main__":
    unittest.main()
