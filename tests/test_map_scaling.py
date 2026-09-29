"""Map scaling: the grid, the image, and how big a creature's token is.

The rule these tests defend: one grid cell is one 5-ft square, the grid sits on the battle map's
own squares, and a creature occupies its 5e footprint in those squares — Medium and smaller 1,
Large 2x2, Huge 3x3, Gargantuan 4x4. Everything that can break that link is pinned here.
"""

from __future__ import annotations

import base64
import io
import json
import pathlib
import sqlite3

import pytest
from PIL import Image

REPO = pathlib.Path(__file__).resolve().parent.parent
MAPS_DIR = REPO / "static" / "maps"

from routes.maps import (MAP_MAX_PX, SIZE_CELLS, _cells_for_role, _cells_for_size, _cell_key_of,
                         _clean_token, _snap_to_cell)
from services.images import MAX_SIZE, fit_blob


def _photo_png(w, h):
    """A busy gradient: PNG keeps it large, so a downscale genuinely wins (as with real battle maps)."""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(0, w):
            px[x, y] = ((x * 7 + y * 3) % 256, (x * 5 - y * 11) % 256, (x * x + y * y) % 256)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _png(w, h, cell=25):
    im = Image.new("RGB", (w, h), (40, 44, 50))
    px = im.load()
    for x in range(0, w, cell):
        for y in range(h):
            px[x, y] = (200, 200, 200)
    for y in range(0, h, cell):
        for x in range(w):
            px[x, y] = (200, 200, 200)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    rid = cur.lastrowid
    db.commit()
    db.close()
    return rid


def _row(db_path, sql, params=()):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    out = db.execute(sql, params).fetchone()
    db.close()
    return dict(out) if out else None


@pytest.fixture
def no_stray_files():
    before = {p.name for p in MAPS_DIR.glob("*")} if MAPS_DIR.exists() else set()
    yield
    if MAPS_DIR.exists():
        for p in MAPS_DIR.glob("*"):
            if p.name not in before:
                p.unlink()


def _upload(client, headers, mid, blob):
    return client.post(f"/api/dm/map/{mid}/image",
                       json={"image": "data:image/png;base64," + base64.b64encode(blob).decode()},
                       headers=headers)


# ── the image pipeline must not silently rescale the art ──────────────────────────────

def test_fit_blob_leaves_a_map_that_already_fits_alone():
    """No resize, no re-encode: an unchanged file cannot break the alignment."""
    blob = _png(1200, 900)
    got = fit_blob(blob, MAP_MAX_PX)
    assert got is not None
    out, media, w, h, src_w, src_h = got
    assert out == blob, "the bytes were re-encoded for no reason"
    assert (w, h) == (1200, 900) == (src_w, src_h)
    assert "webp" not in media


def test_fit_blob_uses_the_map_cap_not_the_portrait_cap():
    """MAP_MAX_PX is 4096; thumbnail_bytes would have clamped the request to MAX_SIZE (1024)."""
    assert MAP_MAX_PX > MAX_SIZE, "the map cap must exceed the portrait cap to be worth having"
    out, media, w, h, src_w, src_h = fit_blob(_png(2400, 1800), MAP_MAX_PX)
    assert (src_w, src_h) == (2400, 1800)
    assert max(w, h) <= MAP_MAX_PX
    assert (w, h) == (2400, 1800), "an image inside the cap must not be touched"


def test_an_oversized_blob_comes_back_either_untouched_or_exactly_fitted():
    """Two legitimate outcomes, and the silent-portrait-cap one is neither of them."""
    out, media, w, h, src_w, src_h = fit_blob(_photo_png(6000, 3000), MAP_MAX_PX)
    assert (src_w, src_h) == (6000, 3000), "the source dimensions must always be reported"
    if (w, h) == (src_w, src_h):
        # the re-encode came out bigger than the original, so the original is kept — which is
        # fine for a map (no loss, no size win to chase) and needs no grid rescaling
        assert out == _photo_png(6000, 3000)
        return
    assert (w, h) == (4096, 2048), "an image that IS shrunk must be shrunk to the cap exactly"
    assert abs(w / src_w - h / src_h) < 0.001, "aspect ratio must survive"


