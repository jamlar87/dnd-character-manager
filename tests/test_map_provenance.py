"""Provenance: an ingested map must say (and link to) the manual page it came from."""
from __future__ import annotations

import sqlite3

APP_DB = "characters.db"


def _row(db_path, sql, params=()):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(sql, params).fetchone()
    con.close()
    return dict(row) if row else {}


def test_create_stores_the_source_manual_and_page(client, seeded_db, auth_headers):
    r = client.post("/api/dm/map/create",
                    json={"name": "Death House", "grid_type": "square", "grid_size": 100,
                          "feet_per_cell": 5, "source_manual": "COS", "source_page": 24},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    row = _row(seeded_db["db_path"],
               "SELECT source_manual, source_page FROM dm_maps WHERE id=?", (mid,))
    assert row == {"source_manual": "COS", "source_page": 24}

    # and the detail endpoint the map page reads returns them
    detail = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    assert detail["source_manual"] == "COS" and detail["source_page"] == 24


def test_update_can_attribute_a_map_that_was_imported_before(client, seeded_db, auth_headers):
    mid = client.post("/api/dm/map/create", json={"name": "Old import"},
                      headers=auth_headers).json()["id"]
    assert _row(seeded_db["db_path"], "SELECT source_manual FROM dm_maps WHERE id=?",
                (mid,))["source_manual"] == ""
    client.post(f"/api/dm/map/{mid}/update",
                json={"source_manual": "DMG", "source_page": 14}, headers=auth_headers)
    assert _row(seeded_db["db_path"], "SELECT source_manual, source_page FROM dm_maps WHERE id=?",
                (mid,)) == {"source_manual": "DMG", "source_page": 14}


def test_the_map_page_links_to_the_manual_page(client, seeded_db, auth_headers):
    mid = client.post("/api/dm/map/create",
                      json={"name": "Linked", "grid_size": 50, "source_manual": "PHB",
                            "source_page": 192}, headers=auth_headers).json()["id"]
    html = client.get(f"/dm-map/{mid}", headers=auth_headers).text
    assert "/api/reference/open/PHB?page=192" in html, "the map must link back to its page"
    assert "vtt-source" in html


def test_a_map_without_provenance_renders_no_dead_link(client, seeded_db, auth_headers):
    mid = client.post("/api/dm/map/create", json={"name": "Unsourced"},
                      headers=auth_headers).json()["id"]
    html = client.get(f"/dm-map/{mid}", headers=auth_headers).text
    assert "/api/reference/open/" not in html, "no link when there is no source"


def test_the_schema_adds_the_columns_to_an_existing_database(seeded_db):
    """init_db must upgrade a database that predates the columns (the ALTER pattern)."""
    cols = {r[1] for r in sqlite3.connect(str(seeded_db["db_path"])).execute(
        "PRAGMA table_info(dm_maps)")}
    assert {"source_manual", "source_page"} <= cols
