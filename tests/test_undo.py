"""Undo: the last action, put back — or refused when the world has moved on.

The refusal is the part that matters. An undo that restores an older value over newer work is data
loss wearing a helpful face, so the interesting tests here are the ones where it says no.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


def _db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _character(db_path, user_id, name="Rowan", hp=40, xp=0):
    con = _db(db_path)
    try:
        cur = con.execute(
            "INSERT INTO characters (user_id, name, race, class_name, level, hp_current, hp_max, xp) "
            "VALUES (?, ?, 'Human', 'Fighter', 3, ?, ?, ?)", (user_id, name, hp, hp, xp))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _headers_for(token: str) -> dict:
    """A cookie and a matching X-CSRF-Token: the middleware compares them, so a bare header is a 403."""
    csrf = "test-csrf-token"
    return {"Cookie": f"dnd_token={token}; csrf_token={csrf}", "X-CSRF-Token": csrf}


def _session(db_path, user_id):
    """A real session token, minted the way the app mints them."""
    from services.sessions import create_session

    return create_session(db_path, user_id, 1)


def _campaign(db_path, user_id, characters):
    con = _db(db_path)
    try:
        cur = con.execute("INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?, ?, ?)",
                          (user_id, "Windrun", json.dumps(characters)))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _hp(db_path, char_id):
    con = _db(db_path)
    try:
        return con.execute("SELECT hp_current FROM characters WHERE id=?", (char_id,)).fetchone()[0]
    finally:
        con.close()


def test_the_dms_hp_write_can_be_undone(seeded_db, client, auth_headers):
    char_id = _character(seeded_db["db_path"], 1, hp=40)
    _campaign(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    assert client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": 12},
                       headers=auth_headers).status_code == 200
    assert _hp(seeded_db["db_path"], char_id) == 12

    peek = client.get("/api/undo/peek", headers=auth_headers).json()["entry"]
    assert peek["kind"] == "hp" and "12" in peek["label"]

    r = client.post("/api/undo", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "hp"
    assert _hp(seeded_db["db_path"], char_id) == 40, "the damage came back off"

    assert client.get("/api/undo/peek", headers=auth_headers).json()["entry"] is None
    assert client.post("/api/undo", headers=auth_headers).status_code == 404, "nothing left to undo"


def test_undo_refuses_rather_than_overwriting_newer_work(seeded_db, client, auth_headers):
    """The whole point: an undo that clobbers a later edit is data loss, not an undo.

    The realistic shape of it: the DM damages a player, the player then heals on their own sheet,
    and the DM's undo — which remembers HP 12 — is now stale. It must refuse and leave the heal
    alone."""
    char_id = _character(seeded_db["db_path"], 2, "Rowan", hp=40)     # the player's character
    _campaign(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])   # the DM's roster
    client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": 12},
                headers=auth_headers)                                   # recorded for the DM

    # the player heals it back up, from their own sheet, under their own user
    token = _session(seeded_db["db_path"], 2)
    r = client.post(f"/api/character/{char_id}/update", json={"hp_current": 33},
                    headers=_headers_for(token))
    assert r.status_code == 200, r.text
    assert _hp(seeded_db["db_path"], char_id) == 33

    r = client.post("/api/undo", headers=auth_headers)
    assert r.status_code == 409, r.text
    body = r.json()
    assert "changed since" in body["error"]
    assert body["reason"] == "stale"
    assert _hp(seeded_db["db_path"], char_id) == 33, "newer work must survive the refusal"


def test_a_sheet_save_is_undoable_across_every_field_it_touched(seeded_db, client, auth_headers):
    """One save, several columns, one undo. (`conditions` is not in this route's allowed set — it
    has routes of its own — so the fields here are the ones this route actually writes.)"""
    char_id = _character(seeded_db["db_path"], 1, hp=40)
    client.post(f"/api/character/{char_id}/update",
                json={"hp_current": 25, "temp_hp": 5, "notes": "concentrating"}, headers=auth_headers)

    con = _db(seeded_db["db_path"])
    got = con.execute("SELECT hp_current, temp_hp, notes FROM characters WHERE id=?",
                      (char_id,)).fetchone()
    con.close()
    assert (got["hp_current"], got["temp_hp"], got["notes"]) == (25, 5, "concentrating")

    r = client.post("/api/undo", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert set(r.json()["restored"][0]["fields"]) == {"hp_current", "temp_hp", "notes"}
    con = _db(seeded_db["db_path"])
    got = con.execute("SELECT hp_current, temp_hp, notes FROM characters WHERE id=?",
                      (char_id,)).fetchone()
    con.close()
    assert (got["hp_current"], got["temp_hp"]) == (40, 0), "the whole save came back"
    assert (got["notes"] or "") != "concentrating"


def test_adding_and_removing_a_condition_is_undoable(seeded_db, client, auth_headers):
    """Removing the wrong condition is a two-second mistake with real consequences."""
    char_id = _character(seeded_db["db_path"], 1, hp=40)
    client.post(f"/api/character/{char_id}/conditions", json={"name": "Prone"}, headers=auth_headers)
    client.post(f"/api/character/{char_id}/conditions", json={"name": "Restrained"},
                headers=auth_headers)
    client.request("DELETE", f"/api/character/{char_id}/conditions",
                   json={"name": "Prone"}, headers=auth_headers)

    con = _db(seeded_db["db_path"])
    names = [c["name"] for c in json.loads(con.execute(
        "SELECT conditions FROM characters WHERE id=?", (char_id,)).fetchone()[0])]
    con.close()
    assert names == ["Restrained"]

    assert client.post("/api/undo", headers=auth_headers).status_code == 200
    con = _db(seeded_db["db_path"])
    names = [c["name"] for c in json.loads(con.execute(
        "SELECT conditions FROM characters WHERE id=?", (char_id,)).fetchone()[0])]
    con.close()
    assert sorted(names) == ["Prone", "Restrained"], "the removed condition is back"

    assert client.post("/api/undo", headers=auth_headers).status_code == 200
    con = _db(seeded_db["db_path"])
    names = [c["name"] for c in json.loads(con.execute(
        "SELECT conditions FROM characters WHERE id=?", (char_id,)).fetchone()[0])]
    con.close()
    assert names == ["Prone"], "and the one added before that is gone again"


def test_an_xp_award_to_the_party_undoes_for_all_of_them(seeded_db, client, auth_headers):
    first = _character(seeded_db["db_path"], 1, "Rowan", xp=100)
    second = _character(seeded_db["db_path"], 1, "Bramble", xp=50)
    camp_id = _campaign(seeded_db["db_path"], 1, [{"id": first, "name": "Rowan"},
                                                 {"id": second, "name": "Bramble"}])

    client.post(f"/api/dm/campaign/{camp_id}/award-xp",
                json={"amount": 400, "note": "session 6"}, headers=auth_headers)
    con = _db(seeded_db["db_path"])
    assert [r["xp"] for r in con.execute("SELECT xp FROM characters ORDER BY id")] == [500, 450]
    con.close()

    r = client.post("/api/undo", headers=auth_headers)
    assert r.status_code == 200 and r.json()["kind"] == "xp"
    assert len(r.json()["restored"]) == 2, "one undo, both characters"
    con = _db(seeded_db["db_path"])
    assert [r["xp"] for r in con.execute("SELECT xp FROM characters ORDER BY id")] == [100, 50]
    con.close()


def test_a_summon_hp_change_undoes_as_a_whole_blob(seeded_db, client, auth_headers):
    char_id = _character(seeded_db["db_path"], 1, hp=40)
    _campaign(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])
    summons = [{"name": "Wolf", "hp_current": 11, "hp_max": 11}]
    con = _db(seeded_db["db_path"])
    con.execute("UPDATE characters SET summons=? WHERE id=?", (json.dumps(summons), char_id))
    con.commit()
    con.close()

    assert client.post("/api/dm/summon-hp",
                       json={"character_id": char_id, "summon_idx": 0, "hp_current": 3},
                       headers=auth_headers).status_code == 200
    assert client.post("/api/undo", headers=auth_headers).status_code == 200
    con = _db(seeded_db["db_path"])
    got = json.loads(con.execute("SELECT summons FROM characters WHERE id=?",
                                 (char_id,)).fetchone()[0])
    con.close()
    assert got[0]["hp_current"] == 11, "the summon's HP came back"


def test_undo_tells_every_screen_that_shows_the_character(seeded_db, client, auth_headers):
    """A silent undo would leave the player's sheet showing damage the DM just took back."""
    from services import events

    char_id = _character(seeded_db["db_path"], 1, hp=40)
    camp_id = _campaign(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])
    client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": 12},
                headers=auth_headers)

    q = events.subscribe(camp_id)
    try:
        assert client.post("/api/undo", headers=auth_headers).status_code == 200
        ev = q.get_nowait()
        assert ev["kind"] == "hp" and ev["hp_current"] == 40, f"the undo published: {ev}"
    finally:
        events.unsubscribe(camp_id, q)