def test_fit_blob_resizes_when_the_smaller_file_is_worth_it():
    """A real battle map is a big photo: the fitted WebP is far smaller, so it is used.

    Driven through a JPEG at a small cap so the branch is exercised without an 18-megapixel loop.
    """
    w0, h0 = 1600, 1000
    im = Image.effect_noise((w0, h0), 48).convert("RGB")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    blob = b.getvalue()
    out, media, w, h, src_w, src_h = fit_blob(blob, 1024)
    assert (src_w, src_h) == (w0, h0), "the source dimensions are reported either way"
    assert abs(max(w, h) - 1024) <= 1, f"brought down to the cap, got {w}x{h}"
    assert abs(w / h - w0 / h0) < 0.01, "aspect ratio must survive"
    assert len(out) < len(blob), f"and it should be the smaller file ({len(out)} vs {len(blob)})"


def test_fit_blob_never_upscales_and_survives_junk():
    out, media, w, h, src_w, src_h = fit_blob(_png(300, 200), MAP_MAX_PX)
    assert (w, h) == (300, 200)
    assert fit_blob(b"not an image", MAP_MAX_PX) is None


# ── uploading keeps the grid on the art ───────────────────────────────────────────────

def test_a_map_inside_the_cap_keeps_its_pixels(client, seeded_db, auth_headers, no_stray_files):
    mid = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name, grid_size) VALUES (?,?,?)",
                  (1, "Big map", 50))
    r = _upload(client, auth_headers, mid, _png(2000, 1500, cell=50))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["image_w"], body["image_h"]) == (2000, 1500)
    assert body["grid_scaled"] is False and body["grid_size"] == 50, "no resize, no rescale"
    stored = Image.open(REPO / body["image_path"].lstrip("/")).size
    assert stored == (2000, 1500), "a 2000px battle map must not become 1024px"
    assert 2000 / body["grid_size"] == 40, "40 five-foot squares across, as the DM drew it"


def test_an_oversized_map_scales_the_grid_with_the_art(client, seeded_db, auth_headers, no_stray_files):
    """The regression: shrinking the art while leaving grid_size alone covered 2x the squares."""
    mid = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name, grid_size) VALUES (?,?,?)",
                  (1, "Huge map", 100))
    r = _upload(client, auth_headers, mid, _png(6000, 3000, cell=100))
    body = r.json()
    assert r.status_code == 200, r.text
    assert (body["image_w"], body["image_h"]) == (4096, 2048)
    assert body["grid_scaled"] is True
    # 6000px at 100px/cell = 60 squares across; after the fit to 4096 that must still be 60
    assert abs(4096 / body["grid_size"] - 60) < 0.5, (
        f"grid_size {body['grid_size']} no longer describes {4096}px as 60 squares")
    row = _row(seeded_db["db_path"], "SELECT grid_size, image_w, image_h FROM dm_maps WHERE id=?", (mid,))
    assert (row["image_w"], row["image_h"]) == (4096, 2048)
    assert row["grid_size"] == body["grid_size"], "the response and the row must agree"


def test_the_offsets_scale_with_the_art_too(client, seeded_db, auth_headers, no_stray_files):
    mid = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_maps (user_id, name, grid_size, grid_offset_x, grid_offset_y) "
                  "VALUES (?,?,?,?,?)", (1, "Offset map", 100, 40, 20))
    body = _upload(client, auth_headers, mid, _png(6000, 3000, cell=100)).json()
    assert body["grid_offset_x"] < 40 and body["grid_offset_y"] < 20, (
        "an unaligned grid stays unaligned after the art shrinks")


def test_grid_size_and_offsets_can_be_set_from_the_ui(client, seeded_db, auth_headers):
    mid = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                  (1, "Align me"))
    r = client.post(f"/api/dm/map/{mid}/update",
                    json={"grid_size": 37, "grid_offset_x": -12, "grid_offset_y": 8,
                          "feet_per_cell": 5}, headers=auth_headers)
    assert r.status_code == 200, r.text
    row = _row(seeded_db["db_path"],
               "SELECT grid_size, grid_offset_x, grid_offset_y FROM dm_maps WHERE id=?", (mid,))
    assert row == {"grid_size": 37, "grid_offset_x": -12, "grid_offset_y": 8}
    # and the clamps hold
    client.post(f"/api/dm/map/{mid}/update", json={"grid_size": 5000, "grid_offset_x": 9999},
                headers=auth_headers)
    row = _row(seeded_db["db_path"], "SELECT grid_size, grid_offset_x FROM dm_maps WHERE id=?", (mid,))
    assert row["grid_size"] == 400 and row["grid_offset_x"] == 400


# ── one 5-ft square: the grid maths must agree with itself ────────────────────────────

