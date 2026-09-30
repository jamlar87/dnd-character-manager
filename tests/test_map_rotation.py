"""Turning the artwork: a region map scanned sideways should be readable on the table.

The rotation is stored on the map (0/90/180/270) and applied when the art is drawn. The world frame is
deliberately NOT rotated - tokens, grid, fog and hit-testing all measure in world units - so a turn
cannot silently move a token off the square the DM put it on.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "vtt.js").read_text()
PAGE = (ROOT / "templates" / "map.html").read_text()

from routes.maps import _quarter_turn


def test_a_rotation_snaps_to_a_quarter_turn():
    """The control exists to straighten a scan, and a free angle would fight the square grid."""
    assert _quarter_turn(0) == 0
    assert _quarter_turn(90) == 90
    assert _quarter_turn(180) == 180
    assert _quarter_turn(270) == 270
    assert _quarter_turn(450) == 90, "a full turn past 90 is still 90"
    assert _quarter_turn(-90) == 270, "negative angles normalise rather than going missing"
    assert _quarter_turn(95) == 90
    assert _quarter_turn(37) == 0, "anything nearer upright stays upright"
    assert _quarter_turn("180") == 180, "a string from JSON is accepted"
    assert _quarter_turn(None) == 0
    assert _quarter_turn("sideways") == 0


def test_the_update_route_carries_the_rotation(client, seeded_db, auth_headers):
    made = client.post("/api/dm/map/create", json={"name": "ZZ rot scratch", "grid_size": 100},
                       headers=auth_headers)
    assert made.status_code == 200, made.text
    mid = made.json()["id"]
    try:
        r = client.post(f"/api/dm/map/{mid}/update", json={"rotation": 450}, headers=auth_headers)
        assert r.status_code == 200, r.text
        got = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()
        row = got.get("map", got)
        assert row.get("rotation") == 90, f"expected a snapped 90, got {row.get('rotation')}"
    finally:
        client.post(f"/api/dm/map/{mid}/delete", json={}, headers=auth_headers)


def test_turning_only_moves_the_picture():
    """The draw call rotates the image; nothing else in the world frame is touched."""
    frame = JS.split("function drawFrame()")[1].split("function pokePlayers")[0]
    assert "ctx.rotate(" in frame, "the art must actually be turned"
    assert "ctx.save()" in frame and "ctx.restore()" in frame, "the turn must not leak into the next draw"
    # the world->screen transform is the one everything else depends on; it must stay a plain camera
    w2s = JS.split("function worldToScreen")[1].split("function screenToWorld")[0]
    assert "rotate" not in w2s, "rotating the world frame would move tokens off their squares"


def test_the_turned_footprint_is_what_measurements_use():
    """At 90 and 270 the axes swap, so fit and the cells-across readout must ask mapDims()."""
    assert "function mapDims()" in JS
    dims = JS.split("function mapDims()")[1].split("\n  }")[0]
    assert "rot() % 180" in dims, "90 and 270 swap width and height"
    gca = JS.split("function gridCellsAcross()")[1].split("\n  }")[0]
    assert "mapDims()" in gca, "cells across must describe the turned art, not the stored file"
    assert "image_w" not in gca, "reading the raw width reports the wrong axis after a turn"
    fit = JS.split("function fit()")[1].split("\n  }")[0]
    assert "mapDims()" in fit, "fit must frame the turned art"


def test_there_is_a_control_and_a_shortcut_and_it_is_exported():
    assert "VTT.rotateBy(90)" in PAGE, "the View panel needs a way to turn the map"
    assert "rotateBy: rotateBy" in JS, "the button calls VTT.rotateBy, so it must be exported"
    keys = JS.split("addEventListener('keydown'")[1]
    assert "'r' || ev.key === 'R'" in keys or "ev.key === 'r'" in keys, "R should turn the map"
