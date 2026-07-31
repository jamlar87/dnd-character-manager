"""Regression tests for security and schema hardening."""

import sqlite3


def test_new_database_does_not_create_default_admin(test_db):
    db = sqlite3.connect(test_db)
    assert db.execute("SELECT COUNT(*) FROM users WHERE email = 'admin'").fetchone()[0] == 0
    db.close()


def test_expired_session_is_rejected(seeded_db):
    db = sqlite3.connect(seeded_db["db_path"])
    db.execute("UPDATE sessions SET expires_at = datetime('now', '-1 day')")
    db.commit()
    db.close()

    from main import _get_user_by_token
    assert _get_user_by_token(seeded_db["user_token"]) is None


def test_reset_password_does_not_change_password(client, seeded_db):
    response = client.post(
        "/reset-password",
        data={"email": "test@test.com", "password": "new-password"},
    )
    assert response.status_code == 200
    assert "unavailable" in response.text.lower()

    from main import _get_user
    from main import _verify
    user = _get_user("test@test.com")
    assert user is not None
    assert _verify("testpass", user["password_hash"])
    assert not _verify("new-password", user["password_hash"])


def test_security_headers_are_present(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "x-request-id" in response.headers


def test_cross_origin_unsafe_request_is_rejected(client, auth_headers):
    response = client.post(
        "/api/dm/encounter/create",
        json={"name": "cross-origin"},
        headers={**auth_headers, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert "cross-origin" in response.json()["error"].lower()


def test_new_indexes_exist(test_db):
    db = sqlite3.connect(test_db)
    indexes = {
        row[1] for row in db.execute(
            "SELECT type, name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert {
        "idx_campaigns_user",
        "idx_encounters_user",
        "idx_npcs_user",
        "idx_traps_user",
        "idx_relationships_character",
        "idx_relationships_user",
    } <= indexes
    db.close()


def test_session_schema_has_expiration(test_db):
    db = sqlite3.connect(test_db)
    columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    assert "expires_at" in columns
    db.close()
