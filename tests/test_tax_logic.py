"""Tests for the per-destination import tax calculators.

Run with: python -m pytest
"""
import math
import unittest

from veylo import costs

# Independent copy of the published 2026 first-year vehicle tax table (Law no. 239/2025,
# GEO no. 78/2025, Codul fiscal Art. 468-472), transcribed separately from costs.py's
# ROMANIA_RATE_TABLE so a typo in one doesn't get validated by the other.
REFERENCE_RATES = [
    (1600, {"0-3": 19.5, "4": 18.8, "5": 17.6, "6": 16.5}),
    (2000, {"0-3": 29.7, "4": 28.5, "5": 26.7, "6": 25.1}),
    (2600, {"0-3": 92.2, "4": 88.6, "5": 82.8, "6": 77.8}),
    (3000, {"0-3": 182.9, "4": 172.8, "5": 154.1, "6": 151.2}),
    (None, {"0-3": 319.0, "4": 297.3, "5": 294.4, "6": 290.0}),   # None = no upper bound
]
EURO_STANDARD_FOR_BUCKET = {"0-3": 3, "4": 4, "5": 5, "6": 6}


def expected_ron(cc, bucket_key):
    for max_cc, rates in REFERENCE_RATES:
        if max_cc is None or cc <= max_cc:
            return math.ceil(cc / 200) * rates[bucket_key]
    raise AssertionError("no band matched cc=%r" % cc)


class RomaniaCombustionRateTableTests(unittest.TestCase):
    """Every cc band x Euro standard combo must match the published 2026 rate table exactly."""

    CC_SAMPLES = {
        "<=1600": 1600,
        "1601-2000": 2000,
        "2001-2600": 2600,
        "2601-3000": 3000,
        ">3000": 3500,
    }

    def test_all_bands_all_euro_standards(self):
        for band_name, cc in self.CC_SAMPLES.items():
            for bucket_key, euro_standard in EURO_STANDARD_FOR_BUCKET.items():
                with self.subTest(band=band_name, euro=bucket_key):
                    result = costs.calculate_romania_tax(
                        cc, euro_standard, euro_is_confirmed=True,
                        fuel_bucket=costs.FUEL_PETROL_DIESEL)
                    self.assertTrue(result["calculable"])
                    expected_eur = costs.to_eur(expected_ron(cc, bucket_key), "RON")
                    self.assertAlmostEqual(result["import_tax_eur"], expected_eur, places=6)
                    self.assertIsNone(result["alternative"])
                    self.assertEqual(result["tax_label"], "First-year ownership tax")

    def test_admin_fees_unaffected_by_rate_change(self):
        result = costs.calculate_romania_tax(1998, 6, True, fuel_bucket=costs.FUEL_PETROL_DIESEL)
        self.assertAlmostEqual(
            result["admin_fees_eur"], costs.to_eur(costs.ROMANIA_ADMIN_FEES_RON, "RON"), places=6)

    def test_full_annual_rate_proration_note_present(self):
        result = costs.calculate_romania_tax(1998, 6, True, fuel_bucket=costs.FUEL_PETROL_DIESEL)
        self.assertTrue(any("prorated" in note for note in result["notes"]))


class RomaniaElectricVehicleTests(unittest.TestCase):
    def test_ev_flat_annual_tax(self):
        result = costs.calculate_romania_tax(None, None, True, fuel_bucket=costs.FUEL_BEV)
        self.assertTrue(result["calculable"])
        expected_eur = costs.to_eur(costs.ROMANIA_BEV_ANNUAL_TAX_RON, "RON")
        self.assertAlmostEqual(result["import_tax_eur"], expected_eur, places=6)
        self.assertIsNone(result["alternative"])

    def test_ev_does_not_require_cc_or_euro_standard(self):
        result = costs.calculate_romania_tax(None, None, False, fuel_bucket=costs.FUEL_BEV)
        self.assertTrue(result["calculable"])
        self.assertIsNone(result["unavailable_reason"])

    def test_ev_with_cc_present_still_uses_flat_rate(self):
        # cc present but fuel_bucket says BEV: flat rate must win, not the displacement formula.
        result = costs.calculate_romania_tax(1998, 6, True, fuel_bucket=costs.FUEL_BEV)
        expected_eur = costs.to_eur(costs.ROMANIA_BEV_ANNUAL_TAX_RON, "RON")
        self.assertAlmostEqual(result["import_tax_eur"], expected_eur, places=6)


