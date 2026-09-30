"""The hex overlay has two orientations a quarter turn apart.

A printed map's own hexes often run the other way from the overlay, and no amount of offset nudging
fixes that - the lattice itself has to turn. Rather than maintain two sets of hex maths, the lattice is
built as it always was and the frame is turned around it: toHexFrame maps a world point into lattice
space, fromHexFrame maps a lattice point back out.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "vtt.js").read_text()
PAGE = (ROOT / "templates" / "map.html").read_text()


def test_the_frame_transform_is_its_own_inverse():
    """toHexFrame and fromHexFrame must undo each other exactly, or a cell centre would not round-trip
    back to its own key and fog would land on the wrong hex."""
    to = JS.split("function toHexFrame")[1].split("\n  }")[0]
    fro = JS.split("function fromHexFrame")[1].split("\n  }")[0]
    assert "[y, -x]" in to, "a quarter turn into lattice space"
    assert "[-y, x]" in fro, "and the opposite quarter turn back out"
    assert "hexTurned()" in to and "hexTurned()" in fro, "both must respect the flag"
    assert "[x, y]" in to and "[x, y]" in fro, "and both must be the identity when it is off"


def test_the_lattice_maths_goes_through_the_frame():
    key = JS.split("function cellKeyFor")[1].split("function cellCentreWorld")[0]
    assert "toHexFrame(" in key, "a world point must be mapped into lattice space before indexing"
    centre = JS.split("function cellCentreWorld")[1].split("function hexOnPath")[0]
    assert "fromHexFrame(" in centre, "a lattice cell must be mapped back out to be drawn"
    axial = JS.split("function axialToWorld")[1].split("function cellsInRect")[0]
    assert "fromHexFrame(" in axial


def test_the_hex_centre_respects_the_grid_offset():
    """The square branch has always added the offsets; the hex branch left them off, which put a revealed
    cell's centre somewhere other than its hex as soon as a DM nudged the grid."""
    centre = JS.split("function cellCentreWorld")[1].split("function hexOnPath")[0]
    hex_branch = centre.split("g.type === 'hex'")[1]
    assert "g.ox" in hex_branch and "g.oy" in hex_branch, "the hex centre must honour the grid offset"


def test_both_hex_shape_builders_turn_with_the_frame():
    """The outline and the fog/draw path both build hexes; if only one turns, fog stops matching the grid."""
    assert JS.count("hexTurned() ? 90 : 0") == 2, "the outline and the path builder must both turn"


def test_the_draw_loop_covers_turned_space():
    # anchor on drawGrid itself: cellKeyFor has a hex branch too, and splitting on the bare condition
    # silently read that one instead, which made this test fail against correct code
    loop = JS.split("function drawGrid()")[1].split("} else {")[0]
    assert "toHexFrame(-cam.x" in loop, "the loop origin must be found in lattice space"
    assert "fromHexFrame(" in loop, "each cell must be mapped out through the frame"
    assert "turned ? H : W" in loop, "the viewport must be measured as the lattice sees it"


def test_there_is_a_control_a_shortcut_and_it_is_exported():
    assert "VTT.flipHex(90)" not in PAGE and "VTT.flipHex()" in PAGE, "the Grid panel needs the control"
    assert "flipHex: flipHex" in JS, "the button calls VTT.flipHex, so it must be exported"
    assert "ev.key === 'h'" in JS, "H should flip the hexes"


def test_the_update_route_carries_the_orientation(client, seeded_db, auth_headers):
    made = client.post("/api/dm/map/create", json={"name": "ZZ hex scratch", "grid_type": "hex",
                                                   "grid_size": 100}, headers=auth_headers)
    assert made.status_code == 200, made.text
    mid = made.json()["id"]
    try:
        client.post(f"/api/dm/map/{mid}/update", json={"hex_turn": 1}, headers=auth_headers)
        got = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()
        row = got.get("map", got)
        assert row.get("hex_turn") == 1, "a flip must be stored"
        # it is a two-state flag: anything truthy lands on 1, nothing lands on 0
        client.post(f"/api/dm/map/{mid}/update", json={"hex_turn": 7}, headers=auth_headers)
        got = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()
        assert (got.get("map", got)).get("hex_turn") == 1
        client.post(f"/api/dm/map/{mid}/update", json={"hex_turn": 0}, headers=auth_headers)
        got = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()
        assert (got.get("map", got)).get("hex_turn") == 0
    finally:
        client.post(f"/api/dm/map/{mid}/delete", json={}, headers=auth_headers)
