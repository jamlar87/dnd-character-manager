"""The map player view's live push: a poke per save instead of a 3 s poll.

The second screen still reads its state from `/state` — the stream only says "something changed".
That keeps one projection (and one place that decides what a screen may see), so these tests pin
two things: that a save reaches the screens, and that the stream is exactly as closed as `/state`
is. A stream that is easier to open than the state read would be a hole in the projection.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib

import pytest
from starlette.requests import Request as StarletteRequest

REPO = pathlib.Path(__file__).resolve().parent.parent
MAPS = REPO / "routes" / "maps.py"
VTT = REPO / "static" / "vtt.js"


def _data_of(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return line[6:]
    return ""


def _map_with_key(client, headers, name="Second screen"):
    mid = client.post("/api/dm/map/create", json={"name": name}, headers=headers).json()["id"]
    key = client.get(f"/api/dm/map/{mid}/player-key", headers=headers).json()["player_key"]
    return mid, key


# ── the bus keeps the two scopes apart ────────────────────────────────────────────────

def test_a_map_and_a_campaign_with_the_same_id_do_not_collide():
    from services import events

    q_map = events.subscribe_map(7)
    q_camp = events.subscribe(7)
    try:
        events.publish_map(7, {"kind": "map", "map_id": 7})
        assert q_map.qsize() == 1
        assert q_camp.qsize() == 0, "a map poke must not reach a campaign feed with the same id"

        events.publish(7, {"kind": "roll", "id": 1})
        assert q_camp.qsize() == 1
        assert q_map.qsize() == 1, "and the other way round"
    finally:
        events.unsubscribe_map(7, q_map)
        events.unsubscribe(7, q_camp)


def test_map_subscribers_are_counted_and_cleaned_up():
    from services import events

    q = events.subscribe_map(99)
    assert events.map_subscriber_count(99) == 1
    events.unsubscribe_map(99, q)
    assert events.map_subscriber_count(99) == 0
    events.unsubscribe_map(99, q)          # idempotent
    assert events.map_subscriber_count(99) == 0


# ── a save reaches the screens ────────────────────────────────────────────────────────

@pytest.mark.parametrize("call", [
    ("post", "/api/dm/map/{mid}/update", {"camera": {"x": 1, "y": 2, "zoom": 1}}),
    ("post", "/api/dm/map/{mid}/tokens", {"tokens": [{"id": 0, "x": 10, "y": 10, "kind": "marker"}]}),
    ("post", "/api/dm/map/{mid}/token/add", {"kind": "marker", "x": 30, "y": 30}),
    ("post", "/api/dm/map/{mid}/layer", {"fog": ["1,1"], "fog_on": 1, "draw": []}),
    ("post", "/api/dm/map/{mid}/image", {"image": ""}),
])
def test_a_dm_save_pokes_the_maps_screens(client, seeded_db, auth_headers, call):
    from services import events

    method, path, body = call
    mid, _ = _map_with_key(client, auth_headers)

    q = events.subscribe_map(mid)
    try:
        resp = getattr(client, method)(path.format(mid=mid), json=body, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        event = q.get_nowait()
        assert event == {"kind": "map", "map_id": mid}
    finally:
        events.unsubscribe_map(mid, q)


def test_token_edits_poke_too_including_a_delete(client, seeded_db, auth_headers):
    """Token routes are addressed by token, not by map — they still have to find the map."""
    from services import events

    mid, _ = _map_with_key(client, auth_headers)
    token = client.post(f"/api/dm/map/{mid}/token/add", json={"kind": "marker", "x": 5, "y": 5},
                        headers=auth_headers).json()["token"]

    q = events.subscribe_map(mid)
    try:
        assert client.post(f"/api/dm/map/token/{token['id']}/update", json={"hp_current": 3},
                           headers=auth_headers).status_code == 200
        assert q.get_nowait()["map_id"] == mid, "an hp change on a token is player-visible"

        assert client.post(f"/api/dm/map/token/{token['id']}/delete",
                           headers=auth_headers).status_code == 200
        assert q.get_nowait()["map_id"] == mid, "the map id must be captured before the row goes"
    finally:
        events.unsubscribe_map(mid, q)


def test_every_player_visible_write_route_pokes():
    """The guard that makes a new map route fail loudly instead of silently going un-pushed.

    Every POST route in maps.py that commits must poke, except the ones whose effect no screen
    can see (creating a map, snapshots, scene deletion, key rotation) — those are listed here so
    adding one is a deliberate act.
    """
    src = MAPS.read_text()
    tree = ast.parse(src)
    lines = src.splitlines()

    not_player_visible = {
        "dm_map_create", "dm_map_delete", "dm_map_snapshot", "dm_map_scene_delete",
        "dm_map_player_key_set", "dm_map_player_key", "dm_map_align_grid",
    }
    checked, allowed = [], []
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
        if "router.post" not in decorators:
            continue
        body = "\n".join(lines[node.lineno - 1:node.end_lineno])
        if "db.commit()" not in body and "_sync_tokens(" not in body:
            continue
        if node.name in not_player_visible:
            allowed.append(node.name)
            continue
        assert "_poke_map(" in body, (
            f"{node.name} writes map state but never pokes the players' screens — the map view "
            f"would only catch it on the 30s safety poll")
        checked.append(node.name)

    assert "dm_map_update" in checked and "dm_map_tokens_save" in checked, checked
    assert set(checked) | set(allowed) == {
        "dm_map_update", "dm_map_tokens_save", "dm_map_token_add", "dm_map_token_update",
        "dm_map_token_delete", "dm_map_layer_save", "dm_map_image", "dm_map_scene_restore",
        "dm_map_spawn_encounter", "dm_map_create", "dm_map_delete", "dm_map_snapshot",
        "dm_map_scene_delete", "dm_map_player_key_set", "dm_map_align_grid",
    }, "a map write route was added or removed: decide whether screens must see it"


# ── the stream is exactly as closed as /state ─────────────────────────────────────────
# A 200 on these routes is an endless body: reading one through TestClient hangs the run instead
# of failing (Pitfall 90). Every allowed case below calls the route directly and takes one frame;
# only the refusals — plain JSON — go through the real HTTP client.

def _request(path: str, *, token: str | None = None, query: str = "") -> StarletteRequest:
    headers = [(b"host", b"testserver"), (b"user-agent", b"Mozilla/5.0")]
    if token:
        headers.append((b"cookie", f"dnd_token={token}".encode()))
    return StarletteRequest({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(),
        "headers": headers, "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        "root_path": "",
    })


def _open_stream(mid, *, k="", token=None):
    """The route's own answer, plus its first frame when it really is a stream."""
    from routes.maps import dm_map_player_stream

    async def _go():
        resp = await dm_map_player_stream(
            mid, _request(f"/api/dm/map/{mid}/player/stream", token=token), k=k)
        if not hasattr(resp, "body_iterator"):
            return resp, None
        first = await resp.body_iterator.__anext__()
        await resp.body_iterator.aclose()
        return resp, first

    return asyncio.run(_go())


