"""Flask and Flask-SocketIO entry point for UNO Live."""

from __future__ import annotations

import os
import json
import time
import io
from pathlib import Path
from typing import Any, Callable

from flask import Flask, current_app, jsonify, render_template, request, send_from_directory, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from flask_socketio import SocketIO, emit, join_room as socket_join_room, leave_room as socket_leave_room

from bot_player import perform_bot_turn
from game_engine import (
    GameRuleError,
    ensure_connected_turn,
    accept_wild4,
    catch_uno,
    challenge_wild4,
    current_player,
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
    set_room_options,
    start_game,
)
from models import db, delete_room, ensure_schema, load_rooms, record_match, save_room
from rooms import RoomManager
from profiles import create_profile, resolve_profile, update_profile, profile_payload
from avatars import ProfilePhoto, avatar_url, save_photo


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
        for restored_room in manager.rooms.values():
            for restored_player in restored_room["players"]:
                if restored_player.get("reconnect_deadline"):
                    _schedule_reconnect_timeout(restored_room, restored_player)

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

    @app.before_request
    def avatar_upload_limit():
        if request.endpoint == "upload_profile_avatar":
            request.max_content_length = 2 * 1024 * 1024 + 16 * 1024

    @app.post("/api/profile/avatar")
    def upload_profile_avatar():
        try:
            profile = resolve_profile(request.form.get("profileToken"))
            upload = request.files.get("photo")
            if not upload:
                raise GameRuleError("Choose a photo to upload.")
            update_profile(profile, {**request.form.to_dict(), "avatar": "photo"}, commit=False, photo_pending=True)
            save_photo(profile, upload.read(2 * 1024 * 1024 + 1))
            _sync_profile_avatar(profile)
            return jsonify(profile=profile_payload(profile))
        except RequestEntityTooLarge:
            db.session.rollback()
            return jsonify(error="That upload is too large. Choose a smaller photo."), 413
        except GameRuleError as exc:
            db.session.rollback()
            return jsonify(error=str(exc)), 400
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Profile photo upload failed")
            return jsonify(error="The photo could not be saved. Please try again."), 500

    @app.get("/avatars/<image_id>.jpg")
    def profile_photo(image_id):
        if len(image_id) != 32 or any(c not in "0123456789abcdef" for c in image_id):
            return "", 404
        photo = db.session.execute(db.select(ProfilePhoto).where(ProfilePhoto.image_id == image_id)).scalar_one_or_none()
        if not photo:
            return "", 404
        return send_file(io.BytesIO(photo.content), mimetype="image/jpeg", max_age=3600, etag=image_id)

    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
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


def _voice_payload(room: dict[str, Any]) -> dict[str, Any]:
    members = room.get("voice_members", {})
    return {
        "members": [
            {
                "playerId": player["id"],
                "username": player["username"],
                "speaking": bool(members.get(player["id"], {}).get("speaking")),
            }
            for player in room["players"]
            if player["id"] in members and player["connected"] and not player.get("is_bot")
        ]
    }


def _broadcast_voice(room: dict[str, Any]) -> None:
    socketio.emit("voiceParticipants", _voice_payload(room), to=room["code"])


def _remove_voice_member(room: dict[str, Any], player_id: str) -> None:
    if room.setdefault("voice_members", {}).pop(player_id, None) is not None:
        socketio.emit("voicePeerLeft", {"playerId": player_id}, to=room["code"])
        _broadcast_voice(room)


