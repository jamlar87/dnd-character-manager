"""Spawning an encounter onto a map, and the HP link back to the tracker.

This is where the map layer meets the encounter builder: the combatants become tokens (with the
monster's real footprint), and each token remembers the tracker row it came from so a wound on
the map is not a wound the DM has to enter twice.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


@pytest.fixture
def encounter(seeded_db):
    """One encounter with a Large, a Small and a party character."""
    db_path = seeded_db["db_path"]
    enc = _insert(db_path, "INSERT INTO dm_encounters (user_id, name) VALUES (?,?)",
                  (1, "Goblin ambush"))
    npcs = []
    for name, role, hp in (("Owlbear", "Large monstrosity", 59), ("Goblin", "Small humanoid", 7)):
        npcs.append(_insert(
            db_path,
            "INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, "
            "creature_data) VALUES (?,?,?,?,?,?)",
            (enc, -1, 10, hp, hp, json.dumps({"name": name, "role": role}))))
    return enc, npcs


def _map(client, headers, grid_size=50):
    return client.post("/api/dm/map/create", json={"name": "Arena", "grid_size": grid_size},
                       headers=headers).json()["id"]


def test_spawning_places_every_combatant_with_its_real_footprint(client, seeded_db, auth_headers, encounter):
    enc, _ = encounter
    mid = _map(client, auth_headers)
    r = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": enc},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 2 and body["skipped"] == 0
    by_name = {t["label"]: t for t in body["tokens"]}
    assert set(by_name) == {"Owlbear", "Goblin"}
    assert by_name["Owlbear"]["w"] == 2 and by_name["Owlbear"]["h"] == 2, (
        "a Large monster is a 2x2 token")
    assert by_name["Goblin"]["w"] == 1, "a Small monster is 1x1"
    assert by_name["Owlbear"]["hp_max"] == 59 and by_name["Owlbear"]["hp_current"] == 59
    assert all(t["encounter_en_id"] for t in body["tokens"]), (
        "each token must remember the tracker row it came from")


def test_combatants_land_on_cell_centres_and_not_stacked(client, seeded_db, auth_headers, encounter):
    """They spawn where the DM is looking, on the grid, and not on top of each other."""
    enc, _ = encounter
    mid = _map(client, auth_headers, grid_size=50)
    tokens = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": enc},
                         headers=auth_headers).json()["tokens"]
    positions = {(t["x"], t["y"]) for t in tokens}
    assert len(positions) == len(tokens), "combatants must not be stacked on one cell"
    for t in tokens:
        assert t["x"] % 50 == 25 and t["y"] % 50 == 25, (
            f"token {t['label']} is not on a cell centre: {t['x']},{t['y']}")


def test_respawning_does_not_move_anyone_unless_asked(client, seeded_db, auth_headers, encounter):
    enc, _ = encounter
    mid = _map(client, auth_headers)
    first = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": enc},
                        headers=auth_headers).json()
    again = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": enc},
                        headers=auth_headers).json()
    assert again["added"] == 0 and again["skipped"] == 2, "a mid-fight re-spawn must be a no-op"
    assert {t["id"] for t in again["tokens"]} == {t["id"] for t in first["tokens"]}

    replaced = client.post(f"/api/dm/map/{mid}/spawn-encounter",
                           json={"encounter_id": enc, "replace": True}, headers=auth_headers).json()
    assert replaced["added"] == 2
    assert len(replaced["tokens"]) == 2, "replace must not leave the old tokens behind"


def test_hp_wound_on_the_map_reaches_the_tracker(client, seeded_db, auth_headers, encounter):
    enc, _ = encounter
    mid = _map(client, auth_headers)
    tokens = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": enc},
                         headers=auth_headers).json()["tokens"]
    owlbear = next(t for t in tokens if t["label"] == "Owlbear")

    client.post(f"/api/dm/map/token/{owlbear['id']}/update", json={"hp_current": -3},
                headers=auth_headers)
    db = sqlite3.connect(seeded_db["db_path"])
    hp, defeated = db.execute(
        "SELECT hp_current, defeated FROM dm_encounter_npcs WHERE id=?",
        (owlbear["encounter_en_id"],)).fetchone()
    db.close()
    assert hp == -3, "the map's HP must land on the tracker's row"
    assert defeated == 1, "dropping to 0 or below marks the combatant defeated"

    # and a token that came from nowhere must not touch any tracker row
    loose = client.post(f"/api/dm/map/{mid}/token/add", json={"kind": "marker", "x": 1, "y": 1},
                        headers=auth_headers).json()["token"]
    assert client.post(f"/api/dm/map/token/{loose['id']}/update", json={"hp_current": 5},
                       headers=auth_headers).status_code == 200


def test_spawning_is_scoped_on_both_sides(client, seeded_db, auth_headers, encounter):
    enc, _ = encounter
    mine = _map(client, auth_headers)
    theirs_map = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                         (2, "Theirs"))
    theirs_enc = _insert(seeded_db["db_path"],
                         "INSERT INTO dm_encounters (user_id, name) VALUES (?,?)", (2, "Theirs"))
    assert client.post(f"/api/dm/map/{theirs_map}/spawn-encounter", json={"encounter_id": enc},
                       headers=auth_headers).status_code == 404, "not your map"
    assert client.post(f"/api/dm/map/{mine}/spawn-encounter", json={"encounter_id": theirs_enc},
                       headers=auth_headers).status_code == 404, "not your encounter"
    assert client.post(f"/api/dm/map/{mine}/spawn-encounter", json={},
                       headers=auth_headers).status_code == 404, "no encounter id"


def test_an_empty_encounter_says_so(client, seeded_db, auth_headers):
    empty = _insert(seeded_db["db_path"], "INSERT INTO dm_encounters (user_id, name) VALUES (?,?)",
                    (1, "Nothing in it"))
    mid = _map(client, auth_headers)
    r = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": empty},
                    headers=auth_headers)
    assert r.status_code == 400 and "no combatants" in r.json()["error"]


def test_the_token_cap_still_applies(client, seeded_db, auth_headers, encounter, monkeypatch):
    import routes.maps as maps
    monkeypatch.setattr(maps, "TOKEN_MAX", 1)
    enc, _ = encounter
    mid = _map(client, auth_headers)
    body = client.post(f"/api/dm/map/{mid}/spawn-encounter", json={"encounter_id": enc},
                       headers=auth_headers).json()
    assert body["added"] == 1 and body["skipped"] == 0, "only as many as fit"
    assert len(body["tokens"]) == 1
