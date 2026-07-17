from app import create_app, socketio


def event_payload(client, event_name):
    events = client.get_received()
    event = next(item for item in events if item["name"] == event_name)
    return event["args"][0]


def test_health_and_two_player_room_flow(tmp_path):
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
    joined = event_payload(host, "roomJoined")
    code = joined["roomCode"]
    host.get_received()

    host.emit("createRoom", {"username": "Second Seat", "avatar": "bolt"})
    duplicate_room_error = event_payload(host, "errorMessage")
    assert "current room" in duplicate_room_error["message"]

    guest.emit(
        "joinRoom",
        {"roomCode": code, "username": "Guest User", "avatar": "wave"},
    )
    guest_joined = event_payload(guest, "roomJoined")
    assert guest_joined["roomCode"] == code
    guest.get_received()
    host.get_received()

    host.emit("startGame", {"roomCode": code})
    host_state = event_payload(host, "gameState")
    guest_state = event_payload(guest, "gameState")
    assert host_state["status"] == "playing"
    assert len(host_state["hand"]) == 7
    assert len(guest_state["hand"]) == 7
    assert host_state["hand"] != guest_state["hand"]
    assert all("hand" not in player for player in host_state["players"])

    host_player_id = joined["playerId"]
    host.disconnect()
    guest.get_received()

    reconnected_host = socketio.test_client(app)
    reconnected_host.emit(
        "rejoinRoom", {"roomCode": code, "playerId": host_player_id}
    )
    reconnect_events = reconnected_host.get_received()
    reconnect_joined = next(
        item["args"][0] for item in reconnect_events if item["name"] == "roomJoined"
    )
    reconnect_state = next(
        item["args"][0] for item in reconnect_events if item["name"] == "gameState"
    )
    assert reconnect_joined["rejoined"] is True
    assert reconnect_state["hand"] == host_state["hand"]

    reconnected_host.disconnect()
    guest.disconnect()
