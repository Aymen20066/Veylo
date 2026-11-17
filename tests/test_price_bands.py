"""Tests for the stratified price-band discovery funnel:
compute_price_bands() and build_search_url().

Run with: python -m pytest
"""
import unittest

from veylo import config, retrieval


class ComputePriceBandsTests(unittest.TestCase):
    def test_splits_range_into_the_requested_number_of_equal_width_bands(self):
        bands = retrieval.compute_price_bands(10000, 60000, num_bands=5)
        self.assertEqual(len(bands), 5)
        self.assertEqual(bands[0][0], 10000)
        self.assertEqual(bands[-1][1], 60000)

    def test_bands_are_contiguous_with_no_gaps_or_overlaps(self):
        bands = retrieval.compute_price_bands(1000, 34000, num_bands=5)
        for (_, hi), (next_lo, _) in zip(bands, bands[1:]):
            self.assertEqual(hi, next_lo)

    def test_bands_cover_the_full_requested_range(self):
        bands = retrieval.compute_price_bands(20000, 150000, num_bands=5)
        self.assertEqual(bands[0][0], 20000)
        self.assertEqual(bands[-1][1], 150000)

    def test_single_band_when_min_equals_max(self):
        bands = retrieval.compute_price_bands(50000, 50000, num_bands=5)
        self.assertEqual(bands, [(50000, 50000)])

    def test_single_band_when_max_below_min(self):
        # Defensive: shouldn't happen from the UI (a slider can't invert), but must not
        # crash or silently drop the search if it ever does.
        bands = retrieval.compute_price_bands(50000, 10000, num_bands=5)
        self.assertEqual(bands, [(50000, 10000)])

    def test_single_band_when_num_bands_is_not_positive(self):
        bands = retrieval.compute_price_bands(10000, 60000, num_bands=0)
        self.assertEqual(bands, [(10000, 60000)])

    def test_default_band_count_comes_from_bands_for_price_range(self):
        bands = retrieval.compute_price_bands(10000, 60000)
        self.assertEqual(len(bands), retrieval.bands_for_price_range(10000, 60000))

    def test_bands_are_contiguous_at_every_valid_band_count(self):
        for num_bands in range(config.MIN_PRICE_BANDS, config.MAX_PRICE_BANDS + 1):
            bands = retrieval.compute_price_bands(1000, 200000, num_bands=num_bands)
            self.assertEqual(len(bands), num_bands)
            for (_, hi), (next_lo, _) in zip(bands, bands[1:]):
                self.assertEqual(hi, next_lo, f"gap/overlap at num_bands={num_bands}")


class BandsForPriceRangeTests(unittest.TestCase):
    """A flat band count gave wildly different slice widths for a narrow vs. a wide
    search — a €10k-15k search and a €10k-150k search got the same 5 bands. Band count
    should scale with range width instead."""

    def test_narrow_range_floors_at_minimum(self):
        self.assertEqual(retrieval.bands_for_price_range(10000, 15000), config.MIN_PRICE_BANDS)

    def test_even_narrower_range_still_floors_at_minimum(self):
        self.assertEqual(retrieval.bands_for_price_range(10000, 10500), config.MIN_PRICE_BANDS)

    def test_zero_width_range_floors_at_minimum(self):
        self.assertEqual(retrieval.bands_for_price_range(50000, 50000), config.MIN_PRICE_BANDS)

    def test_mid_range_case(self):
        self.assertEqual(retrieval.bands_for_price_range(10000, 50000), 3)

    def test_wide_range_caps_at_maximum(self):
        self.assertEqual(retrieval.bands_for_price_range(10000, 150000), config.MAX_PRICE_BANDS)

    def test_even_wider_range_still_caps_at_maximum(self):
        self.assertEqual(retrieval.bands_for_price_range(1000, 300000), config.MAX_PRICE_BANDS)

    def test_result_always_within_bounds(self):
        for lo, hi in [(0, 1), (0, 15000), (0, 34000), (5000, 5000), (1000, 1000000)]:
            n = retrieval.bands_for_price_range(lo, hi)
            self.assertGreaterEqual(n, config.MIN_PRICE_BANDS)
            self.assertLessEqual(n, config.MAX_PRICE_BANDS)


class BuildSearchUrlTests(unittest.TestCase):
    def setUp(self):
        self.url = retrieval.build_search_url(
            "bmw", "3-series", 8000, 15000, 2016, 2020, 120000,
            ["D", "B", "F", "NL", "I"],
        )

    def test_path_encodes_make_and_model(self):
        self.assertTrue(self.url.startswith("https://www.autoscout24.com/lst/bmw/3-series?"))

    def test_sorts_cheapest_first(self):
        self.assertIn("sort=price", self.url)
        self.assertIn("desc=0", self.url)

    def test_combines_all_requested_countries(self):
        self.assertIn("cy=D,B,F,NL,I", self.url)

    def test_price_band_params(self):
        self.assertIn("pricefrom=8000", self.url)
        self.assertIn("priceto=15000", self.url)

    def test_year_range_params(self):
        self.assertIn("fregfrom=2016", self.url)
        self.assertIn("fregto=2020", self.url)

    def test_mileage_param(self):
        self.assertIn("kmto=120000", self.url)


if __name__ == "__main__":
    unittest.main()
