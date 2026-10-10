"""The create-character payload contract.

`POST /api/character/create` validates the body with `CreateCharacter` and then
builds the character from it. Two regressions are pinned here:

* the schema's own documented ability fields (strength..charisma) were validated
  and then silently dropped — only the wizard's `abilities` map reached the
  builder, so an API client posting the documented fields got a 10/10/10
  character;
* the validated model normalises an explicit ``None`` to ``""`` for the optional
  string fields, but discarding it wrote SQL NULL instead.
"""

import sqlite3

ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def _row(db_path, cid):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    con.close()
    return dict(row) if row else {}


def _create(client, headers, **payload):
    body = {"name": "Payload Probe", "race": "Human", "class_name": "Fighter", "level": 1}
    body.update(payload)
    r = client.post("/api/character/create", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_documented_ability_fields_are_honoured(client, auth_headers, seeded_db):
    """The schema's strength..charisma fields must reach the character."""
    cid = _create(client, auth_headers, strength=18, dexterity=16, constitution=14,
                  intelligence=12, wisdom=10, charisma=8)
    row = _row(seeded_db["db_path"], cid)
    # Human grants +1 to every ability, so stored = posted + 1.
    assert [row[a] for a in ABILITIES] == [19, 17, 15, 13, 11, 9]


def test_abilities_map_still_wins(client, auth_headers, seeded_db):
    """The wizard posts `abilities`; that path must keep working unchanged."""
    cid = _create(client, auth_headers,
                  abilities={"strength": 18, "dexterity": 16, "constitution": 14})
    row = _row(seeded_db["db_path"], cid)
    assert [row[a] for a in ("strength", "dexterity", "constitution")] == [19, 17, 15]


def test_explicit_nulls_are_stored_as_empty_strings(client, auth_headers, seeded_db):
    """A JSON null for an optional string field must not land as SQL NULL."""
    cid = _create(client, auth_headers,
                  subrace=None, subclass=None, background=None, alignment=None)
    row = _row(seeded_db["db_path"], cid)
    for field in ("subrace", "subclass", "background", "alignment"):
        assert row[field] == "", f"{field} stored as {row[field]!r}"


def test_out_of_range_ability_is_rejected(client, auth_headers, seeded_db):
    """The schema's bounds stay the API contract (422, not a silent clamp)."""
    r = client.post("/api/character/create", headers=auth_headers,
                    json={"name": "Bad", "race": "Human", "class_name": "Fighter", "strength": 30})
    assert r.status_code == 422, r.text
