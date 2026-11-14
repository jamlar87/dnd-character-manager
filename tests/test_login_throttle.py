"""Brute-force protection on POST /login and POST /register.

The login form used to verify a bcrypt hash for every request with no cap, so
guessing was limited only by the CPU. These tests pin the sliding-window
behaviour: five failures lock an account for the window, a real login wipes the
slate, expiry restores access, and one account's failures never touch another's.
"""

import sqlite3
import uuid
from types import SimpleNamespace

import pytest

import routes.auth as auth_routes
from main import _hash
from services import login_guard

PASSWORD = "testpass"


@pytest.fixture(autouse=True)
def _clean_guard():
    """The guard is process-global; each test starts and ends with empty state."""
    login_guard.reset()
    yield
    login_guard.reset()


@pytest.fixture
def fake_clock(monkeypatch):
    """Controllable monotonic clock: `clock["t"] += 901` jumps the window."""
    clock = {"t": 10_000.0}
    monkeypatch.setattr(login_guard, "_now", lambda: clock["t"])
    return clock


def _make_user(seeded_db, password=PASSWORD):
    """A fresh account per test, so a leftover bucket can never bleed across."""
    email = f"throttle-{uuid.uuid4().hex[:12]}@test.com"
    con = sqlite3.connect(str(seeded_db["db_path"]))
    try:
        con.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 0)",
            (email, _hash(password)),
        )
        con.commit()
    finally:
        con.close()
    return email


def _login(client, email, password):
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=False
    )


def _register(client, email, password="secret1"):
    return client.post(
        "/register", data={"email": email, "password": password}, follow_redirects=False
    )


# ── the throttle ────────────────────────────────────────────────────────────

def test_five_failures_block_even_the_correct_password(client, seeded_db):
    email = _make_user(seeded_db)
    for attempt in range(5):
        r = _login(client, email, "wrong-password")
        assert r.status_code == 200, f"attempt {attempt}: {r.status_code} {r.text[:200]}"
        assert "Invalid email or password" in r.text

    blocked = _login(client, email, PASSWORD)
    assert blocked.status_code == 429, f"6th attempt was not throttled: {blocked.status_code}"
    assert "Too many attempts" in blocked.text
    assert int(blocked.headers["Retry-After"]) > 0


def test_a_real_login_clears_the_counter(client, seeded_db):
    email = _make_user(seeded_db)
    assert _login(client, email, "nope").status_code == 200
    assert _login(client, email, PASSWORD).status_code == 303

    # The next mistake must be a plain rejection, not the lockout page.
    r = _login(client, email, "nope")
    assert r.status_code == 200, "successful login left the failure counter armed"
    assert "Invalid email or password" in r.text
    assert _login(client, email, PASSWORD).status_code == 303


def test_window_expiry_restores_access(client, seeded_db, fake_clock):
    email = _make_user(seeded_db)
    for _ in range(5):
        assert _login(client, email, "wrong-password").status_code == 200
    assert _login(client, email, PASSWORD).status_code == 429

    fake_clock["t"] += auth_routes.LOGIN_WINDOW_S + 1
    assert _login(client, email, PASSWORD).status_code == 303


def test_counters_are_per_account(client, seeded_db):
    victim = _make_user(seeded_db)
    bystander = _make_user(seeded_db)
    for _ in range(5):
        _login(client, victim, "wrong-password")

    assert _login(client, victim, PASSWORD).status_code == 429
    # Another account from the same address (same IP budget) still gets in.
    assert _login(client, bystander, PASSWORD).status_code == 303


def test_unknown_email_is_throttled_like_a_real_one(client, seeded_db):
    """No enumeration hint: the lockout looks identical for absent accounts."""
    ghost = f"nobody-{uuid.uuid4().hex[:8]}@test.com"
    for _ in range(5):
        assert _login(client, ghost, "wrong-password").status_code == 200
    assert _login(client, ghost, "wrong-password").status_code == 429


def test_ip_budget_blocks_a_spray_across_accounts(client, seeded_db, monkeypatch):
    """Many distinct emails from one address hit the per-IP window, not the per-account one."""
    monkeypatch.setattr(auth_routes, "LOGIN_IP_LIMIT", 6)
    for i in range(6):
        _login(client, f"spray-{i}-{uuid.uuid4().hex[:6]}@test.com", "wrong-password")

    r = _login(client, _make_user(seeded_db), PASSWORD)
    assert r.status_code == 429, "per-IP budget did not fire"
    assert "Too many attempts" in r.text


def test_register_is_rate_limited_per_ip(client, seeded_db, monkeypatch):
    monkeypatch.setattr(auth_routes, "REGISTER_IP_LIMIT", 2)
    for _ in range(2):
        assert _register(client, f"reg-{uuid.uuid4().hex[:10]}@test.com").status_code == 303

    blocked = _register(client, f"reg-{uuid.uuid4().hex[:10]}@test.com")
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text
    assert blocked.headers["Retry-After"]


# ── client-key derivation ───────────────────────────────────────────────────

def test_forwarded_header_is_used_behind_a_local_proxy():
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"cf-connecting-ip": "203.0.113.9"},
    )
    assert auth_routes._client_ip(request) == "203.0.113.9"


def test_forwarded_header_is_ignored_on_a_direct_connection():
    request = SimpleNamespace(
        client=SimpleNamespace(host="93.184.216.34"),
        headers={"cf-connecting-ip": "198.51.100.7"},
    )
    assert auth_routes._client_ip(request) == "93.184.216.34"


def test_missing_addresses_coalesce_to_unknown():
    request = SimpleNamespace(client=None, headers={})
    assert auth_routes._client_ip(request) == "unknown"


# ── guard internals that matter for memory safety ───────────────────────────

def test_guard_cannot_grow_without_bound(monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_KEYS", 50)
    for i in range(200):
        login_guard.record_failure(f"flood:{i}", limit=5, window_s=900)
    assert len(login_guard._buckets) <= 50
