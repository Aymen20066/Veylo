"""Tests for the AutoScout24 priceEvaluation-driven price-rating badge.

Run with: python -m pytest
"""
import unittest

from veylo import transform


class CalculateDealQualityTests(unittest.TestCase):
    """calculate_price_rating() is driven entirely by AutoScout24's own priceEvaluation
    field, not our own (now deliberately cheap-skewed) sample medians. Only "toolow-price "
    and "unknown" have actually been observed in live testing (see docs/experiments.md) —
    everything else here exercises the keyword-matching design meant to handle values we
    haven't seen yet."""

    def test_confirmed_value_toolow_price_with_trailing_space(self):
        # The exact raw string observed in real API output, trailing space included.
        label, css_class, tier = transform.calculate_price_rating("toolow-price ")
        self.assertEqual(tier, "toolow")
        self.assertEqual(css_class, "tag-average")
        self.assertIn("AutoScout24", label)

    def test_toolow_is_a_caution_tier_not_the_top_badge(self):
        # This is the one confirmed value, and it appeared exclusively on junk listings
        # (a EUR1 BMW, 250k+ km cars) in testing — it must not read as "excellent deal".
        _, css_class, tier = transform.calculate_price_rating("toolow-price")
        self.assertEqual(tier, "toolow")
        self.assertNotEqual(css_class, "tag-excellent")

    def test_confirmed_value_unknown(self):
        label, css_class, tier = transform.calculate_price_rating("unknown")
        self.assertEqual(tier, "unrated")
        self.assertEqual(css_class, "tag-neutral")
        self.assertIn("Not Rated", label)

    def test_missing_value_treated_same_as_unknown(self):
        label, css_class, tier = transform.calculate_price_rating(None)
        self.assertEqual(tier, "unrated")
        self.assertEqual(css_class, "tag-neutral")

    def test_case_insensitive(self):
        _, _, tier = transform.calculate_price_rating("TOOLOW-PRICE ")
        self.assertEqual(tier, "toolow")

    def test_good_price_tier(self):
        _, css_class, tier = transform.calculate_price_rating("good-price")
        self.assertEqual(tier, "good")
        self.assertEqual(css_class, "tag-good")

    def test_top_price_tier(self):
        _, css_class, tier = transform.calculate_price_rating("top-price")
        self.assertEqual(tier, "top")
        self.assertEqual(css_class, "tag-excellent")

    def test_expensive_tier_is_a_negative_signal(self):
        _, css_class, tier = transform.calculate_price_rating("somewhat-expensive")
        self.assertEqual(tier, "expensive")
        self.assertEqual(css_class, "tag-weak")

    def test_unseen_value_is_shown_verbatim_not_dropped_or_guessed(self):
        # Simulates a real value we haven't observed yet — must not crash, must not be
        # silently miscategorized into an existing tier, and must still display something
        # derived from the actual value (never guess).
        label, css_class, tier = transform.calculate_price_rating("brand-new-rating-slug")
        self.assertEqual(tier, "unrecognized")
        self.assertEqual(css_class, "tag-neutral")
        self.assertIn("Brand new rating slug", label)

    def test_empty_string_treated_as_unrated(self):
        _, _, tier = transform.calculate_price_rating("")
        self.assertEqual(tier, "unrated")


if __name__ == "__main__":
    unittest.main()
