"""Server-authoritative UNO rules and viewer-safe state projection."""

from __future__ import annotations

import random
import time
import uuid
from typing import Any


COLORS = ("red", "yellow", "green", "blue")
ACTION_VALUES = ("skip", "reverse", "draw2")
WILD_VALUES = ("wild", "wild4")


class GameRuleError(ValueError):
    """Raised when a client requests an action that UNO rules do not allow."""


def make_card(color: str, value: str) -> dict[str, str]:
    return {"id": uuid.uuid4().hex, "color": color, "value": value}


def create_deck(rng: random.Random | None = None) -> list[dict[str, str]]:
    deck: list[dict[str, str]] = []
    for color in COLORS:
        deck.append(make_card(color, "0"))
        for number in range(1, 10):
            deck.extend((make_card(color, str(number)), make_card(color, str(number))))
        for action in ACTION_VALUES:
            deck.extend((make_card(color, action), make_card(color, action)))
    for _ in range(4):
        deck.extend((make_card("wild", "wild"), make_card("wild", "wild4")))

    (rng or random.SystemRandom()).shuffle(deck)
    return deck


def active_players(room: dict[str, Any]) -> list[dict[str, Any]]:
    return [player for player in room["players"] if not player["spectator"]]


def current_player(room: dict[str, Any]) -> dict[str, Any] | None:
    players = active_players(room)
    if not players:
        return None
    room["current_index"] %= len(players)
    return players[room["current_index"]]


def _clear_draw_window(room: dict[str, Any]) -> None:
    room["drawn_card_id"] = None
    room["drawn_by_id"] = None


def advance_turn(room: dict[str, Any], steps: int = 1) -> None:
    players = active_players(room)
    if not players:
        return
    room["current_index"] = (
        room["current_index"] + room["direction"] * steps
    ) % len(players)
    _clear_draw_window(room)


def ensure_connected_turn(room: dict[str, Any]) -> None:
    """Move past disconnected seats while retaining their hand for reconnection."""
    players = active_players(room)
    if not players or not any(player["connected"] for player in players):
        return
    for _ in range(len(players)):
        player = current_player(room)
        if player and player["connected"]:
            return
        advance_turn(room)


def _reshuffle_discard(room: dict[str, Any]) -> None:
    if len(room["discard_pile"]) <= 1:
        raise GameRuleError("No cards are available to draw.")
    top = room["discard_pile"].pop()
    recycled = room["discard_pile"]
    random.SystemRandom().shuffle(recycled)
    room["draw_pile"] = recycled
    room["discard_pile"] = [top]


def _draw_one(room: dict[str, Any]) -> dict[str, str]:
    if not room["draw_pile"]:
        _reshuffle_discard(room)
    return room["draw_pile"].pop()


def _draw_many(room: dict[str, Any], player: dict[str, Any], count: int) -> None:
    for _ in range(count):
        player["hand"].append(_draw_one(room))
    player["said_uno"] = False


def can_play(card: dict[str, str], top_card: dict[str, str], current_color: str) -> bool:
    return (
        card["color"] == "wild"
        or card["color"] == current_color
        or card["value"] == top_card["value"]
    )


def _wild4_is_legal(
    hand: list[dict[str, str]], selected_card: dict[str, str], current_color: str
) -> bool:
    return not any(
        card["id"] != selected_card["id"] and card["color"] == current_color
        for card in hand
    )


def card_is_playable(
    room: dict[str, Any], player: dict[str, Any], card: dict[str, str]
) -> bool:
    top = room["discard_pile"][-1]
    if not can_play(card, top, room["current_color"]):
        return False
    if card["value"] == "wild4":
        return _wild4_is_legal(player["hand"], card, room["current_color"])
    return True


def _log(room: dict[str, Any], message: str) -> None:
    room["events"].append(message)
    room["events"] = room["events"][-80:]
    room["updated_at"] = time.time()


def _resolve_missed_uno(room: dict[str, Any]) -> None:
    pending_id = room.get("uno_pending_player_id")
    if not pending_id:
        return
    player = next(
        (candidate for candidate in room["players"] if candidate["id"] == pending_id),
        None,
    )
    room["uno_pending_player_id"] = None
    if not player or player["said_uno"] or len(player["hand"]) != 1:
        return
    _draw_many(room, player, 2)
    _log(room, f"{player['username']} missed UNO and drew 2 cards.")