def _with_player_action(
    data: dict[str, Any] | None,
    action: Callable[[dict[str, Any], str, dict[str, Any]], str | None],
    sound_effect: str | None = None,
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
                    _record_and_notify_profiles(room)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("Match history persistence failed")
            _persist(room)
            _send_state(room)
        if notification:
            socketio.emit("notification", {"message": notification}, to=room["code"])
        if sound_effect:
            socketio.emit("soundEffect", {"type": sound_effect}, to=room["code"])
        if finished_transition:
            winner_name = room["winner"].get("teamLabel") or room["winner"]["username"]
            socketio.emit(
                "notification",
                {"message": f"{winner_name} won {room['winner']['points']} points!"},
                to=room["code"],
            )
            _schedule_rematch(room)
        elif room and room["status"] == "playing":
            _schedule_bot_turn(room)
    except Exception as exc:  # Event boundaries must never leak stack traces to clients.
        current_app.logger.exception("Socket action failed") if not isinstance(exc, GameRuleError) else None
        _error(exc)


def _bot_action_needed(room: dict[str, Any]) -> bool:
    if room.get("status") != "playing":
        return False
    challenge = room.get("wild4_challenge")
    if challenge:
        target = next(
            (player for player in room["players"] if player["id"] == challenge["target_id"]),
            None,
        )
        return bool(target and target.get("is_bot") and target.get("connected"))
    player = current_player(room)
    return bool(player and player.get("is_bot") and player.get("connected"))


def _schedule_bot_turn(room: dict[str, Any]) -> None:
    """Schedule at most one delayed server-side computer decision."""
    manager = _manager()
    with manager.lock:
        if not _bot_action_needed(room) or room.get("_bot_task_scheduled"):
            return
        room["_bot_task_scheduled"] = True
        delay = max(0.8, float(room.get("uno_catch_available_at") or 0) - time.time())
        app_object = current_app._get_current_object()
        socketio.start_background_task(
            _bot_turn_worker,
            app_object,
            room["code"],
            room.get("game_id"),
            delay,
        )


def _bot_turn_worker(app_object: Flask, room_code: str, game_id: str | None, delay: float = 0.8) -> None:
    socketio.sleep(delay)
    with app_object.app_context():
        manager = _manager()
        room: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        finished_transition = False
        with manager.lock:
            room = manager.rooms.get(room_code)
            if not room:
                return
            room["_bot_task_scheduled"] = False
            if room.get("game_id") != game_id or not _bot_action_needed(room):
                return
            was_playing = room["status"] == "playing"
            try:
                result = perform_bot_turn(room)
            except GameRuleError:
                current_app.logger.exception("Computer player action was rejected")
                return
            if not result:
                return
            finished_transition = was_playing and room["status"] == "finished"
            if finished_transition:
                try:
                    _record_and_notify_profiles(room)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("Computer match persistence failed")
            _persist(room)
            _send_state(room)

        if result.get("message"):
            socketio.emit("notification", {"message": result["message"]}, to=room_code)
        for effect in result.get("effects", []):
            socketio.emit("soundEffect", {"type": effect}, to=room_code)
        if finished_transition and room:
            winner_name = room["winner"].get("teamLabel") or room["winner"]["username"]
            socketio.emit(
                "notification",
                {"message": f"{winner_name} won {room['winner']['points']} points!"},
                to=room_code,
            )
            _schedule_rematch(room)
        elif room and room["status"] == "playing":
            _schedule_bot_turn(room)


def _record_and_notify_profiles(room: dict[str, Any]) -> None:
    if not record_match(room):
        return
    for player in room["players"]:
        if player.get("profile_id") and player.get("socket_id") and player["connected"]:
            from profiles import PlayerProfile
            profile = db.session.get(PlayerProfile, player["profile_id"])
            if profile:
                socketio.emit("profileUpdated", profile_payload(profile), to=player["socket_id"])


def _sync_profile_avatar(profile) -> None:
    manager = _manager()
    with manager.lock:
        url = avatar_url(profile)
        for room in manager.rooms.values():
            changed = False
            for player in room["players"]:
                if player.get("profile_id") != profile.id:
                    continue
                if player["avatar"] == profile.avatar and player.get("avatar_url") == url:
                    continue
                player["avatar"], player["avatar_url"] = profile.avatar, url
                for message in room.get("chat", []):
                    if message.get("playerId") == player["id"]:
                        message["avatar"], message["avatarUrl"] = profile.avatar, url
                changed = True
            if changed:
                _persist(room)
                _send_state(room)


def _schedule_reconnect_timeout(room: dict[str, Any], player: dict[str, Any]) -> None:
    if current_app.config.get("TESTING"):
        return
    socketio.start_background_task(
        _reconnect_timeout, current_app._get_current_object(), room["code"],
        player["id"], player["reconnect_deadline"],
    )


def _reconnect_timeout(app_object: Flask, room_code: str, player_id: str, deadline: float) -> None:
    socketio.sleep(max(0, deadline - time.time()))
    with app_object.app_context():
        manager = _manager()
        with manager.lock:
            room = manager.rooms.get(room_code)
            player = next((p for p in room["players"] if p["id"] == player_id), None) if room else None
            if not player or player["connected"] or player.get("reconnect_deadline") != deadline:
                return
            if time.time() < deadline:
                _schedule_reconnect_timeout(room, player)
                return
            player["reconnect_deadline"] = None
            if room["status"] == "playing":
                ensure_connected_turn(room)
            manager._transfer_host_if_needed(room)
            _persist(room)
            _send_state(room)
        if room["status"] == "playing":
            _schedule_bot_turn(room)


@socketio.on("ensureProfile")
def handle_ensure_profile(data=None) -> None:
    try:
        payload = data or {}
        token = payload.get("profileToken")
        if token:
            profile = resolve_profile(token)
        else:
            profile, token = create_profile(payload.get("displayName") or "Player", payload.get("avatar"))
        emit("profileSession", {"profileToken": token, "profile": profile_payload(profile)})
    except GameRuleError as exc:
        emit("profileExpired", {"message": str(exc)})
    except Exception as exc:
        db.session.rollback()
        _error(exc)


@socketio.on("updateProfile")
def handle_update_profile(data=None) -> None:
    try:
        payload = data or {}
        profile = resolve_profile(payload.get("profileToken"))
        update_profile(profile, payload)
        _sync_profile_avatar(profile)
        emit("profileUpdated", profile_payload(profile))
        emit("profileSaved")
    except Exception as exc:
        db.session.rollback()
        _error(exc)


@socketio.on("getProfile")
def handle_get_profile(data=None) -> None:
    try:
        profile = resolve_profile((data or {}).get("profileToken"))
        emit("profileUpdated", profile_payload(profile))
    except Exception as exc:
        _error(exc)


@socketio.on("bindProfile")
def handle_bind_profile(data=None) -> None:
    """Attach a browser profile to its own pre-upgrade recovered seat."""
    try:
        payload = data or {}
        profile = resolve_profile(payload.get("profileToken"))
        manager = _manager()
        with manager.lock:
            room, player = manager.room_for_sid(request.sid, payload.get("roomCode"))
            if player.get("profile_id"):
                if player["profile_id"] != profile.id:
                    raise GameRuleError("This seat belongs to a different saved profile.")
                return
            if any(p.get("profile_id") == profile.id and p["id"] != player["id"] for p in room["players"]):
                raise GameRuleError("This profile already has a seat here.")
            player["profile_id"] = profile.id
            profile.display_name = player["username"]
            player["avatar"], player["avatar_url"] = profile.avatar, avatar_url(profile)
            _persist(room)
            _send_state(room)
    except Exception as exc:
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
            can_start = (
                len(connected) == 4
                if room.get("play_format", "individual") == "teams"
                else len(connected) >= 2
            )
            if can_start:
                start_game(room)
                message = "The next round started automatically."
            else:
                return_to_lobby(room)
                message = "Waiting in the lobby for another player."
            _persist(room)
            _send_state(room)
        socketio.emit("notification", {"message": message}, to=room_code)
        if room["status"] == "playing":
            _schedule_bot_turn(room)


@socketio.on("createRoom")
def handle_create_room(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        profile = resolve_profile(payload["profileToken"]) if payload.get("profileToken") else None
        room, player = _manager().create_room(
            request.sid, payload.get("username"), payload.get("avatar"), profile.id if profile else None
        )
        if profile:
            profile.display_name, profile.avatar = player["username"], player["avatar"]
            player["avatar_url"] = avatar_url(profile)
        socket_join_room(room["code"])
        _persist(room)
        emit(
            "roomJoined",
            {
                "roomCode": room["code"],
                "playerId": player["id"],
                "recoveryToken": player["recovery_token"],
                "username": player["username"],
                "avatar": player["avatar"],
                "avatarUrl": player.get("avatar_url"),
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
        profile = resolve_profile(payload["profileToken"]) if payload.get("profileToken") else None
        room, player, rejoined = _manager().join_room(
            request.sid,
            payload.get("roomCode"),
            payload.get("username"),
            payload.get("avatar"),
            payload.get("recoveryToken"),
            profile.id if profile else None,
        )
        if profile and not rejoined:
            profile.display_name, profile.avatar = player["username"], player["avatar"]
            player["avatar_url"] = avatar_url(profile)
        socket_join_room(room["code"])
        _persist(room)
        emit(
            "roomJoined",
            {
                "roomCode": room["code"],
                "playerId": player["id"],
                "recoveryToken": player["recovery_token"],
                "username": player["username"],
                "avatar": player["avatar"],
                "avatarUrl": player.get("avatar_url"),
                "isHost": player["id"] == room["host_id"],
                "spectator": player["spectator"],
                "rejoined": rejoined,
            },
        )
        socketio.emit(
            "notification",
            {
                "message": (
                    f"{player['username']} rejoined and can continue the game."
                    if rejoined
                    else f"{player['username']} joined the room."
                )
            },
            to=room["code"],
        )
        _send_state(room)
        if room["status"] == "playing":
            _schedule_bot_turn(room)
    except Exception as exc:
        _error(exc)


@socketio.on("rejoinRoom")
def handle_rejoin_room(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().rejoin_room(
            request.sid, payload.get("roomCode"), payload.get("recoveryToken")
        )
        socket_join_room(room["code"])
        _persist(room)
        emit(
            "roomJoined",
            {
                "roomCode": room["code"],
                "playerId": player["id"],
                "recoveryToken": player["recovery_token"],
                "username": player["username"],
                "avatar": player["avatar"],
                "avatarUrl": player.get("avatar_url"),
                "isHost": player["id"] == room["host_id"],
                "spectator": player["spectator"],
                "rejoined": True,
            },
        )
        socketio.emit(
            "notification",
            {"message": f"{player['username']} rejoined and can continue the game."},
            to=room["code"],
        )
        _send_state(room)
        if room["status"] == "playing":
            _schedule_bot_turn(room)
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


@socketio.on("setRoomOptions")
def handle_set_room_options(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, payload: dict[str, Any]) -> str:
        if room["host_id"] != player_id:
            raise GameRuleError("Only the host can change table options.")
        set_room_options(
            room,
            str(payload.get("playFormat") or "individual"),
            payload.get("rules"),
        )
        return "Custom table rules updated."

    _with_player_action(data, action)


@socketio.on("addBot")
def handle_add_bot(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, payload: dict[str, Any]) -> str:
        bot = _manager().add_bot(
            room,
            player_id,
            payload.get("difficulty"),
            payload.get("personality"),
        )
        label = str(bot["bot_difficulty"]).title()
        return f"{bot['username']} joined as a {label} computer opponent."

    _with_player_action(data, action)


@socketio.on("removeBot")
def handle_remove_bot(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, payload: dict[str, Any]) -> str:
        bot = _manager().remove_bot(room, player_id, payload.get("botId"))
        return f"{bot['username']} was removed."

    _with_player_action(data, action)


@socketio.on("playCard")
def handle_play_card(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, payload: dict[str, Any]) -> None:
        play_card(
            room,
            player_id,
            str(payload.get("cardId") or ""),
            payload.get("chosenColor"),
            payload.get("targetPlayerId"),
        )

    _with_player_action(data, action, sound_effect="play")


@socketio.on("drawCard")
def handle_draw_card(data: dict[str, Any] | None) -> None:
    result: dict[str, Any] = {"auto_played": False, "room_code": None}

    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> None:
        previous_top = room["discard_pile"][-1]["id"]
        draw_card(room, player_id)
        result["room_code"] = room["code"]
        result["auto_played"] = bool(
            room.get("discard_pile") and room["discard_pile"][-1]["id"] != previous_top
        )

    _with_player_action(data, action)
    if result["auto_played"] and result["room_code"]:
        socketio.emit("soundEffect", {"type": "play"}, to=result["room_code"])


@socketio.on("passTurn")
def handle_pass_turn(data: dict[str, Any] | None) -> None:
    _with_player_action(data, lambda room, player_id, _: pass_turn(room, player_id))


@socketio.on("declareUno")
def handle_declare_uno(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> str | None:
        player = next(player for player in room["players"] if player["id"] == player_id)
        return f"{player['username']} called UNO!" if declare_uno(room, player_id) else None

    _with_player_action(data, action, sound_effect="uno")


@socketio.on("catchUno")
def handle_catch_uno(data: dict[str, Any] | None) -> None:
    def action(room: dict[str, Any], player_id: str, _: dict[str, Any]) -> str:
        catcher = next(player for player in room["players"] if player["id"] == player_id)
        offender_name = catch_uno(room, player_id)
        return f"{catcher['username']} caught {offender_name}. Two-card penalty!"

    _with_player_action(data, action, sound_effect="catch")


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


@socketio.on("voiceJoin")
def handle_voice_join(data: dict[str, Any] | None) -> None:
    try:
        room, player = _manager().room_for_sid(
            request.sid, (data or {}).get("roomCode")
        )
        if player.get("is_bot"):
            raise GameRuleError("Computer players cannot join voice chat.")
        room.setdefault("voice_members", {})[player["id"]] = {"speaking": False}
        _broadcast_voice(room)
    except Exception as exc:
        _error(exc)


@socketio.on("voiceLeave")
def handle_voice_leave(data: dict[str, Any] | None) -> None:
    try:
        room, player = _manager().room_for_sid(
            request.sid, (data or {}).get("roomCode")
        )
        _remove_voice_member(room, player["id"])
    except Exception as exc:
        _error(exc)


@socketio.on("voiceSpeaking")
def handle_voice_speaking(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().room_for_sid(request.sid, payload.get("roomCode"))
        member = room.setdefault("voice_members", {}).get(player["id"])
        if member is None:
            raise GameRuleError("Join voice chat before sending voice status.")
        speaking = bool(payload.get("speaking"))
        if member.get("speaking") != speaking:
            member["speaking"] = speaking
            _broadcast_voice(room)
    except Exception as exc:
        _error(exc)


@socketio.on("voiceSignal")
def handle_voice_signal(data: dict[str, Any] | None) -> None:
    try:
        payload = data or {}
        room, player = _manager().room_for_sid(request.sid, payload.get("roomCode"))
        if player["id"] not in room.setdefault("voice_members", {}):
            raise GameRuleError("Join voice chat before connecting to another player.")
        target_id = str(payload.get("targetPlayerId") or "")
        target = next(
            (
                seat
                for seat in room["players"]
                if seat["id"] == target_id
                and seat["connected"]
                and seat.get("socket_id")
                and target_id in room["voice_members"]
            ),
            None,
        )
        signal = payload.get("signal")
        if not target or not isinstance(signal, dict):
            raise GameRuleError("That voice participant is unavailable.")
        if signal.get("type") not in {"offer", "answer", "candidate"}:
            raise GameRuleError("Unsupported voice signal.")
        if len(json.dumps(signal)) > 12_000:
            raise GameRuleError("Voice signal is too large.")
        socketio.emit(
            "voiceSignal",
            {"fromPlayerId": player["id"], "signal": signal},
            to=target["socket_id"],
        )
    except Exception as exc:
        _error(exc)


@socketio.on("leaveRoom")
def handle_leave_room(data: dict[str, Any] | None) -> None:
    try:
        requested = (data or {}).get("roomCode")
        room, _ = _manager().room_for_sid(request.sid, requested)
        code = room["code"]
        _, leaving_player = _manager().room_for_sid(request.sid, requested)
        _remove_voice_member(room, leaving_player["id"])
        socket_leave_room(code)
        remaining, removed_code = _manager().leave(request.sid)
        if remaining:
            if remaining["status"] == "finished" and rematch_all_ready(remaining):
                start_game(remaining)
            _persist(remaining)
            _send_state(remaining)
            if remaining["status"] == "playing":
                _schedule_bot_turn(remaining)
        elif removed_code:
            delete_room(removed_code)
        emit("leftRoom")
    except Exception as exc:
        _error(exc)


@socketio.on("disconnect")
def handle_disconnect() -> None:
    identity = _manager().sid_to_player.get(request.sid)
    room_before = _manager().rooms.get(identity[0]) if identity else None
    player_id = identity[1] if identity else None
    if room_before and player_id:
        _remove_voice_member(room_before, player_id)
    room = _manager().disconnect(request.sid)
    if room:
        if player_id:
            disconnected_player = next((p for p in room["players"] if p["id"] == player_id), None)
            if disconnected_player and disconnected_player.get("reconnect_deadline"):
                _schedule_reconnect_timeout(room, disconnected_player)
        _persist(room)
        _send_state(room)
        if room["status"] == "playing":
            _schedule_bot_turn(room)


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
