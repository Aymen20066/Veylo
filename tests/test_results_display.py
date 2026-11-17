"""Tests for the card/table split of search results.

Run with: python -m pytest
"""
import unittest

import pandas as pd

from veylo import config, transform


class RankAndSplitTests(unittest.TestCase):
    """Reproduces the reported bug: a search returning 149 vehicles displayed 20 cards
    with the other 129 nowhere to be found. Every row must now end up in either the
    card slice or the remaining-results slice — never dropped."""

    def test_no_row_is_dropped(self):
        df = pd.DataFrame({"price": range(149)})
        cards, remaining = transform.rank_and_split(df, df["price"], config.CARDS_SHOWN)
        self.assertEqual(len(cards) + len(remaining), 149)

    def test_cards_capped_at_cards_shown(self):
        df = pd.DataFrame({"price": range(149)})
        cards, remaining = transform.rank_and_split(df, df["price"], config.CARDS_SHOWN)
        self.assertEqual(len(cards), config.CARDS_SHOWN)
        self.assertEqual(len(remaining), 149 - config.CARDS_SHOWN)

    def test_fewer_results_than_cards_shown_has_no_remaining_slice(self):
        df = pd.DataFrame({"price": range(5)})
        cards, remaining = transform.rank_and_split(df, df["price"], config.CARDS_SHOWN)
        self.assertEqual(len(cards), 5)
        self.assertTrue(remaining.empty)

    def test_cards_are_the_best_ranked_by_sort_key(self):
        df = pd.DataFrame({"id": ["a", "b", "c", "d"], "cost": [400, 100, 300, 200]})
        cards, remaining = transform.rank_and_split(df, df["cost"], cards_shown=2)
        self.assertEqual(list(cards["id"]), ["b", "d"])
        self.assertEqual(list(remaining["id"]), ["c", "a"])

    def test_remaining_slice_excludes_every_card_row(self):
        df = pd.DataFrame({"id": range(50), "cost": list(range(50))})
        cards, remaining = transform.rank_and_split(df, df["cost"], config.CARDS_SHOWN)
        self.assertEqual(set(cards["id"]).intersection(set(remaining["id"])), set())

    def test_exactly_cards_shown_results_has_no_remaining_slice(self):
        df = pd.DataFrame({"price": range(config.CARDS_SHOWN)})
        cards, remaining = transform.rank_and_split(df, df["price"], config.CARDS_SHOWN)
        self.assertEqual(len(cards), config.CARDS_SHOWN)
        self.assertTrue(remaining.empty)


if __name__ == "__main__":
    unittest.main()
