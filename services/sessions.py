"""Session persistence service, independent of FastAPI request handling."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from .db import connect


def create_session(db_path: Path, user_id: int, ttl_days: int) -> str:
    token = secrets.token_hex(32)
    db = connect(db_path)
    try:
        db.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, datetime('now', ?))",
            (user_id, token, f"+{int(ttl_days)} days"),
        )
        db.commit()
    finally:
        db.close()
    return token


def get_user_by_token(db_path: Path, token: str) -> dict | None:
    db = connect(db_path)
    try:
        row = db.execute(
            """SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id
               WHERE s.token = ? AND datetime(s.expires_at) > datetime('now')""",
            (token,),
        ).fetchone()
        db.execute("DELETE FROM sessions WHERE datetime(expires_at) <= datetime('now')")
        db.commit()
        return dict(row) if row else None
    finally:
        db.close()


def invalidate_user_sessions(db_path: Path, user_id: int) -> None:
    db = connect(db_path)
    try:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.commit()
    finally:
        db.close()
