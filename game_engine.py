"""Server-authoritative UNO rules and viewer-safe state projection."""

from __future__ import annotations

import random
import time
import uuid
from typing import Any


COLORS = ("red", "yellow", "green", "blue")
ACTION_VALUES = ("skip", "reverse", "draw2")
GAME_MODES = ("classic", "wild")
SCORE_TARGET = 500


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


def _stack_is_legal(room: dict[str, Any], card: dict[str, str]) -> bool:
    pending_type = room.get("pending_draw_type")
    if pending_type == "draw2":
        return card["value"] == "draw2"
    if pending_type == "wild4":
        return card["value"] in ("wild4", "draw2")
    return False


def card_is_playable(
    room: dict[str, Any], player: dict[str, Any], card: dict[str, str]
) -> bool:
    if room.get("pending_draw", 0):
        return room.get("mode") == "wild" and _stack_is_legal(room, card)
    return can_play(card, room["discard_pile"][-1], room["current_color"])


def card_points(card: dict[str, str]) -> int:
    if card["value"].isdigit():
        return int(card["value"])
    if card["value"] in ACTION_VALUES:
        return 20
    return 50


def hand_points(hand: list[dict[str, str]]) -> int:
    return sum(card_points(card) for card in hand)


def _log(room: dict[str, Any], message: str) -> None:
    room.setdefault("events", []).append(message)
    room["events"] = room["events"][-100:]
    room["updated_at"] = time.time()


def _close_uno_window(room: dict[str, Any]) -> None:
    room["uno_pending_player_id"] = None


