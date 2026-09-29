"""The player view (second screen): what it may see, and how it authenticates.

The second screen is usually a TV or a tablet that is not signed in, so it uses the map's
player key — which means the KEY must only ever expose a projection. These tests pin that
projection: no DM drawing, no hidden token, nothing the fog still covers, and never the key
itself or another user's map.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
VTT = REPO / "static" / "vtt.js"
TEMPLATE = REPO / "templates" / "map_player.html"


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


@pytest.fixture
def map_with_tokens(client, seeded_db, auth_headers):
    """A map with one visible token, one hidden token, and a stroke on the drawing layer."""
    mid = client.post("/api/dm/map/create", json={"name": "Second screen", "grid_size": 50},
                      headers=auth_headers).json()["id"]
    visible = client.post(f"/api/dm/map/{mid}/token/add",
                          json={"kind": "creature", "ref_name": "Goblin", "label": "Goblin",
                                "x": 25, "y": 25, "hp_max": 7, "hp_current": 7},
                          headers=auth_headers).json()["token"]
    hidden = client.post(f"/api/dm/map/{mid}/token/add",
                         json={"kind": "creature", "ref_name": "Owlbear", "label": "Owlbear",
                               "x": 75, "y": 25, "hp_max": 59, "hidden": 1},
                         headers=auth_headers).json()["token"]
    client.post(f"/api/dm/map/{mid}/layer", json={
        "fog": ["0,0"], "fog_on": 1,
        "draw": [{"color": "#ff0000", "width": 4, "points": [[0, 0], [50, 50]]}]},
        headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/update", json={"camera": {"x": 10, "y": 20, "zoom": 1.5}},
                headers=auth_headers)
    return mid, visible, hidden


def _key(client, headers, mid):
    r = client.get(f"/api/dm/map/{mid}/player-key", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── the key ────────────────────────────────────────────────────────────────────────────

def test_a_key_is_created_once_and_can_be_rotated_or_revoked(client, seeded_db, auth_headers):
    mid = client.post("/api/dm/map/create", json={"name": "Keys"}, headers=auth_headers).json()["id"]
    first = _key(client, auth_headers, mid)
    assert len(first["player_key"]) >= 16 and first["url"].endswith("?k=" + first["player_key"])
    assert _key(client, auth_headers, mid)["player_key"] == first["player_key"], "not re-rolled"

    rotated = client.post(f"/api/dm/map/{mid}/player-key", json={"key": "abc123"},
                          headers=auth_headers).json()
    assert rotated["player_key"] == "abc123"
    revoked = client.post(f"/api/dm/map/{mid}/player-key", json={"key": ""},
                          headers=auth_headers).json()
    assert revoked["player_key"] == "" and revoked["url"] == ""


def test_only_the_owner_gets_the_key(client, seeded_db, auth_headers):
    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                     (2, "Theirs"))
    assert client.get(f"/api/dm/map/{theirs}/player-key", headers=auth_headers).status_code == 404
    assert client.post(f"/api/dm/map/{theirs}/player-key", json={"key": "x"},
                       headers=auth_headers).status_code == 404


# ── the projection ─────────────────────────────────────────────────────────────────────

def test_the_player_state_withholds_what_is_not_the_partys(client, seeded_db, auth_headers, map_with_tokens):
    mid, visible, hidden = map_with_tokens
    key = _key(client, auth_headers, mid)["player_key"]
    r = client.get(f"/api/dm/map/{mid}/state?k={key}")
    assert r.status_code == 200, r.text
    body = r.json()

    ids = {t["id"] for t in body["tokens"]}
    assert visible["id"] in ids, "a visible token on a revealed cell must be shown"
    assert hidden["id"] not in ids, "a hidden token must never reach the second screen"
    assert "draw" not in body and "draw_data" not in body["map"], (
        "the DM's drawing is not the party's business")
    assert body["camera"], "the second screen follows the DM's camera"
    assert "player_key" not in body["map"] and key not in r.text, (
        "the key must not be echoed back in the payload")


def test_the_fog_decides_what_the_party_can_see(client, seeded_db, auth_headers):
    """A token the party has not met is not on their screen, revealed cell or not."""
    mid = client.post("/api/dm/map/create", json={"name": "Fog", "grid_size": 50},
                      headers=auth_headers).json()["id"]
    in_dark = client.post(f"/api/dm/map/{mid}/token/add",
                          json={"kind": "creature", "ref_name": "Owlbear", "x": 125, "y": 25},
                          headers=auth_headers).json()["token"]
    in_light = client.post(f"/api/dm/map/{mid}/token/add",
                           json={"kind": "creature", "ref_name": "Goblin", "x": 25, "y": 25},
                           headers=auth_headers).json()["token"]
    key = _key(client, auth_headers, mid)["player_key"]

    # no fog at all: everything is visible
    ids = {t["id"] for t in client.get(f"/api/dm/map/{mid}/state?k={key}").json()["tokens"]}
    assert ids == {in_dark["id"], in_light["id"]}

    # reveal only the first cell (0,0 = the goblin)
    client.post(f"/api/dm/map/{mid}/layer", json={"fog": ["0,0"], "fog_on": 1}, headers=auth_headers)
    ids = {t["id"] for t in client.get(f"/api/dm/map/{mid}/state?k={key}").json()["tokens"]}
    assert ids == {in_light["id"]}, "the token in the dark cell must be withheld"

    # revealing its cell brings it back
    client.post(f"/api/dm/map/{mid}/layer", json={"fog": ["0,0", "2,0"]}, headers=auth_headers)
    ids = {t["id"] for t in client.get(f"/api/dm/map/{mid}/state?k={key}").json()["tokens"]}
    assert ids == {in_dark["id"], in_light["id"]}


def test_the_state_needs_a_key_or_the_owner_session(client, seeded_db, auth_headers, map_with_tokens):
    mid, _, _ = map_with_tokens
    anon = client.get(f"/api/dm/map/{mid}/state")
    assert anon.status_code == 403, "anonymous callers must not read the map state"
    wrong = client.get(f"/api/dm/map/{mid}/state?k=not-the-key")
    assert wrong.status_code == 403 or wrong.status_code == 404
    assert client.get(f"/api/dm/map/{mid}/state", headers=auth_headers).status_code == 200, (
        "the owner's own session still works (the DM previewing their screen)")

    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                     (2, "Theirs"))
    assert client.get(f"/api/dm/map/{theirs}/state", headers=auth_headers).status_code == 404


# ── the page ───────────────────────────────────────────────────────────────────────────

def test_the_player_page_opens_with_a_key_and_no_login(client, seeded_db, auth_headers, map_with_tokens):
    mid, _, _ = map_with_tokens
    key = _key(client, auth_headers, mid)["player_key"]
    r = client.get(f"/dm-map/{mid}/player?k={key}")          # no session cookie at all
    assert r.status_code == 200, r.text
    assert "vttCanvas" in r.text and "vtt.js" in r.text
    assert "PLAYER_CFG" in r.text and key in r.text, "the page needs the key to poll with"


def test_the_player_page_is_closed_without_a_key(client, seeded_db, auth_headers, map_with_tokens):
    mid, _, _ = map_with_tokens
    anon = client.get(f"/dm-map/{mid}/player", follow_redirects=False)
    assert anon.status_code in (303, 401, 403), (
        "an anonymous visitor without the key must be sent to login")
    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                     (2, "Theirs"))
    assert client.get(f"/dm-map/{theirs}/player", headers=auth_headers,
                      follow_redirects=False).status_code == 404
    key = _key(client, auth_headers, mid)["player_key"]
    client.post(f"/api/dm/map/{mid}/player-key", json={"key": ""}, headers=auth_headers)
    revoked = client.get(f"/dm-map/{mid}/player?k={key}", follow_redirects=False)
    assert revoked.status_code in (303, 401, 403), "a revoked key must stop working"


# ── the renderer ───────────────────────────────────────────────────────────────────────

def test_the_renderer_has_a_read_only_player_mode():
    src = VTT.read_text()
    assert "readOnly" in src and "function initPlayer" in src
    assert "playerApply" in src and "playerFetch" in src
    # the party's screen gets no drawing and hides (not dims) what the fog covers
    apply_body = src.split("function playerApply")[1].split("\n  }")[0]
    assert "state.strokes = []" in apply_body
    token_body = src.split("function drawToken")[1].split("\n  }")[0]
    assert "state.readOnly && unrevealed" in token_body, (
        "on the player screen an unseen token must not be drawn at all")
    fog_body = src.split("function drawFog")[1].split("function ")[0]
    assert "state.readOnly ?" in fog_body, "the party's fog is solid, the DM's is not"


def test_the_second_screen_is_poked_on_every_change_and_polls_as_a_fallback():
    src = VTT.read_text()
    assert "BroadcastChannel" in src, "a second window on this machine should react instantly"
    assert "pokePlayers" in src
    for fn in ("markDirty", "markLayerDirty", "saveCamera"):
        body = src.split(f"function {fn}")[1].split("\n  }")[0]
        assert "pokePlayers()" in body, f"{fn} must tell the second screen something changed"
    init = src.split("function initPlayer")[1]
    assert "setInterval" in init[:3000], "a TV with no shared browser context needs the poll"
    assert "cache: 'no-store'" in src, "a cached state response would freeze the second screen"


def test_the_dm_path_stands_down_on_the_player_page():
    """One asset, two surfaces: without this guard the DM init runs on the party's screen,
    fetches without the key (403), clobbers the projection with an empty map and attaches the
    DM's tool shortcuts to a screen the players can touch."""
    src = VTT.read_text()
    init_head = src.split("function init()")[1][:400]
    assert "window.PLAYER_CFG" in init_head and "return" in init_head
    # and the page really does set that flag before loading the asset
    page = TEMPLATE.read_text()
    assert page.index("PLAYER_CFG") < page.index("vtt.js"), (
        "PLAYER_CFG must be set before the asset runs its own init")


def test_the_player_controls_are_reachable():
    """A control the page can call is not the same as one that exists: the Follow button threw
    `VTT.player.toggleFollow is not a function` until this wiring was added."""
    src = VTT.read_text()
    page = TEMPLATE.read_text()
    assert "player.toggleFollow = toggleFollow" in src, "the Follow button would throw on click"
    for call in re.findall(r"VTT\.player\.(\w+)\(", page):
        assert f"player.{call} = " in src, f"VTT.player.{call} is called by the page but never set"


def test_the_player_page_is_chromeless():
    page = TEMPLATE.read_text()
    assert "extends" not in page, "the player view must not inherit the app chrome"
    assert "static_asset_version('vtt.js')" in page
    assert "requestFullscreen" in page, "a TV view needs a full-screen button"
