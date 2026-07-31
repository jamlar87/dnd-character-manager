"""User repository operations."""

from __future__ import annotations

from pathlib import Path

from .db import connect
from .auth import hash_password


def get_user(db_path: Path, email: str) -> dict | None:
    db = connect(db_path)
    try:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def update_password(db_path: Path, user_id: int, password: str) -> None:
    db = connect(db_path)
    try:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        db.commit()
    finally:
        db.close()


def create_user(db_path: Path, email: str, password: str) -> int:
    db = connect(db_path)
    try:
        cursor = db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, hash_password(password)))
        db.commit()
        return int(cursor.lastrowid)
    finally:
        db.close()


def is_admin(user: dict) -> bool:
    return bool(user.get("is_admin"))
