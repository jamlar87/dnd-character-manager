"""Session persistence service, independent of FastAPI request handling."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from .db import connect

# Sweeping expired rows used to run as a DELETE + COMMIT on EVERY authenticated request — a writer
# transaction serialising the whole app on the sheet's 2-second poll. Now each db file is swept at
# most once a minute, plus once at startup (main.lifespan).
_SWEEP_INTERVAL = 60.0
_last_sweep: dict[str, float] = {}


def _sweep(db, db_path: Path, *, force: bool = False) -> None:
    key = str(db_path)
    now = time.monotonic()
    last = _last_sweep.get(key)
    if not force and last is not None and (now - last) < _SWEEP_INTERVAL:
        return
    _last_sweep[key] = now
    db.execute("DELETE FROM sessions WHERE datetime(expires_at) <= datetime('now')")
    db.commit()


def sweep_expired_sessions(db_path: Path, *, force: bool = True) -> None:
    db = connect(db_path)
    try:
        _sweep(db, db_path, force=force)
    finally:
        db.close()


def create_session(db_path: Path, user_id: int, ttl_days: int) -> str:
    token = secrets.token_hex(32)
    db = connect(db_path)
    try:
        db.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, datetime('now', ?))",
            (user_id, token, f"+{int(ttl_days)} days"),
        )
        db.commit()
        _sweep(db, db_path, force=True)
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
        _sweep(db, db_path)
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