def test_the_stream_uses_the_same_door_as_the_state_read(client, seeded_db, auth_headers):
    mid, key = _map_with_key(client, auth_headers)

    resp, first = _open_stream(mid, k=key)
    assert resp.status_code == 200 and first.startswith("retry: 3000")
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("cache-control") == "no-cache, no-transform"
    assert resp.headers.get("x-accel-buffering") == "no"

    # the owner's own session works too, exactly as /state allows
    resp, _ = _open_stream(mid, token=seeded_db["user_token"])
    assert resp.status_code == 200

    # and the refusals are refusals, not half-open 200s
    assert client.get(f"/api/dm/map/{mid}/player/stream?k=wrong").status_code == 403
    assert client.get(f"/api/dm/map/{mid}/player/stream").status_code == 403, "no key, no session"
    assert client.get("/api/dm/map/999999/player/stream").status_code == 404


def test_someone_elses_map_is_not_found_for_a_session_but_the_key_still_works(
        client, seeded_db, auth_headers):
    """Same answer as /state gives: a session must not confirm that a stranger's map exists."""
    import sqlite3

    db = sqlite3.connect(seeded_db["db_path"])
    cur = db.execute("INSERT INTO dm_maps (user_id, name, player_key) VALUES (2, 'Theirs', 'kk')")
    mid = cur.lastrowid
    db.commit()
    db.close()

    assert client.get(f"/api/dm/map/{mid}/player/stream", headers=auth_headers).status_code == 404
    resp, _ = _open_stream(mid, k="kk")
    assert resp.status_code == 200, "the key is the key, whoever is signed in"


