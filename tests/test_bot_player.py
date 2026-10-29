import random

from bot_player import legal_bot_cards, perform_bot_turn
from game_engine import make_card, start_game
from rooms import RoomManager


def bot_room():
    return {
        "code": "BOT123",
        "host_id": "human",
        "status": "playing",
        "mode": "classic",
        "round_number": 1,
        "game_id": "bot-game",
        "players": [
            {
                "id": "human",
                "username": "Human",
                "avatar": "ember",
                "socket_id": "sid-human",
                "connected": True,
                "spectator": False,
                "left": False,
                "hand": [make_card("blue", "2"), make_card("green", "3")],
                "said_uno": False,
                "is_bot": False,
            },
            {
                "id": "bot",
                "username": "Computer 1",
                "avatar": "bolt",
                "socket_id": None,
                "connected": True,
                "spectator": False,
                "left": False,
                "hand": [],
                "said_uno": False,
                "is_bot": True,
            },
        ],
        "chat": [],
        "leaderboard": {},
        "match_history": [],
        "draw_pile": [make_card("yellow", "1") for _ in range(20)],
        "discard_pile": [make_card("red", "5")],
        "direction": 1,
        "current_index": 1,
        "current_color": "red",
        "winner": None,
        "match_champion": None,
        "events": [],
        "move_count": 0,
        "uno_pending_player_id": None,
        "uno_catch_available_at": None,
        "drawn_card_id": None,
        "drawn_by_id": None,
        "pending_draw": 0,
        "pending_draw_type": None,
        "wild4_challenge": None,
        "last_challenge_result": None,
        "rematch_choices": {},
        "rematch_deadline": None,
        "updated_at": 0,
    }


def test_host_can_add_computer_and_start_with_one_human():
    manager = RoomManager()
    room, human = manager.create_room("sid", "Solo Player", "ember")
    bot = manager.add_bot(room, human["id"])
    assert bot["is_bot"] is True
    assert bot["connected"] is True

    start_game(room)
    assert room["status"] == "playing"
    assert all(len(player["hand"]) == 7 for player in room["players"])


def test_computer_plays_and_calls_uno_using_normal_engine_rules():
    room = bot_room()
    playable = make_card("red", "9")
    room["players"][1]["hand"] = [playable, make_card("blue", "4")]

    result = perform_bot_turn(room, random.Random(2))

    assert room["discard_pile"][-1]["id"] == playable["id"]
    assert room["players"][1]["said_uno"] is True
    assert room["current_index"] == 0
    assert result["effects"] == ["play", "uno"]


def test_computer_does_not_bluff_illegal_wild_four_in_classic_mode():
    room = bot_room()
    wild_four = make_card("wild", "wild4")
    matching_color = make_card("red", "2")
    room["players"][1]["hand"] = [wild_four, matching_color, make_card("blue", "4")]

    legal = legal_bot_cards(room, room["players"][1])
    assert matching_color in legal
    assert wild_four not in legal

    perform_bot_turn(room, random.Random(4))
    assert room["discard_pile"][-1]["id"] == matching_color["id"]


class CatchEveryTime(random.Random):
    def random(self):
        return 0.0


def test_bot_continues_turn_if_uno_grace_has_not_expired(monkeypatch):
    room = bot_room()
    room['players'][0]['hand'] = [make_card('blue', '2')]
    playable = make_card('red', '9')
    room['players'][1]['hand'] = [playable, make_card('blue', '4')]
    room['uno_pending_player_id'] = 'human'
    room['uno_catch_available_at'] = 101.0
    monkeypatch.setattr('bot_player.time.time', lambda: 100.8)
    result = perform_bot_turn(room, CatchEveryTime())
    assert result['effects'] == ['play', 'uno']
    assert room['discard_pile'][-1] == playable
    assert len(room['players'][0]['hand']) == 1
    assert room['current_index'] == 0


def test_bot_can_catch_at_grace_deadline_then_complete_turn(monkeypatch):
    room = bot_room()
    room['players'][0]['hand'] = [make_card('blue', '2')]
    playable = make_card('red', '9')
    room['players'][1]['hand'] = [playable, make_card('blue', '4')]
    room['uno_pending_player_id'] = 'human'
    room['uno_catch_available_at'] = 101.0
    monkeypatch.setattr('bot_player.time.time', lambda: 101.0)
    result = perform_bot_turn(room, CatchEveryTime())
    assert result['effects'] == ['catch']
    assert len(room['players'][0]['hand']) == 3
    result = perform_bot_turn(room, CatchEveryTime())
    assert 'play' in result['effects']
    assert room['current_index'] == 0


def test_bot_forced_drawn_seven_selects_swap_target():
    room = bot_room()
    room['rules'] = {'seven_zero': True, 'forced_play': True}
    original = [make_card('blue', '2')]
    human_hand = room['players'][0]['hand'].copy()
    room['players'][1]['hand'] = original.copy()
    seven = make_card('red', '7')
    room['draw_pile'] = [seven]
    result = perform_bot_turn(room, random.Random(2))
    assert 'play' in result['effects']
    assert room['discard_pile'][-1] == seven
    assert room['players'][1]['hand'] == human_hand
    assert room['players'][0]['hand'] == original
    assert room['current_index'] == 0
