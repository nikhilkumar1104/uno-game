"""Gameplay regressions for UNO Flip and Show 'Em No Mercy."""
import pytest

from game_engine import (
    GameRuleError, make_card, start_game, play_card, draw_card, public_state,
    resolve_roulette, set_game_mode,
)
from variant_decks import create_flip_deck


def room(mode="flip", count=2, playing=True):
    players = [
        dict(id=f"p{i}", username=f"Player {i}", avatar="ember",
             connected=True, spectator=False, left=False, hand=[],
             said_uno=False, socket_id=f"sid{i}", eliminated=False, team=None)
        for i in range(count)
    ]
    value = {
        "code": "TEST11", "host_id": "p0", "mode": mode,
        "status": "playing" if playing else "lobby", "play_format": "individual",
        "players": players, "chat": [], "leaderboard": {}, "match_history": [],
        "round_number": 1, "game_id": "g1", "rules": {},
        "draw_pile": [make_card("yellow", "3") for _ in range(100)],
        "discard_pile": [make_card("red", "5")], "direction": 1,
        "current_index": 0, "current_color": "red",
        "winner": None, "match_champion": None, "events": [], "move_count": 0,
        "uno_pending_player_id": None, "uno_catch_available_at": None,
        "drawn_card_id": None, "drawn_by_id": None,
        "pending_draw": 0, "pending_draw_type": None, "wild4_challenge": None,
        "last_challenge_result": None, "rematch_choices": {},
        "rematch_deadline": None, "updated_at": 0,
        "roulette_target_id": None, "flip_side": "light", "eliminated_cards": [],
    }
    return value


def test_flip_deals_two_faced_cards_but_keeps_opposite_face_private():
    value = room(playing=False)
    set_game_mode(value, "flip")
    start_game(value)
    assert value["flip_side"] == "light"
    assert len(value["draw_pile"]) == 97
    assert len(value["players"][0]["hand"]) == 7
    assert all("dark" in c for c in value["players"][0]["hand"])
    view = public_state(value, "p0")
    assert view["flipSide"] == "light"
    assert set(view["hand"][0]) == {"id", "color", "value"}
    assert set(view["topDiscard"]) == {"id", "color", "value"}


def test_flip_card_switches_all_hands_and_top_discard():
    value = room()
    card = {
        "id": "flip-one", "light": {"color": "red", "value": "flip"},
        "dark": {"color": "pink", "value": "3"},
        "color": "red", "value": "flip",
    }
    back = {
        "id": "hidden", "light": {"color": "blue", "value": "8"},
        "dark": {"color": "purple", "value": "2"},
        "color": "blue", "value": "8",
    }
    value["players"][0]["hand"] = [card, back]
    play_card(value, "p0", "flip-one")
    assert value["flip_side"] == "dark"
    assert value["discard_pile"][-1]["value"] == "3"
    assert value["players"][0]["hand"][0]["color"] == "purple"
    assert value["current_color"] == "pink"
    assert public_state(value, "p1")["topDiscard"]["color"] == "pink"


def test_no_mercy_penalties_stack_if_equal_or_greater():
    value = room("no_mercy", count=3)
    card = make_card("red", "draw2")
    value["players"][0]["hand"] = [card, make_card("blue", "9")]
    target_stack = make_card("yellow", "draw4")
    value["players"][1]["hand"] = [target_stack, make_card("blue", "7")]
    value["players"][2]["hand"] = [make_card("green", "3")]
    play_card(value, "p0", card["id"])
    assert value["pending_draw"] == 2
    assert value["pending_draw_type"] == "draw2"
    play_card(value, "p1", target_stack["id"])
    assert value["pending_draw"] == 6
    assert value["pending_draw_type"] == "draw4"
    draw_card(value, "p2")
    assert len(value["players"][2]["hand"]) == 7
    assert value["pending_draw"] == 0


def test_no_mercy_knocks_out_player_at_25_cards():
    value = room("no_mercy")
    value["pending_draw"] = 2
    value["pending_draw_type"] = "draw2"
    value["players"][0]["hand"] = [make_card("red", "4") for _ in range(24)]
    value["players"][1]["hand"] = [make_card("blue", "2")]
    draw_card(value, "p0")
    assert value["players"][0]["eliminated"]
    assert value["status"] == "finished"
    assert value["winner"]["id"] == "p1"
    assert value["winner"]["points"] >= 250


def test_no_mercy_seven_requires_another_player():
    value = room("no_mercy")
    seven = make_card("red", "7")
    value["players"][0]["hand"] = [seven, make_card("yellow", "3")]
    value["players"][1]["hand"] = [make_card("blue", "1"), make_card("green", "2")]
    with pytest.raises(GameRuleError, match="Choose another player"):
        play_card(value, "p0", seven["id"])
    play_card(value, "p0", seven["id"], target_player_id="p1")
    assert [c["value"] for c in value["players"][0]["hand"]] == ["1", "2"]


def test_no_mercy_roulette_affected_player_selects_color():
    value = room("no_mercy")
    roulette = make_card("wild", "wild_color_roulette")
    value["players"][0]["hand"] = [roulette, make_card("blue", "8")]
    value["players"][1]["hand"] = [make_card("green", "2")]
    value["draw_pile"] = [make_card("blue", "3"), make_card("yellow", "6")]
    play_card(value, "p0", roulette["id"])
    assert value["roulette_target_id"] == "p1"
    with pytest.raises(GameRuleError):
        resolve_roulette(value, "p0", "blue")
    resolve_roulette(value, "p1", "blue")
    assert value["roulette_target_id"] is None
    assert len(value["players"][1]["hand"]) == 3


def test_no_mercy_draws_until_matching_card_and_auto_plays():
    value = room("no_mercy")
    value["players"][0]["hand"] = [make_card("blue", "9")]
    value["players"][1]["hand"] = [make_card("green", "2")]
    value["draw_pile"] = [make_card("red", "8"), make_card("yellow", "1")]
    draw_card(value, "p0")
    assert value["discard_pile"][-1]["value"] == "8"
    assert value["current_index"] == 1


def test_no_mercy_discard_all_plays_matching_color_cards():
    value = room("no_mercy")
    all_card = make_card("red", "discard_all")
    red = make_card("red", "9")
    blue = make_card("blue", "2")
    value["players"][0]["hand"] = [all_card, red, blue]
    value["players"][1]["hand"] = [make_card("yellow", "3")]
    play_card(value, "p0", all_card["id"])
    assert [c["id"] for c in value["players"][0]["hand"]] == [blue["id"]]
    assert len(value["discard_pile"]) == 3


def test_variant_lobby_mode_rejects_invalid_and_team_format():
    value = room(playing=False)
    set_game_mode(value, "no_mercy")
    assert value["mode"] == "no_mercy"
    with pytest.raises(GameRuleError):
        set_game_mode(value, "unknown")
