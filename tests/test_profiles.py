import json

import pytest

from app import create_app, socketio
from game_engine import GameRuleError, make_card, play_card, start_game
from models import PlayerStat, db, record_match
from profiles import PlayerProfile, ProfileRound, create_profile, profile_payload, resolve_profile, update_profile
from rooms import RoomManager


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'profiles.sqlite3'}"})


def profile_round(profile_id, *, win=True, team=False):
    manager = RoomManager()
    room, human = manager.create_room('human', 'Nikhil', 'ember', profile_id)
    _, other, _ = manager.join_room('other', room['code'], 'Opponent', 'wave')
    if team:
        _, partner, _ = manager.join_room('partner', room['code'], 'Partner', 'leaf')
        _, fourth, _ = manager.join_room('fourth', room['code'], 'Fourth', 'star')
        room['play_format'] = 'teams'
    start_game(room)
    winner = human if win else other
    room['current_index'] = room['players'].index(winner)
    room['current_color'] = 'red'
    room['discard_pile'] = [make_card('red', '1')]
    for player in room['players']:
        player['hand'] = [make_card('blue', '5')]
    final = make_card('red', '9'); winner['hand'] = [final]
    room['round_stats'] = {human['id']: {'uno_calls': 1, 'catches': 1, 'challenges': 1}}
    play_card(room, winner['id'], final['id'])
    return room


def test_profile_secret_is_hashed_and_public_id_is_not_a_credential(app):
    with app.app_context():
        profile, token = create_profile('Nikhil', 'wave')
        assert profile.token_hash != token
        assert resolve_profile(token).id == profile.id
        for invalid in (profile.id, '', 'x'*43, None, 123):
            with pytest.raises(GameRuleError): resolve_profile(invalid)
        payload = json.dumps(profile_payload(profile))
        assert token not in payload and profile.token_hash not in payload


def test_profile_stats_and_achievements_continue_across_rooms_without_duplicate_awards(app):
    with app.app_context():
        profile, token = create_profile('Nikhil')
        rooms = [profile_round(profile.id) for _ in range(3)]
        for room in rooms:
            assert record_match(room) is True
            assert record_match(room) is False
        result = profile_payload(resolve_profile(token))
        assert (result['games'], result['wins'], result['points'], result['bestStreak']) == (3, 3, 15, 3)
        earned = {a['id'] for a in result['achievements'] if a['earnedAt']}
        assert {'first_round', 'first_win', 'hat_trick', 'uno_call', 'catch', 'challenge'} <= earned
        assert 'five_wins' not in earned
        assert len(result['history']) == 3
        assert record_match(profile_round(profile.id, win=False))
        result = profile_payload(profile)
        assert result['games'] == 4 and result['wins'] == 3 and result['bestStreak'] == 3
        assert json.loads(profile.counters_json)['streak'] == 0


def test_profile_preferences_are_validated_and_cannot_modify_scores(app):
    with app.app_context():
        profile, _ = create_profile('Nikhil')
        update_profile(profile, {'displayName': 'New Name', 'avatar': 'nova', 'tableTheme': 'ocean', 'cardBack': 'royal', 'wins': 999, 'achievements': ['first_win']})
        payload = profile_payload(profile)
        assert (payload['displayName'], payload['avatar'], payload['tableTheme'], payload['cardBack']) == ('New Name', 'nova', 'ocean', 'royal')
        assert payload['wins'] == 0 and not any(a['earnedAt'] for a in payload['achievements'])
        with pytest.raises(GameRuleError): update_profile(profile, {'tableTheme': 'invalid'})
        assert profile.table_theme == 'ocean'


def test_profiles_survive_database_restart(app):
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    with app.app_context():
        profile, token = create_profile('Nikhil')
        update_profile(profile, {'tableTheme': 'plum', 'cardBack': 'aurora'})
        record_match(profile_round(profile.id))
    restored = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': uri})
    with restored.app_context():
        payload = profile_payload(resolve_profile(token))
        assert payload['games'] == 1 and payload['cardBack'] == 'aurora' and payload['tableTheme'] == 'plum'