class RomaniaHybridDualScenarioTests(unittest.TestCase):
    def test_regular_hybrid_single_number_no_alternative(self):
        result = costs.calculate_romania_tax(1998, 6, True, fuel_bucket=costs.FUEL_HYBRID)
        self.assertTrue(result["calculable"])
        self.assertIsNone(result["alternative"])
        expected_eur = costs.to_eur(expected_ron(1998, "6"), "RON")
        self.assertAlmostEqual(result["import_tax_eur"], expected_eur, places=6)

    def test_phev_shows_both_full_and_reduced_scenario(self):
        result = costs.calculate_romania_tax(1998, 6, True, fuel_bucket=costs.FUEL_PHEV)
        self.assertTrue(result["calculable"])
        full_eur = costs.to_eur(expected_ron(1998, "6"), "RON")
        self.assertAlmostEqual(result["import_tax_eur"], full_eur, places=6)
        self.assertIsNotNone(result["alternative"])
        reduced_eur = full_eur * (1 - costs.ROMANIA_HYBRID_LOW_CO2_REDUCTION_PCT)
        self.assertAlmostEqual(result["alternative"]["import_tax_eur"], reduced_eur, places=6)

    def test_phev_reduction_is_exactly_30_percent(self):
        result = costs.calculate_romania_tax(1998, 6, True, fuel_bucket=costs.FUEL_PHEV)
        full = result["import_tax_eur"]
        reduced = result["alternative"]["import_tax_eur"]
        self.assertAlmostEqual(reduced, full * 0.70, places=6)


class RomaniaMissingDataTests(unittest.TestCase):
    def test_missing_cc_not_calculated(self):
        result = costs.calculate_romania_tax(None, 6, True, fuel_bucket=costs.FUEL_PETROL_DIESEL)
        self.assertFalse(result["calculable"])
        self.assertIsNotNone(result["unavailable_reason"])

    def test_missing_euro_standard_and_year_not_calculated(self):
        result = costs.calculate_romania_tax(1998, None, True, fuel_bucket=costs.FUEL_PETROL_DIESEL)
        self.assertFalse(result["calculable"])
        self.assertIsNotNone(result["unavailable_reason"])

    def test_unconfirmed_euro_standard_adds_estimate_note(self):
        result = costs.calculate_romania_tax(1998, 5, False, fuel_bucket=costs.FUEL_PETROL_DIESEL)
        self.assertTrue(result["calculable"])
        self.assertTrue(any("estimated as Euro" in note for note in result["notes"]))

    def test_below_euro_3_adds_eligibility_warning(self):
        result = costs.calculate_romania_tax(1998, 2, True, fuel_bucket=costs.FUEL_PETROL_DIESEL)
        self.assertTrue(any("restricts registration" in note for note in result["notes"]))


class OtherDestinationsUnaffectedTests(unittest.TestCase):
    """Light smoke checks that the Romania rewrite didn't disturb the other calculators."""

    def test_poland_bev_still_zero(self):
        result = costs.calculate_poland_tax(20000, None, costs.FUEL_BEV)
        self.assertTrue(result["calculable"])
        self.assertEqual(result["import_tax_eur"], 0.0)

    def test_bulgaria_still_callable(self):
        result = costs.calculate_bulgaria_tax(2019, costs.FUEL_PETROL_DIESEL, current_year=2026)
        self.assertIn("calculable", result)

    def test_czech_still_callable(self):
        result = costs.calculate_czech_tax(6, True)
        self.assertIn("calculable", result)


if __name__ == "__main__":
    unittest.main()
