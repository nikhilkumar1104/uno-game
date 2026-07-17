"""Thread-safe runtime room registry and player identity management."""

from __future__ import annotations

import re
import secrets
import string
import threading
import time
from typing import Any

from game_engine import GameRuleError, ensure_connected_turn


ROOM_ALPHABET = string.ascii_uppercase + string.digits
USERNAME_PATTERN = re.compile(r"[^A-Za-z0-9 _-]")
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def clean_username(value: Any) -> str:
    value = USERNAME_PATTERN.sub("", str(value or "")).strip()
    value = re.sub(r"\s+", " ", value)
    if not 2 <= len(value) <= 18:
        raise GameRuleError("Display names must contain 2 to 18 letters or numbers.")
    return value


def clean_avatar(value: Any) -> str:
    value = str(value or "ember").lower().strip()
    return value if value in {"ember", "bolt", "wave", "leaf", "star", "nova"} else "ember"


def clean_chat(value: Any) -> str:
    value = CONTROL_PATTERN.sub("", str(value or "")).replace("<", "").replace(">", "")
    value = value.strip()[:240]
    if not value:
        raise GameRuleError("Message cannot be empty.")
    return value


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, Any]] = {}
        self.sid_to_player: dict[str, tuple[str, str]] = {}
        self.lock = threading.RLock()

    def restore(self, snapshots: list[dict[str, Any]]) -> None:
        with self.lock:
            for room in snapshots:
                for player in room.get("players", []):
                    player["connected"] = False
                    player["socket_id"] = None
                self.rooms[room["code"]] = room

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(6))
            if code not in self.rooms:
                return code

    def _require_unseated_socket(self, sid: str) -> None:
        if sid in self.sid_to_player:
            raise GameRuleError("Leave your current room before joining another one.")

    @staticmethod
    def _new_player(
        sid: str, username: str, avatar: str, *, spectator: bool = False
    ) -> dict[str, Any]:
        return {
            "id": secrets.token_urlsafe(24),
            "username": username,
            "avatar": avatar,
            "socket_id": sid,
            "connected": True,
            "spectator": spectator,
            "left": False,
            "hand": [],
            "said_uno": False,
        }

    def create_room(self, sid: str, username: Any, avatar: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.lock:
            self._require_unseated_socket(sid)
            name = clean_username(username)
            player = self._new_player(sid, name, clean_avatar(avatar))
            code = self._new_code()
            now = time.time()
            room = {
                "code": code,
                "host_id": player["id"],
                "status": "lobby",
                "players": [player],
                "chat": [],
                "leaderboard": {},
                "match_history": [],
                "game_id": None,
                "draw_pile": [],
                "discard_pile": [],
                "direction": 1,
                "current_index": 0,
                "current_color": None,
                "winner": None,
                "events": [],
                "move_count": 0,
                "uno_pending_player_id": None,
                "drawn_card_id": None,
                "drawn_by_id": None,
                "created_at": now,
                "updated_at": now,
            }
            self.rooms[code] = room
            self.sid_to_player[sid] = (code, player["id"])
            return room, player

    def join_room(
        self, sid: str, room_code: Any, username: Any, avatar: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.lock:
            self._require_unseated_socket(sid)
            code = str(room_code or "").strip().upper()
            room = self.rooms.get(code)
            if not room:
                raise GameRuleError("Room not found. Check the invite code.")
            name = clean_username(username)
            if any(player["username"].casefold() == name.casefold() for player in room["players"]):
                raise GameRuleError("That display name is already used in this room.")

            active_count = sum(not player["spectator"] for player in room["players"])
            spectator = room["status"] != "lobby" or active_count >= 6
            spectator_count = sum(player["spectator"] for player in room["players"])
            if spectator and spectator_count >= 12:
                raise GameRuleError("This room's spectator seats are full.")

            player = self._new_player(sid, name, clean_avatar(avatar), spectator=spectator)
            room["players"].append(player)
            room["updated_at"] = time.time()
            self.sid_to_player[sid] = (code, player["id"])
            return room, player

    def rejoin_room(self, sid: str, room_code: Any, player_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.lock:
            code = str(room_code or "").strip().upper()
            room = self.rooms.get(code)
            if not room:
                raise GameRuleError("That room has expired.")
            token = str(player_id or "")
            player = next((seat for seat in room["players"] if seat["id"] == token), None)
            if not player or player.get("left"):
                raise GameRuleError("Your saved seat is no longer available.")
            old_sid = player.get("socket_id")
            if old_sid:
                self.sid_to_player.pop(old_sid, None)
            player["socket_id"] = sid
            player["connected"] = True
            self.sid_to_player[sid] = (code, player["id"])
            room["updated_at"] = time.time()
            return room, player

    def room_for_sid(self, sid: str, requested_code: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.lock:
            identity = self.sid_to_player.get(sid)
            if not identity:
                raise GameRuleError("Join a room before sending game actions.")
            code, player_id = identity
            if requested_code and str(requested_code).upper() != code:
                raise GameRuleError("Socket identity does not match that room.")
            room = self.rooms.get(code)
            if not room:
                raise GameRuleError("Room not found.")
            player = next((seat for seat in room["players"] if seat["id"] == player_id), None)
            if not player or not player["connected"] or player["socket_id"] != sid:
                raise GameRuleError("This player session is no longer active.")
            return room, player

    def disconnect(self, sid: str) -> dict[str, Any] | None:
        with self.lock:
            identity = self.sid_to_player.pop(sid, None)
            if not identity:
                return None
            code, player_id = identity
            room = self.rooms.get(code)
            if not room:
                return None
            player = next((seat for seat in room["players"] if seat["id"] == player_id), None)
            if player:
                player["connected"] = False
                player["socket_id"] = None
            self._transfer_host_if_needed(room)
            if room["status"] == "playing":
                ensure_connected_turn(room)
            room["updated_at"] = time.time()
            return room

    def leave(self, sid: str) -> tuple[dict[str, Any] | None, str | None]:
        with self.lock:
            identity = self.sid_to_player.pop(sid, None)
            if not identity:
                return None, None
            code, player_id = identity
            room = self.rooms.get(code)
            if not room:
                return None, code
            player = next((seat for seat in room["players"] if seat["id"] == player_id), None)
            if player and room["status"] == "playing" and not player["spectator"]:
                player["connected"] = False
                player["socket_id"] = None
                player["left"] = True
                ensure_connected_turn(room)
            elif player:
                room["players"].remove(player)

            self._transfer_host_if_needed(room)
            if not room["players"] or not any(
                not seat["spectator"] and not seat.get("left") for seat in room["players"]
            ):
                self.rooms.pop(code, None)
                return None, code
            room["updated_at"] = time.time()
            return room, code

    @staticmethod
    def _transfer_host_if_needed(room: dict[str, Any]) -> None:
        host = next((seat for seat in room["players"] if seat["id"] == room["host_id"]), None)
        if host and host["connected"] and not host.get("left"):
            return
        replacement = next(
            (seat for seat in room["players"] if seat["connected"] and not seat["spectator"]),
            None,
        ) or next((seat for seat in room["players"] if seat["connected"]), None)
        if replacement:
            room["host_id"] = replacement["id"]

    def add_chat(self, room: dict[str, Any], player: dict[str, Any], text: Any) -> dict[str, Any]:
        with self.lock:
            message = {
                "id": secrets.token_hex(8),
                "playerId": player["id"],
                "username": player["username"],
                "avatar": player["avatar"],
                "text": clean_chat(text),
                "sentAt": int(time.time()),
            }
            room["chat"].append(message)
            room["chat"] = room["chat"][-80:]
            room["updated_at"] = time.time()
            return message

    def prune(self, max_age_seconds: int = 21_600) -> list[str]:
        with self.lock:
            cutoff = time.time() - max_age_seconds
            expired = [
                code
                for code, room in self.rooms.items()
                if room["updated_at"] < cutoff and not any(p["connected"] for p in room["players"])
            ]
            for code in expired:
                self.rooms.pop(code, None)
            return expired
