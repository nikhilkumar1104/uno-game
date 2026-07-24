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
BOT_AVATARS = ("bolt", "wave", "leaf", "star", "nova")
BOT_DIFFICULTIES = ("easy", "medium", "hard")
BOT_PERSONALITIES = ("balanced", "aggressive", "defensive", "wild_saver")


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
                self._normalize_room(room)
                for player in room.get("players", []):
                    player["connected"] = bool(player.get("is_bot"))
                    player["socket_id"] = None
                self.rooms[room["code"]] = room

    @staticmethod
    def _normalize_room(room: dict[str, Any]) -> None:
        defaults = {
            "mode": "classic",
            "play_format": "individual",
            "rules": {"seven_zero": False, "jump_in": False, "forced_play": False},
            "team_scores": {"0": 0, "1": 0},
            "round_number": 0,
            "pending_draw": 0,
            "pending_draw_type": None,
            "wild4_challenge": None,
            "last_challenge_result": None,
            "match_champion": None,
            "rematch_choices": {},
            "rematch_deadline": None,
            "voice_members": {},
            "uno_catch_available_at": None,
        }
        for key, value in defaults.items():
            room.setdefault(key, value)
        room["rules"] = {
            "seven_zero": bool(room.get("rules", {}).get("seven_zero", False)),
            "jump_in": bool(room.get("rules", {}).get("jump_in", False)),
            "forced_play": bool(room.get("rules", {}).get("forced_play", False)),
        }
        room["voice_members"] = {}
        for row in room.get("leaderboard", {}).values():
            row.setdefault("points", 0)
            row.setdefault("wins", 0)
        room["_bot_task_scheduled"] = False
        for player in room.get("players", []):
            player.setdefault("is_bot", False)
            player.setdefault("team", None)
            if player.get("is_bot"):
                player.setdefault("bot_difficulty", "medium")
                player.setdefault("bot_personality", "balanced")

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
            "is_bot": False,
            "team": None,
        }

    def add_bot(
        self,
        room: dict[str, Any],
        host_id: str,
        difficulty: Any = "medium",
        personality: Any = "balanced",
    ) -> dict[str, Any]:
        """Add one server-controlled seat to a lobby."""
        with self.lock:
            if room["host_id"] != host_id:
                raise GameRuleError("Only the host can add computer players.")
            if room["status"] != "lobby":
                raise GameRuleError("Computer players can be added only in the lobby.")
            if sum(not player["spectator"] for player in room["players"]) >= 6:
                raise GameRuleError("The table already has 6 active players.")

            used_names = {player["username"].casefold() for player in room["players"]}
            number = next(
                index
                for index in range(1, 100)
                if f"Computer {index}".casefold() not in used_names
            )
            difficulty_value = str(difficulty or "medium").lower()
            personality_value = str(personality or "balanced").lower()
            if difficulty_value not in BOT_DIFFICULTIES:
                raise GameRuleError("Choose Easy, Medium, or Hard computer difficulty.")
            if personality_value not in BOT_PERSONALITIES:
                raise GameRuleError("Choose a valid computer personality.")
            bot = {
                "id": f"bot_{secrets.token_urlsafe(18)}",
                "username": f"Computer {number}",
                "avatar": BOT_AVATARS[(number - 1) % len(BOT_AVATARS)],
                "socket_id": None,
                "connected": True,
                "spectator": False,
                "left": False,
                "hand": [],
                "said_uno": False,
                "is_bot": True,
                "bot_difficulty": difficulty_value,
                "bot_personality": personality_value,
                "team": None,
            }
            room["players"].append(bot)
            room["updated_at"] = time.time()
            return bot

    def remove_bot(self, room: dict[str, Any], host_id: str, bot_id: Any) -> dict[str, Any]:
        """Remove a specific computer seat before a game starts."""
        with self.lock:
            if room["host_id"] != host_id:
                raise GameRuleError("Only the host can remove computer players.")
            if room["status"] != "lobby":
                raise GameRuleError("Computer players can be removed only in the lobby.")
            bot = next(
                (
                    player
                    for player in room["players"]
                    if player["id"] == str(bot_id or "") and player.get("is_bot")
                ),
                None,
            )
            if not bot:
                raise GameRuleError("That computer seat was not found.")
            room["players"].remove(bot)
            room["updated_at"] = time.time()
            return bot

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
                "mode": "classic",
                "play_format": "individual",
                "rules": {"seven_zero": False, "jump_in": False, "forced_play": False},
                "team_scores": {"0": 0, "1": 0},
                "round_number": 0,
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
                "uno_catch_available_at": None,
                "drawn_card_id": None,
                "drawn_by_id": None,
                "pending_draw": 0,
                "pending_draw_type": None,
                "wild4_challenge": None,
                "last_challenge_result": None,
                "match_champion": None,
                "rematch_choices": {},
                "rematch_deadline": None,
                "voice_members": {},
                "created_at": now,
                "updated_at": now,
            }
            self.rooms[code] = room
            self.sid_to_player[sid] = (code, player["id"])
            return room, player

    def join_room(
        self, sid: str, room_code: Any, username: Any, avatar: Any, player_id: Any = None
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        with self.lock:
            self._require_unseated_socket(sid)
            code = str(room_code or "").strip().upper()
            room = self.rooms.get(code)
            if not room:
                raise GameRuleError("Room not found. Check the invite code.")
            name = clean_username(username)
            existing = next(
                (player for player in room["players"] if player["username"].casefold() == name.casefold()),
                None,
            )
            recovery_token = str(player_id or "")
            if existing:
                valid_recovery = (
                    recovery_token
                    and secrets.compare_digest(existing["id"], recovery_token)
                    and (not existing.get("left") or room["status"] == "playing")
                    and not existing.get("is_bot")
                )
                if not valid_recovery:
                    raise GameRuleError(
                        "That name already has a reserved seat. Reopen this room in the same browser to continue."
                    )
                old_sid = existing.get("socket_id")
                if old_sid:
                    self.sid_to_player.pop(old_sid, None)
                existing["socket_id"] = sid
                existing["connected"] = True
                existing["left"] = False
                self.sid_to_player[sid] = (code, existing["id"])
                room["updated_at"] = time.time()
                return room, existing, True

            active_count = sum(not player["spectator"] for player in room["players"])
            spectator = room["status"] != "lobby" or active_count >= 6
            spectator_count = sum(player["spectator"] for player in room["players"])
            if spectator and spectator_count >= 12:
                raise GameRuleError("This room's spectator seats are full.")

            player = self._new_player(sid, name, clean_avatar(avatar), spectator=spectator)
            room["players"].append(player)
            room["updated_at"] = time.time()
            self.sid_to_player[sid] = (code, player["id"])
            return room, player, False

    def rejoin_room(self, sid: str, room_code: Any, player_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.lock:
            code = str(room_code or "").strip().upper()
            room = self.rooms.get(code)
            if not room:
                raise GameRuleError("That room has expired.")
            token = str(player_id or "")
            player = next((seat for seat in room["players"] if seat["id"] == token), None)
            if (
                not player
                or player.get("is_bot")
                or (player.get("left") and room["status"] != "playing")
            ):
                raise GameRuleError("Your saved seat is no longer available.")
            old_sid = player.get("socket_id")
            if old_sid:
                self.sid_to_player.pop(old_sid, None)
            player["socket_id"] = sid
            player["connected"] = True
            player["left"] = False
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
            if not any(not seat.get("is_bot") and not seat.get("left") for seat in room["players"]):
                self.rooms.pop(code, None)
                return None, code
            room["updated_at"] = time.time()
            return room, code

    @staticmethod
    def _transfer_host_if_needed(room: dict[str, Any]) -> None:
        host = next((seat for seat in room["players"] if seat["id"] == room["host_id"]), None)
        if host and host["connected"] and not host.get("left") and not host.get("is_bot"):
            return
        replacement = next(
            (
                seat
                for seat in room["players"]
                if seat["connected"] and not seat["spectator"] and not seat.get("is_bot")
            ),
            None,
        ) or next(
            (seat for seat in room["players"] if seat["connected"] and not seat.get("is_bot")),
            None,
        )
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
                if room["updated_at"] < cutoff
                and not any(p["connected"] and not p.get("is_bot") for p in room["players"])
            ]
            for code in expired:
                self.rooms.pop(code, None)
            return expired
