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
    accept_wild4,
    catch_uno,
    challenge_wild4,
    declare_uno,
    draw_card,
    lobby_state,
    pass_turn,
    play_card,
    public_state,
    queue_rematch,
    rematch_all_ready,
    return_to_lobby,
    set_game_mode,
    start_game,
)
from models import db, delete_room, ensure_schema, load_rooms, record_match, save_room
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
        ensure_schema()
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


def _persist(room: dict[str, Any] | None) -> bool:
    if not room:
        return False
    try:
        save_room(room)
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Room snapshot persistence failed")
        return False


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
    action: Callable[[dict[str, Any], str, dict[str, Any]], str | None],
) -> None:
    room: dict[str, Any] | None = None
    finished_transition = False
    notification: str | None = None
    try:
        payload = data or {}
        manager = _manager()
        with manager.lock:
            room, player = manager.room_for_sid(request.sid, payload.get("roomCode"))
            was_playing = room["status"] == "playing"
            try:
                notification = action(room, player["id"], payload)
            except GameRuleError:
                if room["status"] == "finished":
                    _send_state(room, request.sid)
                    return
                raise
            finished_transition = was_playing and room["status"] == "finished"
            if finished_transition:
                try:
                    record_match(room)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("Match history persistence failed")
            _persist(room)
            _send_state(room)
        if notification:
            socketio.emit("notification", {"message": notification}, to=room["code"])
        if finished_transition:
            socketio.emit(
                "notification",
                {"message": f"{room['winner']['username']} won {room['winner']['points']} points!"},
                to=room["code"],
            )
            _schedule_rematch(room)
    except Exception as exc:  # Event boundaries must never leak stack traces to clients.
        current_app.logger.exception("Socket action failed") if not isinstance(exc, GameRuleError) else None
        _error(exc)


def _schedule_rematch(room: dict[str, Any]) -> None:
    if current_app.config.get("TESTING"):
        return
    app_object = current_app._get_current_object()
    socketio.start_background_task(
        _rematch_timeout,
        app_object,
        room["code"],
        room["game_id"],
    )


def _rematch_timeout(app_object: Flask, room_code: str, game_id: str) -> None:
    socketio.sleep(10.2)
    with app_object.app_context():
        manager = _manager()
        with manager.lock:
            room = manager.rooms.get(room_code)
            if not room or room["status"] != "finished" or room.get("game_id") != game_id:
                return
            for player in room["players"]:
                if not player["spectator"] and player["connected"] and not player.get("left"):
                    room.setdefault("rematch_choices", {})[player["id"]] = "ready"
            connected = [
                player
                for player in room["players"]
                if not player["spectator"] and player["connected"] and not player.get("left")
            ]
            if len(connected) >= 2:
                start_game(room)
                message = "The next round started automatically."
            else:
                return_to_lobby(room)
                message = "Waiting in the lobby for another player."
            _persist(room)
            _send_state(room)
        socketio.emit("notification", {"message": message}, to=room_code)


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


@socketio.on("setGameMode")
def handle_set_game_mode(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, payload: dict[str, Any]) -> str:
        if room["host_id"] != player_id:
            raise GameRuleError("Only the host can choose the game mode.")
        set_game_mode(room, str(payload.get("mode") or ""))
        label = "Classic" if room["mode"] == "classic" else "Wild stacking"
        return f"The host selected {label} mode."

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
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> str | None:
        player = next(player for player in room["players"] if player["id"] == player_id)
        return f"{player['username']} called UNO!" if declare_uno(room, player_id) else None

    _with_player_action(data, action)


@socketio.on("catchUno")
def handle_catch_uno(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> str:
        catcher = next(player for player in room["players"] if player["id"] == player_id)
        offender_name = catch_uno(room, player_id)
        return f"{catcher['username']} caught {offender_name}. Two-card penalty!"

    _with_player_action(data, action)


@socketio.on("acceptWild4")
def handle_accept_wild4(data: dict[str, Any] | None) -> None:
    _with_player_action(
        data,
        lambda room, player_id, _: (
            accept_wild4(room, player_id) or "Wild Draw Four accepted."
        ),
    )


@socketio.on("challengeWild4")
def handle_challenge_wild4(data: dict[str, Any] | None) -> None:
    reveal: dict[str, Any] = {}

    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> str:
        pending = room.get("wild4_challenge") or {}
        offender = next(
            (
                player
                for player in room["players"]
                if player["id"] == pending.get("offender_id")
            ),
            None,
        )
        revealed_hand = (
            [{"color": card["color"], "value": card["value"]} for card in offender["hand"]]
            if offender
            else []
        )
        challenge_wild4(room, player_id)
        result = str(room.get("last_challenge_result") or "Challenge resolved.")
        reveal.update(
            offenderName=offender["username"] if offender else "Player",
            hand=revealed_hand,
            result=result,
        )
        return result

    _with_player_action(data, action)
    if reveal:
        emit("challengeReveal", reveal)


@socketio.on("playAgain")
def handle_play_again(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> str:
        queue_rematch(room, player_id)
        player = next(player for player in room["players"] if player["id"] == player_id)
        if rematch_all_ready(room):
            start_game(room)
            return "Everyone is ready. The next round has started!"
        return f"{player['username']} is ready for the next round."

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
            if remaining["status"] == "finished" and rematch_all_ready(remaining):
                start_game(remaining)
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
