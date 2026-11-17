"""Tests for Gemini-based damage/salvage title screening.

Run with: python -m pytest
"""
import unittest
from unittest.mock import patch

import pandas as pd

from veylo import config, screening, transform


class FakeResponse:
    """Minimal stand-in for requests.Response, just enough for classify_title_batch."""

    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data


def make_gemini_body(mapping, tokens=100):
    """Builds a fake Gemini generateContent success body for the given {key: category}."""
    import json
    return {
        "usageMetadata": {"totalTokenCount": tokens},
        "candidates": [{"content": {"parts": [{"text": json.dumps(mapping)}]}}],
    }


class ClassifyTitleBatchJsonParsingTests(unittest.TestCase):
    """JSON parsing: a well-formed, schema-conformant response must parse into exactly
    the requested categories."""

    def test_valid_response_parses_correctly(self):
        titles = {"0": "BMW 320d, Unfallfrei", "1": "Audi A4, nur Teile"}
        body = make_gemini_body({"0": "CLEAN", "1": "PARTS_ONLY"}, tokens=250)

        def fake_post(url, json=None, headers=None, timeout=None):
            return FakeResponse(200, body)

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(error)
        self.assertEqual(result, {"0": "CLEAN", "1": "PARTS_ONLY"})
        self.assertEqual(tokens_used, 250)

    def test_response_missing_a_key_is_rejected_not_defaulted(self):
        titles = {"0": "BMW 320d", "1": "Audi A4"}
        body = make_gemini_body({"0": "CLEAN"})  # "1" missing entirely

        def fake_post(url, json=None, headers=None, timeout=None):
            return FakeResponse(200, body)

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(result)
        self.assertIsNotNone(error)

    def test_response_with_invalid_category_value_is_rejected(self):
        titles = {"0": "BMW 320d"}
        body = make_gemini_body({"0": "NOT_A_REAL_CATEGORY"})

        def fake_post(url, json=None, headers=None, timeout=None):
            return FakeResponse(200, body)

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(result)
        self.assertIsNotNone(error)

    def test_unparseable_body_is_rejected(self):
        titles = {"0": "BMW 320d"}

        def fake_post(url, json=None, headers=None, timeout=None):
            return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(result)
        self.assertIsNotNone(error)


class ClassifyTitleBatchApiFailureTests(unittest.TestCase):
    """API-failure path: must never fall back to marking everything CLEAN."""

    def test_non_200_status_returns_none_not_clean(self):
        titles = {"0": "BMW 320d"}

        def fake_post(url, json=None, headers=None, timeout=None):
            return FakeResponse(500, text="internal error")

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(tokens_used, 0)

    def test_network_exception_returns_none_not_clean(self):
        titles = {"0": "BMW 320d"}

        def fake_post(url, json=None, headers=None, timeout=None):
            raise ConnectionError("no network")

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(result)
        self.assertIn("no network", error)

    def test_missing_api_key_returns_none_not_clean(self):
        with patch.object(config, "GEMINI_API_KEY", None):
            result, tokens_used, error = screening.classify_title_batch({"0": "BMW 320d"})

        self.assertIsNone(result)
        self.assertIn("No Gemini API key", error)

    def test_429_is_retried_once_then_succeeds(self):
        titles = {"0": "BMW 320d"}
        calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                return FakeResponse(429, text="Please retry in 0.01s.")
            return FakeResponse(200, make_gemini_body({"0": "CLEAN"}))

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result, {"0": "CLEAN"})

    def test_429_that_fails_again_on_retry_returns_none(self):
        titles = {"0": "BMW 320d"}

        def fake_post(url, json=None, headers=None, timeout=None):
            return FakeResponse(429, text="Please retry in 0.01s.")

        with patch.object(config, "GEMINI_API_KEY", "fake-key"):
            result, tokens_used, error = screening.classify_title_batch(titles, post_fn=fake_post)

        self.assertIsNone(result)


class ParseRetryDelayTests(unittest.TestCase):
    def test_extracts_delay_from_real_error_message(self):
        text = ("Quota exceeded... Please retry in 35.104886228s.")
        self.assertAlmostEqual(screening._parse_retry_delay_seconds(text), 35.104886228)

    def test_returns_none_when_no_delay_present(self):
        self.assertIsNone(screening._parse_retry_delay_seconds("some other error"))
        self.assertIsNone(screening._parse_retry_delay_seconds(""))
        self.assertIsNone(screening._parse_retry_delay_seconds(None))


