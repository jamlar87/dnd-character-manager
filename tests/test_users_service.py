"""Tests for user repository operations."""

from services.auth import verify_password
from services.db import connect
from services.users import create_user, get_user, is_admin, update_password


def test_user_repository_round_trip(tmp_path):
    path = tmp_path / "users.db"
    db = connect(path)
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, password_hash TEXT, is_admin INTEGER DEFAULT 0)")
    db.commit(); db.close()
    user_id = create_user(path, "player@example.com", "old-password")
    user = get_user(path, "player@example.com")
    assert user["id"] == user_id
    assert verify_password("old-password", user["password_hash"])
    update_password(path, user_id, "new-password")
    user = get_user(path, "player@example.com")
    assert verify_password("new-password", user["password_hash"])
    assert not is_admin(user)


def test_is_admin_reads_flag():
    assert is_admin({"is_admin": 1})
    assert not is_admin({"is_admin": 0})
    assert not is_admin({})


def test_duplicate_user_is_rejected(tmp_path):
    path = tmp_path / "users.db"
    db = connect(path)
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, password_hash TEXT, is_admin INTEGER DEFAULT 0)")
    db.commit(); db.close()
    create_user(path, "same@example.com", "password")
    try:
        create_user(path, "same@example.com", "password")
    except Exception as exc:
        assert "unique" in str(exc).lower()
    else:
        raise AssertionError("duplicate user was accepted")
