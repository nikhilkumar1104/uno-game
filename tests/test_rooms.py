import copy

import pytest

from game_engine import GameRuleError, lobby_state, public_state, start_game
from rooms import RoomManager


def test_legacy_snapshot_gets_private_token_and_rejects_old_public_id():
    manager = RoomManager()
    room, player = manager.create_room('old-socket', 'Returning', 'ember')
    legacy = copy.deepcopy(room)
    del legacy['players'][0]['recovery_token']
    restored = RoomManager()
    restored.restore([legacy])
    seat = restored.rooms[room['code']]['players'][0]
    assert seat['recovery_token'] != player['id']
    with pytest.raises(GameRuleError):
        restored.rejoin_room('attacker', room['code'], player['id'])
    with pytest.raises(GameRuleError):
        restored.join_room('attacker', room['code'], 'Returning', 'ember', player['id'])
    assert seat['recovery_token'] not in str(lobby_state(legacy))


def test_valid_recovery_replaces_socket_and_is_idempotent():
    manager = RoomManager()
    room, player = manager.create_room('old-socket', 'Returning', 'ember')
    token = player['recovery_token']
    for _ in range(2):
        restored_room, restored = manager.rejoin_room('new-socket', room['code'], token)
        assert restored['id'] == player['id']
        assert restored_room is room
    with pytest.raises(GameRuleError):
        manager.room_for_sid('old-socket')
    assert manager.room_for_sid('new-socket')[1] is player


def test_tokens_do_not_appear_in_shared_states_or_chat():
    import json
    manager = RoomManager()
    room, host = manager.create_room('host', 'Host', 'ember')
    _, guest, _ = manager.join_room('guest', room['code'], 'Guest', 'bolt')
    manager.add_chat(room, host, 'Hello')
    states = [lobby_state(room)]
    start_game(room)
    states.extend([public_state(room, host['id']), public_state(room, guest['id'])])
    for player in (host, guest):
        assert player['recovery_token'] not in json.dumps(states)


def test_bot_seat_cannot_be_recovered_through_join():
    manager = RoomManager()
    room, human = manager.create_room('human', 'Human', 'ember')
    bot = manager.add_bot(room, human['id'])
    with pytest.raises(GameRuleError, match='reserved seat'):
        manager.join_room('other', room['code'], bot['username'], 'bolt', bot['id'])
