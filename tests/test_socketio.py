from app import _schedule_bot_turn, create_app, socketio
from game_engine import make_card
from models import PlayerStat, db


def received_payload(client, event_name):
    events = client.get_received()
    return next(item["args"][0] for item in events if item["name"] == event_name)


def test_index_bundles_socket_client_instead_of_external_cdn(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'assets.sqlite3'}",
            "SECRET_KEY": "asset-secret",
        }
    )
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert "/static/vendor/socket.io.min.js" in html
    assert "cdn.socket.io" not in html
    asset = client.get("/static/vendor/socket.io.min.js")
    assert asset.status_code == 200
    assert b"Socket.IO v4.8.3" in asset.data[:200]
    assert client.get("/static/manifest.webmanifest").status_code == 200
    assert client.get("/static/offline.html").status_code == 200
    service_worker = client.get("/sw.js")
    assert service_worker.status_code == 200
    assert service_worker.headers["Service-Worker-Allowed"] == "/"
    assert b'uno-live-release-1-v2' in service_worker.data
    assert '/static/style.css?v=6' in html
    assert 'id="leaveConfirmModal"' in html
    assert 'data-game-aside-panel="rulesPanel"' in html
    assert "Play a 7 to choose a player and swap hands" in html


def test_full_socket_flow_winner_persistence_reconnect_and_rematch(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.sqlite3'}",
            "SECRET_KEY": "test-secret",
        }
    )
    assert app.test_client().get("/health").get_json()["status"] == "ok"

    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    host.emit("createRoom", {"username": "Host User", "avatar": "ember"})
    joined = received_payload(host, "roomJoined")
    code = joined["roomCode"]
    host.get_received()

    host.emit("setGameMode", {"roomCode": code, "mode": "wild"})
    lobby = received_payload(host, "lobbyState")
    assert lobby["mode"] == "wild"

    guest.emit(
        "joinRoom",
        {"roomCode": code, "username": "Guest User", "avatar": "wave"},
    )
    guest_joined = received_payload(guest, "roomJoined")
    assert guest_joined["roomCode"] == code
    guest.get_received()
    host.get_received()

    host.emit("startGame", {"roomCode": code})
    host_state = received_payload(host, "gameState")
    guest_state = received_payload(guest, "gameState")
    assert host_state["status"] == "playing"
    assert host_state["mode"] == "wild"
    assert len(host_state["hand"]) == 7
    assert len(guest_state["hand"]) == 7
    assert host_state["hand"] != guest_state["hand"]
    assert all("hand" not in player for player in host_state["players"])

    host_player_id = joined["playerId"]
    host.disconnect()
    guest.get_received()
    reconnected_host = socketio.test_client(app)
    reconnected_host.emit("rejoinRoom", {"roomCode": code, "playerId": host_player_id})
    reconnect_events = reconnected_host.get_received()
    rejoined = next(
        item["args"][0] for item in reconnect_events if item["name"] == "roomJoined"
    )
    reconnect_state = next(
        item["args"][0] for item in reconnect_events if item["name"] == "gameState"
    )
    assert rejoined["rejoined"] is True
    assert any(
        item["name"] == "notification"
        and "rejoined and can continue" in item["args"][0]["message"]
        for item in reconnect_events
    )
    assert reconnect_state["hand"] == host_state["hand"]

    manager = app.extensions["room_manager"]
    with manager.lock:
        room = manager.rooms[code]
        host_seat = next(player for player in room["players"] if player["id"] == host_player_id)
        guest_seat = next(player for player in room["players"] if player["id"] == guest_joined["playerId"])
        winning_card = make_card("red", "7")
        host_seat["hand"] = [winning_card]
        guest_seat["hand"] = [make_card("wild", "wild"), make_card("blue", "skip")]
        room["discard_pile"] = [make_card("red", "3")]
        room["current_color"] = "red"
        room["current_index"] = 0
        room["pending_draw"] = 0
        room["pending_draw_type"] = None

    reconnected_host.emit("playCard", {"roomCode": code, "cardId": winning_card["id"]})
    finish_events = reconnected_host.get_received()
    finished = next(
        item["args"][0] for item in finish_events if item["name"] == "gameState"
    )
    assert finished["status"] == "finished"
    assert finished["winner"]["username"] == "Host User"
    assert finished["winner"]["points"] == 70
    assert not any(item["name"] == "errorMessage" for item in finish_events)

    with app.app_context():
        stat = db.session.execute(
            db.select(PlayerStat).where(PlayerStat.player_key == host_player_id)
        ).scalar_one()
        assert stat.games == 1
        assert stat.wins == 1
        assert stat.points == 70

    guest.get_received()
    reconnected_host.emit("playAgain", {"roomCode": code})
    ready_state = received_payload(reconnected_host, "gameState")
    assert ready_state["status"] == "finished"
    assert ready_state["rematchChoice"] == "ready"
    # The other player receives the intermediate ready-state broadcast too.
    guest.get_received()
    guest.emit("playAgain", {"roomCode": code})
    next_round_host = received_payload(reconnected_host, "gameState")
    next_round_guest = received_payload(guest, "gameState")
    assert next_round_host["status"] == "playing"
    assert next_round_guest["status"] == "playing"
    assert next_round_host["roundNumber"] == 2

    reconnected_host.disconnect()
    guest.disconnect()


