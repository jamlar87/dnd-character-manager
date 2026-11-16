"""A logged-out hero handoff must survive the sign-in bounce.

Players who are not signed in when the DM app sends them to the wizard used to
land on /dashboard after logging in, so the sheet they built never returned to
the campaign. These tests pin the whole chain: the wizard rendering, the login
and register bounces, and the host allowlist that stops ?next= becoming an open
redirect.
"""

from urllib.parse import unquote

import pytest

from core.security import NEXT_ALLOWED_HOSTS, safe_next

DM_HANDOFF = "https://dm.jamlarnet.stream/campaign/1056/party/add?source_char_id={id}"


def _landed(resp) -> str:
    """Location headers percent-encode `{}`, so compare the decoded target."""
    return unquote(resp.headers["location"])


@pytest.fixture
def creds(seeded_db):
    return {"email": "test@test.com", "password": "testpass"}


# ── safe_next: local paths and allowlisted hosts only ─────────────────────────

def test_local_paths_are_kept():
    assert safe_next("/campaign/5") == "/campaign/5"


def test_protocol_relative_and_foreign_hosts_are_dropped():
    assert safe_next("//evil.example/x") == ""
    assert safe_next("https://evil.example/x") == ""
    assert safe_next("javascript:alert(1)") == ""
    assert safe_next("") == ""
    assert safe_next(None) == ""


def test_the_dm_host_is_allowlisted():
    assert "dm.jamlarnet.stream" in NEXT_ALLOWED_HOSTS
    assert safe_next(DM_HANDOFF) == DM_HANDOFF


# ── login bounce ─────────────────────────────────────────────────────────────

def test_login_page_renders_hidden_next(client, seeded_db):
    r = client.get("/login", params={"next": DM_HANDOFF})
    assert r.status_code == 200
    assert 'name="next"' in r.text
    assert "dm.jamlarnet.stream" in r.text


def test_login_page_drops_a_foreign_next(client, seeded_db):
    r = client.get("/login", params={"next": "https://evil.example/steal"})
    assert r.status_code == 200
    assert "evil.example" not in r.text


def test_login_returns_to_the_campaign(client, seeded_db, creds):
    r = client.post("/login", data={**creds, "next": DM_HANDOFF},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _landed(r) == DM_HANDOFF


def test_login_without_next_still_goes_to_the_dashboard(client, seeded_db, creds):
    r = client.post("/login", data=creds, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_a_failed_login_keeps_the_handoff(client, seeded_db, creds):
    r = client.post("/login", data={"email": creds["email"], "password": "wrong",
                                    "next": DM_HANDOFF})
    assert r.status_code == 200
    assert 'name="next"' in r.text and "dm.jamlarnet.stream" in r.text


def test_the_guard_redirect_carries_the_target(client, seeded_db):
    """A protected page hit while logged out must point back at itself."""
    r = client.get("/create", params={"next": DM_HANDOFF}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    loc = r.headers["location"]
    assert loc.startswith("/login?next=")
    assert "dm.jamlarnet.stream" in unquote(loc)


# ── register bounce ─────────────────────────────────────────────────────────

def test_register_page_carries_next(client, seeded_db):
    r = client.get("/register", params={"next": DM_HANDOFF})
    assert r.status_code == 200
    assert 'name="next"' in r.text and "dm.jamlarnet.stream" in r.text


def test_register_returns_to_the_campaign(client, seeded_db):
    r = client.post("/register",
                    data={"email": "fresh@example.com", "password": "hunter2x",
                          "next": DM_HANDOFF},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _landed(r) == DM_HANDOFF


# ── the wizard itself still hands off ───────────────────────────────────────

def test_create_page_exposes_next_to_the_client(client, seeded_db):
    from services.sessions import create_session
    token = create_session(seeded_db["db_path"], 1, 30)
    r = client.get("/create", params={"next": DM_HANDOFF},
                   cookies={"dnd_token": token})
    assert r.status_code == 200
    assert "CREATE_NEXT" in r.text and "dm.jamlarnet.stream" in r.text
