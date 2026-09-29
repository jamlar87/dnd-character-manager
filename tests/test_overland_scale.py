"""Overland maps are measured in miles per hex, which the old 1-100 clamp could not express.

The DMG's scales are 1 mile (5280 ft), 6 miles (31,680 ft) and 60 miles (316,800 ft) per hex, so a
map at province scale could not record its own scale at all: the field clamped 5280 to 100.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent


def _insert(db_path, sql, values):
    con = sqlite3.connect(str(db_path))
    con.execute(sql, values)
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


def _get(db_path, sql, params=()):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(sql, params).fetchone()
    con.close()
    return dict(row) if row else {}


@pytest.mark.parametrize("feet,dmg_scale", [
    (5280, "province: 1 mile per hex"),
    (31680, "kingdom: 6 miles per hex"),
    (316800, "continent: 60 miles per hex"),
])
def test_an_overland_scale_survives_create_and_update(client, seeded_db, auth_headers, feet, dmg_scale):
    assert dmg_scale
    mid = client.post("/api/dm/map/create",
                      json={"name": "Overland", "grid_type": "hex", "grid_size": 48,
                            "feet_per_cell": feet}, headers=auth_headers).json()["id"]
    row = _get(seeded_db["db_path"], "SELECT grid_type, feet_per_cell FROM dm_maps WHERE id=?", (mid,))
    assert row["feet_per_cell"] == feet, f"{feet} was clamped away: {row}"
    assert row["grid_type"] == "hex"

    # and the update route must not clamp it either
    other = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                    (1, "Set later"))
    r = client.post(f"/api/dm/map/{other}/update", json={"feet_per_cell": feet}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _get(seeded_db["db_path"],
                "SELECT feet_per_cell FROM dm_maps WHERE id=?", (other,))["feet_per_cell"] == feet


def test_a_battle_map_scale_still_works_and_the_clamp_still_bounds(client, seeded_db, auth_headers):
    mid = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                  (1, "Battle"))
    client.post(f"/api/dm/map/{mid}/update", json={"feet_per_cell": 5}, headers=auth_headers)
    assert _get(seeded_db["db_path"],
                "SELECT feet_per_cell FROM dm_maps WHERE id=?", (mid,))["feet_per_cell"] == 5
    # zero and negatives still fall back to the default rather than storing nonsense
    client.post(f"/api/dm/map/{mid}/update", json={"feet_per_cell": 0}, headers=auth_headers)
    assert _get(seeded_db["db_path"],
                "SELECT feet_per_cell FROM dm_maps WHERE id=?", (mid,))["feet_per_cell"] >= 1
    # and an absurd value is still bounded
    client.post(f"/api/dm/map/{mid}/update", json={"feet_per_cell": 99_000_000}, headers=auth_headers)
    from routes.maps import FEET_PER_CELL_MAX
    assert _get(seeded_db["db_path"],
                "SELECT feet_per_cell FROM dm_maps WHERE id=?", (mid,))["feet_per_cell"] == FEET_PER_CELL_MAX


def test_the_client_guards_agree_with_the_server_clamp():
    """The toolbar input's max and the JS guard must match FEET_PER_CELL_MAX, or the UI silently
    refuses a scale the server would accept (that is how the 100 cap was felt)."""
    from routes.maps import FEET_PER_CELL_MAX
    number = f"{FEET_PER_CELL_MAX}"
    toolbar = (APP / "templates" / "_map_toolbar.html").read_text()
    js = (APP / "static" / "vtt.js").read_text()
    assert f'max="{number}"' in toolbar, f"toolbar input max should be {number}"
    assert f"v > {number}" in js, f"vtt.js guard should allow up to {number}"


def test_the_measure_tool_says_miles_on_an_overland_map():
    """`316800 ft` is not a readable answer for a hex map; the label must switch unit."""
    js = (APP / "static" / "vtt.js").read_text()
    assert "function scaleLabel(" in js
    assert "scaleLabel(state.feetPerCell)" in js, "the grid readout should use it"
    assert "scaleLabel(dist.feet)" in js, "so should the ruler"
    assert re.search(r"feet >= 5280", js), "the switch is at a mile"
