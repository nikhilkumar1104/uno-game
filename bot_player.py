"""Fair, server-side computer player decisions for UNO Live."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from game_engine import (
    COLORS,
    accept_wild4,
    card_is_playable,
    catch_uno,
    challenge_wild4,
    current_player,
    declare_uno,
    draw_card,
    pass_turn,
    play_card,
)


def _classic_wild4_is_legal(
    room: dict[str, Any], hand: list[dict[str, str]], card: dict[str, str]
) -> bool:
    if room.get("mode", "classic") != "classic" or card["value"] != "wild4":
        return True
    return not any(
        other["id"] != card["id"] and other["color"] == room.get("current_color")
        for other in hand
    )


def legal_bot_cards(room: dict[str, Any], player: dict[str, Any]) -> list[dict[str, str]]:
    """Return legal cards without giving the bot permission to bluff an illegal +4."""
    return [
        card
        for card in player["hand"]
        if card_is_playable(room, player, card)
        and _classic_wild4_is_legal(room, player["hand"], card)
    ]


def choose_color(hand: list[dict[str, str]], rng: random.Random) -> str:
    counts = Counter(card["color"] for card in hand if card["color"] in COLORS)
    if not counts:
        return rng.choice(list(COLORS))
    best_count = max(counts.values())
    return rng.choice([color for color, count in counts.items() if count == best_count])


def choose_card(cards: list[dict[str, str]], rng: random.Random) -> dict[str, str]:
    """Use visible hand information only and preserve flexible wilds when possible."""
    weights = {"draw2": 38, "skip": 34, "reverse": 30, "wild": 15, "wild4": 18}
    score = lambda card: weights.get(card["value"], int(card["value"]) if card["value"].isdigit() else 0)
    best_score = max(score(card) for card in cards)
    candidates = [
        card
        for card in cards
        if score(card) == best_score
    ]
    return rng.choice(candidates)


def perform_bot_turn(
    room: dict[str, Any], rng: random.Random | None = None
) -> dict[str, Any] | None:
    """Resolve one computer decision using the same engine functions as humans."""
    rng = rng or random.SystemRandom()
    if room.get("status") != "playing":
        return None

    challenge = room.get("wild4_challenge")
    if challenge:
        target = next(
            (player for player in room["players"] if player["id"] == challenge["target_id"]),
            None,
        )
        if not target or not target.get("is_bot"):
            return None
        # The choice is intentionally independent of the hidden legality flag.
        if rng.random() < 0.35:
            challenge_wild4(room, target["id"])
            return {
                "message": room.get("last_challenge_result") or "The computer resolved the challenge.",
                "effects": [],
            }
        accept_wild4(room, target["id"])
        return {"message": f"{target['username']} accepted the Wild Draw Four.", "effects": []}

    player = current_player(room)
    if not player or not player.get("is_bot") or not player.get("connected"):
        return None

    offender_id = room.get("uno_pending_player_id")
    if offender_id and offender_id != player["id"] and rng.random() < 0.7:
        offender_name = catch_uno(room, player["id"])
        return {
            "message": f"{player['username']} caught {offender_name}. Two-card penalty!",
            "effects": ["catch"],
        }

    playable = legal_bot_cards(room, player)
    if playable:
        card = choose_card(playable, rng)
        chosen_color = choose_color(
            [item for item in player["hand"] if item["id"] != card["id"]], rng
        ) if card["color"] == "wild" else None
        play_card(room, player["id"], card["id"], chosen_color)
        effects = ["play"]
        message = None
        if room["status"] == "playing" and len(player["hand"]) == 1:
            declare_uno(room, player["id"])
            effects.append("uno")
            message = f"{player['username']} called UNO!"
        return {"message": message, "effects": effects}

    draw_card(room, player["id"])
    if room["status"] != "playing" or room.get("drawn_by_id") != player["id"]:
        return {"message": None, "effects": []}

    drawn = next(
        (card for card in legal_bot_cards(room, player) if card["id"] == room.get("drawn_card_id")),
        None,
    )
    if not drawn:
        pass_turn(room, player["id"])
        return {"message": None, "effects": []}

    chosen_color = choose_color(
        [item for item in player["hand"] if item["id"] != drawn["id"]], rng
    ) if drawn["color"] == "wild" else None
    play_card(room, player["id"], drawn["id"], chosen_color)
    effects = ["play"]
    message = None
    if room["status"] == "playing" and len(player["hand"]) == 1:
        declare_uno(room, player["id"])
        effects.append("uno")
        message = f"{player['username']} called UNO!"
    return {"message": message, "effects": effects}