def _leaderboard_row(room: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
    board = room.setdefault("leaderboard", {})
    row = board.setdefault(
        player["id"],
        {
            "player_id": player["id"],
            "username": player["username"],
            "wins": 0,
            "points": 0,
        },
    )
    row.setdefault("points", 0)
    row.setdefault("wins", 0)
    row["username"] = player["username"]
    return row


def set_game_mode(room: dict[str, Any], mode: str) -> None:
    if room["status"] != "lobby":
        raise GameRuleError("Game mode can be changed only in the lobby.")
    if mode not in GAME_MODES:
        raise GameRuleError("Choose Classic or Wild mode.")
    room["mode"] = mode
    room["updated_at"] = time.time()


def start_game(room: dict[str, Any]) -> None:
    connected = [
        player
        for player in room["players"]
        if not player["spectator"] and player["connected"] and not player.get("left")
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
        if not player["spectator"]:
            _leaderboard_row(room, player)

    room.update(
        {
            "status": "playing",
            "game_id": uuid.uuid4().hex,
            "round_number": room.get("round_number", 0) + 1,
            "draw_pile": create_deck(),
            "discard_pile": [],
            "direction": 1,
            "current_index": 0,
            "current_color": None,
            "winner": None,
            "match_champion": None,
            "events": [],
            "move_count": 0,
            "uno_pending_player_id": None,
            "drawn_card_id": None,
            "drawn_by_id": None,
            "pending_draw": 0,
            "pending_draw_type": None,
            "wild4_challenge": None,
            "last_challenge_result": None,
            "rematch_choices": {},
            "rematch_deadline": None,
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
    label = "Classic" if room.get("mode", "classic") == "classic" else "Wild stacking"
    _log(room, f"Round {room['round_number']} started in {label} mode.")


def _require_turn(room: dict[str, Any], player_id: str) -> dict[str, Any]:
    if room["status"] != "playing":
        raise GameRuleError("The game is not active.")
    ensure_connected_turn(room)
    player = current_player(room)
    if not player or player["id"] != player_id:
        raise GameRuleError("It is not your turn.")
    if room.get("wild4_challenge"):
        raise GameRuleError("Resolve the Wild Draw Four before taking another action.")
    return player


def _finish_round(room: dict[str, Any], player: dict[str, Any]) -> None:
    points = sum(
        hand_points(opponent["hand"])
        for opponent in active_players(room)
        if opponent["id"] != player["id"]
    )
    row = _leaderboard_row(room, player)
    row["wins"] += 1
    row["points"] += points
    room["status"] = "finished"
    room["winner"] = {
        "id": player["id"],
        "username": player["username"],
        "points": points,
        "totalPoints": row["points"],
    }
    room["match_champion"] = (
        {"id": player["id"], "username": player["username"], "points": row["points"]}
        if row["points"] >= SCORE_TARGET
        else None
    )
    room["uno_pending_player_id"] = None
    room["pending_draw"] = 0
    room["pending_draw_type"] = None
    room["wild4_challenge"] = None
    room["rematch_choices"] = {
        seat["id"]: "ready" if seat.get("is_bot") else "pending"
        for seat in active_players(room)
        if seat["connected"]
    }
    room["rematch_deadline"] = time.time() + 10
    room.setdefault("match_history", []).append(
        {
            "game_id": room["game_id"],
            "winner": player["username"],
            "points": points,
            "players": [seat["username"] for seat in active_players(room)],
            "moves": room["move_count"],
            "finished_at": int(time.time()),
            "mode": room.get("mode", "classic"),
        }
    )
    room["match_history"] = room["match_history"][-20:]
    _log(room, f"{player['username']} won the round and earned {points} points.")


def _apply_classic_effect(
    room: dict[str, Any], card: dict[str, str], player: dict[str, Any], wild4_legal: bool
) -> None:
    players = active_players(room)
    if card["value"] == "reverse":
        room["direction"] *= -1
        advance_turn(room, 2 if len(players) == 2 else 1)
    elif card["value"] == "skip":
        advance_turn(room, 2)
    elif card["value"] == "draw2":
        advance_turn(room)
        ensure_connected_turn(room)
        target = current_player(room)
        if target:
            _draw_many(room, target, 2)
            _log(room, f"{target['username']} drew 2 cards and was skipped.")
        advance_turn(room)
    elif card["value"] == "wild4":
        previous_color = room.get("previous_color")
        advance_turn(room)
        ensure_connected_turn(room)
        target = current_player(room)
        if target:
            room["wild4_challenge"] = {
                "offender_id": player["id"],
                "offender_name": player["username"],
                "target_id": target["id"],
                "previous_color": previous_color,
                "was_legal": wild4_legal,
            }
            _log(room, f"{target['username']} must accept 4 cards or challenge the Wild Draw Four.")
    else:
        advance_turn(room)
    ensure_connected_turn(room)


def _apply_wild_effect(room: dict[str, Any], card: dict[str, str]) -> None:
    players = active_players(room)
    if card["value"] == "reverse":
        room["direction"] *= -1
        advance_turn(room, 2 if len(players) == 2 else 1)
    elif card["value"] == "skip":
        advance_turn(room, 2)
    elif card["value"] in ("draw2", "wild4"):
        room["pending_draw"] = room.get("pending_draw", 0) + (2 if card["value"] == "draw2" else 4)
        room["pending_draw_type"] = card["value"]
        advance_turn(room)
        target = current_player(room)
        if target:
            _log(room, f"{target['username']} must stack or draw {room['pending_draw']} cards.")
    else:
        advance_turn(room)
    ensure_connected_turn(room)


def play_card(
    room: dict[str, Any], player_id: str, card_id: str, chosen_color: str | None = None
) -> None:
    player = _require_turn(room, player_id)
    card = next((item for item in player["hand"] if item["id"] == card_id), None)
    if not card:
        raise GameRuleError("That card is not in your hand.")
    if room.get("drawn_by_id") == player_id and room.get("drawn_card_id") != card_id:
        raise GameRuleError("After drawing, only the drawn card may be played.")
    if not card_is_playable(room, player, card):
        if room.get("pending_draw"):
            raise GameRuleError("That card cannot be stacked on the current draw penalty.")
        raise GameRuleError("That card cannot be played now.")
    if card["color"] == "wild" and chosen_color not in COLORS:
        raise GameRuleError("Choose red, yellow, green, or blue.")

    # A rejected/malformed action must not close another player's Catch UNO window.
    _close_uno_window(room)
    previous_color = room["current_color"]
    wild4_legal = _wild4_is_legal(player["hand"], card, previous_color)
    player["hand"].remove(card)
    room["discard_pile"].append(card)
    room["previous_color"] = previous_color
    room["current_color"] = chosen_color if card["color"] == "wild" else card["color"]
    player["said_uno"] = False
    room["move_count"] += 1
    _clear_draw_window(room)
    color_note = f" and chose {chosen_color}" if card["color"] == "wild" else ""
    _log(room, f"{player['username']} played {card['value']}{color_note}.")

    if not player["hand"]:
        _finish_round(room, player)
        return
    if len(player["hand"]) == 1:
        room["uno_pending_player_id"] = player["id"]
        _log(room, f"{player['username']} has one card and must call UNO.")

    if room.get("mode", "classic") == "wild":
        _apply_wild_effect(room, card)
    else:
        _apply_classic_effect(room, card, player, wild4_legal)


def draw_card(room: dict[str, Any], player_id: str) -> None:
    player = _require_turn(room, player_id)
    pending = room.get("pending_draw", 0)
    if pending:
        _close_uno_window(room)
        _draw_many(room, player, pending)
        room["move_count"] += 1
        _log(room, f"{player['username']} drew the {pending}-card stack and was skipped.")
        room["pending_draw"] = 0
        room["pending_draw_type"] = None
        advance_turn(room)
        ensure_connected_turn(room)
        return
    if room.get("drawn_by_id") == player_id:
        raise GameRuleError("You already drew a card this turn.")

    _close_uno_window(room)
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
    if room.get("drawn_by_id") != player_id:
        raise GameRuleError("You may pass only after drawing a playable card.")
    _close_uno_window(room)
    _log(room, f"{player['username']} kept the drawn card and passed.")
    advance_turn(room)
    ensure_connected_turn(room)


def declare_uno(room: dict[str, Any], player_id: str) -> bool:
    if room["status"] != "playing":
        raise GameRuleError("The game is not active.")
    player = next(
        (candidate for candidate in active_players(room) if candidate["id"] == player_id),
        None,
    )
    if not player or len(player["hand"]) != 1:
        raise GameRuleError("You can declare UNO only with exactly one card.")
    if player["said_uno"]:
        return False
    player["said_uno"] = True
    if room.get("uno_pending_player_id") == player_id:
        room["uno_pending_player_id"] = None
    _log(room, f"{player['username']} declared UNO!")
    return True


def catch_uno(room: dict[str, Any], catcher_id: str) -> str:
    if room["status"] != "playing":
        raise GameRuleError("The game is not active.")
    offender_id = room.get("uno_pending_player_id")
    if not offender_id:
        raise GameRuleError("There is nobody to catch right now.")
    if offender_id == catcher_id:
        raise GameRuleError("You cannot catch yourself. Call UNO instead.")
    catcher = next((p for p in active_players(room) if p["id"] == catcher_id), None)
    offender = next((p for p in active_players(room) if p["id"] == offender_id), None)
    if not catcher or not offender or offender["said_uno"] or len(offender["hand"]) != 1:
        room["uno_pending_player_id"] = None
        raise GameRuleError("The UNO catch window has closed.")
    _draw_many(room, offender, 2)
    room["uno_pending_player_id"] = None
    _log(room, f"{catcher['username']} caught {offender['username']}; 2 penalty cards were drawn.")
    return offender["username"]


def _require_challenge_target(room: dict[str, Any], player_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if room["status"] != "playing" or not room.get("wild4_challenge"):
        raise GameRuleError("There is no Wild Draw Four decision to resolve.")
    challenge = room["wild4_challenge"]
    if challenge["target_id"] != player_id:
        raise GameRuleError("Only the challenged next player can make this decision.")
    target = next((p for p in active_players(room) if p["id"] == player_id), None)
    if not target:
        raise GameRuleError("The challenged player is no longer active.")
    return challenge, target


def accept_wild4(room: dict[str, Any], player_id: str) -> None:
    _, target = _require_challenge_target(room, player_id)
    _close_uno_window(room)
    _draw_many(room, target, 4)
    room["wild4_challenge"] = None
    room["move_count"] += 1
    _log(room, f"{target['username']} accepted the Wild Draw Four, drew 4, and was skipped.")
    advance_turn(room)
    ensure_connected_turn(room)


def challenge_wild4(room: dict[str, Any], player_id: str) -> bool:
    challenge, target = _require_challenge_target(room, player_id)
    _close_uno_window(room)
    offender = next(
        (p for p in active_players(room) if p["id"] == challenge["offender_id"]),
        None,
    )
    if not offender:
        raise GameRuleError("The challenged player is no longer active.")
    room["wild4_challenge"] = None
    room["move_count"] += 1
    if challenge["was_legal"]:
        _draw_many(room, target, 6)
        result = f"Challenge failed: {offender['username']} had no {challenge['previous_color']} card. {target['username']} drew 6."
        room["last_challenge_result"] = result
        _log(room, result)
        advance_turn(room)
        ensure_connected_turn(room)
        return False
    _draw_many(room, offender, 4)
    result = f"Challenge succeeded: {offender['username']} held the active color and drew 4."
    room["last_challenge_result"] = result
    _log(room, result)
    return True


def queue_rematch(room: dict[str, Any], player_id: str) -> None:
    if room["status"] != "finished":
        raise GameRuleError("The round has not finished.")
    player = next((p for p in active_players(room) if p["id"] == player_id), None)
    if not player:
        raise GameRuleError("Only round players can join the rematch.")
    room.setdefault("rematch_choices", {})[player_id] = "ready"
    room["updated_at"] = time.time()


def rematch_all_ready(room: dict[str, Any]) -> bool:
    connected = [
        p for p in active_players(room) if p["connected"] and not p.get("left")
    ]
    return len(connected) >= 2 and all(
        room.get("rematch_choices", {}).get(p["id"]) == "ready" for p in connected
    )


def return_to_lobby(room: dict[str, Any]) -> None:
    room["status"] = "lobby"
    room["winner"] = None
    room["rematch_deadline"] = None
    room["rematch_choices"] = {}
    room["wild4_challenge"] = None
    room["pending_draw"] = 0
    room["pending_draw_type"] = None
    for player in room["players"]:
        player["hand"] = []
        player["said_uno"] = False
        if player.get("left") or not player["connected"]:
            player["spectator"] = True
    room["updated_at"] = time.time()


def public_state(room: dict[str, Any], viewer_id: str) -> dict[str, Any]:
    ensure_connected_turn(room)
    viewer = next((player for player in room["players"] if player["id"] == viewer_id), None)
    turn = current_player(room) if room["status"] == "playing" else None
    top_card = room["discard_pile"][-1] if room.get("discard_pile") else None
    challenge = room.get("wild4_challenge")
    playable_ids: list[str] = []
    if (
        viewer
        and turn
        and viewer["id"] == turn["id"]
        and not viewer["spectator"]
        and not challenge
    ):
        if room.get("drawn_by_id") == viewer_id:
            playable_ids = [room["drawn_card_id"]]
        else:
            playable_ids = [
                card["id"] for card in viewer["hand"] if card_is_playable(room, viewer, card)
            ]

    pending_uno_id = room.get("uno_pending_player_id")
    pending_uno_player = next(
        (p for p in active_players(room) if p["id"] == pending_uno_id), None
    )
    catchable = None
    if (
        viewer
        and pending_uno_player
        and viewer["id"] != pending_uno_player["id"]
        and not pending_uno_player["said_uno"]
        and len(pending_uno_player["hand"]) == 1
    ):
        catchable = {
            "id": pending_uno_player["id"],
            "username": pending_uno_player["username"],
        }

    leaderboard = sorted(
        room.get("leaderboard", {}).values(),
        key=lambda row: (-row.get("points", 0), -row.get("wins", 0), row["username"].casefold()),
    )
    challenge_public = None
    if challenge:
        challenge_public = {
            "offenderName": challenge["offender_name"],
            "previousColor": challenge["previous_color"],
            "canRespond": challenge["target_id"] == viewer_id,
        }
    choices = room.get("rematch_choices", {})
    return {
        "code": room["code"],
        "status": room["status"],
        "mode": room.get("mode", "classic"),
        "scoreTarget": SCORE_TARGET,
        "roundNumber": room.get("round_number", 0),
        "hostId": room["host_id"],
        "currentPlayerId": turn["id"] if turn else None,
        "currentColor": room.get("current_color"),
        "direction": room.get("direction", 1),
        "topDiscard": top_card,
        "drawPileCount": len(room.get("draw_pile", [])),
        "pendingDraw": room.get("pending_draw", 0),
        "wild4Challenge": challenge_public,
        "lastChallengeResult": room.get("last_challenge_result"),
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
                "isBot": bool(player.get("is_bot")),
            }
            for player in room["players"]
        ],
        "hand": [] if not viewer or viewer["spectator"] else viewer["hand"],
        "playableCardIds": playable_ids,
        "canPass": room.get("drawn_by_id") == viewer_id and not challenge,
        "mustDeclareUno": pending_uno_id == viewer_id,
        "catchableUnoPlayer": catchable,
        "events": room.get("events", [])[-35:],
        "chat": room.get("chat", [])[-60:],
        "leaderboard": leaderboard,
        "matchHistory": room.get("match_history", [])[-10:][::-1],
        "winner": room.get("winner"),
        "matchChampion": room.get("match_champion"),
        "rematchDeadline": room.get("rematch_deadline"),
        "rematchChoice": choices.get(viewer_id),
        "rematchReadyCount": sum(choice == "ready" for choice in choices.values()),
    }


def lobby_state(room: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": room["code"],
        "status": room["status"],
        "mode": room.get("mode", "classic"),
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
                "isBot": bool(player.get("is_bot")),
            }
            for player in room["players"]
        ],
        "chat": room.get("chat", [])[-60:],
    }
