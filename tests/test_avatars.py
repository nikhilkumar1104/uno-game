import io
import json

import pytest
from PIL import Image

from app import create_app, socketio
from avatars import CHARACTER_AVATARS, ProfilePhoto
from models import db
from profiles import create_profile, profile_payload, resolve_profile


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'avatars.sqlite3'}"})


def image_bytes(fmt='PNG', size=(640, 480)):
    data = io.BytesIO();Image.new('RGB', size, '#e78b41').save(data, format=fmt)
    return data.getvalue()


def upload(client, token, content=None, **fields):
    return client.post('/api/profile/avatar', data={
        'profileToken': token, 'displayName': 'Nikhil', **fields,
        'photo': (io.BytesIO(image_bytes() if content is None else content), 'avatar.png'),
    })


def new_profile(app):
    with app.app_context():
        profile, token = create_profile('Nikhil')
        return profile.id, token


def test_photo_is_decoded_resized_and_served_as_jpeg(app):
    profile_id, token = new_profile(app)
    client = app.test_client();response = upload(client, token, tableTheme='ocean', cardBack='royal')
    assert response.status_code == 200
    profile = response.get_json()['profile'];url = profile['avatarUrl']
    assert profile['avatar'] == 'photo' and profile['photoUrl'] == url
    assert profile['tableTheme'] == 'ocean' and profile['cardBack'] == 'royal'
    photo = client.get(url)
    assert photo.status_code == 200 and photo.mimetype == 'image/jpeg'
    decoded = Image.open(io.BytesIO(photo.data))
    assert decoded.size == (256, 256) and decoded.format == 'JPEG' and not decoded.getexif()
    with app.app_context():
        assert db.session.get(ProfilePhoto, profile_id).content == photo.data
    assert token not in url and token.encode() not in photo.data


@pytest.mark.parametrize('content', [b'not an image', b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', b'x' * (2 * 1024 * 1024 + 1)])
def test_invalid_or_oversized_photo_is_rejected_without_changing_profile(app, content):
    profile_id, token = new_profile(app)
    response = upload(app.test_client(), token, content)
    assert response.status_code in {400, 413}
    with app.app_context():
        assert resolve_profile(token).avatar == 'ember'
        assert db.session.get(ProfilePhoto, profile_id) is None


def test_photo_upload_requires_profile_secret_and_not_public_id(app):
    profile_id, _ = new_profile(app)
    response = upload(app.test_client(), profile_id)
    assert response.status_code == 400
    with app.app_context(): assert db.session.get(ProfilePhoto, profile_id) is None


def test_replacement_invalidates_old_url_and_bad_upload_preserves_existing_photo(app):
    _, token = new_profile(app);client = app.test_client()
    old_url = upload(client, token).get_json()['profile']['avatarUrl']
    assert upload(client, token, b'bad file').status_code == 400
    assert client.get(old_url).status_code == 200
    new_url = upload(client, token, image_bytes('JPEG')).get_json()['profile']['avatarUrl']
    assert old_url != new_url and client.get(old_url).status_code == 404 and client.get(new_url).status_code == 200


def test_photo_update_is_atomic_with_profile_validation(app):
    profile_id, token = new_profile(app)
    assert upload(app.test_client(), token, tableTheme='invalid').status_code == 400
    with app.app_context():
        profile = resolve_profile(token)
        assert profile.avatar == 'ember' and profile.table_theme == 'emerald'
        assert db.session.get(ProfilePhoto, profile_id) is None


def test_uploaded_photo_and_characters_update_room_and_chat_immediately(app):
    profile_id, token = new_profile(app)
    host, guest = socketio.test_client(app), socketio.test_client(app)
    host.emit('createRoom', {'username': 'Nikhil', 'profileToken': token})
    joined = next(e['args'][0] for e in host.get_received() if e['name'] == 'roomJoined')
    code = joined['roomCode']
    guest.emit('joinRoom', {'roomCode': code, 'username': 'Guest'});guest.get_received();host.get_received()
    host.emit('chatMessage', {'roomCode': code, 'text': 'Hello'});host.get_received();guest.get_received()
    response = upload(app.test_client(), token)
    assert response.status_code == 200
    url = response.get_json()['profile']['avatarUrl']
    state = next(e['args'][0] for e in guest.get_received() if e['name'] == 'lobbyState')
    player = next(p for p in state['players'] if p['id'] == joined['playerId'])
    assert player['avatarUrl'] == url and state['chat'][0]['avatarUrl'] == url
    assert token not in json.dumps(state)
    host.emit('updateProfile', {'profileToken': token, 'avatar': 'fox'})
    state = next(e['args'][0] for e in guest.get_received() if e['name'] == 'lobbyState')
    player = next(p for p in state['players'] if p['id'] == joined['playerId'])
    assert player['avatar'] == 'fox' and player['avatarUrl'] == '/static/avatars/fox.svg'
    with app.app_context():
        payload = profile_payload(resolve_profile(token))
        assert payload['photoUrl'] == url  # Can return to a saved photo without re-uploading.
    host.disconnect();guest.disconnect()


def test_photo_and_character_selection_survives_restart(app):
    _, token = new_profile(app);client = app.test_client()
    url = upload(client, token).get_json()['profile']['avatarUrl']
    restored = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': app.config['SQLALCHEMY_DATABASE_URI']})
    assert restored.test_client().get(url).status_code == 200
    with restored.app_context(): assert profile_payload(resolve_profile(token))['avatarUrl'] == url


def test_all_character_assets_are_valid_and_available_offline(app):
    from xml.etree import ElementTree
    client = app.test_client();worker = client.get('/sw.js').get_data(as_text=True)
    for name in CHARACTER_AVATARS:
        path = f'/static/avatars/{name}.svg'
        response = client.get(path)
        assert response.status_code == 200 and path in worker
        ElementTree.fromstring(response.data)


def test_missing_photo_cannot_be_selected(app):
    _, token = new_profile(app);client = socketio.test_client(app)
    client.emit('updateProfile', {'profileToken': token, 'avatar': 'photo'})
    assert any(e['name'] == 'errorMessage' for e in client.get_received())
    with app.app_context(): assert resolve_profile(token).avatar == 'ember'
    client.disconnect()