def test_wild_four_challenge_reveals_hand_only_to_challenger(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'challenge.sqlite3'}",
            "SECRET_KEY": "challenge-secret",
        }
    )
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    host.emit("createRoom", {"username": "Dealer", "avatar": "ember"})
    joined = received_payload(host, "roomJoined")
    code = joined["roomCode"]
    host.get_received()
    guest.emit("joinRoom", {"roomCode": code, "username": "Challenger", "avatar": "wave"})
    guest_joined = received_payload(guest, "roomJoined")
    host.get_received()
    guest.get_received()
    host.emit("startGame", {"roomCode": code})
    host.get_received()
    guest.get_received()

    manager = app.extensions["room_manager"]
    with manager.lock:
        room = manager.rooms[code]
        dealer = next(player for player in room["players"] if player["id"] == joined["playerId"])
        challenger = next(
            player for player in room["players"] if player["id"] == guest_joined["playerId"]
        )
        wild_four = make_card("wild", "wild4")
        dealer["hand"] = [wild_four, make_card("red", "8"), make_card("blue", "9")]
        challenger["hand"] = [make_card("green", "2")]
        room["discard_pile"] = [make_card("red", "3")]
        room["current_color"] = "red"
        room["current_index"] = 0

    host.emit(
        "playCard",
        {"roomCode": code, "cardId": wild_four["id"], "chosenColor": "blue"},
    )
    host.get_received()
    guest.get_received()
    guest.emit("challengeWild4", {"roomCode": code})
    challenger_events = guest.get_received()
    reveal = next(
        item["args"][0] for item in challenger_events if item["name"] == "challengeReveal"
    )
    assert reveal["offenderName"] == "Dealer"
    assert {tuple(card.values()) for card in reveal["hand"]} == {("red", "8"), ("blue", "9")}
    assert all("id" not in card for card in reveal["hand"])
    assert "succeeded" in reveal["result"]
    assert not any(item["name"] == "challengeReveal" for item in host.get_received())

    host.disconnect()
    guest.disconnect()


def test_solo_room_adds_and_runs_server_controlled_computer(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'bot.sqlite3'}",
            "SECRET_KEY": "bot-secret",
        }
    )
    host = socketio.test_client(app)
    host.emit("createRoom", {"username": "Solo", "avatar": "ember"})
    joined = received_payload(host, "roomJoined")
    code = joined["roomCode"]
    host.get_received()

    host.emit("addBot", {"roomCode": code})
    lobby = received_payload(host, "lobbyState")
    bot_public = next(player for player in lobby["players"] if player["isBot"])
    assert bot_public["username"] == "Computer 1"
    host.get_received()

    host.emit("startGame", {"roomCode": code})
    host.get_received()
    manager = app.extensions["room_manager"]
    with app.app_context(), manager.lock:
        room = manager.rooms[code]
        bot = next(player for player in room["players"] if player.get("is_bot"))
        playable = make_card("red", "7")
        bot["hand"] = [playable, make_card("blue", "4")]
        room["discard_pile"] = [make_card("red", "3")]
        room["current_color"] = "red"
        room["current_index"] = 1
        room["_bot_task_scheduled"] = False
        _schedule_bot_turn(room)

    socketio.sleep(1.1)
    events = host.get_received()
    states = [item["args"][0] for item in events if item["name"] == "gameState"]
    assert states
    latest = states[-1]
    computer = next(player for player in latest["players"] if player["isBot"])
    assert computer["cardCount"] == 1
    assert computer["saidUno"] is True
    assert latest["currentPlayerId"] == joined["playerId"]

    host.disconnect()


def test_room_options_bot_profiles_and_voice_signaling(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'release1.sqlite3'}",
            "SECRET_KEY": "release-one-secret",
        }
    )
    host = socketio.test_client(app)
    guest = socketio.test_client(app)
    host.emit("createRoom", {"username": "Voice Host", "avatar": "ember"})
    host_joined = received_payload(host, "roomJoined")
    code = host_joined["roomCode"]
    host.get_received()

    host.emit(
        "setRoomOptions",
        {
            "roomCode": code,
            "playFormat": "individual",
            "rules": {"seven_zero": True, "jump_in": True, "forced_play": True},
        },
    )
    lobby = received_payload(host, "lobbyState")
    assert all(lobby["rules"].values())

    host.emit(
        "addBot",
        {"roomCode": code, "difficulty": "hard", "personality": "aggressive"},
    )
    lobby = received_payload(host, "lobbyState")
    bot = next(player for player in lobby["players"] if player["isBot"])
    assert bot["botDifficulty"] == "hard"
    assert bot["botPersonality"] == "aggressive"

    guest.emit(
        "joinRoom",
        {"roomCode": code, "username": "Voice Guest", "avatar": "wave"},
    )
    guest_joined = received_payload(guest, "roomJoined")
    host.get_received()
    guest.get_received()
    host.emit("voiceJoin", {"roomCode": code})
    host.get_received()
    guest.get_received()
    guest.emit("voiceJoin", {"roomCode": code})
    host_events = host.get_received()
    participants = next(
        event["args"][0]
        for event in host_events
        if event["name"] == "voiceParticipants"
    )
    assert {member["username"] for member in participants["members"]} == {
        "Voice Host",
        "Voice Guest",
    }

    host.emit(
        "voiceSignal",
        {
            "roomCode": code,
            "targetPlayerId": guest_joined["playerId"],
            "signal": {"type": "offer", "description": {"type": "offer", "sdp": "test"}},
        },
    )
    signal = received_payload(guest, "voiceSignal")
    assert signal["fromPlayerId"] == host_joined["playerId"]
    assert signal["signal"]["type"] == "offer"

    host.disconnect()
    guest.disconnect()
