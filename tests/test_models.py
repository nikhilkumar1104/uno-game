import sqlite3

from sqlalchemy import inspect

from app import create_app
from models import db


def test_existing_sqlite_database_gets_scoring_columns(tmp_path):
    """Render deployments created before scoring migrate without a manual reset."""
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE match_record (
            id INTEGER PRIMARY KEY,
            game_id VARCHAR(32) NOT NULL UNIQUE,
            room_code VARCHAR(8) NOT NULL,
            winner_id VARCHAR(64) NOT NULL,
            winner_name VARCHAR(18) NOT NULL,
            players_json TEXT NOT NULL,
            move_count INTEGER NOT NULL DEFAULT 0,
            finished_at DATETIME NOT NULL
        );
        CREATE TABLE player_stat (
            id INTEGER PRIMARY KEY,
            player_key VARCHAR(64) NOT NULL UNIQUE,
            display_name VARCHAR(18) NOT NULL,
            games INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL
        );
        """
    )
    connection.close()

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
            "SECRET_KEY": "migration-test",
        }
    )
    with app.app_context():
        inspector = inspect(db.engine)
        match_columns = {column["name"] for column in inspector.get_columns("match_record")}
        stat_columns = {column["name"] for column in inspector.get_columns("player_stat")}
        assert "points" in match_columns
        assert "points" in stat_columns
