"""Tests for session repository/service boundary."""

import sqlite3
from datetime import datetime, timedelta

from services.sessions import create_session, get_user_by_token, invalidate_user_sessions


def db_with_schema(tmp_path):
    path = tmp_path / "sessions.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        );
        INSERT INTO users VALUES (1, 'one@example.com');
    """)
    db.commit(); db.close()
    return path


def test_create_and_read_session(tmp_path):
    path = db_with_schema(tmp_path)
    token = create_session(path, 1, 30)
    assert len(token) == 64
    user = get_user_by_token(path, token)
    assert user["email"] == "one@example.com"


def test_expired_session_is_rejected_and_cleaned(tmp_path):
    path = db_with_schema(tmp_path)
    db = sqlite3.connect(path)
    db.execute("INSERT INTO sessions(user_id, token, expires_at) VALUES (1, 'expired', datetime('now', '-1 day'))")
    db.commit(); db.close()
    assert get_user_by_token(path, "expired") is None
    db = sqlite3.connect(path)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    db.close()


def test_invalidate_user_sessions(tmp_path):
    path = db_with_schema(tmp_path)
    create_session(path, 1, 30)
    invalidate_user_sessions(path, 1)
    assert get_user_by_token(path, "missing") is None
    db = sqlite3.connect(path)
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=1").fetchone()[0] == 0
    db.close()
