"""Tests for undisclosed result-truncation detection.

Run with: python -m pytest
"""
import unittest

from veylo import config, retrieval


class IsMarketTruncatedTests(unittest.TestCase):
    """Reproduces the reported bug: Germany returned exactly 50 listings (the
    MAX_RESULTS_DEFAULT cap) while Netherlands (40), Italy (37), Belgium (11), and
    France (11) came in under it. Only Germany should be flagged as possibly truncated."""

    def test_market_at_exactly_the_cap_is_flagged(self):
        self.assertTrue(retrieval.is_market_truncated(50, 50))

    def test_market_under_the_cap_is_not_flagged(self):
        self.assertFalse(retrieval.is_market_truncated(40, 50))
        self.assertFalse(retrieval.is_market_truncated(37, 50))
        self.assertFalse(retrieval.is_market_truncated(11, 50))

    def test_reported_scenario_flags_only_germany(self):
        results = {"Germany": 50, "Belgium": 11, "France": 11, "Netherlands": 40, "Italy": 37}
        flagged = [c for c, count in results.items() if retrieval.is_market_truncated(count, 50)]
        self.assertEqual(flagged, ["Germany"])

    def test_zero_results_not_flagged(self):
        self.assertFalse(retrieval.is_market_truncated(0, 50))

    def test_body_type_filter_cap_is_respected(self):
        # PER_BAND_MAX_RESULTS_WITH_BODY_TYPE_FILTER (200) is a different cap than the
        # default (50).
        self.assertFalse(retrieval.is_market_truncated(50, config.PER_BAND_MAX_RESULTS_WITH_BODY_TYPE_FILTER))
        self.assertTrue(retrieval.is_market_truncated(200, config.PER_BAND_MAX_RESULTS_WITH_BODY_TYPE_FILTER))

    def test_count_beyond_the_cap_is_still_flagged(self):
        # Defensive: shouldn't happen, but a count above the cap is still evidence of
        # truncation somewhere, not proof of anything less.
        self.assertTrue(retrieval.is_market_truncated(51, 50))


if __name__ == "__main__":
    unittest.main()