class ScreenTitlesForDamageIndexMappingTests(unittest.TestCase):
    """Index-to-ID mapping: local per-batch indices ("0".."n-1") must resolve back to the
    correct real listing IDs, including across multiple batches."""

    def test_single_batch_maps_back_correctly(self):
        id_to_title = {"listing-a": "BMW 320d", "listing-b": "Audi A4, nur Teile"}

        def fake_classify(titles_by_local_index):
            # Confirms local keys are "0", "1" — not the real IDs — reaching the classifier.
            assert set(titles_by_local_index.keys()) == {"0", "1"}
            return {"0": "CLEAN", "1": "PARTS_ONLY"}, 50, None

        flags, unavailable, tokens, batches, error = screening.screen_titles_for_damage(
            id_to_title, classify_fn=fake_classify, batch_size=10, min_seconds_between_calls=0,
        )
        self.assertEqual(flags, {"listing-a": "CLEAN", "listing-b": "PARTS_ONLY"})
        self.assertEqual(unavailable, set())
        self.assertEqual(batches, 1)

    def test_multiple_batches_do_not_collide_on_local_index(self):
        # 5 listings, batch_size=2 -> 3 batches, each restarting local indices at "0".
        id_to_title = {f"id-{i}": f"Title {i}" for i in range(5)}
        # Expected real category per id, keyed to prove correct resolution regardless of
        # which batch (and which local index within it) each id landed in.
        expected = {"id-0": "CLEAN", "id-1": "DAMAGE", "id-2": "AUCTION",
                    "id-3": "EXPORT_PRICE", "id-4": "LEASE_OR_FINANCE_PRICE"}

        def fake_classify(titles_by_local_index):
            # Answer using the actual titles (which encode the real id) to prove the
            # caller is responsible for correct re-mapping, not the fake itself.
            result = {}
            for local_key, title in titles_by_local_index.items():
                real_id = "id-" + title.split()[-1]
                result[local_key] = expected[real_id]
            return result, 30, None

        flags, unavailable, tokens, batches, error = screening.screen_titles_for_damage(
            id_to_title, classify_fn=fake_classify, batch_size=2, min_seconds_between_calls=0,
        )
        self.assertEqual(flags, expected)
        self.assertEqual(unavailable, set())
        self.assertEqual(batches, 3)
        self.assertEqual(tokens, 90)

    def test_no_id_is_silently_dropped(self):
        id_to_title = {f"id-{i}": f"Title {i}" for i in range(7)}

        def fake_classify(titles_by_local_index):
            return {k: "CLEAN" for k in titles_by_local_index}, 10, None

        flags, unavailable, tokens, batches, error = screening.screen_titles_for_damage(
            id_to_title, classify_fn=fake_classify, batch_size=3, min_seconds_between_calls=0,
        )
        self.assertEqual(len(flags) + len(unavailable), len(id_to_title))
        self.assertEqual(set(flags) | unavailable, set(id_to_title))


class ScreenTitlesForDamageFailurePathTests(unittest.TestCase):
    """A failed batch must mark its listings unavailable, never CLEAN — and one failing
    batch must not corrupt the results of other, successful batches."""

    def test_failed_batch_listings_are_unavailable_not_clean(self):
        id_to_title = {"a": "Title A", "b": "Title B"}

        def fake_classify(titles_by_local_index):
            return None, 0, "simulated failure"

        flags, unavailable, tokens, batches, error = screening.screen_titles_for_damage(
            id_to_title, classify_fn=fake_classify, batch_size=10, min_seconds_between_calls=0,
        )
        self.assertEqual(flags, {})
        self.assertEqual(unavailable, {"a", "b"})
        self.assertIsNotNone(error)

    def test_one_failed_batch_does_not_affect_a_successful_batch(self):
        id_to_title = {f"id-{i}": f"Title {i}" for i in range(4)}
        call_count = {"n": 0}

        def fake_classify(titles_by_local_index):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None, 0, "batch 1 failed"
            return {k: "CLEAN" for k in titles_by_local_index}, 10, None

        flags, unavailable, tokens, batches, error = screening.screen_titles_for_damage(
            id_to_title, classify_fn=fake_classify, batch_size=2, min_seconds_between_calls=0,
        )
        self.assertEqual(len(unavailable), 2)
        self.assertEqual(len(flags), 2)
        self.assertEqual(all(v == "CLEAN" for v in flags.values()), True)


class SplitCardsWithDamageFilterTests(unittest.TestCase):
    """Toggle behavior, and the guarantee that flagged listings are never dropped from
    the dataframe — only kept out of the card slice."""

    def make_df(self, n_clean=3, n_flagged=2):
        rows = []
        for i in range(n_clean):
            rows.append({"price": 10000 + i, "is_flagged": False, "damage_category": "CLEAN"})
        for i in range(n_flagged):
            rows.append({"price": 5000 + i, "is_flagged": True, "damage_category": "DAMAGE"})
        return pd.DataFrame(rows)

    def test_toggle_on_excludes_flagged_from_cards(self):
        df = self.make_df()
        cards, remaining = transform.split_cards_with_damage_filter(
            df, df["price"], cards_shown=10, exclude_damaged=True)
        self.assertFalse(cards["is_flagged"].any())
        self.assertTrue(remaining["is_flagged"].all())

    def test_toggle_off_includes_flagged_in_cards(self):
        df = self.make_df()
        cards, remaining = transform.split_cards_with_damage_filter(
            df, df["price"], cards_shown=10, exclude_damaged=False)
        self.assertEqual(len(cards), len(df))
        self.assertTrue(remaining.empty)

    def test_flagged_listings_never_dropped_from_dataframe(self):
        df = self.make_df(n_clean=3, n_flagged=2)
        cards, remaining = transform.split_cards_with_damage_filter(
            df, df["price"], cards_shown=10, exclude_damaged=True)
        self.assertEqual(len(cards) + len(remaining), len(df))

    def test_cards_shown_cap_still_respected_within_eligible_set(self):
        df = self.make_df(n_clean=5, n_flagged=2)
        cards, remaining = transform.split_cards_with_damage_filter(
            df, df["price"], cards_shown=2, exclude_damaged=True)
        self.assertEqual(len(cards), 2)
        self.assertEqual(len(cards) + len(remaining), len(df))
        self.assertFalse(cards["is_flagged"].any())

    def test_all_flagged_degrades_to_showing_everyone_rather_than_empty(self):
        df = self.make_df(n_clean=0, n_flagged=3)
        cards, remaining = transform.split_cards_with_damage_filter(
            df, df["price"], cards_shown=10, exclude_damaged=True)
        self.assertEqual(len(cards), 3)
        self.assertTrue(remaining.empty)


if __name__ == "__main__":
    unittest.main()
