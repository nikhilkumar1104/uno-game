import random

import pytest

from game_engine import (
    GameRuleError,
    create_deck,
    declare_uno,
    draw_card,
    make_card,
    play_card,
    public_state,
    start_game,
)


def room_with_players(count=2):
    players = []
    for index in range(count):
        players.append(
            {
                "id": f"p{index}",
                "username": f"Player {index}",
                "avatar": "ember",
                "socket_id": f"sid{index}",
                "connected": True,
                "spectator": False,
                "left": False,
                "hand": [],
                "said_uno": False,
            }
        )
    return {
        "code": "ABC123",
        "host_id": "p0",
        "status": "lobby",
        "players": players,
        "chat": [],
        "leaderboard": {},
        "match_history": [],
        "updated_at": 0,
    }


def rig_playing_room():
    room = room_with_players()
    room.update(
        {
            "status": "playing",
            "game_id": "game1",
            "draw_pile": [make_card("yellow", "3") for _ in range(20)],
            "discard_pile": [make_card("red", "5")],
            "direction": 1,
            "current_index": 0,
            "current_color": "red",
            "winner": None,
            "events": [],
            "move_count": 0,
            "uno_pending_player_id": None,
            "drawn_card_id": None,
            "drawn_by_id": None,
        }
    )
    return room


def test_standard_deck_has_108_unique_cards():
    deck = create_deck(random.Random(7))
    assert len(deck) == 108
    assert len({card["id"] for card in deck}) == 108
    assert sum(card["value"] == "wild" for card in deck) == 4
    assert sum(card["value"] == "wild4" for card in deck) == 4


def test_start_deals_seven_cards_and_safe_opening_discard():
    room = room_with_players(3)
    start_game(room)
    assert all(len(player["hand"]) == 7 for player in room["players"])
    assert len(room["draw_pile"]) == 86
    assert room["discard_pile"][-1]["color"] != "wild"
    assert room["discard_pile"][-1]["value"] not in {"skip", "reverse", "draw2"}


def test_draw_two_penalizes_and_skips_next_player():
    room = rig_playing_room()
    draw_two = make_card("red", "draw2")
    room["players"][0]["hand"] = [draw_two, make_card("blue", "1")]
    room["players"][1]["hand"] = [make_card("green", "2")]
    play_card(room, "p0", draw_two["id"])
    assert len(room["players"][1]["hand"]) == 3
    assert room["current_index"] == 0


def test_wild_draw_four_rejected_when_current_color_is_held():
    room = rig_playing_room()
    wild_four = make_card("wild", "wild4")
    red_card = make_card("red", "8")
    room["players"][0]["hand"] = [wild_four, red_card]
    with pytest.raises(GameRuleError, match="Wild Draw Four"):
        play_card(room, "p0", wild_four["id"], "blue")


def test_missed_uno_draws_two_when_next_action_begins():
    room = rig_playing_room()
    playable = make_card("red", "7")
    room["players"][0]["hand"] = [playable, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("yellow", "1"), make_card("green", "4")]
    play_card(room, "p0", playable["id"])
    assert room["uno_pending_player_id"] == "p0"
    draw_card(room, "p1")
    assert len(room["players"][0]["hand"]) == 3
    assert room["uno_pending_player_id"] is None


def test_declared_uno_avoids_penalty():
    room = rig_playing_room()
    playable = make_card("red", "7")
    room["players"][0]["hand"] = [playable, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("yellow", "1"), make_card("green", "4")]
    play_card(room, "p0", playable["id"])
    declare_uno(room, "p0")
    draw_card(room, "p1")
    assert len(room["players"][0]["hand"]) == 1


def test_public_state_hides_opponent_hands_and_marks_legal_cards():
    room = rig_playing_room()
    red = make_card("red", "8")
    blue = make_card("blue", "1")
    room["players"][0]["hand"] = [red, blue]
    room["players"][1]["hand"] = [make_card("green", "2")]
    state = public_state(room, "p0")
    assert state["hand"] == [red, blue]
    assert state["playableCardIds"] == [red["id"]]
    assert "hand" not in state["players"][1]
