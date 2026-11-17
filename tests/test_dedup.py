"""Tests for cross-market duplicate-listing removal.

Run with: python -m pytest
"""
import unittest

import pandas as pd

from veylo import transform


def make_df(rows):
    return pd.DataFrame(rows)


class CrossDomainDuplicateTests(unittest.TestCase):
    """Reproduces the reported bug: the identical BMW i3 120Ah - 42.2 kWh Advanced, 2019,
    55,863 km, €16,250 appeared once under the Belgian domain and once under the French
    domain, with two different URLs, because it's one car cross-listed on both."""

    def test_cross_domain_duplicate_is_merged(self):
        df = make_df([
            {"title": "BMW i3 120Ah - 42.2 kWh Advanced", "price": 16250, "mileageKm": 55863,
             "firstRegistration": "2019-01-01", "url": "https://www.autoscout24.be/bmw-i3-123",
             "source_country": "Belgium"},
            {"title": "BMW i3 120Ah - 42.2 kWh Advanced", "price": 16250, "mileageKm": 55863,
             "firstRegistration": "2019-01-01", "url": "https://www.autoscout24.fr/bmw-i3-456",
             "source_country": "France"},
        ])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 1)
        self.assertEqual(len(deduped), 1)
        # First occurrence (Belgium) is kept.
        self.assertEqual(deduped.iloc[0]["source_country"], "Belgium")

    def test_distinct_cars_of_the_same_model_are_not_merged(self):
        df = make_df([
            {"title": "BMW i3 120Ah - 42.2 kWh Advanced", "price": 16250, "mileageKm": 55863,
             "firstRegistration": "2019-01-01", "url": "https://a/1", "source_country": "Belgium"},
            {"title": "BMW i3 120Ah - 42.2 kWh Advanced", "price": 17900, "mileageKm": 40000,
             "firstRegistration": "2020-03-01", "url": "https://a/2", "source_country": "Germany"},
        ])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 0)
        self.assertEqual(len(deduped), 2)


class ExactUrlDuplicateTests(unittest.TestCase):
    def test_identical_url_is_merged_even_if_composite_fields_differ(self):
        # Same page re-scraped/overlapping pagination: same URL is enough on its own.
        df = make_df([
            {"title": "BMW i3", "price": 16250, "mileageKm": 55863,
             "firstRegistration": "2019-01-01", "url": "https://x/1", "source_country": "Belgium"},
            {"title": "BMW i3", "price": 16250, "mileageKm": 55863,
             "firstRegistration": "2019-01-01", "url": "https://x/1", "source_country": "Belgium"},
        ])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 1)
        self.assertEqual(len(deduped), 1)


class MissingFieldsDoNotCrashOrOverMergeTests(unittest.TestCase):
    def test_missing_url_column_falls_back_to_composite_only(self):
        df = make_df([
            {"title": "BMW i3", "price": 16250, "mileageKm": 55863, "firstRegistration": "2019-01-01"},
            {"title": "BMW i3", "price": 16250, "mileageKm": 55863, "firstRegistration": "2019-01-01"},
        ])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 1)

    def test_rows_missing_a_composite_field_are_never_merged_via_fallback(self):
        # Two listings both missing title: must NOT be treated as duplicates of each other
        # just because the remaining fields happen to match.
        df = make_df([
            {"title": None, "price": 16250, "mileageKm": 55863, "firstRegistration": "2019-01-01",
             "url": None},
            {"title": None, "price": 16250, "mileageKm": 55863, "firstRegistration": "2019-01-01",
             "url": None},
        ])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 0)
        self.assertEqual(len(deduped), 2)

    def test_empty_dataframe_does_not_crash(self):
        df = make_df([])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 0)
        self.assertEqual(len(deduped), 0)

    def test_no_duplicates_returns_zero_merged(self):
        df = make_df([
            {"title": "BMW i3", "price": 16250, "mileageKm": 55863, "firstRegistration": "2019-01-01",
             "url": "https://a/1"},
            {"title": "Audi A4", "price": 20000, "mileageKm": 30000, "firstRegistration": "2021-01-01",
             "url": "https://a/2"},
        ])
        deduped, merged = transform.deduplicate_listings(df)
        self.assertEqual(merged, 0)
        self.assertEqual(len(deduped), 2)


if __name__ == "__main__":
    unittest.main()