def test_undo_is_scoped_to_the_user_who_acted(seeded_db, client, auth_headers):
    """Two DMs with their own characters: one must never be able to undo the other's table."""
    mine = _character(seeded_db["db_path"], 1, "Mine", hp=40)
    theirs = _character(seeded_db["db_path"], 2, "Theirs", hp=40)
    _campaign(seeded_db["db_path"], 1, [{"id": mine, "name": "Mine"}])

    client.post("/api/character/" + str(mine) + "/update", json={"hp_current": 10},
                headers=auth_headers)                      # recorded for user 1

    token = _session(seeded_db["db_path"], 2)
    r = client.post("/api/undo", headers=_headers_for(token))
    assert r.status_code == 404, "the other user has no action of their own to undo"
    con = _db(seeded_db["db_path"])
    assert con.execute("SELECT hp_current FROM characters WHERE id=?", (mine,)).fetchone()[0] == 10
    assert con.execute("SELECT hp_current FROM characters WHERE id=?", (theirs,)).fetchone()[0] == 40
    con.close()


def test_the_stack_is_shallow_on_purpose(seeded_db, client, auth_headers):
    """Deep enough for a bad minute, shallow enough that this never becomes an audit log."""
    from services.undo import UNDO_DEPTH

    char_id = _character(seeded_db["db_path"], 1, hp=100)
    _campaign(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])
    for i in range(UNDO_DEPTH + 5):
        client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": 100 - i},
                    headers=auth_headers)

    con = _db(seeded_db["db_path"])
    kept = con.execute("SELECT COUNT(*) FROM action_undo WHERE user_id=1").fetchone()[0]
    con.close()
    assert kept == UNDO_DEPTH, f"kept {kept} records, expected {UNDO_DEPTH}"


