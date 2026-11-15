"""Deck builders and rule metadata for the upcoming Flip and No Mercy modes.

Cards use the existing engine's id/color/value representation. Flip cards
carry two faces; only the active face should be exposed to opponents.
"""
import random
import uuid

FLIP_LIGHT_COLORS = ("red", "yellow", "green", "blue")
FLIP_DARK_COLORS = ("pink", "teal", "orange", "purple")
NO_MERCY_COLORS = ("red", "yellow", "green", "blue")
NO_MERCY_ELIMINATION_COUNT = 25
NO_MERCY_DRAW_PENALTIES = {"draw2": 2, "draw4": 4, "wild6": 6, "wild10": 10}


def _card(color, value):
    return {"id": uuid.uuid4().hex, "color": color, "value": value}


def create_flip_deck(rng=None):
    """112 physical double-faced cards, each with light and dark faces."""
    light = []
    dark = []
    for color in FLIP_LIGHT_COLORS:
        light.extend([(color, "1")] * 2)
        for number in range(2, 10):
            light.extend([(color, str(number))] * 2)
        for action in ("draw1", "reverse", "skip", "flip"):
            light.extend([(color, action)] * 2)
    light.extend([("wild", "wild")] * 4)
    light.extend([("wild", "wild2")] * 4)

    for color in FLIP_DARK_COLORS:
        for number in range(1, 10):
            dark.extend([(color, str(number))] * 2)
        for action in ("draw5", "reverse", "skip_everyone", "flip"):
            dark.extend([(color, action)] * 2)
    dark.extend([("wild", "wild")] * 4)
    dark.extend([("wild", "wild_color")] * 4)

    assert len(light) == len(dark) == 112
    (rng or random.SystemRandom()).shuffle(dark)
    deck = [
        {"id": uuid.uuid4().hex,
         "light": {"color": lc, "value": lv},
         "dark": {"color": dc, "value": dv}}
        for (lc, lv), (dc, dv) in zip(light, dark)
    ]
    (rng or random.SystemRandom()).shuffle(deck)
    return deck


def flip_face(card, side):
    """Return the visible face without leaking the opposite face."""
    if side not in ("light", "dark"):
        raise ValueError("Invalid flip side")
    return {"id": card["id"], **card[side]}


def create_no_mercy_deck(rng=None):
    """168-card experimental No Mercy deck; distribution requires rules verification."""
    deck = []
    for color in NO_MERCY_COLORS:
        for number in range(0, 10):
            deck.extend(_card(color, str(number)) for _ in range(2))
        for value in ("skip", "reverse", "draw2", "draw4", "discard_all", "skip_everyone"):
            deck.extend(_card(color, value) for _ in range(3))
    for value in ("wild", "wild6", "wild10", "wild_reverse_draw4"):
        deck.extend(_card("wild", value) for _ in range(4))
    # Explicit count guards against accidentally shipping a malformed deck.
    assert len(deck) == 168
    (rng or random.SystemRandom()).shuffle(deck)
    return deck


def can_stack_no_mercy(pending_penalty, card):
    """A draw card may stack if its penalty is at least the previous penalty."""
    return NO_MERCY_DRAW_PENALTIES.get(card["value"], 0) >= pending_penalty > 0


def is_eliminated_no_mercy(hand):
    return len(hand) >= NO_MERCY_ELIMINATION_COUNT
