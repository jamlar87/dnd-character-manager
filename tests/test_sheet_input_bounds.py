"""Stage 0 regression: every client-facing numeric write path is bounded.

`UpdateCharacter` sets `extra="allow"`, so `POST /api/character/{id}/update` accepted
`{"hp_max": 100000000, "strength": 9999, "exhaustion": 99}` and wrote it to the row; the sheet then
rendered an HP bar at 0%, an unreadable modifier and a character that could never rest. Character
creation had the same hole one level earlier (`abilities: {"strength": 9999}` is not a declared
field), and the DM's NPC editor parsed with bare `int()`.

Bounds live in services/validation.FIELD_BOUNDS; these tests pin the behaviour of the three routes
that use them.
"""

import sqlite3


def _conn(seeded_db):
    db = sqlite3.connect(seeded_db["db_path"])
    db.row_factory = sqlite3.Row
    return db


def _make_character(db, user_id, name="Bounded"):
    db.execute(
        "INSERT INTO characters (user_id, name, race, class_name) VALUES (?,?,?,?)",
        (user_id, name, "Human", "Fighter"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Sheet update ────────────────────────────────────────────────────────────────────

def test_sheet_update_clamps_out_of_range_extras(client, seeded_db, auth_headers):
    db = _conn(seeded_db)
    char_id = _make_character(db, 1)
    db.commit()
    db.close()

    resp = client.post(
        f"/api/character/{char_id}/update",
        json={
            "hp_max": 100_000_000,
            "hp_current": 100_000_000,
            "strength": 9999,
            "exhaustion": 99,
            "speed": 999_999,
            "ac": 9999,
            "proficiency_bonus": 99,
            "passive_perception": 5000,
            "gp": -50,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    row = db.execute(
        "SELECT hp_max, hp_current, strength, exhaustion, speed, ac, "
        "proficiency_bonus, passive_perception, gp FROM characters WHERE id=?",
        (char_id,),
    ).fetchone()
    db.close()
    assert row["hp_max"] == 100_000
    assert row["hp_current"] == 100_000
    assert row["strength"] == 30
    assert row["exhaustion"] == 6
    assert row["speed"] == 1_000
    assert row["ac"] == 100
    assert row["proficiency_bonus"] == 20
    assert row["passive_perception"] == 100
    assert row["gp"] == 0


def test_sheet_update_rejects_level_above_twenty(client, seeded_db, auth_headers):
    """`level` is the one field the schema bounds itself — it must be refused, not clamped."""
    db = _conn(seeded_db)
    char_id = _make_character(db, 1)
    db.commit()
    db.close()

    resp = client.post(f"/api/character/{char_id}/update", json={"level": 40},
                       headers=auth_headers)
    assert resp.status_code == 422, resp.text


def test_sheet_update_accepts_ordinary_values_unchanged(client, seeded_db, auth_headers):
    """The clamp table must not round-trip real play values (e.g. a 45 ft. speed, 24 AC)."""
    db = _conn(seeded_db)
    char_id = _make_character(db, 1)
    db.commit()
    db.close()

    resp = client.post(f"/api/character/{char_id}/update",
                       json={"hp_current": 17, "ac": 24, "speed": 45, "strength": 20},
                       headers=auth_headers)
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    row = db.execute("SELECT hp_current, ac, speed, strength FROM characters WHERE id=?",
                     (char_id,)).fetchone()
    db.close()
    assert (row["hp_current"], row["ac"], row["speed"], row["strength"]) == (17, 24, 45, 20)


def test_sheet_update_cannot_write_unlisted_columns(client, seeded_db, auth_headers):
    db = _conn(seeded_db)
    char_id = _make_character(db, 1)
    other_id = _make_character(db, 2, "Other")
    db.commit()
    db.close()

    resp = client.post(f"/api/character/{char_id}/update",
                       json={"user_id": 2, "id": other_id, "name": "Renamed"},
                       headers=auth_headers)
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    row = db.execute("SELECT user_id, id, name FROM characters WHERE id=?",
                     (char_id,)).fetchone()
    db.close()
    assert row["user_id"] == 1
    assert row["id"] == char_id
    assert row["name"] == "Renamed"


# ── Character creation ──────────────────────────────────────────────────────────────

def test_creation_clamps_abilities_map(client, seeded_db, auth_headers):
    """`abilities` is undeclared in CreateCharacter, so it reached the builder raw."""
    resp = client.post("/api/character/create", json={
        "name": "Oversized",
        "race": "Human",
        "class_name": "Fighter",
        "level": 1,
        "abilities": {"strength": 9999, "dexterity": -40},
    }, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    char_id = resp.json()["id"]

    db = _conn(seeded_db)
    row = db.execute("SELECT strength, dexterity FROM characters WHERE id=?", (char_id,)).fetchone()
    db.close()
    assert row["strength"] == 30
    assert row["dexterity"] == 1


def test_creation_level_is_capped(client, seeded_db, auth_headers):
    """A level above 20 used to skip the schema (non-int path) and inflate HP/prof bonus."""
    resp = client.post("/api/character/create", json={
        "name": "Too High", "race": "Human", "class_name": "Fighter", "level": 200,
    }, headers=auth_headers)
    # Declared `level` is le=20, so the schema rejects it — either answer is safe, but
    # nothing above 20 may ever be stored.
    if resp.status_code == 200:
        db = _conn(seeded_db)
        level = db.execute("SELECT level FROM characters WHERE id=?",
                           (resp.json()["id"],)).fetchone()[0]
        db.close()
        assert level == 20
    else:
        assert resp.status_code == 422, resp.text


# ── DM NPC editor ───────────────────────────────────────────────────────────────────

def test_dm_npc_create_clamps_numbers_and_survives_typos(client, seeded_db, admin_headers):
    resp = client.post("/api/dm/npc/create", json={
        "name": "Kaiju",
        "hp_max": 1e9,
        "ac": 9999,
        "speed": 999999,
        "strength": 9999,
        "level": 200,
        "xp_reward": 10 ** 12,
    }, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    npc_id = resp.json()["id"]

    db = _conn(seeded_db)
    row = db.execute("SELECT hp_max, ac, speed, strength, level, xp_reward "
                     "FROM dm_npcs WHERE id=?", (npc_id,)).fetchone()
    db.close()
    assert row["hp_max"] == 100_000
    assert row["ac"] == 100
    assert row["speed"] == 1_000
    assert row["strength"] == 30
    assert row["level"] == 20
    assert row["xp_reward"] == 1_000_000_000


def test_dm_npc_create_typo_falls_back_to_default(client, seeded_db, admin_headers):
    resp = client.post("/api/dm/npc/create",
                       json={"name": "Typo", "hp_max": "lots"},
                       headers=admin_headers)
    assert resp.status_code == 200, resp.text

    db = _conn(seeded_db)
    row = db.execute("SELECT hp_max FROM dm_npcs WHERE id=?", (resp.json()["id"],)).fetchone()
    db.close()
    assert row["hp_max"] == 10
