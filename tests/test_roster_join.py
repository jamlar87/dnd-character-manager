"""The campaign/character join table has to agree with the roster JSON.

History: `dm_campaign_characters` shipped but no write path ever filled it. On the
live database five campaigns had rosters and the table had zero rows, so every
reader parsed `dm_campaigns.characters` and any new JOIN returned nothing. These
tests pin both halves of the fix — the writers mirror their roster writes, and a
startup backfill repairs a database that predates the mirror.
"""

import json
import sqlite3

from services import roster


def _seed_campaign(db_path, *, camp_id=7, roster_entries=(), user_id=1):
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO dm_campaigns (id, user_id, name, characters) VALUES (?,?,?,?)",
            (camp_id, user_id, f"Camp {camp_id}", json.dumps(list(roster_entries))),
        )
        con.commit()
    finally:
        con.close()


def _seed_character(db_path, *, char_id=11, user_id=1, name="Garim"):
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO characters (id, user_id, name, race, class_name, level)"
            " VALUES (?,?,?,?,?,?)",
            (char_id, user_id, name, "Dragonborn", "Cleric", 8),
        )
        con.commit()
    finally:
        con.close()


def _join_rows(db_path, camp_id=7):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT campaign_id, character_id, status FROM dm_campaign_characters"
            " WHERE campaign_id = ? ORDER BY character_id", (camp_id,))]
    finally:
        con.close()


# ── the module contract ─────────────────────────────────────────────────────────────

def test_parse_entries_tolerates_junk_and_bare_ids():
    assert roster.parse_entries('[{"id": 3}, 4, "nope", {"no_id": 1}, {"id": "x"}]') == [
        {"id": 3}, {"id": 4}, {"no_id": 1}, {"id": "x"}]
    assert roster.character_ids('[{"id": 3}, 4, {"id": 3}]') == [3, 4]
    assert roster.parse_entries("not json") == []
    assert roster.parse_entries(None) == []


def test_sync_adds_updates_and_removes(test_db):
    _seed_campaign(test_db, camp_id=7)
    _seed_character(test_db, char_id=1)
    _seed_character(test_db, char_id=2)
    _seed_character(test_db, char_id=3)
    con = sqlite3.connect(str(test_db))
    try:
        roster.sync(con, 7, [{"id": 1, "status": "active"}, {"id": 2, "status": "retired"}])
        con.commit()
        assert [(r["character_id"], r["status"]) for r in _join_rows(test_db)] == [
            (1, "active"), (2, "retired")]

        # a status change lands, a dropped character leaves, a new one arrives
        counts = roster.sync(con, 7, [{"id": 2, "status": "active"}, {"id": 3}])
        con.commit()
        assert counts == {"added": 1, "updated": 1, "removed": 1}
        assert [(r["character_id"], r["status"]) for r in _join_rows(test_db)] == [
            (2, "active"), (3, "active")]

        # idempotent: the same roster again changes nothing
        assert roster.sync(con, 7, [{"id": 2, "status": "active"}, {"id": 3}]) == {
            "added": 0, "updated": 0, "removed": 0}
    finally:
        con.close()


def test_sync_is_scoped_to_one_campaign(test_db):
    _seed_campaign(test_db, camp_id=7)
    _seed_campaign(test_db, camp_id=8)
    _seed_character(test_db, char_id=1)
    _seed_character(test_db, char_id=2)
    con = sqlite3.connect(str(test_db))
    try:
        roster.sync(con, 7, [{"id": 1}])
        roster.sync(con, 8, [{"id": 2}])
        con.commit()
        assert [r["character_id"] for r in _join_rows(test_db, 7)] == [1]
        assert [r["character_id"] for r in _join_rows(test_db, 8)] == [2]
    finally:
        con.close()


# ── the backfill: a database that predates the mirror ───────────────────────────────

