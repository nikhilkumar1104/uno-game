"""SQLite persistence for room snapshots, results, and player statistics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError


db = SQLAlchemy()


class RoomSnapshot(db.Model):
    code = db.Column(db.String(8), primary_key=True)
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatchRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    room_code = db.Column(db.String(8), nullable=False, index=True)
    winner_id = db.Column(db.String(64), nullable=False)
    winner_name = db.Column(db.String(18), nullable=False)
    players_json = db.Column(db.Text, nullable=False)
    move_count = db.Column(db.Integer, nullable=False, default=0)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class PlayerStat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(18), nullable=False)
    games = db.Column(db.Integer, nullable=False, default=0)
    wins = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def _snapshot_payload(room: dict[str, Any]) -> str:
    clean = json.loads(json.dumps(room))
    for player in clean["players"]:
        player["socket_id"] = None
        player["connected"] = False
    return json.dumps(clean, separators=(",", ":"))


def save_room(room: dict[str, Any]) -> None:
    snapshot = db.session.get(RoomSnapshot, room["code"])
    if snapshot:
        snapshot.payload = _snapshot_payload(room)
    else:
        db.session.add(RoomSnapshot(code=room["code"], payload=_snapshot_payload(room)))
    db.session.commit()


def delete_room(code: str) -> None:
    snapshot = db.session.get(RoomSnapshot, code)
    if snapshot:
        db.session.delete(snapshot)
        db.session.commit()


def load_rooms() -> list[dict[str, Any]]:
    restored: list[dict[str, Any]] = []
    for snapshot in db.session.execute(db.select(RoomSnapshot)).scalars():
        try:
            restored.append(json.loads(snapshot.payload))
        except (TypeError, json.JSONDecodeError):
            db.session.delete(snapshot)
    db.session.commit()
    return restored


def record_match(room: dict[str, Any]) -> bool:
    winner = room.get("winner")
    game_id = room.get("game_id")
    if not winner or not game_id:
        return False
    active = [player for player in room["players"] if not player["spectator"]]
    record = MatchRecord(
        game_id=game_id,
        room_code=room["code"],
        winner_id=winner["id"],
        winner_name=winner["username"],
        players_json=json.dumps([player["username"] for player in active]),
        move_count=room.get("move_count", 0),
    )
    db.session.add(record)
    for player in active:
        stat = db.session.execute(
            db.select(PlayerStat).where(PlayerStat.player_key == player["id"])
        ).scalar_one_or_none()
        if not stat:
            stat = PlayerStat(player_key=player["id"], display_name=player["username"])
            db.session.add(stat)
        stat.display_name = player["username"]
        stat.games += 1
        if player["id"] == winner["id"]:
            stat.wins += 1
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False
