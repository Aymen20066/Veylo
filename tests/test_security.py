"""Security tests: scraped text is shown as plain text (never as markup), only https://
listing URLs become links, and API credentials travel in headers, never in a URL.

Run with: python -m pytest
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from veylo import config, retrieval, screening, transform

from test_app_smoke import SEARCH_PARAMS, make_listing, make_results_df

ROOT = Path(__file__).resolve().parent.parent
FAKE_TOKEN = "apify_api_FAKE-TOKEN-0123456789"
FAKE_GEMINI_KEY = "FAKE-GEMINI-KEY-0123456789"


def card_html_for(**listing_overrides):
    df = make_results_df([make_listing(0, **listing_overrides)])
    return transform.build_listing_card_html(df.iloc[0], dict(SEARCH_PARAMS))


class CardHtmlEscapingTests(unittest.TestCase):
    def test_script_tag_in_title_is_shown_as_plain_text(self):
        html = card_html_for(title='BMW 320d <script>alert("x")</script>')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", html)

    def test_bold_tag_in_title_is_shown_as_plain_text(self):
        html = card_html_for(title="BMW <b>TOP DEAL</b> 320d")
        self.assertNotIn("<b>TOP DEAL</b>", html)
        self.assertIn("BMW &lt;b&gt;TOP DEAL&lt;/b&gt; 320d", html)

    def test_other_scraped_fields_are_escaped(self):
        payload = "<img src=x onerror=alert(1)>"
        for field in ("city", "transmission", "fuelType", "bodyType", "sellerType",
                      "priceEvaluation"):
            with self.subTest(field=field):
                html = card_html_for(**{field: payload})
                self.assertNotIn("<img", html)

    def test_notes_are_escaped(self):
        df = make_results_df([make_listing(0)])
        row = df.iloc[0].copy()
        row["import_notes"] = ["note with <i>markup</i>"]
        html = transform.build_listing_card_html(row, dict(SEARCH_PARAMS))
        self.assertNotIn("<i>markup</i>", html)
        self.assertIn("&lt;i&gt;markup&lt;/i&gt;", html)


class ListingUrlTests(unittest.TestCase):
    def test_https_url_is_linked(self):
        html = card_html_for(url="https://www.autoscout24.com/offers/abc")
        self.assertIn('href="https://www.autoscout24.com/offers/abc"', html)

    def test_non_https_urls_are_never_put_in_an_href(self):
        for bad in ("javascript:alert(1)", "http://example.com/x", "data:text/html,x",
                    "//example.com/x", "", None):
            with self.subTest(url=bad):
                html = card_html_for(url=bad)
                self.assertNotIn("href=", html)
                self.assertIn("listing link unavailable", html)

    def test_quote_in_url_cannot_break_out_of_the_attribute(self):
        html = card_html_for(url='https://example.com/"><script>alert(1)</script>')
        self.assertNotIn("<script>", html)
        self.assertIn('href="https://example.com/&quot;&gt;&lt;script&gt;', html)

    def test_safe_listing_url(self):
        self.assertEqual(transform.safe_listing_url("https://a.example/x"), "https://a.example/x")
        self.assertIsNone(transform.safe_listing_url("javascript:alert(1)"))
        self.assertIsNone(transform.safe_listing_url(None))
        self.assertIsNone(transform.safe_listing_url(float("nan")))


class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


class NoCredentialInUrlTests(unittest.TestCase):
    def test_apify_token_is_sent_as_bearer_header_never_in_a_url(self):
        calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append((url, headers))
            return FakeResponse(201, {"data": {"id": "run123"}})

        def fake_get(url, headers=None, timeout=None):
            calls.append((url, headers))
            if url.endswith("/dataset/items"):
                return FakeResponse(200, [])
            return FakeResponse(200, {"data": {"status": "SUCCEEDED"}})

        with patch.object(config, "APIFY_TOKEN", FAKE_TOKEN), \
                patch.object(retrieval.requests, "post", fake_post), \
                patch.object(retrieval.requests, "get", fake_get):
            run_id, error = retrieval.run_apify_band_search(
                "bmw", "320d", 10000, 20000, 2015, 2024, 150000, ["D", "B"], 5)
            self.assertEqual((run_id, error), ("run123", None))
            self.assertEqual(retrieval.poll_run(run_id), "SUCCEEDED")
            self.assertEqual(retrieval.get_results(run_id), [])

        self.assertEqual(len(calls), 3)
        for url, headers in calls:
            self.assertNotIn(FAKE_TOKEN, url)
            self.assertNotIn("token=", url)
            self.assertEqual(headers, {"Authorization": f"Bearer {FAKE_TOKEN}"})

    def test_search_url_contains_no_token(self):
        with patch.object(config, "APIFY_TOKEN", FAKE_TOKEN):
            url = retrieval.build_search_url("bmw", "320d", 10000, 20000, 2015, 2024,
                                             150000, ["D", "B"])
        self.assertNotIn(FAKE_TOKEN, url)

    def test_gemini_key_is_sent_as_header_never_in_a_url(self):
        calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append((url, headers))
            return FakeResponse(200, {
                "usageMetadata": {"totalTokenCount": 10},
                "candidates": [{"content": {"parts": [{"text": '{"0": "CLEAN"}'}]}}],
            })

        with patch.object(config, "GEMINI_API_KEY", FAKE_GEMINI_KEY):
            result, _, error = screening.classify_title_batch({"0": "BMW 320d"},
                                                              post_fn=fake_post)
        self.assertIsNone(error)
        self.assertEqual(result, {"0": "CLEAN"})
        url, headers = calls[0]
        self.assertNotIn(FAKE_GEMINI_KEY, url)
        self.assertNotIn("key=", url)
        self.assertEqual(headers, {"x-goog-api-key": FAKE_GEMINI_KEY})


class GeminiModelOverrideTests(unittest.TestCase):
    def _model_with_env(self, value):
        env = dict(os.environ)
        env.pop("GEMINI_MODEL", None)
        if value is not None:
            env["GEMINI_MODEL"] = value
        out = subprocess.run(
            [sys.executable, "-c", "from veylo import config; print(config.GEMINI_MODEL)"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_environment_variable_overrides_the_model(self):
        self.assertEqual(self._model_with_env("some-other-model"), "some-other-model")


if __name__ == "__main__":
    unittest.main()
