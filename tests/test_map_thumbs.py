"""Thumbnails for the map list: small, cached, and never a reason for the list to be slow.

689 maps must not mean 689 full-size images. Each thumb is generated once at request time, written
under `static/maps/thumbs/`, and served immutable from then on — the file name carries the art's own
content hash, so replacing a map's art changes the URL and a year-long cache stays honest.
"""
from __future__ import annotations

import base64
import io
import pathlib

import pytest
from PIL import Image

APP = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_thumbs():
    """Thumbs are written next to the real art, so a test must not leave one behind for a map that
    only existed inside the test."""
    d = APP / "static" / "maps" / "thumbs"
    before = set(d.glob("*.webp")) if d.is_dir() else set()
    yield
    for f in (set(d.glob("*.webp")) - before) if d.is_dir() else []:
        try:
            f.unlink()
        except OSError:
            pass


def _png(w, h, cell=25):
    im = Image.new("RGB", (w, h), "white")
    for x in range(0, w, cell):
        for y in range(h):
            im.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _make_map_with_art(client, headers, name="Thumbed", px=600):
    mid = client.post("/api/dm/map/create", json={"name": name, "grid_size": 50},
                      headers=headers).json()["id"]
    blob = _png(px, px)
    body = base64.b64encode(blob).decode()
    r = client.post(f"/api/dm/map/{mid}/image", json={"image": "data:image/png;base64," + body},
                    headers=headers)
    assert r.status_code == 200, r.text
    return mid


def test_a_thumb_is_small_webp_and_cached_forever(client, seeded_db, auth_headers):
    mid = _make_map_with_art(client, auth_headers)
    r = client.get(f"/api/dm/map/{mid}/thumb?size=96", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    assert "immutable" in r.headers.get("cache-control", "")
    with Image.open(io.BytesIO(r.content)) as im:
        assert max(im.size) <= 96, f"thumb should be <=96px, got {im.size}"
    # a 600px source must come back much smaller, or the list pays full price anyway
    assert len(r.content) < len(_png(600, 600)) / 2


def test_the_thumb_is_written_to_disk_so_the_next_request_is_a_read(client, seeded_db, auth_headers):
    mid = _make_map_with_art(client, auth_headers, name="Cached")
    first = client.get(f"/api/dm/map/{mid}/thumb?size=96", headers=auth_headers)
    assert first.status_code == 200
    thumbs = list((APP / "static" / "maps" / "thumbs").glob("*-96.webp")) if (
        APP / "static" / "maps" / "thumbs").is_dir() else []
    assert thumbs, "the first request should have written the thumb next to the art"
    second = client.get(f"/api/dm/map/{mid}/thumb?size=96", headers=auth_headers)
    assert second.status_code == 200 and second.content == first.content


def test_a_map_without_art_has_no_thumb(client, seeded_db, auth_headers):
    mid = client.post("/api/dm/map/create", json={"name": "Bare"}, headers=auth_headers).json()["id"]
    r = client.get(f"/api/dm/map/{mid}/thumb", headers=auth_headers)
    assert r.status_code == 404
    assert r.headers.get("cache-control") == "no-store", "a missing thumb must not be cached"


def test_the_size_parameter_is_bounded(client, seeded_db, auth_headers):
    mid = _make_map_with_art(client, auth_headers, name="Bounded")
    r = client.get(f"/api/dm/map/{mid}/thumb?size=99999", headers=auth_headers)
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as im:
        assert max(im.size) <= 512, "an unbounded size would let the list ask for full-size art again"


def test_the_client_loads_thumbs_lazily():
    """The list is the one screen that scales without limit: without lazy loading, opening a
    93-map group would fetch 93 images at once on a phone."""
    js = (APP / "static" / "dm_tools.js").read_text()
    assert "function mapThumb(" in js
    assert 'loading="lazy"' in js and 'decoding="async"' in js
    assert "/thumb?size=" in js
