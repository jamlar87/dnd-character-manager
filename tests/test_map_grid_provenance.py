"""Whose grid is it?

The DM can nudge the overlay until it sits on the printed lines, and that has to stick. It does — the
canvas saves on every nudge — but nothing used to record that a PERSON decided it, so the automatic
paths could not tell a correction from a default and would re-measure over it. These pin the
distinction: an edit claims the map, an autosave of what was just loaded does not, and once a map is
claimed the automatic paths leave it alone.
"""
from __future__ import annotations

import base64
import io
import random

from PIL import Image


def _map(client, headers, **kw):
    body = {"name": "Grid provenance"}
    body.update(kw)
    return client.post("/api/dm/map/create", json=body, headers=headers).json()["id"]


def _gridded_png(pitch=64, offset=31, size=900, seed=4):
    rnd = random.Random(seed)
    img = Image.new("RGB", (size, size))
    img.putdata([(150 + rnd.randint(-16, 16),) * 3 for _ in range(size * size)])
    px = img.load()
    for x in range(offset % pitch, size, pitch):
        for t in (0, 1):
            for y in range(size):
                px[x + t, y] = (30, 30, 30)
    for y in range(offset % pitch, size, pitch):
        for t in (0, 1):
            for x in range(size):
                px[x, y + t] = (30, 30, 30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _grid_source(client, headers, mid):
    return client.get(f"/api/dm/map/{mid}", headers=headers).json()["map"]["grid_source"]


def test_a_hand_placed_grid_is_claimed_by_the_dm(client, seeded_db, auth_headers):
    """A nudge that changes the value is a decision and must be recorded as one."""
    mid = _map(client, auth_headers)
    assert _grid_source(client, auth_headers, mid) == ""
    client.post(f"/api/dm/map/{mid}/update", json={"grid_offset_x": 37, "grid_offset_y": 12},
                headers=auth_headers)
    assert _grid_source(client, auth_headers, mid) == "user"
    row = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    assert row["grid_offset_x"] == 37 and row["grid_offset_y"] == 12


def test_the_canvas_autosave_does_not_claim_the_map(client, seeded_db, auth_headers):
    """The canvas posts what it just loaded. That is not an edit, and claiming the map on it would
    freeze every map against the automatic pass the moment somebody opened it."""
    mid = _map(client, auth_headers)
    row = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    client.post(f"/api/dm/map/{mid}/update",
                json={"grid_type": row["grid_type"], "grid_size": row["grid_size"],
                      "grid_offset_x": row["grid_offset_x"], "grid_offset_y": row["grid_offset_y"]},
                headers=auth_headers)
    assert _grid_source(client, auth_headers, mid) == ""


def test_a_hand_placed_grid_survives_new_art(client, seeded_db, auth_headers):
    """Reported concern: does a manual fix get overwritten? It must not — including the case that
    first exposed the gap, a correction that lands exactly on 0,0 and so looks 'never placed'."""
    mid = _map(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/update", json={"grid_offset_x": 0, "grid_offset_y": 0,
                                                  "grid_size": 64}, headers=auth_headers)
    row = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    client.post(f"/api/dm/map/{mid}/update", json={"grid_offset_x": 1, "grid_offset_y": 1},
                headers=auth_headers)          # a real change, so the map is the DM's
    client.post(f"/api/dm/map/{mid}/update", json={"grid_offset_x": 0, "grid_offset_y": 0},
                headers=auth_headers)          # back to 0,0 — still the DM's choice
    body = client.post(f"/api/dm/map/{mid}/image", json={"image": _gridded_png()},
                       headers=auth_headers).json()
    assert body.get("auto_aligned") in (False, None), "the automatic pass overrode a hand-placed grid"
    row = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    assert (row["grid_offset_x"], row["grid_offset_y"]) == (0, 0), row
    assert row["grid_size"] == 64, "the DM's cell size was changed by the automatic pass"
    assert row["grid_source"] == "user"


def test_an_untouched_map_is_still_aligned_automatically(client, seeded_db, auth_headers, monkeypatch):
    """The other half: a map nobody has touched gets the default treatment, so its grid lands on the
    art without anyone pressing anything.

    The measurement is stubbed here on purpose — this is about the plumbing (does upload align, and
    without claiming the map as the DM's). Whether the measurement itself is right is settled by the
    known-answer tests in test_map_grid_align, against synthetic grids with known pitch and phase.
    """
    from services import map_grid_align as align
    monkeypatch.setattr(align, "detect", lambda p, hint_pitch=None: {
        "has_grid": True, "score": 2.5, "axis_agree": 1.0, "pitch_px": 64, "offset_x": 31,
        "offset_y": 20, "z_x": 4.0, "z_y": 3.0})
    mid = _map(client, auth_headers)
    body = client.post(f"/api/dm/map/{mid}/image", json={"image": _gridded_png(pitch=64, offset=31)},
                       headers=auth_headers).json()
    assert body["ok"] and body["auto_aligned"] is True, body
    row = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["map"]
    size = row["grid_size"]
    assert (row["grid_offset_x"], row["grid_offset_y"]) == (31 % size, 20 % size), row
    assert row["grid_source"] == "", "an automatic alignment must not claim the map as the DM's"
    # and the map's own cell size is left alone: only the DM or an explicit measurement sets that
    assert size == 50, f"the default size should survive an automatic alignment: {size}"


def test_asking_for_a_measurement_claims_the_map(client, seeded_db, auth_headers, monkeypatch):
    """Pressing the button is a decision too, so it protects the result from later automatic passes."""
    from services import map_grid_align as align

    monkeypatch.setattr(align, "detect", lambda p, hint_pitch=None: {
        "has_grid": True, "score": 3.0, "axis_agree": 1.0, "pitch_px": 64, "offset_x": 31,
        "offset_y": 20, "z_x": 6.0, "z_y": 6.0})
    mid = _map(client, auth_headers, grid_size=64)
    client.post(f"/api/dm/map/{mid}/image", json={"image": _gridded_png()}, headers=auth_headers)
    body = client.post(f"/api/dm/map/{mid}/align-grid", headers=auth_headers).json()
    assert body["ok"], body
    assert _grid_source(client, auth_headers, mid) == "user"
