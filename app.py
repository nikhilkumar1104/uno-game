"""Flask and Flask-SocketIO entry point for UNO Live."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from flask import Flask, current_app, jsonify, render_template, request
from flask_socketio import SocketIO, emit, join_room as socket_join_room, leave_room as socket_leave_room

from game_engine import (
    GameRuleError,
    declare_uno,
    draw_card,
    lobby_state,
    pass_turn,
    play_card,
    public_state,
    start_game,
)
from models import db, delete_room, load_rooms, record_match, save_room
from rooms import RoomManager


socketio = SocketIO(async_mode="threading", logger=False, engineio_logger=False)


def _database_uri() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured.replace("postgres://", "postgresql://", 1)
    database_dir = Path(__file__).resolve().parent / "database"
    database_dir.mkdir(exist_ok=True)
    return f"sqlite:///{database_dir / 'uno.sqlite3'}"


def _allowed_origins() -> str | list[str]:
    value = os.getenv("ALLOWED_ORIGINS", "*").strip()
    if value == "*":
        return "*"
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", os.urandom(32).hex()),
        SQLALCHEMY_DATABASE_URI=_database_uri(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins=_allowed_origins(),
        ping_interval=20,
        ping_timeout=30,
        max_http_buffer_size=16 * 1024,
    )

    manager = RoomManager()
    app.extensions["room_manager"] = manager
    with app.app_context():
        db.create_all()
        manager.restore(load_rooms())

    @app.get("/")
    def index() -> str:
        return render_template("index.html", initial_room_code="")

    @app.get("/room/<room_code>")
    def room_link(room_code: str) -> str:
        code = room_code.strip().upper()[:8]
        return render_template("index.html", initial_room_code=code)

    @app.get("/health")
    def health():
        return jsonify(
            status="ok",
            rooms=len(_manager().rooms),
            timestamp=int(time.time()),
        )

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    return app


def _manager() -> RoomManager:
    return current_app.extensions["room_manager"]


def _error(exc: Exception) -> None:
    message = str(exc) if isinstance(exc, GameRuleError) else "The server could not process that action."
    emit("errorMessage", {"message": message})


def _persist(room: dict[str, Any] | None) -> None:
    if room:
        save_room(room)


def _send_state(room: dict[str, Any], sid: str | None = None) -> None:
    manager = _manager()
    if sid:
        try:
            _, player = manager.room_for_sid(sid, room["code"])
        except GameRuleError:
            return
        event = "lobbyState" if room["status"] == "lobby" else "gameState"
        payload = lobby_state(room) if event == "lobbyState" else public_state(room, player["id"])
        socketio.emit(event, payload, to=sid)
        return

    for player in room["players"]:
        player_sid = player.get("socket_id")
        if player["connected"] and player_sid:
            event = "lobbyState" if room["status"] == "lobby" else "gameState"
            payload = lobby_state(room) if event == "lobbyState" else public_state(room, player["id"])
            socketio.emit(event, payload, to=player_sid)


def _with_player_action(
    data: dict[str, Any] | None,
    action: Callable[[dict[str, Any], str, dict[str, Any]], None],
) -> None:
    try:
        payload = data or {}
        room, player = _manager().room_for_sid(request.sid, payload.get("roomCode"))
        was_playing = room["status"] == "playing"
        action(room, player["id"], payload)
        if was_playing and room["status"] == "finished":
            record_match(room)
        _persist(room)
        _send_state(room)
    except Exception as exc:  # Event boundaries must never leak stack traces to clients.
        current_app.logger.exception("Socket action failed") if not isinstance(exc, GameRuleError) else None
        _error(exc)


@socketio.on("createRoom")
def handle_create_room(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().create_room(
            request.sid, payload.get("username"), payload.get("avatar")
        )
        socket_join_room(room["code"])
        _persist(room)
        emit(
            "roomJoined",
            {
                "roomCode": room["code"],
                "playerId": player["id"],
                "isHost": True,
                "spectator": False,
            },
        )
        _send_state(room)
    except Exception as exc:
        _error(exc)


@socketio.on("joinRoom")
def handle_join_room(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().join_room(
            request.sid,
            payload.get("roomCode"),
            payload.get("username"),
            payload.get("avatar"),
        )
        socket_join_room(room["code"])
        _persist(room)
        emit(
            "roomJoined",
            {
                "roomCode": room["code"],
                "playerId": player["id"],
                "isHost": player["id"] == room["host_id"],
                "spectator": player["spectator"],
            },
        )
        socketio.emit(
            "notification",
            {"message": f"{player['username']} joined the room."},
            to=room["code"],
        )
        _send_state(room)
    except Exception as exc:
        _error(exc)


@socketio.on("rejoinRoom")
def handle_rejoin_room(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().rejoin_room(
            request.sid, payload.get("roomCode"), payload.get("playerId")
        )
        socket_join_room(room["code"])
        _persist(room)
        emit(
            "roomJoined",
            {
                "roomCode": room["code"],
                "playerId": player["id"],
                "isHost": player["id"] == room["host_id"],
                "spectator": player["spectator"],
                "rejoined": True,
            },
        )
        _send_state(room)
    except Exception as exc:
        emit("sessionExpired", {"message": str(exc)})


@socketio.on("startGame")
def handle_start_game(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> None:
        if room["host_id"] != player_id:
            raise GameRuleError("Only the host can start the game.")
        if room["status"] != "lobby":
            raise GameRuleError("This room is not in the lobby.")
        start_game(room)

    _with_player_action(data, action)


@socketio.on("playCard")
def handle_play_card(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, payload: dict[str, Any]) -> None:
        play_card(room, player_id, str(payload.get("cardId") or ""), payload.get("chosenColor"))

    _with_player_action(data, action)


@socketio.on("drawCard")
def handle_draw_card(data: dict[str, Any] | None) -> None:
    _with_player_action(data, lambda room, player_id, _: draw_card(room, player_id))


@socketio.on("passTurn")
def handle_pass_turn(data: dict[str, Any] | None) -> None:
    _with_player_action(data, lambda room, player_id, _: pass_turn(room, player_id))


@socketio.on("declareUno")
def handle_declare_uno(data: dict[str, Any] | None) -> None:
    _with_player_action(data, lambda room, player_id, _: declare_uno(room, player_id))


@socketio.on("playAgain")
def handle_play_again(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> None:
        if room["host_id"] != player_id:
            raise GameRuleError("Only the host can start the next match.")
        if room["status"] != "finished":
            raise GameRuleError("The current match has not finished.")
        start_game(room)

    _with_player_action(data, action)


@socketio.on("chatMessage")
def handle_chat(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().room_for_sid(request.sid, payload.get("roomCode"))
        message = _manager().add_chat(room, player, payload.get("text"))
        _persist(room)
        socketio.emit("chatMessage", message, to=room["code"])
    except Exception as exc:
        _error(exc)


@socketio.on("leaveRoom")
def handle_leave_room(data: dict[str, Any] | None) -> None:
    try:
        requested = (data or {}).get("roomCode")
        room, _ = _manager().room_for_sid(request.sid, requested)
        code = room["code"]
        socket_leave_room(code)
        remaining, removed_code = _manager().leave(request.sid)
        if remaining:
            _persist(remaining)
            _send_state(remaining)
        elif removed_code:
            delete_room(removed_code)
        emit("leftRoom")
    except Exception as exc:
        _error(exc)


@socketio.on("disconnect")
def handle_disconnect() -> None:
    room = _manager().disconnect(request.sid)
    if room:
        _persist(room)
        _send_state(room)


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=os.getenv("FLASK_DEBUG") == "1",
        allow_unsafe_werkzeug=True,
    )