def test_profile_updates_are_private_and_require_secret_token(app):
    host, guest = socketio.test_client(app), socketio.test_client(app)
    host.emit('ensureProfile', {'displayName': 'Nikhil'})
    session = next(e['args'][0] for e in host.get_received() if e['name'] == 'profileSession')
    token, profile_id = session['profileToken'], session['profile']['id']
    host.emit('createRoom', {'username': 'Nikhil', 'profileToken': token})
    joined = next(e['args'][0] for e in host.get_received() if e['name'] == 'roomJoined')
    guest.emit('joinRoom', {'roomCode': joined['roomCode'], 'username': 'Guest'})
    assert token not in json.dumps(guest.get_received())
    guest.emit('updateProfile', {'profileToken': profile_id, 'displayName': 'Hijacked'})
    assert any(e['name'] == 'errorMessage' for e in guest.get_received())
    host.emit('updateProfile', {'profileToken': token, 'displayName': 'Nikhil', 'tableTheme': 'midnight', 'cardBack': 'carbon'})
    events = host.get_received()
    assert any(e['name'] == 'profileSaved' for e in events)
    assert not guest.get_received()
    host.disconnect(); guest.disconnect()


def test_same_profile_cannot_occupy_two_seats_in_one_room(app):
    manager = RoomManager()
    room, _ = manager.create_room('a', 'Nikhil', 'ember', 'profile-one')
    with pytest.raises(GameRuleError, match='already has a seat'):
        manager.join_room('b', room['code'], 'Different Name', 'nova', profile_id='profile-one')
    assert len(room['players']) == 1


def test_team_partner_receives_profile_win_and_points(app):
    with app.app_context():
        profile, _ = create_profile('Partner')
        room = profile_round(None, team=True)
        # Seat 2 is on the winning Red team, although seat 0 played the final card.
        room['players'][2]['profile_id'] = profile.id
        assert record_match(room)
        result = profile_payload(profile)
        assert result['wins'] == 1 and result['points'] == 10
        assert result['history'][0]['won'] is True


def test_finished_socket_round_pushes_updated_profile(app):
    host, guest = socketio.test_client(app), socketio.test_client(app)
    host.emit('ensureProfile', {'displayName': 'Nikhil'})
    session = next(e['args'][0] for e in host.get_received() if e['name'] == 'profileSession')
    host.emit('createRoom', {'username': 'Nikhil', 'profileToken': session['profileToken']})
    joined = next(e['args'][0] for e in host.get_received() if e['name'] == 'roomJoined')
    guest.emit('joinRoom', {'roomCode': joined['roomCode'], 'username': 'Guest'}); guest.get_received();host.get_received()
    host.emit('startGame', {'roomCode': joined['roomCode']}); host.get_received();guest.get_received()
    room = app.extensions['room_manager'].rooms[joined['roomCode']]
    card = make_card('red', '9')
    room['players'][0]['hand'] = [card];room['players'][1]['hand'] = [make_card('blue', '5')]
    room['current_index'] = 0;room['current_color'] = 'red';room['discard_pile'] = [make_card('red', '1')]
    host.emit('playCard', {'roomCode': joined['roomCode'], 'cardId': card['id']})
    updated = next(e['args'][0] for e in host.get_received() if e['name'] == 'profileUpdated')
    assert updated['games'] == 1 and updated['wins'] == 1 and updated['points'] == 5
    assert not any(e['name'] == 'profileUpdated' for e in guest.get_received())
    host.disconnect();guest.disconnect()


def test_legacy_recovered_seat_can_bind_its_own_profile_only(app):
    host, outsider = socketio.test_client(app), socketio.test_client(app)
    host.emit('createRoom', {'username': 'Returning'})
    joined = next(e['args'][0] for e in host.get_received() if e['name'] == 'roomJoined')
    host.emit('ensureProfile', {'displayName': 'Returning'})
    session = next(e['args'][0] for e in host.get_received() if e['name'] == 'profileSession')
    outsider.emit('bindProfile', {'roomCode': joined['roomCode'], 'profileToken': session['profileToken']})
    assert any(e['name'] == 'errorMessage' for e in outsider.get_received())
    host.emit('bindProfile', {'roomCode': joined['roomCode'], 'profileToken': session['profileToken']})
    room = app.extensions['room_manager'].rooms[joined['roomCode']]
    assert room['players'][0]['profile_id'] == session['profile']['id']
    outsider.emit('ensureProfile', {'displayName': 'Other'})
    other = next(e['args'][0] for e in outsider.get_received() if e['name'] == 'profileSession')
    host.emit('bindProfile', {'roomCode': joined['roomCode'], 'profileToken': other['profileToken']})
    assert any(e['name'] == 'errorMessage' for e in host.get_received())
    assert room['players'][0]['profile_id'] == session['profile']['id']
    host.disconnect();outsider.disconnect()
