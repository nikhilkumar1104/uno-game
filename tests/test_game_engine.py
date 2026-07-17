import random

import pytest

from game_engine import (
    GameRuleError,
    accept_wild4,
    catch_uno,
    challenge_wild4,
    create_deck,
    declare_uno,
    draw_card,
    make_card,
    play_card,
    public_state,
    queue_rematch,
    rematch_all_ready,
    start_game,
)


def room_with_players(count=2, mode="classic"):
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
        "mode": mode,
        "round_number": 0,
        "players": players,
        "chat": [],
        "leaderboard": {},
        "match_history": [],
        "updated_at": 0,
    }


def rig_playing_room(mode="classic"):
    room = room_with_players(mode=mode)
    room.update(
        {
            "status": "playing",
            "game_id": "game1",
            "round_number": 1,
            "draw_pile": [make_card("yellow", "3") for _ in range(40)],
            "discard_pile": [make_card("red", "5")],
            "direction": 1,
            "current_index": 0,
            "current_color": "red",
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


def test_classic_draw_two_penalizes_and_skips_next_player():
    room = rig_playing_room()
    draw_two = make_card("red", "draw2")
    room["players"][0]["hand"] = [draw_two, make_card("blue", "1")]
    room["players"][1]["hand"] = [make_card("green", "2")]
    play_card(room, "p0", draw_two["id"])
    assert len(room["players"][1]["hand"]) == 3
    assert room["current_index"] == 0
    assert room["pending_draw"] == 0


def test_catch_uno_adds_two_cards_during_open_window():
    room = rig_playing_room()
    playable = make_card("red", "7")
    room["players"][0]["hand"] = [playable, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("yellow", "1"), make_card("green", "4")]
    play_card(room, "p0", playable["id"])
    assert room["uno_pending_player_id"] == "p0"
    catch_uno(room, "p1")
    assert len(room["players"][0]["hand"]) == 3
    assert room["uno_pending_player_id"] is None


def test_uno_catch_window_closes_when_next_action_begins():
    room = rig_playing_room()
    playable = make_card("red", "7")
    room["players"][0]["hand"] = [playable, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("yellow", "1"), make_card("green", "4")]
    play_card(room, "p0", playable["id"])
    draw_card(room, "p1")
    assert len(room["players"][0]["hand"]) == 1
    with pytest.raises(GameRuleError, match="nobody"):
        catch_uno(room, "p1")


def test_rejected_action_does_not_close_uno_catch_window():
    room = rig_playing_room()
    playable = make_card("red", "7")
    blocked = make_card("blue", "8")
    room["players"][0]["hand"] = [playable, make_card("blue", "9")]
    room["players"][1]["hand"] = [blocked, make_card("green", "4")]
    play_card(room, "p0", playable["id"])

    with pytest.raises(GameRuleError, match="cannot be played"):
        play_card(room, "p1", blocked["id"])

    assert room["uno_pending_player_id"] == "p0"
    catch_uno(room, "p1")
    assert len(room["players"][0]["hand"]) == 3


def test_declared_uno_closes_catch_window_and_is_idempotent():
    room = rig_playing_room()
    playable = make_card("red", "7")
    room["players"][0]["hand"] = [playable, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("yellow", "1")]
    play_card(room, "p0", playable["id"])
    assert declare_uno(room, "p0") is True
    assert declare_uno(room, "p0") is False
    assert room["players"][0]["said_uno"] is True
    with pytest.raises(GameRuleError, match="nobody"):
        catch_uno(room, "p1")


def test_illegal_wild_four_challenge_succeeds_and_offender_draws_four():
    room = rig_playing_room()
    wild_four = make_card("wild", "wild4")
    room["players"][0]["hand"] = [wild_four, make_card("red", "8"), make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("green", "2")]
    play_card(room, "p0", wild_four["id"], "blue")
    assert room["wild4_challenge"]["was_legal"] is False
    assert challenge_wild4(room, "p1") is True
    assert len(room["players"][0]["hand"]) == 6
    assert len(room["players"][1]["hand"]) == 1
    assert room["current_index"] == 1


def test_legal_wild_four_failed_challenge_draws_six_and_skips():
    room = rig_playing_room()
    wild_four = make_card("wild", "wild4")
    room["players"][0]["hand"] = [wild_four, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("green", "2")]
    play_card(room, "p0", wild_four["id"], "blue")
    assert challenge_wild4(room, "p1") is False
    assert len(room["players"][1]["hand"]) == 7
    assert room["current_index"] == 0


def test_accept_wild_four_draws_four_and_skips():
    room = rig_playing_room()
    wild_four = make_card("wild", "wild4")
    room["players"][0]["hand"] = [wild_four, make_card("blue", "9")]
    room["players"][1]["hand"] = [make_card("green", "2")]
    play_card(room, "p0", wild_four["id"], "blue")
    accept_wild4(room, "p1")
    assert len(room["players"][1]["hand"]) == 5
    assert room["current_index"] == 0


def test_wild_mode_stacks_draw_twos_and_collects_total_penalty():
    room = rig_playing_room("wild")
    first = make_card("red", "draw2")
    second = make_card("blue", "draw2")
    room["players"][0]["hand"] = [first, make_card("blue", "1")]
    room["players"][1]["hand"] = [second, make_card("green", "3")]
    play_card(room, "p0", first["id"])
    assert room["pending_draw"] == 2
    play_card(room, "p1", second["id"])
    assert room["pending_draw"] == 4
    draw_card(room, "p0")
    assert len(room["players"][0]["hand"]) == 5
    assert room["pending_draw"] == 0
    assert room["current_index"] == 1


def test_wild_mode_allows_draw_two_on_wild_four_but_not_reverse_order():
    room = rig_playing_room("wild")
    wild_four = make_card("wild", "wild4")
    draw_two = make_card("green", "draw2")
    room["players"][0]["hand"] = [wild_four, make_card("blue", "1")]
    room["players"][1]["hand"] = [draw_two, make_card("green", "3")]
    play_card(room, "p0", wild_four["id"], "blue")
    play_card(room, "p1", draw_two["id"])
    assert room["pending_draw"] == 6
    assert room["pending_draw_type"] == "draw2"

    room = rig_playing_room("wild")
    draw_two = make_card("red", "draw2")
    wild_four = make_card("wild", "wild4")
    room["players"][0]["hand"] = [draw_two, make_card("blue", "1")]
    room["players"][1]["hand"] = [wild_four, make_card("green", "3")]
    play_card(room, "p0", draw_two["id"])
    with pytest.raises(GameRuleError, match="stacked"):
        play_card(room, "p1", wild_four["id"], "blue")


def test_round_winner_receives_official_card_points_and_rematch_window():
    room = rig_playing_room()
    winning_card = make_card("red", "7")
    room["players"][0]["hand"] = [winning_card]
    room["players"][1]["hand"] = [
        make_card("blue", "9"),
        make_card("green", "skip"),
        make_card("wild", "wild"),
    ]
    play_card(room, "p0", winning_card["id"])
    assert room["status"] == "finished"
    assert room["winner"]["points"] == 79
    assert room["leaderboard"]["p0"]["points"] == 79
    assert room["rematch_deadline"] is not None
    queue_rematch(room, "p0")
    assert rematch_all_ready(room) is False
    queue_rematch(room, "p1")
    assert rematch_all_ready(room) is True


def test_public_state_hides_hands_and_exposes_only_viewer_actions():
    room = rig_playing_room()
    red = make_card("red", "8")
    blue = make_card("blue", "1")
    room["players"][0]["hand"] = [red, blue]
    room["players"][1]["hand"] = [make_card("green", "2")]
    state = public_state(room, "p0")
    assert state["hand"] == [red, blue]
    assert state["playableCardIds"] == [red["id"]]
    assert "hand" not in state["players"][1]
