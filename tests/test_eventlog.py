"""The audit trail: what changed, who changed it, and when.

`event_log` is a row per meaningful write. The write paths are covered by the
route tests below; this file pins the parts that make the trail trustworthy:

- `log_event` never raises. A lost audit row is bad; a lost character is worse.
- `before` and `after` come back as objects, not as strings.
- The read endpoints answer JSON, and they never leak another user's trail.
"""

from __future__ import annotations

import json
import sqlite3


def _db(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def _character(db_path, user_id=1, name="Rowan", shared=0):
    con = _db(db_path)
    try:
        cur = con.execute(
            "INSERT INTO characters (user_id, name, race, class_name, level, shared) "
            "VALUES (?, ?, 'Human', 'Fighter', 3, ?)", (user_id, name, shared))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _campaign(db_path, user_id=1, characters=()):
    con = _db(db_path)
    try:
        cur = con.execute(
            "INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?, ?, ?)",
            (user_id, "Windrun", json.dumps(list(characters))))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _events(db_path, target=None):
    con = _db(db_path)
    try:
        if target is None:
            rows = con.execute("SELECT * FROM event_log ORDER BY id ASC").fetchall()
        else:
            rows = con.execute("SELECT * FROM event_log WHERE target=? ORDER BY id ASC",
                               (target,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ── the writer ────────────────────────────────────────────────────────────────────

def test_log_event_writes_one_row_with_the_change(seeded_db):
    from services.eventlog import log_event

    log_event("character_update", campaign_id=3, actor="dm@test.com",
              target="char:7", before={"hp_current": 10}, after={"hp_current": 4})
    rows = _events(seeded_db["db_path"])
    assert len(rows) == 1
    row = rows[0]
    assert row["campaign_id"] == 3
    assert row["kind"] == "character_update"
    assert row["actor"] == "dm@test.com"
    assert row["target"] == "char:7"
    assert json.loads(row["before"]) == {"hp_current": 10}
    assert json.loads(row["after"]) == {"hp_current": 4}
    assert row["ts"]


def test_log_event_never_raises_when_the_database_is_gone(seeded_db, monkeypatch):
    from services import eventlog

    def _boom():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(eventlog, "get_db", _boom, raising=False)
    import core.db

    monkeypatch.setattr(core.db, "get_db", _boom)
    # The call must not raise: the write it describes already succeeded.
    eventlog.log_event("character_update", target="char:1")
    assert _events(seeded_db["db_path"]) == []


def test_log_event_writes_nothing_when_the_table_is_missing(seeded_db):
    from services import eventlog

    con = _db(seeded_db["db_path"])
    con.execute("DROP TABLE event_log")
    con.commit()
    con.close()
    # The call must not raise: the write it describes already happened.
    eventlog.log_event("character_update", target="char:1")
    con = _db(seeded_db["db_path"])
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    con.close()
    assert "event_log" not in names


# ── the read side ────────────────────────────────────────────────────────────────

def test_character_history_returns_the_rows_as_objects(client, seeded_db, auth_headers):
    from services.eventlog import log_event

    char_id = _character(seeded_db["db_path"])
    log_event("character_update", target=f"char:{char_id}",
              before={"hp_current": 20}, after={"hp_current": 12})
    log_event("xp_award", target=f"char:{char_id}", before={"xp": 0}, after={"xp": 50})

    r = client.get(f"/api/character/{char_id}/history.json", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == char_id
    assert body["name"] == "Rowan"
    assert body["count"] == 2
    assert [e["kind"] for e in body["events"]] == ["character_update", "xp_award"]
    first = body["events"][0]
    # before/after are objects, not strings.
    assert first["before"] == {"hp_current": 20}
    assert first["after"] == {"hp_current": 12}
    assert set(first) == {"id", "campaign_id", "kind", "actor", "target",
                          "before", "after", "ts"}


def test_character_history_404s_for_a_character_that_is_not_there(client, auth_headers):
    r = client.get("/api/character/999999/history.json", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"] == "character not found"


def test_character_history_filters_by_kind(client, seeded_db, auth_headers):
    from services.eventlog import log_event

    char_id = _character(seeded_db["db_path"])
    log_event("character_update", target=f"char:{char_id}")
    log_event("xp_award", target=f"char:{char_id}")
    body = client.get(f"/api/character/{char_id}/history.json?kind=xp_award",
                       headers=auth_headers).json()
    assert body["count"] == 1
    assert body["events"][0]["kind"] == "xp_award"


def test_character_history_does_not_leak_another_users_private_character(client, seeded_db, auth_headers):
    """A stranger gets the same 404 as a character that does not exist."""
    char_id = _character(seeded_db["db_path"], user_id=2, name="Theirs")
    r = client.get(f"/api/character/{char_id}/history.json", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"] == "character not found"


def test_character_history_reaches_a_shared_character(client, seeded_db):
    """A character its owner published is readable — but only as far as the
    published sheet is: the trail of an unpublished sibling stays shut."""
    shared_id = _character(seeded_db["db_path"], user_id=2, name="Shared One", shared=1)
    private_id = _character(seeded_db["db_path"], user_id=2, name="Private One", shared=0)
    assert client.get(f"/api/character/{shared_id}/history.json").status_code == 200
    assert client.get(f"/api/character/{private_id}/history.json").status_code == 404


def test_campaign_timeline_returns_the_campaigns_rows(client, seeded_db, auth_headers):
    from services.eventlog import log_event

    camp_id = _campaign(seeded_db["db_path"])
    log_event("roster_add", campaign_id=camp_id, target="char:1")
    log_event("xp_award", campaign_id=camp_id, target="char:1")

    r = client.get(f"/api/campaign/{camp_id}/timeline.json", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == camp_id
    assert body["count"] == 2
    assert [e["kind"] for e in body["events"]] == ["roster_add", "xp_award"]
    assert body["events"][0]["before"] == {}


def test_campaign_timeline_404s_for_a_campaign_that_is_not_there(client, auth_headers):
    r = client.get("/api/campaign/999999/timeline.json", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"] == "campaign not found"


def test_campaign_timeline_does_not_leak_another_users_campaign(client, seeded_db, auth_headers):
    """Another DM's campaign is a campaign that is not there."""
    camp_id = _campaign(seeded_db["db_path"], user_id=2)
    r = client.get(f"/api/campaign/{camp_id}/timeline.json", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"] == "campaign not found"


def test_an_anonymous_caller_gets_json_not_a_login_redirect(client, seeded_db):
    char_id = _character(seeded_db["db_path"])
    r = client.get(f"/api/character/{char_id}/history.json")
    assert r.status_code == 404
    assert r.json()["error"] == "character not found"


# ── the passive writers ──────────────────────────────────────────────────────────

def test_a_sheet_save_is_logged(client, seeded_db, auth_headers):
    char_id = _character(seeded_db["db_path"])
    r = client.post(f"/api/character/{char_id}/update",
                    json={"hp_current": 7}, headers=auth_headers)
    assert r.status_code == 200
    rows = _events(seeded_db["db_path"], f"char:{char_id}")
    assert [row["kind"] for row in rows] == ["character_update"]
    assert json.loads(rows[0]["after"])["hp_current"] == 7


def test_adding_a_character_to_a_roster_is_logged(client, seeded_db, auth_headers):
    char_id = _character(seeded_db["db_path"])
    camp_id = _campaign(seeded_db["db_path"])
    r = client.post(f"/api/dm/campaign/{camp_id}/add-character",
                    json={"character_id": char_id}, headers=auth_headers)
    assert r.status_code == 200
    rows = _events(seeded_db["db_path"], f"char:{char_id}")
    assert "roster_add" in [row["kind"] for row in rows]
    assert rows[-1]["campaign_id"] == camp_id


def test_awarding_xp_is_logged(client, seeded_db, auth_headers):
    char_id = _character(seeded_db["db_path"])
    camp_id = _campaign(seeded_db["db_path"], characters=[{"id": char_id}])
    r = client.post(f"/api/dm/campaign/{camp_id}/award-xp",
                    json={"amount": 25, "note": "killed the goblin"}, headers=auth_headers)
    assert r.status_code == 200
    rows = _events(seeded_db["db_path"], f"char:{char_id}")
    kinds = [row["kind"] for row in rows]
    assert "xp_award" in kinds
    award = [row for row in rows if row["kind"] == "xp_award"][0]
    assert json.loads(award["after"])["xp"] == 25


def test_an_undo_is_logged(client, seeded_db, auth_headers):
    char_id = _character(seeded_db["db_path"])
    client.post(f"/api/character/{char_id}/update",
                json={"hp_current": 7}, headers=auth_headers)
    r = client.post("/api/undo", headers=auth_headers)
    assert r.status_code == 200
    kinds = [row["kind"] for row in _events(seeded_db["db_path"], f"char:{char_id}")]
    assert "undo" in kinds


# ── the status page ──────────────────────────────────────────────────────────────

def test_the_status_page_renders_the_deep_report(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Deep checks" in r.text


def test_the_status_page_and_the_deep_probe_agree(client):
    page = client.get("/status").text
    body = client.get("/api/health?deep=1").json()
    for name in body["deep"]:
        assert name in page, f"{name} is missing from the status page"
    for entry in body["deep"].values():
        assert set(entry) >= {"name", "ok", "detail"}


def test_the_deep_probe_reports_the_audit_trail(client):
    body = client.get("/api/health?deep=1").json()
    assert "event_log" in body["deep"]
    assert body["deep"]["event_log"]["ok"] is True
