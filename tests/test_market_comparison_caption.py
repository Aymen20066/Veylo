"""Test for the Market Comparison 'rows don't visibly add up' disclosure in app.py.

Run with: python -m pytest
"""
import unittest
from pathlib import Path

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class MarketComparisonCaptionTests(unittest.TestCase):
    """Reported bug: Germany's row read €23,600 + €115 + €66 = €23,781 on paper, but
    the table showed €23,962 — mathematically correct (each column is an independent
    median), but with no explanation, it reads as a broken table. The fix is a caption,
    not a math change — this just confirms the caption exists."""

    def test_independent_median_caption_present(self):
        with open(APP_PATH, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("its own independent sample median", source)
        self.assertIn("won't", source)

    def test_original_caption_and_thin_market_note_still_present(self):
        # Guards against this fix accidentally clobbering the existing caption text.
        with open(APP_PATH, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("Not enough data", source)
        self.assertIn("a median from one or two cars isn't a reliable figure", source)

    def test_sample_median_relabeling_present(self):
        # Discovery is now a stratified, cheapest-first sample, so our
        # own medians no longer represent the market and must say so wherever shown.
        with open(APP_PATH, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("Sample Median Price (€)", source)
        self.assertIn("Sample Median Acquisition (€)", source)
        self.assertIn("not a market-wide figure", source)


if __name__ == "__main__":
    unittest.main()
