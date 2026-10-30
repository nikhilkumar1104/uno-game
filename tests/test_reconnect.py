import pytest

from app import _reconnect_timeout, create_app
from game_engine import GameRuleError, current_player, ensure_connected_turn, make_card, play_card, public_state, start_game
from rooms import RoomManager


def table():
    manager = RoomManager()
    room, first = manager.create_room('first', 'First', 'ember')
    _, second, _ = manager.join_room('second', room['code'], 'Second', 'wave')
    start_game(room)
    return manager, room, first, second


def test_disconnect_preserves_turn_and_hand_for_30_seconds(monkeypatch):
    manager, room, first, second = table()
    hand = first['hand'].copy()
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('first')
    assert first['reconnect_deadline'] == 130
    assert current_player(room) is first
    assert room['host_id'] == first['id']
    state = public_state(room, second['id'])
    assert state['reconnectWait'] == {'playerId': first['id'], 'username': 'First', 'deadline': 130}
    assert state['playableCardIds'] == []
    manager.rejoin_room('returned', room['code'], first['recovery_token'])
    assert current_player(room) is first and first['hand'] == hand
    assert first['reconnect_deadline'] is None
    assert public_state(room, second['id'])['reconnectWait'] is None


def test_turn_advances_when_grace_expires(monkeypatch):
    manager, room, first, second = table()
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('first')
    monkeypatch.setattr('rooms.time.time', lambda: 130)
    ensure_connected_turn(room)
    assert current_player(room) is second
    assert public_state(room, second['id'])['reconnectWait'] is None


def test_expired_wild_four_is_accepted_instead_of_freezing(monkeypatch):
    manager, room, first, second = table()
    wild = make_card('wild', 'wild4'); first['hand'] = [wild, make_card('green', '2')]
    room['current_color'] = 'red'
    play_card(room, first['id'], wild['id'], 'blue')
    before = len(second['hand'])
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('second')
    assert room['wild4_challenge'] is not None
    monkeypatch.setattr('rooms.time.time', lambda: 130)
    ensure_connected_turn(room)
    assert room['wild4_challenge'] is None
    assert len(second['hand']) == before + 4
    assert current_player(room) is first


def test_expired_stack_is_collected_before_skip(monkeypatch):
    manager, room, first, second = table()
    room['pending_draw'] = 6;room['pending_draw_type'] = 'draw2'
    before = len(first['hand'])
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('first')
    monkeypatch.setattr('rooms.time.time', lambda: 131)
    ensure_connected_turn(room)
    assert len(first['hand']) == before + 6
    assert room['pending_draw'] == 0 and current_player(room) is second


def test_intentional_leave_skips_without_grace():
    manager, room, first, second = table()
    manager.leave('first')
    assert first['reconnect_deadline'] is None
    assert current_player(room) is second


def test_jump_in_is_blocked_during_reconnect_wait(monkeypatch):
    manager, room, first, second = table()
    room['rules']['jump_in'] = True
    top = make_card('red', '2');room['discard_pile'] = [top];room['current_color'] = 'red'
    match = make_card('red', '2'); second['hand'] = [match, make_card('blue', '1')]
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('first')
    with pytest.raises(GameRuleError): play_card(room, second['id'], match['id'])
    assert current_player(room) is first


def test_old_timeout_cannot_skip_a_reconnected_player(tmp_path, monkeypatch):
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'timeout.sqlite3'}"})
    manager = app.extensions['room_manager'];room, first = manager.create_room('first', 'First', 'ember')
    manager.join_room('second', room['code'], 'Second', 'wave');start_game(room)
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('first');deadline = first['reconnect_deadline']
    manager.rejoin_room('returned', room['code'], first['recovery_token'])
    monkeypatch.setattr('app.socketio.sleep', lambda _: None)
    monkeypatch.setattr('rooms.time.time', lambda: 130)
    _reconnect_timeout(app, room['code'], first['id'], deadline)
    assert current_player(room) is first


def test_timeout_worker_advances_turn_and_transfers_host(tmp_path, monkeypatch):
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'worker.sqlite3'}"})
    manager = app.extensions['room_manager'];room, first = manager.create_room('first', 'First', 'ember')
    _, second, _ = manager.join_room('second', room['code'], 'Second', 'wave');start_game(room)
    monkeypatch.setattr('rooms.time.time', lambda: 100)
    manager.disconnect('first');deadline = first['reconnect_deadline']
    monkeypatch.setattr('app.socketio.sleep', lambda _: None)
    monkeypatch.setattr('rooms.time.time', lambda: 130)
    _reconnect_timeout(app, room['code'], first['id'], deadline)
    assert current_player(room) is second and room['host_id'] == second['id']
