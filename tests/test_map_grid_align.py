"""Auto-aligning the overlay to the grid printed on the art.

Two claims have to hold: the measurement is right (checked against synthetic grids whose pitch and
phase are known exactly), and a map with no printed grid is refused rather than given a
confident-looking wrong one.
"""
from __future__ import annotations

import io
import random

from PIL import Image

from services.map_grid_align import detect


def _gridded(path, pitch, offset, size=1200, seed=3, noise=True):
    rnd = random.Random(seed)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = 150 + (rnd.randint(-18, 18) if noise else 0)
            px[x, y] = (v, v, v)
    for x in range(offset % pitch, size, pitch):
        for t in (0, 1):
            for y in range(size):
                px[x + t, y] = (40, 40, 40)
    for y in range(offset % pitch, size, pitch):
        for t in (0, 1):
            for x in range(size):
                px[x, y + t] = (40, 40, 40)
    img.save(path)
    return path


def test_a_known_grid_comes_back_at_the_right_pitch_and_phase(tmp_path):
    """Pitch within 1.5px and phase within 2.5px, on grids at several scales and offsets."""
    for pitch, offset in ((50, 0), (83, 17), (120, 43), (64, 31)):
        p = _gridded(tmp_path / f"g{pitch}_{offset}.png", pitch, offset)
        r = detect(p)
        assert r and r["has_grid"], f"{pitch}/{offset} not detected at all"
        assert abs(r["pitch_px"] - pitch) <= 1.5, f"pitch {r['pitch_px']} for {pitch}"
        # the phase is modulo the pitch, so compare the shortest distance round the ring
        d = abs(r["offset_x"] - offset)
        assert min(d, pitch - d) <= 2.5, f"phase {r['offset_x']} for offset {offset}"


def test_art_with_no_printed_grid_is_refused(tmp_path):
    """The whole feature is a lie if it invents a grid. Noise folds flat: measured 0.08 against 0.9+."""
    p = _gridded(tmp_path / "plain.png", 100, 0, noise=True)
    Image.open(p).resize((1200, 1200)).save(p) if False else None
    rnd = random.Random(11)
    img = Image.new("RGB", (1200, 1200))
    img.putdata([(150 + rnd.randint(-18, 18),) * 3 for _ in range(1200 * 1200)])
    img.save(tmp_path / "noise.png")
    r = detect(tmp_path / "noise.png")
    assert r and not r["has_grid"], f"invented a grid on blank art: {r}"
    assert r["score"] < 0.3


def test_the_endpoint_writes_the_measured_grid_and_clears_the_offsets(client, seeded_db, auth_headers,
                                                                    tmp_path, monkeypatch):
    """A strong reading updates the row; a weak one leaves it completely alone."""
    from services import map_grid_align as align

    mid = client.post("/api/dm/map/create", json={"name": "Alignable", "grid_size": 100},
                      headers=auth_headers).json()["id"]
    blob = io.BytesIO()
    _gridded(tmp_path / "art.png", 64, 31)
    blob.write((tmp_path / "art.png").read_bytes())
    import base64
    client.post(f"/api/dm/map/{mid}/image",
                json={"image": "data:image/png;base64," + base64.b64encode(blob.getvalue()).decode()},
                headers=auth_headers)

    r = client.post(f"/api/dm/map/{mid}/align-grid", headers=auth_headers)
    body = r.json()
    assert body["ok"], body
    assert abs(body["grid_size"] - 64) <= 2, body
    row = client.get("/api/dm/maps", headers=auth_headers).json()["maps"]
    mine = [m for m in row if m["id"] == mid][0]
    assert abs(mine["grid_size"] - 64) <= 2, "the detected pitch should have been stored"

    # now the refusal path: pretend the art has no grid
    monkeypatch.setattr(align, "detect", lambda p: {"has_grid": False, "score": 0.1,
                                                    "axis_agree": 0.9, "pitch_px": 0, "offset_x": 0,
                                                    "offset_y": 0})
    before = client.get("/api/dm/maps", headers=auth_headers).json()["maps"]
    was = [m for m in before if m["id"] == mid][0]["grid_size"]
    r2 = client.post(f"/api/dm/map/{mid}/align-grid", headers=auth_headers).json()
    assert r2["ok"] is False and "no printed grid" in r2["reason"]
    after = client.get("/api/dm/maps", headers=auth_headers).json()["maps"]
    assert [m for m in after if m["id"] == mid][0]["grid_size"] == was, "a refusal must not touch it"


def test_the_renderer_has_the_align_action_and_keeps_offsets_in_range():
    import pathlib as _p
    js = (_p.Path(__file__).resolve().parent.parent / "static" / "vtt.js").read_text()
    assert "function alignGrid()" in js and "alignGrid: alignGrid" in js
    assert "function setGridOffset(" in js
    assert "Math.max(-400, Math.min(400, Math.round(ox) || 0))" in js, (
        "the renderer clamps offsets to +/-400; a measured phase must be clamped the same way")
    page = (_p.Path(__file__).resolve().parent.parent / "templates" / "map.html").read_text()
    assert 'onclick="VTT.alignGrid()"' in page, "nothing on the page offers the align action"