@pytest.mark.parametrize("grid_type,size,ox,oy", [
    ("square", 50, 0, 0), ("square", 50, 17, -23), ("square", 37, -40, 40),
    ("square", 40, 0, 0), ("square", 50, 50, 50),
    ("hex", 50, 0, 0), ("hex", 50, 17, -23), ("hex", 30, 25, 25),
])
def test_a_snapped_token_sits_in_the_cell_it_reports(grid_type, size, ox, oy):
    """The 5-ft-square invariant: snap to a square, and the square you are in is that square.

    The grid is walked on MULTIPLES of the cell as well as odd steps: a point exactly on a cell
    boundary is where Python's banker's rounding (`round(0.5) == 0`) and JS's half-up
    (`Math.round(0.5) == 1`) disagree, which put the server's snap in the cell before the one the
    canvas named.
    """
    row = {"grid_type": grid_type, "grid_size": size, "grid_offset_x": ox, "grid_offset_y": oy}
    steps = list(range(-100, 600, 37))
    steps += [n * size + o for n in range(-2, 13) for o in (0, size // 2)]
    for x in steps:
        for y in steps:
            sx, sy = _snap_to_cell(x, y, row)
            assert _cell_key_of(x, y, row) == _cell_key_of(sx, sy, row), (
                f"{x},{y} snapped to {sx},{sy} which is a different {grid_type} cell")
            # and it is a real centre: snapping again must not move it
            assert _snap_to_cell(sx, sy, row) == (sx, sy)


def test_the_offset_moves_a_hex_grid_too():
    """It used to be ignored on hex maps, so a hex battle map could not be aligned at all."""
    base = {"grid_type": "hex", "grid_size": 50, "grid_offset_x": 0, "grid_offset_y": 0}
    moved = dict(base, grid_offset_x=25, grid_offset_y=13)
    assert _cell_key_of(120, 90, base) != _cell_key_of(120, 90, moved)
    assert _snap_to_cell(120, 90, moved) != _snap_to_cell(120, 90, base)
    sx, sy = _snap_to_cell(120, 90, moved)
    assert abs(sx - (_snap_to_cell(120 - 25, 90 - 13, base)[0] + 25)) < 0.001, (
        "an offset hex grid is the un-offset one shifted: the maths has to say so")


def test_a_square_cell_is_a_cell_wide_and_a_5ft_square_is_one_cell():
    row = {"grid_type": "square", "grid_size": 40, "grid_offset_x": 0, "grid_offset_y": 0}
    assert _snap_to_cell(0, 0, row) == (20, 20)
    assert _snap_to_cell(39, 39, row) == (20, 20)
    assert _snap_to_cell(41, 41, row) == (60, 60)
    assert _cell_key_of(0, 0, row) == "0,0" and _cell_key_of(41, 41, row) == "1,1"


# ── creature footprints, per 5e ───────────────────────────────────────────────────────

def test_the_size_table_is_the_5e_one():
    assert SIZE_CELLS == {"tiny": 1, "small": 1, "medium": 1, "large": 2, "huge": 3,
                          "gargantuan": 4}


@pytest.mark.parametrize("size,cells", [
    ("Tiny", 1), ("Small", 1), ("Medium", 1), ("Large", 2), ("Huge", 3), ("Gargantuan", 4),
    ("large monstrosity", 2), ("Huge dragon", 3), ("  medium humanoid", 1), ("grg", 4), ("LG", 2),
    ("questgiver", 1), ("", 1), (None, 1), ("whatever", 1),
])
def test_a_creature_size_becomes_a_footprint(size, cells):
    assert _cells_for_size(size) == cells


def test_the_role_line_of_a_stat_block_sizes_a_spawned_creature():
    assert _cells_for_role("Large monstrosity") == 2
    assert _cells_for_role("Gargantuan dragon") == 4
    assert _cells_for_role("") == 1


def test_a_token_placed_by_hand_gets_its_creatures_footprint():
    """A dragon added from the palette must not arrive as one 5-ft square."""
    huge = _clean_token({"kind": "creature", "ref_name": "Adult Blue Dragon", "size": "Huge"}, 1)
    assert (huge["w"], huge["h"]) == (3, 3)
    large = _clean_token({"kind": "creature", "ref_name": "Owlbear", "size": "Large"}, 1)
    assert (large["w"], large["h"]) == (2, 2)
    medium = _clean_token({"kind": "creature", "ref_name": "Goblin", "size": "Medium"}, 1)
    assert (medium["w"], medium["h"]) == (1, 1)
    tarrasque = _clean_token({"kind": "creature", "ref_name": "Tarrasque", "size": "Gargantuan"}, 1)
    assert (tarrasque["w"], tarrasque["h"]) == (4, 4)


def test_an_explicit_size_still_wins():
    """The DM may want a 2x2 "large" guard, or a 1x1 huge illusion."""
    t = _clean_token({"kind": "creature", "ref_name": "Goblin", "size": "Medium", "w": 2, "h": 3}, 1)
    assert (t["w"], t["h"]) == (2, 3)
    t = _clean_token({"kind": "creature", "cells": 2}, 1)
    assert (t["w"], t["h"]) == (2, 2)
    t = _clean_token({"kind": "character", "character_id": 6}, 1)
    assert (t["w"], t["h"]) == (1, 1), "a PC is Medium: one square"
    # a nonsense footprint is clamped, not stored
    t = _clean_token({"kind": "creature", "w": 999, "h": 0}, 1)
    assert (t["w"], t["h"]) == (12, 1)


def test_a_spawned_creature_on_the_map_is_as_big_as_its_size(client, seeded_db, auth_headers):
    mid = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name, grid_size) VALUES (?,?,?)",
                  (1, "Arena", 50))
    for name, size, cells in (("Adult Blue Dragon", "Huge", 3), ("Owlbear", "Large", 2),
                              ("Goblin", "Medium", 1)):
        r = client.post(f"/api/dm/map/{mid}/token/add",
                        json={"kind": "creature", "ref_name": name, "label": name, "size": size},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        tok = r.json()["token"]
        assert (tok["w"], tok["h"]) == (cells, cells), f"{name} ({size}) is {cells}x{cells} squares"
        # and it sits in a cell, not across a corner
        row = _row(seeded_db["db_path"], "SELECT * FROM dm_maps WHERE id=?", (mid,))
        sx, sy = _snap_to_cell(tok["x"], tok["y"], row)
        assert (tok["x"], tok["y"]) == (sx, sy)


# ── spawning lands where the DM is looking, on the map's own grid ─────────────────────

def test_spawning_uses_the_viewport_the_browser_reported():
    """The anchor is a guess without it, so tokens could arrive off the visible map."""
    row = {"grid_type": "square", "grid_size": 50, "grid_offset_x": 0, "grid_offset_y": 0,
           "camera": json.dumps({"x": -100, "y": -50, "zoom": 1})}
    from routes.maps import _spawn_layout
    small = _spawn_layout(1, row, [600, 400])[0]
    big = _spawn_layout(1, row, [1600, 1000])[0]
    assert big[0] > small[0] and big[1] > small[1], "a wider window anchors further into the map"
    assert small == _snap_to_cell(small[0], small[1], row)
    assert _spawn_layout(1, row, None) == _spawn_layout(1, row, [800, 600]), "sane default"


# ── the page and the asset stay honest ────────────────────────────────────────────────

def test_the_toolbar_lets_the_dm_align_the_grid():
    page = (REPO / "templates" / "map.html").read_text()
    toolbar = (REPO / "templates" / "_map_toolbar.html").read_text()
    assert '{% include "_map_toolbar.html" %}' in page, "the toolbar partial is not included"
    for control in ('id="vttGridSize"', 'id="vttGridSquares"', "VTT.nudgeGrid", "VTT.fitGridToImage",
                    'id="vttGridInfo"', "VTT.setSquaresAcross"):
        assert control in toolbar, f"the grid cannot be aligned to the art: {control} is missing"
    assert "5 ft" in toolbar or "five" in toolbar or "5-ft" in toolbar, (
        "nothing tells the DM what a cell represents")


def test_token_art_is_requested_at_the_size_it_is_drawn():
    raw = (REPO / "static" / "vtt.js").read_text()
    # drop comment lines first: the explanation mentions the old flat size
    src = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    assert "function artSizeFor" in src
    assert "?size=256" not in src, "a flat 256 is soft on a big grid or a 2x screen"
    assert "artSizeFor(t)" in src
    art = src.split("function artSizeFor")[1].split("function ")[0]
    assert "devicePixelRatio" in art, "device pixels are what a retina/TV screen draws"
    assert "Math.min(1024" in art and "Math.max(128" in art, "clamp to what the routes serve"


def test_a_big_creature_spans_more_than_one_hex():
    src = (REPO / "static" / "vtt.js").read_text()
    extent = src.split("function tokenExtent")[1].split("function ")[0]
    hex_branch = extent.split("'hex'")[1]
    assert "Math.sqrt(3)" in hex_branch and "(n - 1)" in hex_branch, (
        "on a hex grid every token drew as one hex, so Huge looked Medium")
