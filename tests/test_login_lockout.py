"""A dead `dnd_token` cookie must never lock anyone out of the login form.

The security middleware requires `x-csrf-token` on every non-GET request when a
`dnd_token` cookie is present. A native <form> submit cannot set that header, so
a cookie whose session row was gone made POST /login answer 403 forever — the
user could not log back in without clearing cookies by hand. Reported as
"I can't log in now"; reproduced with a curl carrying a stale cookie.
"""

import secrets
import sqlite3

from services.sessions import create_session


def _stale() -> dict:
    return {"dnd_token": "cookie-from-a-session-that-no-longer-exists"}


def _valid_session_cookie(seeded_db) -> dict:
    token = create_session(seeded_db["db_path"], 1, 30)
    return {"dnd_token": token}


# ── the lockout itself ───────────────────────────────────────────────────────

def test_stale_cookie_does_not_block_the_login_form(client, seeded_db):
    """The exact reported failure: correct credentials, dead cookie → was 403."""
    # follow_redirects=False: TestClient follows the 303 to /dashboard by default,
    # which would hide the very status this test is about
    r = client.post("/login", data={"email": "test@test.com", "password": "testpass"},
                    cookies=_stale(), follow_redirects=False)
    assert r.status_code == 303, f"login blocked with a dead cookie: {r.status_code} {r.text[:200]}"
    assert "dnd_token" in r.headers.get("set-cookie", "")


def test_login_page_clears_the_dead_cookie(client, seeded_db):
    """So the wedge self-heals on a plain reload — no manual cookie clearing."""
    r = client.get("/login", cookies=_stale())
    assert r.status_code == 200
    cleared = r.headers.get("set-cookie", "")
    assert "dnd_token" in cleared and ("Max-Age=0" in cleared or 'dnd_token=""' in cleared), cleared


def test_rejected_login_clears_the_dead_cookie(client, seeded_db):
    r = client.post("/login", data={"email": "test@test.com", "password": "nope"}, cookies=_stale())
    assert r.status_code == 200
    assert "dnd_token" in r.headers.get("set-cookie", "")


def test_a_live_session_is_left_alone_on_the_login_page(client, seeded_db):
    """Only *dead* cookies get dropped; a valid one must not be deleted."""
    r = client.get("/login", cookies=_valid_session_cookie(seeded_db))
    cookie = r.headers.get("set-cookie", "")
    assert 'dnd_token="";' not in cookie.replace(" ", ""), cookie


# ── guards: the exemption must not loosen CSRF for real writes ───────────────

def test_csrf_header_still_required_for_api_writes(client, seeded_db):
    r = client.post("/api/sync-combat-hp", json={"updates": []},
                    cookies=_valid_session_cookie(seeded_db))
    assert r.status_code == 403, "API writes must still demand x-csrf-token"
    assert "CSRF" in r.text


def test_api_write_with_the_header_still_passes_the_middleware(client, seeded_db):
    """Same request + the header: the middleware lets it through (not 403)."""
    cookies = _valid_session_cookie(seeded_db)
    cookies["csrf_token"] = "abc123"
    r = client.post("/api/sync-combat-hp", json={"updates": []},
                    cookies=cookies, headers={"X-CSRF-Token": "abc123"})
    assert r.status_code != 403, r.text[:200]


def test_cross_origin_form_post_is_still_rejected(client, seeded_db):
    """The exemption drops the *token* check, not the same-origin check."""
    r = client.post("/login", data={"email": "test@test.com", "password": "testpass"},
                    cookies=_stale(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "Cross-origin" in r.text


def test_same_origin_form_post_is_allowed(client, seeded_db):
    r = client.post("/login", data={"email": "test@test.com", "password": "testpass"},
                    cookies=_stale(), headers={"Origin": "http://testserver"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_registration_and_reset_are_exempt_too(client, seeded_db):
    """They are native forms for the same reason; both must stay reachable."""
    import main
    assert {"/login", "/register", "/reset-password"} <= main.CSRF_FORM_POST_PATHS
    r = client.post("/reset-password", data={"email": "test@test.com"}, cookies=_stale())
    assert r.status_code != 403, r.text[:200]