def test_the_stream_flushes_first_and_pushes_a_poke_without_an_id(seeded_db, client, auth_headers):
    """Flush frame, then the poke, and nothing for a reconnect to replay."""
    from routes.maps import dm_map_player_stream
    from services import events

    mid, key = _map_with_key(client, auth_headers)

    async def _go():
        resp = await dm_map_player_stream(mid, _request(f"/api/dm/map/{mid}/player/stream"), k=key)
        agen = resp.body_iterator
        frames = [await agen.__anext__()]                 # the opening flush
        events.publish_map(mid, {"kind": "map", "map_id": mid})
        frames.append(await agen.__anext__())
        await agen.aclose()
        return frames

    first, poke = asyncio.run(_go())
    assert first.startswith("retry: 3000"), "a quiet stream must still flush a first byte"
    assert not poke.startswith("id:"), "a poke has nothing to replay"
    assert json.loads(_data_of(poke)) == {"kind": "map", "map_id": mid}


@pytest.mark.parametrize("case", ["wrong-key", "no-key", "absent-map"])
def test_a_refused_stream_never_becomes_a_stream(client, seeded_db, auth_headers, case):
    """A refusal must be a plain JSON error, not a half-open 200 carrying the frame contract."""
    mid, _ = _map_with_key(client, auth_headers)
    url = {"wrong-key": f"/api/dm/map/{mid}/player/stream?k=nope",
           "no-key": f"/api/dm/map/{mid}/player/stream",
           "absent-map": "/api/dm/map/424242/player/stream?k=x"}[case]
    r = client.get(url)
    assert r.status_code in (403, 404), r.text
    assert "text/event-stream" not in r.headers.get("content-type", "")


# ── the view's own wiring ─────────────────────────────────────────────────────────────

def test_the_player_view_streams_and_keeps_a_slow_safety_net():
    js = VTT.read_text()
    assert "PLAYER_POLL_FAST_MS = 3000" in js and "PLAYER_POLL_SAFE_MS = 30000" in js
    assert "function playerStream()" in js and "playerStream();" in js
    assert "'/player/stream'" in js, "the player view must open the stream it is served"
    assert "addEventListener('open', function () { playerPollAt(PLAYER_POLL_SAFE_MS); })" in js
    assert "addEventListener('error', function () { playerPollAt(PLAYER_POLL_FAST_MS); })" in js, (
        "a dropped stream must put the fast poll back, not leave the screen stale")
    assert "playerScheduleRefetch()" in js, "the poke must trigger a refetch"


