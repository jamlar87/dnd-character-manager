"""The map layer's API: maps, placements, snapshots.

Placements are the part that has to be right before a canvas is worth drawing, so these tests
cover the two things a UI cannot be trusted to do: ownership scoping, and keeping a token's
identity (its id) stable across a full-list save — HP and the encounter link ride on that id.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MAPS_DIR = REPO / "static" / "maps"

# 1x1 transparent PNG — small enough that the downscaler leaves it alone
PNG_1PX = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)).decode()


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


def _new_map(client, headers, name="Probe map", **extra):
    payload = {"name": name, **extra}
    r = client.post("/api/dm/map/create", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def no_stray_map_files():
    """The upload route writes into the real static dir — clean up whatever it creates."""
    before = {p.name for p in MAPS_DIR.glob("*")} if MAPS_DIR.exists() else set()
    yield
    if MAPS_DIR.exists():
        for p in MAPS_DIR.glob("*"):
            if p.name not in before:
                p.unlink()


def test_the_tables_exist_and_start_empty(client, seeded_db, auth_headers):
    r = client.get("/api/dm/maps", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["maps"] == []
    db = sqlite3.connect(seeded_db["db_path"])
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    assert {"dm_maps", "dm_map_tokens", "dm_map_scenes"} <= tables


def test_a_map_can_be_created_and_read_back(client, seeded_db, auth_headers):
    mid = _new_map(client, auth_headers, "Cragmaw hideout", grid_type="hex", grid_size=70)
    listed = client.get("/api/dm/maps", headers=auth_headers).json()["maps"]
    assert [m["name"] for m in listed] == ["Cragmaw hideout"]
    assert listed[0]["grid_type"] == "hex" and listed[0]["grid_size"] == 70
    assert listed[0]["token_count"] == 0

    detail = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()
    assert detail["map"]["name"] == "Cragmaw hideout"
    assert detail["tokens"] == [] and detail["scenes"] == []


def test_a_map_needs_a_name(client, seeded_db, auth_headers):
    assert client.post("/api/dm/map/create", json={"name": "  "}, headers=auth_headers).status_code == 400


def test_grid_updates_are_partial(client, seeded_db, auth_headers):
    """A camera save must not wipe the grid, and vice versa."""
    mid = _new_map(client, auth_headers, grid_type="hex", grid_size=70)
    client.post(f"/api/dm/map/{mid}/update", json={"camera": {"x": 10, "y": 20, "zoom": 1.5}},
                headers=auth_headers)
    detail = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    assert detail["grid_type"] == "hex", "saving the camera must not reset the grid"
    assert json.loads(detail["camera"])["zoom"] == 1.5


def test_maps_are_scoped_to_their_owner(client, seeded_db, auth_headers):
    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?, ?)",
                     (2, "Not yours"))
    assert client.get(f"/api/dm/map/{theirs}", headers=auth_headers).status_code == 404
    assert client.post(f"/api/dm/map/{theirs}/update", json={"name": "mine"},
                       headers=auth_headers).status_code == 404
    assert client.post(f"/api/dm/map/{theirs}/delete", headers=auth_headers).status_code == 404
    assert client.get("/api/dm/maps", headers=auth_headers).json()["maps"] == []


def test_uploading_a_map_image_writes_a_file_not_a_row(client, seeded_db, auth_headers, no_stray_map_files):
    """A 4096px battle map must never become a data URL in the database."""
    mid = _new_map(client, auth_headers)
    r = client.post(f"/api/dm/map/{mid}/image",
                    json={"image": f"data:image/png;base64,{PNG_1PX}"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    path = r.json()["image_path"]
    assert path.startswith("/static/maps/"), path
    assert (MAPS_DIR / Path(path).name).is_file(), "the image was not written to disk"

    stored = sqlite3.connect(seeded_db["db_path"]).execute(
        "SELECT image_path FROM dm_maps WHERE id=?", (mid,)).fetchone()[0]
    assert stored == path and not stored.startswith("data:"), "the row must hold a path, not pixels"

    # clearing removes the reference
    cleared = client.post(f"/api/dm/map/{mid}/image", json={"image": ""}, headers=auth_headers).json()
    assert cleared["image_path"] == ""


def test_a_junk_image_is_rejected_without_changing_the_row(client, seeded_db, auth_headers):
    mid = _new_map(client, auth_headers)
    r = client.post(f"/api/dm/map/{mid}/image", json={"image": "data:image/png;base64,notbase64!!"},
                    headers=auth_headers)
    assert r.status_code == 400, r.text
    stored = sqlite3.connect(seeded_db["db_path"]).execute(
        "SELECT image_path FROM dm_maps WHERE id=?", (mid,)).fetchone()[0]
    assert stored == ""


def test_an_external_image_url_is_allowed(client, seeded_db, auth_headers):
    """The reference library serves plenty of remote art; do not force an upload."""
    mid = _new_map(client, auth_headers)
    r = client.post(f"/api/dm/map/{mid}/image",
                    json={"image": "https://example.com/map.png"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["external"] is True


def test_tokens_keep_their_ids_across_a_full_list_save(client, seeded_db, auth_headers):
    mid = _new_map(client, auth_headers)
    a = client.post(f"/api/dm/map/{mid}/token/add",
                    json={"kind": "creature", "ref_name": "Goblin", "x": 1, "y": 2, "hp_max": 7},
                    headers=auth_headers).json()["token"]
    b = client.post(f"/api/dm/map/{mid}/token/add",
                    json={"kind": "character", "ref_name": "Orla", "x": 3, "y": 4, "character_id": 86},
                    headers=auth_headers).json()["token"]
    assert a["id"] != b["id"]

    # the canvas drag-saves the whole list: move one, keep the other, add a third
    saved = client.post(f"/api/dm/map/{mid}/tokens", json={"tokens": [
        {"id": a["id"], "x": 100, "y": 200, "kind": "creature", "ref_name": "Goblin", "hp_max": 7},
        {"kind": "marker", "ref_name": "", "label": "door", "x": 5, "y": 6},
    ]}, headers=auth_headers).json()["tokens"]
    ids = {t["id"] for t in saved}
    assert a["id"] in ids, "a moved token must keep its id — HP and the encounter link ride on it"
    assert b["id"] not in ids, "a token dropped from the list must be deleted"
    moved = next(t for t in saved if t["id"] == a["id"])
    assert (moved["x"], moved["y"]) == (100.0, 200.0)
    assert moved["hp_max"] == 7, "a move must not reset the token's HP"


def test_token_payloads_are_cleaned(client, seeded_db, auth_headers):
    mid = _new_map(client, auth_headers)
    tok = client.post(f"/api/dm/map/{mid}/token/add", json={
        "kind": "dragon", "ref_name": "X" * 400, "label": "Y" * 200,
        "w": 99, "h": 0, "x": "nonsense", "hp_current": 10 ** 9, "hp_max": -5,
    }, headers=auth_headers).json()["token"]
    assert tok["kind"] == "creature", "an unknown kind falls back"
    assert len(tok["ref_name"]) == 120 and len(tok["label"]) == 60
    assert tok["w"] == 12 and tok["h"] == 1, "sizes clamp"
    assert tok["x"] == 0.0, "a non-numeric position is not a crash"
    assert tok["hp_max"] == 0, "hp_max cannot go negative"


def test_the_token_cap_is_enforced(client, seeded_db, auth_headers, monkeypatch):
    import routes.maps as maps
    monkeypatch.setattr(maps, "TOKEN_MAX", 2)
    mid = _new_map(client, auth_headers)
    for _ in range(2):
        assert client.post(f"/api/dm/map/{mid}/token/add", json={"kind": "marker", "x": 1, "y": 1},
                           headers=auth_headers).status_code == 200
    over = client.post(f"/api/dm/map/{mid}/token/add", json={"kind": "marker", "x": 1, "y": 1},
                       headers=auth_headers)
    assert over.status_code == 400, "the map must refuse to grow past its cap"


def test_tokens_are_not_writable_across_users(client, seeded_db, auth_headers):
    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?, ?)",
                     (2, "Theirs"))
    tok = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_map_tokens (map_id, kind, ref_name, x, y) VALUES (?,?,?,?,?)",
                  (theirs, "creature", "Goblin", 1, 1))
    assert client.post(f"/api/dm/map/token/{tok}/update", json={"hp_current": 0},
                       headers=auth_headers).status_code == 404
    assert client.post(f"/api/dm/map/token/{tok}/delete", headers=auth_headers).status_code == 404
    assert client.post(f"/api/dm/map/{theirs}/tokens", json={"tokens": []},
                       headers=auth_headers).status_code == 404
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT COUNT(*) FROM dm_map_tokens WHERE id=?", (tok,)).fetchone()[0] == 1
    db.close()


def test_a_snapshot_round_trips_placements_and_camera(client, seeded_db, auth_headers):
    """Atlas calls these game saves: several setups for one map."""
    mid = _new_map(client, auth_headers)
    tok = client.post(f"/api/dm/map/{mid}/token/add",
                      json={"kind": "creature", "ref_name": "Owlbear", "x": 10, "y": 10,
                            "hp_current": 20, "hp_max": 20}, headers=auth_headers).json()["token"]
    client.post(f"/api/dm/map/{mid}/update", json={"camera": {"x": 0, "y": 0, "zoom": 1}},
                headers=auth_headers)
    snap = client.post(f"/api/dm/map/{mid}/snapshot", json={"name": "Before the ambush"},
                       headers=auth_headers).json()
    assert snap["ok"] is True

    # the fight moves everything, then the DM restores the prepared setup
    client.post(f"/api/dm/map/{mid}/tokens", json={"tokens": [
        {"id": tok["id"], "x": 400, "y": 500, "kind": "creature", "ref_name": "Owlbear",
         "hp_current": 3, "hp_max": 20}]}, headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/update", json={"camera": {"x": 999, "y": 999, "zoom": 3}},
                headers=auth_headers)

    restored = client.post(f"/api/dm/map/scene/{snap['id']}/restore", headers=auth_headers).json()
    assert restored["ok"] is True
    assert json.loads(restored["camera"])["zoom"] == 1
    positions = {(t["x"], t["y"]) for t in restored["tokens"]}
    assert (10.0, 10.0) in positions, "the snapshot must put the tokens back where they were"
    hp = [t["hp_current"] for t in restored["tokens"]]
    assert hp == [20], "restoring a setup also restores its HP"


def test_snapshots_are_scoped_and_deletable(client, seeded_db, auth_headers):
    mid = _new_map(client, auth_headers)
    snap = client.post(f"/api/dm/map/{mid}/snapshot", json={"name": "S1"}, headers=auth_headers).json()
    scenes = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["scenes"]
    assert [s["name"] for s in scenes] == ["S1"]
    assert client.post(f"/api/dm/map/scene/{snap['id']}/delete", headers=auth_headers).status_code == 200
    assert client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["scenes"] == []

    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?, ?)",
                     (2, "Theirs"))
    scene = _insert(seeded_db["db_path"],
                    "INSERT INTO dm_map_scenes (map_id, name, snapshot) VALUES (?,?,?)",
                    (theirs, "Private", "{}"))
    assert client.post(f"/api/dm/map/scene/{scene}/restore", headers=auth_headers).status_code == 404
    assert client.post(f"/api/dm/map/scene/{scene}/delete", headers=auth_headers).status_code == 404


def test_deleting_a_map_takes_its_children(client, seeded_db, auth_headers):
    mid = _new_map(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/token/add", json={"kind": "marker", "x": 1, "y": 1},
                headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/snapshot", json={"name": "S"}, headers=auth_headers)
    assert client.post(f"/api/dm/map/{mid}/delete", headers=auth_headers).status_code == 200
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT COUNT(*) FROM dm_map_tokens WHERE map_id=?", (mid,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM dm_map_scenes WHERE map_id=?", (mid,)).fetchone()[0] == 0
    db.close()