def start_game(room: dict[str, Any]) -> None:
    connected = [
        player
        for player in room["players"]
        if not player["spectator"] and player["connected"]
    ]
    if len(connected) < 2:
        raise GameRuleError("At least 2 connected players are required.")
    if len(connected) > 6:
        raise GameRuleError("A room supports at most 6 active players.")

    connected_ids = {player["id"] for player in connected}
    for player in room["players"]:
        player["spectator"] = player["id"] not in connected_ids
        player["hand"] = []
        player["said_uno"] = False

    room.update(
        {
            "status": "playing",
            "game_id": uuid.uuid4().hex,
            "draw_pile": create_deck(),
            "discard_pile": [],
            "direction": 1,
            "current_index": 0,
            "current_color": None,
            "winner": None,
            "events": [],
            "move_count": 0,
            "uno_pending_player_id": None,
            "drawn_card_id": None,
            "drawn_by_id": None,
        }
    )

    for _ in range(7):
        for player in active_players(room):
            player["hand"].append(_draw_one(room))

    first = _draw_one(room)
    while first["color"] == "wild" or first["value"] in ACTION_VALUES:
        room["draw_pile"].insert(0, first)
        random.SystemRandom().shuffle(room["draw_pile"])
        first = _draw_one(room)
    room["discard_pile"].append(first)
    room["current_color"] = first["color"]
    _log(room, "The match started. Seven cards were dealt to each player.")


def _require_turn(room: dict[str, Any], player_id: str) -> dict[str, Any]:
    if room["status"] != "playing":
        raise GameRuleError("The game is not active.")
    ensure_connected_turn(room)
    player = current_player(room)
    if not player or player["id"] != player_id:
        raise GameRuleError("It is not your turn.")
    return player


def _finish_match(room: dict[str, Any], player: dict[str, Any]) -> None:
    room["status"] = "finished"
    room["winner"] = {"id": player["id"], "username": player["username"]}
    room["uno_pending_player_id"] = None
    board = room.setdefault("leaderboard", {})
    row = board.setdefault(
        player["id"], {"player_id": player["id"], "username": player["username"], "wins": 0}
    )
    row["username"] = player["username"]
    row["wins"] += 1
    room.setdefault("match_history", []).append(
        {
            "game_id": room["game_id"],
            "winner": player["username"],
            "players": [seat["username"] for seat in active_players(room)],
            "moves": room["move_count"],
            "finished_at": int(time.time()),
        }
    )
    room["match_history"] = room["match_history"][-12:]
    _log(room, f"{player['username']} won the match.")


def _apply_card_effect(room: dict[str, Any], card: dict[str, str]) -> None:
    players = active_players(room)
    if card["value"] == "reverse":
        room["direction"] *= -1
        advance_turn(room, 2 if len(players) == 2 else 1)
    elif card["value"] == "skip":
        advance_turn(room, 2)
    elif card["value"] in ("draw2", "wild4"):
        penalty = 2 if card["value"] == "draw2" else 4
        advance_turn(room)
        ensure_connected_turn(room)
        target = current_player(room)
        if target:
            _draw_many(room, target, penalty)
            _log(room, f"{target['username']} drew {penalty} cards and was skipped.")
        advance_turn(room)
    else:
        advance_turn(room)
    ensure_connected_turn(room)


def play_card(
    room: dict[str, Any], player_id: str, card_id: str, chosen_color: str | None = None
) -> None:
    player = _require_turn(room, player_id)
    _resolve_missed_uno(room)

    card = next((item for item in player["hand"] if item["id"] == card_id), None)
    if not card:
        raise GameRuleError("That card is not in your hand.")
    if room.get("drawn_by_id") == player_id and room.get("drawn_card_id") != card_id:
        raise GameRuleError("After drawing, only the drawn card may be played.")
    if not card_is_playable(room, player, card):
        if card["value"] == "wild4":
            raise GameRuleError("Wild Draw Four is legal only when you hold no card of the current color.")
        raise GameRuleError("That card cannot be played now.")
    if card["color"] == "wild" and chosen_color not in COLORS:
        raise GameRuleError("Choose red, yellow, green, or blue.")

    player["hand"].remove(card)
    room["discard_pile"].append(card)
    room["current_color"] = chosen_color if card["color"] == "wild" else card["color"]
    player["said_uno"] = False
    room["move_count"] += 1
    _clear_draw_window(room)
    color_note = f" and chose {chosen_color}" if card["color"] == "wild" else ""
    _log(room, f"{player['username']} played {card['value']}{color_note}.")

    if not player["hand"]:
        _finish_match(room, player)
        return
    if len(player["hand"]) == 1:
        room["uno_pending_player_id"] = player["id"]
        _log(room, f"{player['username']} has one card remaining.")

    _apply_card_effect(room, card)