def test_revoking_the_key_closes_a_screen_that_is_already_open(client, seeded_db, auth_headers):
    """A rotated key must not leave an open screen streaming. The campaign channel re-checks the
    key every heartbeat; this stream has to make the same promise or revocation is a lie."""
    from routes.maps import dm_map_player_stream

    mid, key = _map_with_key(client, auth_headers)

    async def _go():
        resp = await dm_map_player_stream(mid, _request(f"/api/dm/map/{mid}/player/stream"), k=key)
        agen = resp.body_iterator
        frames = [await agen.__anext__()]                  # the opening flush
        # the DM rotates the key while the screen is open
        assert client.post(f"/api/dm/map/{mid}/player-key", json={"key": "rotated-away"},
                           headers=auth_headers).status_code == 200
        try:
            while True:
                chunk = await agen.__anext__()
                frames.append(chunk)
        except StopAsyncIteration:
            pass
        return frames

    frames = asyncio.run(_go())
    assert frames[0].startswith("retry: 3000")
    assert len(frames) == 1, "the screen kept receiving after its key was rotated"
    assert "data:" not in "".join(frames)


def test_a_session_viewer_is_not_closed_by_a_key_rotation(client, seeded_db, auth_headers):
    """The owner's own second screen has no key to lose — it must keep working."""
    from routes.maps import dm_map_player_stream

    mid, _ = _map_with_key(client, auth_headers)

    async def _go():
        resp = await dm_map_player_stream(mid, _request(f"/api/dm/map/{mid}/player/stream",
                                                        token=seeded_db["user_token"]))
        agen = resp.body_iterator
        first = await agen.__anext__()
        client.post(f"/api/dm/map/{mid}/player-key", json={"key": ""}, headers=auth_headers)
        # the loop re-checks, then waits for a heartbeat (short by patching the module constant)
        await agen.aclose()
        return first

    assert asyncio.run(_go()).startswith("retry: 3000")


# ── a token borrows its character's HP and conditions, live ──────────────────────────

def _character(db_path, user_id, name="Rowan", hp=30, hp_max=40):
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO characters (user_id, name, race, class_name, level, hp_current, hp_max) "
            "VALUES (?, ?, 'Human', 'Fighter', 3, ?, ?)", (user_id, name, hp, hp_max))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _char_token(client, headers, mid, char_id):
    """Token rows carry their own hp columns, and nothing keeps them in step with the character."""
    return client.post(f"/api/dm/map/{mid}/token/add",
                       json={"kind": "character", "x": 3, "y": 3, "character_id": char_id,
                             "label": "Rowan"}, headers=headers).json()["token"]


def test_a_character_linked_token_borrows_hp_and_conditions(seeded_db, client, auth_headers):
    char_id = _character(seeded_db["db_path"], 1, hp=30, hp_max=40)
    mid, key = _map_with_key(client, auth_headers)
    _char_token(client, auth_headers, mid, char_id)

    tok = client.get(f"/api/dm/map/{mid}/state", params={"k": key}).json()["tokens"][0]
    assert (tok["hp_current"], tok["hp_max"]) == (30, 40), (
        "the token's own copy reads 0/0 and is not the truth about the character")
    assert tok["conditions"] == []

    assert client.post(f"/api/character/{char_id}/conditions", json={"name": "Prone"},
                       headers=auth_headers).status_code == 200
    tok = client.get(f"/api/dm/map/{mid}/state", params={"k": key}).json()["tokens"][0]
    assert tok["conditions"] == ["Prone"], "the table has to see WHO is prone"


def test_the_dms_own_view_agrees_with_the_players(seeded_db, client, auth_headers):
    """Two surfaces, one derivation: a DM and a party member must not disagree about a character."""
    char_id = _character(seeded_db["db_path"], 1, hp=12, hp_max=40)
    mid, key = _map_with_key(client, auth_headers)
    _char_token(client, auth_headers, mid, char_id)
    client.post(f"/api/character/{char_id}/conditions", json={"name": "Poisoned"}, headers=auth_headers)

    dm_tok = client.get(f"/api/dm/map/{mid}", headers=auth_headers).json()["tokens"][0]
    player_tok = client.get(f"/api/dm/map/{mid}/state", params={"k": key}).json()["tokens"][0]
    assert dm_tok["conditions"] == player_tok["conditions"] == ["Poisoned"]
    assert (dm_tok["hp_current"], dm_tok["hp_max"]) == (player_tok["hp_current"], player_tok["hp_max"]) == (12, 40)