def test_only_columns_a_route_would_write_are_restorable():
    """Undo restores state, it is not a back door to columns nothing writes."""
    from services.undo import RESTORABLE

    assert {"hp_current", "conditions", "summons", "xp"} <= RESTORABLE
    assert "user_id" not in RESTORABLE and "password_hash" not in RESTORABLE


def test_the_ambiguous_endings_are_not_500s(seeded_db, client, auth_headers):
    """Nothing to undo is 404 and stale is 409 — both are answers, not faults."""
    empty = client.post("/api/undo", headers=auth_headers)
    assert empty.status_code == 404 and "Nothing to undo" in empty.json()["error"]
    assert client.get("/api/undo/peek", headers=auth_headers).json() == {"ok": True, "entry": None}


def test_both_surfaces_offer_the_undo_and_name_it_first():
    """The button has to say what it will put back (peek) rather than being a mystery tap, and the
    keyboard shortcut must not steal Ctrl+Z from a field the user is typing in."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    dm_tpl = (repo / "templates" / "dm_tools.html").read_text()
    dm_js = (repo / "static" / "dm_tools.js").read_text()
    sheet_tpl = (repo / "templates" / "sheet.html").read_text()
    sheet_js = (repo / "static" / "sheet.js").read_text()

    assert 'id="btnUndo"' in dm_tpl and 'id="undoStatus"' in dm_tpl
    assert "function refreshUndoLabel(" in dm_js and "function undoLast(" in dm_js
    assert "/api/undo/peek" in dm_js, "the label comes from what would actually be undone"
    assert "e.key === 'z'" in dm_js and "isContentEditable" in dm_js
    assert 'id="sheetUndo"' in sheet_tpl and "function undoLastSheet(" in sheet_js
    assert "refreshSheetUndo();" in sheet_js


def test_an_undone_award_says_so_in_the_ledger(seeded_db, client, auth_headers):
    """A ledger that contradicts the totals is worse than no ledger. The row stays — the DM did
    award it — but it records that the award was put back."""
    char_id = _character(seeded_db["db_path"], 1, "Rowan", xp=0)
    camp_id = _campaign(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])
    client.post(f"/api/dm/campaign/{camp_id}/award-xp",
                json={"amount": 900, "note": "session 7"}, headers=auth_headers)

    con = _db(seeded_db["db_path"])
    assert con.execute("SELECT reversed FROM dm_xp_log ORDER BY id DESC").fetchone()[0] == 0
    con.close()

    assert client.post("/api/undo", headers=auth_headers).status_code == 200

    con = _db(seeded_db["db_path"])
    row = con.execute("SELECT amount, reversed FROM dm_xp_log ORDER BY id DESC").fetchone()
    assert row["reversed"] == 1, "the row remains and admits it was undone"
    assert row["amount"] == 900
    assert con.execute("SELECT xp FROM characters WHERE id=?", (char_id,)).fetchone()[0] == 0
    con.close()

    d = client.get(f"/api/dm/campaign/{camp_id}/xp", headers=auth_headers).json()
    assert d["log"][0]["reversed"] == 1, "the DM's panel can show it struck through"


def test_a_table_from_an_earlier_deploy_gains_the_new_column(test_db):
    """`CREATE TABLE IF NOT EXISTS` silently skips a table that already exists — so a column added
    to the CREATE statement after the table shipped never lands on a live database.

    That is not hypothetical: it happened to action_undo.meta. Every undo record then failed to
    insert, the failure was swallowed by a bare except, and the live probe saw no records while the
    tests (fresh schema) stayed green. This drops the column to reproduce the older shape, then
    runs the same init_db a deploy runs.
    """
    import sqlite3

    from services.db_schema import init_db

    con = sqlite3.connect(str(test_db))
    con.execute("ALTER TABLE action_undo DROP COLUMN meta")
    con.commit()
    assert "meta" not in [r[1] for r in con.execute("PRAGMA table_info(action_undo)")]
    con.close()

    init_db()

    con = sqlite3.connect(str(test_db))
    cols = [r[1] for r in con.execute("PRAGMA table_info(action_undo)")]
    con.close()
    assert "meta" in cols, "the column the new code writes has to be migrated in, not assumed"


def test_a_failed_record_is_logged_rather_than_hidden():
    """The swallow that hid the missing column stayed, but it no longer hides the reason."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "services" / "undo.py").read_text()
    assert "def record_safe(" in src and "logging.getLogger(__name__).warning" in src
    for route in ("routes/dm.py", "routes/characters/sheet.py"):
        text = (pathlib.Path(__file__).resolve().parent.parent / route).read_text()
        assert "record_safe(" in text, f"{route} should record through the one place that logs"