def draw_card(room: dict[str, Any], player_id: str) -> None:
    player = _require_turn(room, player_id)
    _resolve_missed_uno(room)
    if room.get("drawn_by_id") == player_id:
        raise GameRuleError("You already drew a card this turn.")

    card = _draw_one(room)
    player["hand"].append(card)
    player["said_uno"] = False
    room["move_count"] += 1
    _log(room, f"{player['username']} drew one card.")
    if card_is_playable(room, player, card):
        room["drawn_by_id"] = player_id
        room["drawn_card_id"] = card["id"]
    else:
        advance_turn(room)
        ensure_connected_turn(room)


def pass_turn(room: dict[str, Any], player_id: str) -> None:
    player = _require_turn(room, player_id)
    _resolve_missed_uno(room)
    if room.get("drawn_by_id") != player_id:
        raise GameRuleError("You may pass only after drawing a playable card.")
    _log(room, f"{player['username']} kept the drawn card and passed.")
    advance_turn(room)
    ensure_connected_turn(room)


def declare_uno(room: dict[str, Any], player_id: str) -> None:
    if room["status"] != "playing":
        raise GameRuleError("The game is not active.")
    player = next(
        (candidate for candidate in active_players(room) if candidate["id"] == player_id),
        None,
    )
    if not player or len(player["hand"]) != 1:
        raise GameRuleError("You can declare UNO only with exactly one card.")
    player["said_uno"] = True
    if room.get("uno_pending_player_id") == player_id:
        room["uno_pending_player_id"] = None
    _log(room, f"{player['username']} declared UNO!")


def public_state(room: dict[str, Any], viewer_id: str) -> dict[str, Any]:
    ensure_connected_turn(room)
    viewer = next((player for player in room["players"] if player["id"] == viewer_id), None)
    turn = current_player(room) if room["status"] == "playing" else None
    top_card = room["discard_pile"][-1] if room.get("discard_pile") else None
    playable_ids: list[str] = []
    if viewer and turn and viewer["id"] == turn["id"] and not viewer["spectator"]:
        if room.get("drawn_by_id") == viewer_id:
            playable_ids = [room["drawn_card_id"]]
        else:
            playable_ids = [
                card["id"] for card in viewer["hand"] if card_is_playable(room, viewer, card)
            ]

    leaderboard = sorted(
        room.get("leaderboard", {}).values(),
        key=lambda row: (-row["wins"], row["username"].casefold()),
    )
    return {
        "code": room["code"],
        "status": room["status"],
        "hostId": room["host_id"],
        "currentPlayerId": turn["id"] if turn else None,
        "currentColor": room.get("current_color"),
        "direction": room.get("direction", 1),
        "topDiscard": top_card,
        "drawPileCount": len(room.get("draw_pile", [])),
        "players": [
            {
                "id": player["id"],
                "username": player["username"],
                "avatar": player["avatar"],
                "isHost": player["id"] == room["host_id"],
                "connected": player["connected"],
                "spectator": player["spectator"],
                "cardCount": len(player["hand"]),
                "saidUno": player["said_uno"],
            }
            for player in room["players"]
        ],
        "hand": [] if not viewer or viewer["spectator"] else viewer["hand"],
        "playableCardIds": playable_ids,
        "canPass": room.get("drawn_by_id") == viewer_id,
        "mustDeclareUno": room.get("uno_pending_player_id") == viewer_id,
        "events": room.get("events", [])[-30:],
        "chat": room.get("chat", [])[-60:],
        "leaderboard": leaderboard,
        "matchHistory": room.get("match_history", [])[-10:][::-1],
        "winner": room.get("winner"),
    }


def lobby_state(room: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": room["code"],
        "status": room["status"],
        "hostId": room["host_id"],
        "players": [
            {
                "id": player["id"],
                "username": player["username"],
                "avatar": player["avatar"],
                "isHost": player["id"] == room["host_id"],
                "connected": player["connected"],
                "spectator": player["spectator"],
                "cardCount": len(player["hand"]),
                "saidUno": player["said_uno"],
            }
            for player in room["players"]
        ],
        "chat": room.get("chat", [])[-60:],
    }