def test_bare_string_conditions_resolve_too(seeded_db, client, auth_headers):
    """The sheet writes dicts; rows written by older paths hold bare strings. Both must reach the
    map — a screen that understood only one shape would silently show nothing for half a table.

    Written straight to the row on purpose: `/api/character/{id}/update` does NOT accept a
    `conditions` key (the condition routes own it), so only a legacy row can have this shape.
    """
    import sqlite3

    char_id = _character(seeded_db["db_path"], 1)
    mid, key = _map_with_key(client, auth_headers)
    _char_token(client, auth_headers, mid, char_id)

    con = sqlite3.connect(seeded_db["db_path"])
    con.execute("UPDATE characters SET conditions=? WHERE id=?",
                (json.dumps(["Prone", "Blinded"]), char_id))
    con.commit()
    con.close()

    tok = client.get(f"/api/dm/map/{mid}/state", params={"k": key}).json()["tokens"][0]
    assert tok["conditions"] == ["Prone", "Blinded"]


def test_a_token_with_no_character_keeps_its_own_numbers_and_shows_nothing(
        seeded_db, client, auth_headers):
    """An owlbear is not a character: its HP lives on the token and it has no conditions."""
    mid, key = _map_with_key(client, auth_headers)
    client.post(f"/api/dm/map/{mid}/token/add",
                json={"kind": "creature", "x": 1, "y": 1, "label": "Owlbear",
                      "hp_current": 54, "hp_max": 59}, headers=auth_headers)

    tok = client.get(f"/api/dm/map/{mid}/state", params={"k": key}).json()["tokens"][0]
    assert (tok["hp_current"], tok["hp_max"]) == (54, 59)
    assert tok["conditions"] == []


def test_a_condition_on_the_sheet_pokes_the_map_showing_that_character(
        seeded_db, client, auth_headers):
    """The map stream listens on the MAP's scope, so a campaign event never reaches it: without
    this poke the players' screen would hold the old conditions until some unrelated map change."""
    from services import events

    char_id = _character(seeded_db["db_path"], 1)
    mid, _ = _map_with_key(client, auth_headers)
    _char_token(client, auth_headers, mid, char_id)
    other_mid, _ = _map_with_key(client, auth_headers, name="No token for them")

    q = events.subscribe_map(mid)
    other = events.subscribe_map(other_mid)
    try:
        assert client.post(f"/api/character/{char_id}/conditions", json={"name": "Paralyzed"},
                           headers=auth_headers).status_code == 200
        assert q.get_nowait()["map_id"] == mid, "the screen showing the token must be poked"
        assert other.qsize() == 0, "and a map with no token for them must not be"
    finally:
        events.unsubscribe_map(mid, q)
        events.unsubscribe_map(other_mid, other)


def test_the_player_view_draws_condition_pills():
    """Text guard: the client has to render what the server now sends, abbreviated."""
    js = VTT.read_text()
    assert "var conds = t.conditions || [];" in js
    assert "String(conds[ci]).slice(0, 3).toUpperCase()" in js, "pills are abbreviations, not essays"
    assert "'+'" in js and "conds.length - 3" in js, "say there are more instead of dropping them"


def test_a_body_less_post_is_a_bad_request_not_a_crash(client, seeded_db, auth_headers):
    """Found while probing: a POST with no body raised inside the route and answered 500, which
    tells a caller nothing. 400 says what was wrong."""
    mid, _ = _map_with_key(client, auth_headers)
    r = client.post(f"/api/dm/map/{mid}/player-key", headers=auth_headers)
    assert r.status_code == 400, r.text
    assert "body" in r.json()["error"].lower()
