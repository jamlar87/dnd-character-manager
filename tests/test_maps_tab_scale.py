"""The Maps tab has to survive a corpus-sized library: 689 maps must not become 689 DOM rows.

The list is the one screen in the app that grows without bound (the manual ingest took it from 1 map
to 689), so these lock in what keeps it usable: a slim payload, collapsed groups, and a filter.
"""
from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parent.parent


def test_the_list_payload_leaves_the_canvas_state_behind(client, seeded_db, auth_headers):
    """`SELECT m.*` shipped fog, draw_data and the player key for every map: state the list never
    reads, 323 KB of it at 689 maps, on every tab switch."""
    for i in range(3):
        client.post("/api/dm/map/create", json={"name": f"Map {i}", "grid_size": 100},
                    headers=auth_headers)
    body = client.get("/api/dm/maps", headers=auth_headers).json()
    assert body["count"] >= 3
    row = body["maps"][0]
    for blob in ("fog", "draw_data", "notes", "player_key", "camera"):
        assert blob not in row, f"the list should not carry {blob}"
    for needed in ("id", "name", "grid_type", "grid_size", "source_manual", "source_page",
                   "image_path", "token_count", "scene_count"):
        assert needed in row, f"the list needs {needed}"


def test_the_list_still_reports_token_and_scene_counts(client, seeded_db, auth_headers):
    """Slimming the SELECT must not drop the counts the cards show."""
    mid = client.post("/api/dm/map/create", json={"name": "Counted"}, headers=auth_headers).json()["id"]
    body = client.get("/api/dm/maps", headers=auth_headers).json()
    mine = [m for m in body["maps"] if m["id"] == mid][0]
    assert mine["token_count"] == 0 and mine["scene_count"] == 0


def test_the_tab_renders_collapsed_groups_rather_than_every_row():
    js = (APP / "static" / "dm_tools.js").read_text()
    assert "function renderMapList(" in js
    assert "_openBooks" in js, "sections must be collapsed by default, not all open"
    assert "function filterMaps(" in js, "689 maps need a filter"
    assert re.search(r"_openBooks\.has\(book\)", js)
    # the row markup may only be emitted inside an open section
    body = js.split("function renderMapList(")[1].split("\nasync function renderMaps")[0]
    assert "items.map(mapRow)" in body
    assert "${open ?" in body or "${open ?" in body, "rows must be conditional on the section being open"


def test_a_deleted_map_does_not_linger_in_the_cached_list():
    js = (APP / "static" / "dm_tools.js").read_text()
    delete_fn = js.split("async function deleteMap(")[1].split("function toggleShareEncounter")[0]
    assert "_mapsCache = null" in delete_fn, "the list is cached; deleting must drop the cache"
