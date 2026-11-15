"""Variant deck smoke tests. Run: python -m unittest tests.test_variant_decks"""
import random
import unittest
from collections import Counter

from variant_decks import (
    create_flip_deck, create_no_mercy_deck, flip_face,
    can_stack_no_mercy, is_eliminated_no_mercy,
)


class VariantDeckTests(unittest.TestCase):
    def test_flip_has_112_two_sided_cards(self):
        deck = create_flip_deck(random.Random(42))
        self.assertEqual(len(deck), 112)
        self.assertEqual(len({c["id"] for c in deck}), 112)
        self.assertTrue(all("light" in c and "dark" in c for c in deck))
        self.assertEqual(Counter(c["light"]["value"] for c in deck)["flip"], 8)
        self.assertEqual(Counter(c["dark"]["value"] for c in deck)["flip"], 8)

    def test_flip_face_hides_opposite_side(self):
        card = create_flip_deck(random.Random(3))[0]
        visible = flip_face(card, "light")
        self.assertEqual(set(visible), {"id", "color", "value"})
        self.assertEqual(visible["id"], card["id"])
        with self.assertRaises(ValueError):
            flip_face(card, "other")

    def test_no_mercy_deck_size_and_actions(self):
        deck = create_no_mercy_deck(random.Random(4))
        self.assertEqual(len(deck), 168)
        self.assertEqual(len({c["id"] for c in deck}), 168)
        self.assertEqual(sum(c["value"] == "wild10" for c in deck), 4)

    def test_penalty_stacking(self):
        self.assertTrue(can_stack_no_mercy(4, {"value": "wild6"}))
        self.assertFalse(can_stack_no_mercy(10, {"value": "draw2"}))
        self.assertFalse(can_stack_no_mercy(0, {"value": "wild10"}))

    def test_elimination_threshold(self):
        self.assertFalse(is_eliminated_no_mercy([None] * 24))
        self.assertTrue(is_eliminated_no_mercy([None] * 25))


if __name__ == "__main__":
    unittest.main()
