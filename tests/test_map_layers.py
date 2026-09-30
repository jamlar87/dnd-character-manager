"""Fog of war and the drawing layer — storage, cleaning, and the canvas contract.

Two things matter here: the server must never store a fog/draw blob it cannot render (the row
is re-sent on every map load), and the canvas must fill the fog as ONE even-odd path — filling
it and then erasing with `destination-out` would erase the map underneath it.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
VTT = REPO / "static" / "vtt.js"
TEMPLATE = REPO / "templates" / "map.html"


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


def _map(client, headers, name="Layers"):
    return client.post("/api/dm/map/create", json={"name": name, "grid_size": 50},
                       headers=headers).json()["id"]


def _layers(db_path, mid):
    db = sqlite3.connect(db_path)
    row = db.execute("SELECT fog, fog_on, draw_data FROM dm_maps WHERE id=?", (mid,)).fetchone()
    db.close()
    return json.loads(row[0] or "[]"), row[1], json.loads(row[2] or "[]")


# ── the API ────────────────────────────────────────────────────────────────────────────

def test_fog_and_drawing_round_trip(client, seeded_db, auth_headers):
    mid = _map(client, auth_headers)
    stroke = {"color": "#ff5555", "width": 6, "points": [[10, 10], [20, 20], [30, 15]]}
    r = client.post(f"/api/dm/map/{mid}/layer",
                    json={"fog": ["0,0", "1,0", "0,1"], "fog_on": 1, "draw": [stroke]},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["fog"]) == ["0,0", "0,1", "1,0"]

    detail = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    assert sorted(detail["fog"]) == ["0,0", "0,1", "1,0"], "the canvas expects fog parsed, not a string"
    assert detail["fog_on"] == 1
    assert detail["draw"][0]["color"] == "#ff5555"
    assert len(detail["draw"][0]["points"]) == 3


def test_junk_layer_data_is_cleaned_not_stored(client, seeded_db, auth_headers):
    """The row is re-read on every map load, so a bad entry would be re-rendered forever."""
    mid = _map(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/layer", json={
        "fog": ["3,4", "3,4", "not-a-cell", "1;2", "", None, {"a": 1}, -2, "-1,-1"],
        "draw": [
            {"color": "#0f0", "width": 0, "points": [[1, 2], [3, 4]]},
            {"color": "#0f0", "width": 999, "points": [[1, 2], [3, 4]]},
            {"points": [[5, 5]]},                      # one point is not a stroke
            {"color": "#0f0"},                         # no points at all
            "not-a-dict",
            {"points": [["x", 1], [2, 3], None, [4]]},  # unusable points are dropped
        ],
    }, headers=auth_headers)
    fog, fog_on, draw = _layers(seeded_db["db_path"], mid)
    assert fog == ["3,4", "-1,-1"], "bad keys dropped, duplicates collapsed"
    assert fog_on in (0, None), "fog_on was not sent and must stay off"
    assert len(draw) == 3, "a stroke needs two usable points"
    assert draw[0]["width"] == 1 and draw[1]["width"] == 40, "width clamps to 1..40"
    assert len(draw[2]["points"]) == 1 or len(draw[2]["points"]) >= 2


def test_a_partial_layer_save_leaves_the_other_layers_alone(client, seeded_db, auth_headers):
    mid = _map(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/layer", json={"fog": ["0,0"], "draw": [
        {"color": "#fff", "width": 3, "points": [[0, 0], [1, 1]]}]}, headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/layer", json={"fog_on": 1}, headers=auth_headers)
    fog, fog_on, draw = _layers(seeded_db["db_path"], mid)
    assert fog == ["0,0"] and fog_on == 1 and len(draw) == 1, (
        "toggling the fog must not clear the revealed cells or the drawing")


def test_the_layers_are_capped(client, seeded_db, auth_headers):
    import routes.maps as maps
    mid = _map(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/layer", json={
        "fog": [f"{i},{i}" for i in range(maps.FOG_MAX + 50)],
        "draw": [{"color": "#fff", "width": 2, "points": [[0, 0], [1, 1]]}
                 for _ in range(maps.DRAW_MAX_STROKES + 20)],
    }, headers=auth_headers)
    fog, _, draw = _layers(seeded_db["db_path"], mid)
    assert len(fog) == maps.FOG_MAX
    assert len(draw) == maps.DRAW_MAX_STROKES


def test_layers_are_scoped_to_the_owner(client, seeded_db, auth_headers):
    theirs = _insert(seeded_db["db_path"],
                     "INSERT INTO dm_maps (user_id, name, fog, draw_data) VALUES (?,?,?,?)",
                     (2, "Theirs", '["9,9"]', '[]'))
    r = client.post(f"/api/dm/map/{theirs}/layer", json={"fog": [], "draw": []}, headers=auth_headers)
    assert r.status_code == 404, r.text
    fog, _, _ = _layers(seeded_db["db_path"], theirs)
    assert fog == ["9,9"], "another user's fog must not be touched"


def test_a_snapshot_carries_the_fog_and_the_drawing(client, seeded_db, auth_headers):
    """A prepared setup is also what the party has already seen."""
    mid = _map(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/layer",
                json={"fog": ["1,1"], "fog_on": 1,
                      "draw": [{"color": "#fff", "width": 4, "points": [[0, 0], [50, 50]]}]},
                headers=auth_headers)
    snap = client.post(f"/api/dm/map/{mid}/snapshot", json={"name": "Prepared"},
                       headers=auth_headers).json()
    client.post(f"/api/dm/map/{mid}/layer", json={"fog": [], "fog_on": 0, "draw": []},
                headers=auth_headers)
    assert _layers(seeded_db["db_path"], mid)[0] == []

    restored = client.post(f"/api/dm/map/scene/{snap['id']}/restore", headers=auth_headers).json()
    assert restored["ok"] is True
    assert restored["fog"] == ["1,1"] and restored["fog_on"] == 1
    assert len(restored["draw"]) == 1, "the drawing comes back with the setup"
    assert _layers(seeded_db["db_path"], mid)[0] == ["1,1"]


# ── the canvas contract ────────────────────────────────────────────────────────────────

def test_the_fog_is_one_even_odd_path_not_an_erase():
    """`destination-out` would punch the holes through the MAP as well, not just the fog."""
    src = VTT.read_text()
    # strip comments first: the function's own explanation names the trap it avoids
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    assert "function drawFog" in code
    assert "'evenodd'" in code, "the fog must be filled as one even-odd path"
    assert "destination-out" not in code, (
        "erasing the fog erases the map: use the even-odd hole fill")
    assert "Path2D" in code


def test_tokens_are_dimmed_under_the_fog_not_hidden():
    """The DM looks at this screen and still has to move what is waiting in the dark."""
    src = VTT.read_text()
    assert "isCellRevealed" in src and "globalAlpha = 0.35" in src
    order = src.split("drawGrid();")[1].split(".forEach(drawToken)")[0]
    assert order.index("drawFog") < order.index("drawStrokes"), (
        "the DM's own marks go over the fog — an annotation you cannot read is not an annotation")


def test_the_layer_save_is_separate_from_the_token_save():
    src = VTT.read_text()
    assert "'/layer'" in src, "no layer endpoint call"
    assert "keepalive: true" in src
    # the payload the layer save posts (its own function — a `.then(function (r)` would cut a
    # naive split short, which is exactly how this guard first failed)
    payload = src.split("function layerPayload")[1].split("\n  }")[0]
    for key in ("fog", "fog_on", "draw"):
        assert key in payload, f"the layer payload does not carry {key}"
    assert "layerPayload()" in src.split("function saveLayer")[1][:400]


def test_fog_painting_and_the_pen_are_wired_to_the_right_buttons():
    src = VTT.read_text()
    assert "function paintCell" in src and "cellKeyFor" in src
    mouse = src.split("canvas.addEventListener('mousedown'")[1].split("window.addEventListener('mousemove'")[0]
    assert "state.tool === 'fog'" in mouse and "state.tool === 'draw'" in mouse
    assert "ev.button === 2" in mouse, "right-drag must pan while a tool is active"
    move = src.split("window.addEventListener('mousemove'")[1].split("addEventListener('wheel'")[0]
    assert "paintCell" in move and "state.stroke.points.push" in move


def test_the_page_offers_every_layer_action():
    """Every layer action must stay reachable after the toolbar was split up.

    The bar above the map holds the few controls worth reaching for mid-turn; the rest live in
    collapsed panels beside it (both in map.html now), and several were consolidated — the four tool
    buttons became one dropdown, the three brushes became one dropdown, and reveal/hide-all became a
    single two-state button. Consolidation is only acceptable while the capability survives, so this
    checks the reachable control AND, where a button was merged away, that the underlying function is
    still there to be called.
    """
    toolbar = TEMPLATE.parent / "_map_toolbar.html"
    page = TEMPLATE.read_text() + toolbar.read_text()
    assert '{% include "_map_toolbar.html" %}' in TEMPLATE.read_text(), "the partial is not included"
    for call in ("toggleFog()", "clearDraw()", "setPen", "spawnEncounter()", "clearMeasure()",
                 "setFeetPerCell(", "toggleRevealAll()", "toggleGrid()", "fitGridToImage()"):
        assert call in page, f"{call} is no longer reachable from the map page"
    # the consolidated dropdowns offer the same choices as the buttons they replaced
    for value in ("select", "fog", "draw", "measure"):
        assert f'<option value="{value}">' in page, f"the tool dropdown lost {value}"
    assert 'onchange="VTT.setTool(this.value)"' in page
    for brush in ("cell", "rect", "circle"):
        assert f'<option value="{brush}">' in page, f"the brush dropdown lost {brush}"
    assert 'onchange="VTT.setFogBrush(this.value)"' in page
    # ...and the merged-away functions still exist in the asset
    src = VTT.read_text()
    for fn in ("function revealAll", "function hideAll", "function setFogBrush", "function setTool",
               "function setGridType", "function toggleRevealAll"):
        assert fn in src, f"{fn} disappeared with its button"
    # the selects are kept in step with the state they set
    assert "var pick = $('vttToolPick'); if (pick) pick.value = state.tool;" in src
    assert "var bp = $('vttBrushPick'); if (bp) bp.value = state.fogBrush;" in src
    assert "var gt = $('vttGridType'); if (gt) gt.value = state.grid.type;" in src
    assert 'id="vttEncounterPick"' in page, "spawning needs an encounter to pick"
    assert 'id="vttFeet"' in page, "the measure tool is useless without the ft/cell setting"


def test_the_brush_shapes_and_the_ruler_are_cells_first():
    """Both are computed from cell CENTRES, and the ruler has to be honest about diagonals."""
    src = VTT.read_text()
    assert "function cellsInRect" in src and "function cellsInCircle" in src
    assert "function measureCells" in src and "function applyMarquee" in src
    # 5e's simplified table rule: a diagonal counts as one cell (Chebyshev, not Euclidean)
    measure = src.split("function measureCells")[1].split("function ")[0]
    assert "Math.max(Math.abs" in measure, "square-grid distance must use Chebyshev"
    assert "axial hex distance" in measure or "Math.abs(dq)" in measure, "hex distance is axial"
    # the ruler is transient and never travels to a second screen
    assert "state.ruler = null;      // the ruler is a transient overlay" in src
    ruler = src.split("function drawRuler")[1].split("function ")[0]
    assert "state.readOnly" in ruler, "the party must not see the DM's ruler"


def test_a_measure_tool_uses_the_maps_own_scale():
    """Assuming 5 ft/cell would silently give wrong numbers on a 10-ft map."""
    src = VTT.read_text()
    assert "feetPerCell" in src and "feet_per_cell" in src
    routes = (REPO / "routes" / "maps.py").read_text()
    assert '"feet_per_cell"' in routes and "feet_per_cell = ?" in routes
    schema = (REPO / "services" / "db_schema.py").read_text()
    assert "ADD COLUMN feet_per_cell" in schema