def test_backfill_populates_table_from_existing_rosters(test_db):
    _seed_campaign(test_db, camp_id=7, roster_entries=[
        {"id": 11, "name": "Garim", "status": "active"},
        {"id": 12, "name": "Tybald", "status": "retired"},
    ])
    _seed_campaign(test_db, camp_id=8, roster_entries=[])
    _seed_character(test_db, char_id=11)
    _seed_character(test_db, char_id=12)
    assert _join_rows(test_db, 7) == []

    from services.db_schema import init_db
    init_db()

    assert [(r["character_id"], r["status"]) for r in _join_rows(test_db, 7)] == [
        (11, "active"), (12, "retired")]
    assert _join_rows(test_db, 8) == []

    # running it again (every boot) must not duplicate rows
    init_db()
    assert len(_join_rows(test_db, 7)) == 2


def test_backfill_survives_a_corrupt_roster_blob(test_db):
    _seed_campaign(test_db, camp_id=7, roster_entries=[])
    con = sqlite3.connect(str(test_db))
    try:
        con.execute("UPDATE dm_campaigns SET characters = ? WHERE id = 7", ("{not json",))
        con.commit()
    finally:
        con.close()

    from services.db_schema import init_db
    init_db()  # must not raise

    assert _join_rows(test_db, 7) == []


# ── the live writers keep both halves in step ───────────────────────────────────────

def test_add_character_endpoint_writes_the_join_row(seeded_db, client, auth_headers):
    db_path = seeded_db["db_path"]
    _seed_campaign(db_path, camp_id=7)
    _seed_character(db_path, char_id=11)

    r = client.post("/api/dm/campaign/7/add-character", headers=auth_headers,
                    json={"character_id": 11})
    assert r.status_code == 200, r.text
    assert [row["character_id"] for row in _join_rows(db_path, 7)] == [11]

    # the roster JSON is still the source of truth, and still there
    con = sqlite3.connect(str(db_path))
    try:
        blob = con.execute("SELECT characters FROM dm_campaigns WHERE id = 7").fetchone()[0]
    finally:
        con.close()
    assert [e["id"] for e in json.loads(blob)] == [11]


def test_remove_character_endpoint_clears_the_join_row(seeded_db, client, auth_headers):
    db_path = seeded_db["db_path"]
    _seed_campaign(db_path, camp_id=7)
    _seed_character(db_path, char_id=11)
    client.post("/api/dm/campaign/7/add-character", headers=auth_headers,
                json={"character_id": 11})
    assert _join_rows(db_path, 7)

    r = client.post("/api/dm/campaign/7/remove-character", headers=auth_headers,
                    json={"character_id": 11})
    assert r.status_code == 200, r.text
    assert _join_rows(db_path, 7) == []


def test_character_delete_purges_join_rows(seeded_db, client, auth_headers):
    db_path = seeded_db["db_path"]
    _seed_campaign(db_path, camp_id=7)
    _seed_character(db_path, char_id=11)
    client.post("/api/dm/campaign/7/add-character", headers=auth_headers,
                json={"character_id": 11})

    r = client.post("/api/character/11/delete", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _join_rows(db_path, 7) == []


# ── the readers agree ───────────────────────────────────────────────────────────────

def test_join_and_json_roster_agree(seeded_db):
    """After a write, a JOIN and the JSON fallback must name the same characters."""
    from routes.table import roster_ids

    db_path = seeded_db["db_path"]
    _seed_campaign(db_path, camp_id=7, roster_entries=[
        {"id": 11, "name": "Garim"}, {"id": 12, "name": "Tybald"}])
    _seed_character(db_path, char_id=11, name="Garim")
    _seed_character(db_path, char_id=12, name="Tybald")
    from services.db_schema import init_db
    init_db()

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        camp = dict(con.execute("SELECT * FROM dm_campaigns WHERE id = 7").fetchone())
        from_json = roster_ids(camp)
        from_table = {r[0] for r in con.execute(
            "SELECT character_id FROM dm_campaign_characters WHERE campaign_id = 7")}
    finally:
        con.close()

    assert from_json == from_table == {11, 12}
