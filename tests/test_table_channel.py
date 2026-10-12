"""Table channel: shared roll feed + live push.

Tests are grouped by the slice they came from. The live stream itself is proven against a
real HTTP client in the slice-1 verification (TestClient streaming cannot prove delivery
timing or the edge behaviour), so what lives here is the contract: schema, bus, write path,
projection and scope.
"""

import json
import sqlite3

import pytest


# ── Slice 1a: schema ────────────────────────────────────────────────────────────────

def test_roll_log_schema_and_campaign_key(test_db):
    """roll_log exists with its columns, and dm_campaigns gained player_key."""
    con = sqlite3.connect(str(test_db))
    con.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(roll_log)")}
        assert {"id", "campaign_id", "character_id", "character_name", "who", "source",
                "label", "expression", "results", "total", "hidden", "created_at"} <= cols

        camp_cols = {r["name"] for r in con.execute("PRAGMA table_info(dm_campaigns)")}
        assert "player_key" in camp_cols

        # the campaign read path is indexed by (campaign_id, id DESC)
        indexes = {r["name"] for r in con.execute("PRAGMA index_list(roll_log)")}
        assert "idx_roll_log_campaign" in indexes
    finally:
        con.close()


def test_roll_log_survives_reinit(test_db):
    """init_db() runs on every boot: a second run must not fail or duplicate."""
    from main import init_db
    import main as app_module

    con = sqlite3.connect(str(test_db))
    con.execute("INSERT INTO roll_log (campaign_id, who, expression, results, total) "
                "VALUES (1, 'dm', '1d20', '[17]', 17)")
    con.commit()
    con.close()

    init_db()
    assert app_module.DB_PATH == test_db

    con = sqlite3.connect(str(test_db))
    try:
        assert con.execute("SELECT COUNT(*) FROM roll_log").fetchone()[0] == 1
    finally:
        con.close()


# ── Slice 1b: the fan-out bus ───────────────────────────────────────────────────────

def test_publish_reaches_each_subscriber():
    from services import events

    q1 = events.subscribe(1)
    q2 = events.subscribe(1)
    try:
        events.publish(1, {"kind": "roll", "id": 7})
        assert q1.get_nowait()["id"] == 7
        assert q2.get_nowait()["id"] == 7
    finally:
        events.unsubscribe(1, q1)
        events.unsubscribe(1, q2)


def test_publish_with_no_subscribers_is_a_noop():
    from services import events

    events.publish(999, {"kind": "roll"})  # must not raise


def test_publish_is_scoped_to_its_campaign():
    from services import events

    a = events.subscribe(11)
    b = events.subscribe(12)
    try:
        events.publish(11, {"kind": "roll", "id": 1})
        assert a.qsize() == 1
        assert b.qsize() == 0
    finally:
        events.unsubscribe(11, a)
        events.unsubscribe(12, b)


def test_a_stalled_subscriber_is_dropped_instead_of_blocking_the_roller():
    """A client that stopped reading must never wedge the publish path."""
    from services import events

    q = events.subscribe(13)
    try:
        for i in range(events.QUEUE_MAX + 5):
            events.publish(13, {"kind": "roll", "id": i})
        assert events.subscriber_count(13) == 0
    finally:
        events.unsubscribe(13, q)


def test_unsubscribe_is_idempotent():
    from services import events

    q = events.subscribe(14)
    events.unsubscribe(14, q)
    events.unsubscribe(14, q)
    assert events.subscriber_count(14) == 0


# ── Slice 1c: the roll write path ───────────────────────────────────────────────────

def _db(db_path):
    """A row_factory connection to the test DB — the same shape get_db() hands the routes."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _campaign(db_path, user_id: int, name: str = "Feed test") -> int:
    con = _db(db_path)
    try:
        cur = con.execute("INSERT INTO dm_campaigns (user_id, name) VALUES (?, ?)",
                          (user_id, name))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def test_insert_roll_computes_the_total_server_side(test_db):
    """A client may not post a total: it is derived from the dice it claims to have rolled."""
    from routes.table import insert_roll

    db = _db(test_db)
    try:
        row = insert_roll(db, campaign_id=1, who="dm", expression="1d20+5", results=[17])
        assert row["total"] == 22
        assert row["results"] == [17]
        assert row["who"] == "dm"
    finally:
        db.close()


def test_insert_roll_rejects_anything_that_is_not_a_dice_expression(test_db):
    from routes.table import insert_roll

    db = _db(test_db)
    try:
        for bad in ("; DROP TABLE roll_log", "1d2000", "200d6", "fireball", "", "1d20+5; --"):
            with pytest.raises(ValueError):
                insert_roll(db, campaign_id=1, expression=bad, results=[1])
    finally:
        db.close()


def test_insert_roll_rejects_results_that_do_not_match_the_expression(test_db):
    from routes.table import insert_roll

    db = _db(test_db)
    try:
        with pytest.raises(ValueError):
            insert_roll(db, campaign_id=1, expression="1d20", results=[1, 2, 3])   # too many
        with pytest.raises(ValueError):
            insert_roll(db, campaign_id=1, expression="2d6", results=[7, 1])       # face > sides
        with pytest.raises(ValueError):
            insert_roll(db, campaign_id=1, expression="1d20", results=[0])         # impossible face
    finally:
        db.close()


def test_insert_roll_prunes_to_the_newest_500(test_db):
    from routes.table import insert_roll, FEED_LIMIT

    db = _db(test_db)
    try:
        for i in range(FEED_LIMIT + 5):
            insert_roll(db, campaign_id=1, who="dm", expression="1d20", results=[1],
                        label="roll %d" % i)
        rows = db.execute("SELECT COUNT(*) FROM roll_log WHERE campaign_id=1").fetchone()[0]
        assert rows == FEED_LIMIT
        newest = db.execute("SELECT label FROM roll_log WHERE campaign_id=1 "
                            "ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert newest == "roll %d" % (FEED_LIMIT + 4)
        # another campaign's rows are untouched by the prune
        insert_roll(db, campaign_id=2, who="dm", expression="1d20", results=[1])
        assert db.execute("SELECT COUNT(*) FROM roll_log WHERE campaign_id=2").fetchone()[0] == 1
    finally:
        db.close()


# ── Slice 1d: the DM endpoints ──────────────────────────────────────────────────────

def _other_user(db_path) -> str:
    """A third account that owns nothing — the scope probe needs a NON-admin bystander."""
    import secrets as _secrets

    token = _secrets.token_hex(32)
    con = _db(db_path)
    try:
        cur = con.execute("INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 0)",
                          ("bystander-%s@example.com" % token[:8], "x"))
        con.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (cur.lastrowid, token))
        con.commit()
    finally:
        con.close()
    return token


def _hdr(token: str) -> dict:
    return {"Cookie": f"dnd_token={token}; csrf_token=t", "X-CSRF-Token": "t"}


def test_roll_endpoint_writes_the_roll_and_returns_it(seeded_db, client, auth_headers):
    camp_id = _campaign(seeded_db["db_path"], 1)
    resp = client.post(f"/api/campaign/{camp_id}/roll", headers=auth_headers,
                       json={"expression": "1d20+5", "results": [17], "label": "Longsword"})
    assert resp.status_code == 200, resp.text
    roll = resp.json()["roll"]
    assert roll["total"] == 22 and roll["who"] == "dm" and roll["label"] == "Longsword"
    assert roll["hidden"] is False

    hist = client.get(f"/api/campaign/{camp_id}/rolls", headers=auth_headers)
    assert hist.status_code == 200
    assert [r["id"] for r in hist.json()["rolls"]] == [roll["id"]]


def test_roll_endpoint_rejects_a_bad_expression(seeded_db, client, auth_headers):
    camp_id = _campaign(seeded_db["db_path"], 1)
    resp = client.post(f"/api/campaign/{camp_id}/roll", headers=auth_headers,
                       json={"expression": "1d20'; DROP TABLE roll_log; --", "results": [3]})
    assert resp.status_code == 400
    con = _db(seeded_db["db_path"])
    try:
        assert con.execute("SELECT COUNT(*) FROM roll_log").fetchone()[0] == 0
        # the table still exists
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='roll_log'") \
            .fetchone()[0] == 1
    finally:
        con.close()


def test_roll_endpoints_are_scoped_to_the_owning_dm(seeded_db, client, auth_headers):
    camp_id = _campaign(seeded_db["db_path"], 1)
    bystander = _other_user(seeded_db["db_path"])

    assert client.get(f"/api/campaign/{camp_id}/rolls", headers=_hdr(bystander)) \
        .status_code == 404
    assert client.post(f"/api/campaign/{camp_id}/roll", headers=_hdr(bystander),
                       json={"expression": "1d20", "results": [3]}).status_code == 404
    con = _db(seeded_db["db_path"])
    try:
        assert con.execute("SELECT COUNT(*) FROM roll_log").fetchone()[0] == 0
    finally:
        con.close()


def test_rolls_endpoint_returns_newest_first(seeded_db, client, auth_headers):
    camp_id = _campaign(seeded_db["db_path"], 1)
    ids = []
    for i in range(3):
        resp = client.post(f"/api/campaign/{camp_id}/roll", headers=auth_headers,
                           json={"expression": "1d20", "results": [i + 1], "label": "r%d" % i})
        ids.append(resp.json()["roll"]["id"])

    listed = client.get(f"/api/campaign/{camp_id}/rolls", headers=auth_headers).json()["rolls"]
    assert [r["id"] for r in listed] == list(reversed(ids))
    # a limit is clamped, never an unbounded read
    assert len(client.get(f"/api/campaign/{camp_id}/rolls?limit=2", headers=auth_headers)
               .json()["rolls"]) == 2


def test_a_committed_roll_is_published_to_subscribers(seeded_db, client, auth_headers):
    """The push happens after the write: a subscriber sees exactly what the log holds."""
    from services import events

    camp_id = _campaign(seeded_db["db_path"], 1)
    q = events.subscribe(camp_id)
    try:
        resp = client.post(f"/api/campaign/{camp_id}/roll", headers=auth_headers,
                           json={"expression": "2d6", "results": [3, 4]})
        assert resp.status_code == 200
        event = q.get_nowait()
        assert event["kind"] == "roll"
        assert event["id"] == resp.json()["roll"]["id"]
        assert event["total"] == 7
    finally:
        events.unsubscribe(camp_id, q)


# ── Slice 1e: the SSE stream ────────────────────────────────────────────────────────

def _data_of(frame: str) -> str:
    """The JSON payload line of one SSE frame."""
    for line in frame.splitlines():
        if line.startswith("data: "):
            return line[6:]
    raise AssertionError("frame carried no data line: %r" % frame)


def _drain(agen, n: int) -> list[str]:
    """Pull n frames from an async generator, synchronously."""
    import asyncio

    async def _go():
        out = []
        async for chunk in agen:
            out.append(chunk)
            if len(out) >= n:
                break
        return out

    return asyncio.run(_go())


def test_stream_replays_from_last_event_id(seeded_db):
    """A reconnecting client gets the gap, not the whole feed and not nothing."""
    import json as _json

    from routes.table import insert_roll, stream_frames

    camp_id = _campaign(seeded_db["db_path"], 1)
    db = _db(seeded_db["db_path"])
    try:
        first = insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[5])
        second = insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[9])
        frames = _drain(stream_frames(camp_id, last_id=first["id"], heartbeat=0.05), 2)
    finally:
        db.close()

    payload = _json.loads(_data_of(frames[1]))   # frames[0] is the opening flush
    assert payload["id"] == second["id"]
    assert payload["total"] == 9
    assert f"id: {first['id']}" not in frames[0]


def test_a_fresh_stream_does_not_replay_history(seeded_db):
    """No Last-Event-ID means a fresh page: history comes from /rolls, not the stream."""
    from routes.table import insert_roll, stream_frames

    camp_id = _campaign(seeded_db["db_path"], 1)
    db = _db(seeded_db["db_path"])
    try:
        insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[5])
        frames = _drain(stream_frames(camp_id, last_id=0, heartbeat=0.05), 2)
    finally:
        db.close()

    assert frames[0].startswith("retry: 3000")   # the opening flush, before anything else
    assert frames[1].strip() == ": hb"           # then heartbeats while the table is quiet
    assert "1d20" not in "".join(frames), "a fresh stream must not replay history"


def test_stream_events_are_the_roll_rows(seeded_db):
    from routes.table import insert_roll, recent_rolls

    camp_id = _campaign(seeded_db["db_path"], 1)
    db = _db(seeded_db["db_path"])
    try:
        insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20+2", results=[11],
                    label="Save")
        rows = recent_rolls(db, camp_id, 10)
        assert rows[0]["label"] == "Save"
        assert rows[0]["total"] == 13
    finally:
        db.close()


def _request(path: str, token: str = "", last_event_id=None):
    """A minimal ASGI request, so a streaming route can be called without streaming it.

    TestClient reads the whole body, and an event stream has no end — calling the route
    directly is the only way to assert its contract in a unit test. Real delivery over
    HTTP is proven in the slice verification against the deployed service.
    """
    from starlette.requests import Request

    headers = [(b"host", b"testserver"), (b"user-agent", b"Mozilla/5.0")]
    if token:
        headers.append((b"cookie", ("dnd_token=%s; csrf_token=t" % token).encode()))
    if last_event_id is not None:
        headers.append((b"last-event-id", str(last_event_id).encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": headers, "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
    })


def test_stream_requires_ownership(seeded_db):
    """No session: refused. A bystander: 404, never a 403 that confirms the campaign exists."""
    import asyncio

    from fastapi import HTTPException

    from routes.table import campaign_stream

    camp_id = _campaign(seeded_db["db_path"], 1)
    path = f"/api/campaign/{camp_id}/stream"

    with pytest.raises(HTTPException):
        asyncio.run(campaign_stream(camp_id, _request(path)))

    bystander = _other_user(seeded_db["db_path"])
    resp = asyncio.run(campaign_stream(camp_id, _request(path, token=bystander)))
    assert resp.status_code == 404


def test_stream_response_is_an_event_stream(seeded_db):
    """Headers matter: a cached or compressed stream arrives as one late burst."""
    import asyncio

    from routes.table import campaign_stream

    camp_id = _campaign(seeded_db["db_path"], 1)
    resp = asyncio.run(campaign_stream(
        camp_id, _request(f"/api/campaign/{camp_id}/stream", token=seeded_db["user_token"])))

    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"
    assert "no-cache" in resp.headers["cache-control"]
    assert "no-transform" in resp.headers["cache-control"]
    assert resp.headers["x-accel-buffering"] == "no"


def test_stream_honours_last_event_id_from_the_header(seeded_db):
    """EventSource sends Last-Event-ID on reconnect: the route must replay from it."""
    import asyncio
    import json as _json

    from routes.table import campaign_stream, insert_roll

    camp_id = _campaign(seeded_db["db_path"], 1)
    db = _db(seeded_db["db_path"])
    try:
        first = insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[5])
        second = insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[9])
    finally:
        db.close()

    resp = asyncio.run(campaign_stream(
        camp_id, _request(f"/api/campaign/{camp_id}/stream",
                          token=seeded_db["user_token"], last_event_id=first["id"])))

    async def _frames(n):
        """The first n chunks off the response iterator, without waiting for the stream to end."""
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk)
            if len(out) >= n:
                break
        return out

    frames = asyncio.run(_frames(2))
    # frames[0] is the opening flush; the replayed roll is the first real frame after it.
    # Matching "data: " against a whole frame never matches — chunks are frames, not lines —
    # and the loop then spins on heartbeats forever (which hung this test once).
    assert _json.loads(_data_of(frames[1]))["id"] == second["id"]


# ── Slice 2: the player path (campaign key, filtered stream, player rolls) ──────────

def _campaign_with_roster(db_path, user_id: int, characters: list) -> int:
    import json as _json

    con = _db(db_path)
    try:
        cur = con.execute("INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?, ?, ?)",
                          (user_id, "Roster test", _json.dumps(characters)))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def _set_key(db_path, camp_id: int, key: str) -> None:
    """Give a campaign its share key. Routes read committed data through their own connection."""
    con = _db(db_path)
    try:
        con.execute("UPDATE dm_campaigns SET player_key = ? WHERE id = ?", (key, camp_id))
        con.commit()
    finally:
        con.close()


def test_player_key_is_created_then_rotated_then_revoked(seeded_db, client, auth_headers):
    camp_id = _campaign(seeded_db["db_path"], 1)

    first = client.get(f"/api/campaign/{camp_id}/player-key", headers=auth_headers).json()
    assert len(first["player_key"]) == 32
    assert first["url"] == f"/table/{first['player_key']}"

    # asking again returns the same key rather than minting a new one
    assert client.get(f"/api/campaign/{camp_id}/player-key", headers=auth_headers) \
        .json()["player_key"] == first["player_key"]

    rotated = client.post(f"/api/campaign/{camp_id}/player-key", headers=auth_headers,
                          json={"key": "a" * 32}).json()
    assert rotated["player_key"] == "a" * 32

    revoked = client.post(f"/api/campaign/{camp_id}/player-key", headers=auth_headers,
                          json={"key": ""}).json()
    assert revoked["player_key"] == "" and revoked["url"] == ""


def test_player_key_endpoints_are_scoped(seeded_db, client, auth_headers):
    camp_id = _campaign(seeded_db["db_path"], 1)
    bystander = _other_user(seeded_db["db_path"])
    assert client.get(f"/api/campaign/{camp_id}/player-key", headers=_hdr(bystander)) \
        .status_code == 404
    assert client.post(f"/api/campaign/{camp_id}/player-key", headers=_hdr(bystander),
                       json={"key": "x"}).status_code == 404


def test_campaign_by_key_rejects_blank_and_unknown_keys(seeded_db):
    """A blank key must not match every campaign that has one revoked."""
    from routes.table import campaign_by_key

    camp_id = _campaign(seeded_db["db_path"], 1)
    db = _db(seeded_db["db_path"])
    try:
        db.execute("UPDATE dm_campaigns SET player_key = '' WHERE id = ?", (camp_id,))
        db.commit()
        assert campaign_by_key(db, "") is None
        assert campaign_by_key(db, "   ") is None
        assert campaign_by_key(db, "0" * 32) is None
        assert campaign_by_key(db, None) is None

        db.execute("UPDATE dm_campaigns SET player_key = ? WHERE id = ?", ("k" * 32, camp_id))
        db.commit()
        found = campaign_by_key(db, "k" * 32)
        assert found and found["id"] == camp_id
    finally:
        db.close()


def test_player_stream_never_carries_a_hidden_roll(seeded_db):
    """Hidden is a server-side projection, not a client-side courtesy."""
    from routes.table import insert_roll, stream_frames

    camp_id = _campaign(seeded_db["db_path"], 1)
    db = _db(seeded_db["db_path"])
    try:
        insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[11],
                    label="secret", hidden=True)
    finally:
        db.close()

    # a reconnecting player would replay from here, and must still not see it
    frames = _drain(stream_frames(camp_id, last_id=0, heartbeat=0.05, include_hidden=False), 2)
    assert "id:" not in "".join(frames), "the hidden roll must not be replayed"

    # and the history projection agrees
    db = _db(seeded_db["db_path"])
    try:
        from routes.table import recent_rolls
        assert recent_rolls(db, camp_id, 50, include_hidden=False) == []
        assert len(recent_rolls(db, camp_id, 50)) == 1
    finally:
        db.close()


def test_player_stream_drops_a_live_hidden_roll(seeded_db):
    """The live path filters too, not just the replay."""
    import asyncio

    from routes.table import stream_frames
    from services import events

    camp_id = _campaign(seeded_db["db_path"], 1)

    async def _go():
        agen = stream_frames(camp_id, last_id=0, heartbeat=0.05, include_hidden=False)
        first = await agen.__anext__()            # the opening flush
        assert first.startswith("retry: 3000")
        events.publish(camp_id, {"kind": "roll", "id": 99, "total": 20, "hidden": True})
        events.publish(camp_id, {"kind": "roll", "id": 100, "total": 3, "hidden": False})
        second = await agen.__anext__()
        await agen.aclose()
        return first, second

    _, second = asyncio.run(_go())
    assert "id: 100" in second
    assert "id: 99" not in second


def test_player_roll_must_name_a_character_on_the_roster(seeded_db, client):
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": 42, "name": "Rowan"}])
    _set_key(seeded_db["db_path"], camp_id, "k" * 32)

    ok = client.post("/api/table/%s/roll" % ("k" * 32),
                     json={"expression": "1d20", "results": [4], "character_id": 42})
    assert ok.status_code == 200, ok.text
    assert ok.json()["roll"]["character_name"] == "Rowan" or ok.json()["roll"]["character_id"] == 42

    bad = client.post("/api/table/%s/roll" % ("k" * 32),
                      json={"expression": "1d20", "results": [4], "character_id": 7})
    assert bad.status_code == 400

    missing = client.post("/api/table/%s/roll" % ("k" * 32),
                          json={"expression": "1d20", "results": [4]})
    assert missing.status_code == 400


def test_player_roll_cannot_claim_to_be_the_dm_or_hide_itself(seeded_db, client):
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": 42, "name": "Rowan"}])
    _set_key(seeded_db["db_path"], camp_id, "k" * 32)

    resp = client.post("/api/table/%s/roll" % ("k" * 32),
                       json={"expression": "1d20", "results": [4], "character_id": 42,
                             "who": "dm", "hidden": True})
    assert resp.status_code == 200
    roll = resp.json()["roll"]
    assert roll["who"] == "player"
    assert roll["hidden"] is False


def test_player_roll_is_rate_limited(seeded_db, client):
    """A stuck client must not be able to fill the log or the fan-out."""
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": 42, "name": "Rowan"}])
    _set_key(seeded_db["db_path"], camp_id, "k" * 32)

    codes = [client.post("/api/table/%s/roll" % ("k" * 32),
                         json={"expression": "1d20", "results": [4], "character_id": 42}
                         ).status_code for _ in range(25)]
    assert codes.count(200) <= 20
    assert 429 in codes


def test_a_revoked_key_stops_the_player_stream(seeded_db):
    """The key is re-checked while streaming, so revocation does not wait for a reload."""
    import asyncio

    from routes.table import stream_frames

    camp_id = _campaign(seeded_db["db_path"], 1)
    calls = {"n": 0}

    def keep_alive():
        calls["n"] += 1
        return False          # the row no longer carries this key

    async def _go():
        agen = stream_frames(camp_id, last_id=0, heartbeat=0.05, keep_alive=keep_alive)
        return [chunk async for chunk in agen]

    frames = asyncio.run(_go())
    assert frames and frames[0].startswith("retry: 3000"), "the flush happens before the check"
    assert len(frames) == 1, "a revoked key must not receive anything else"
    assert calls["n"] >= 1






# ── Slice 2b: the surfaces ──────────────────────────────────────────────────────────

def test_the_player_page_renders_for_a_key_and_404s_without_one(seeded_db, client):
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1,
                                    [{"id": 42, "name": "Rowan"},
                                     {"id": 43, "name": "Ámbar"}])
    _set_key(seeded_db["db_path"], camp_id, "k" * 32)

    ok = client.get("/table/" + "k" * 32)
    assert ok.status_code == 200, ok.text
    body = ok.text
    assert "Rowan" in body and "Ámbar" in body
    # the surface gets its own key and no campaign-wide secret beyond it
    assert "ROLL_RE" not in body and '"player_key"' not in body

    assert client.get("/table/" + "z" * 32).status_code == 404
    assert client.get("/table/%20").status_code in (404, 422)


def test_the_player_page_needs_no_login(seeded_db, client):
    """The tablet at the table has no session: the key alone must open the page."""
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": 42, "name": "Rowan"}])
    _set_key(seeded_db["db_path"], camp_id, "k" * 32)
    resp = client.get("/table/" + "k" * 32)          # no cookies at all
    assert resp.status_code == 200
    assert "Cookie" not in resp.request.headers


def test_the_dm_tools_page_mounts_the_feed_panel():
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    html = (repo / "templates" / "dm_tools.html").read_text()
    js = (repo / "static" / "dm_tools.js").read_text()
    for needed in ("rollFeedPanel", "rollFeedList", "rfStatus", "rfRollMode",
                   "rfHiddenToggle", "rfShareBtn"):
        assert needed in html, f"the DM panel is missing {needed}"
    assert "initRollFeed()" in js, "the panel is never mounted"
    assert "loadCombatCampaign" in js and js.count("initRollFeed()") >= 2, (
        "the feed has to be (re)mounted by the campaign select, including the no-campaign case")
    # the player page must exist and be reachable
    assert (repo / "templates" / "table.html").exists()


def _character(db_path, user_id: int, name: str = "Rowan") -> int:
    """A real character row: the feed routes authorise through the character's owner."""
    con = _db(db_path)
    try:
        cur = con.execute("INSERT INTO characters (user_id, name, race, class_name, level) "
                          "VALUES (?, ?, 'Human', 'Fighter', 3)", (user_id, name))
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def test_the_sheet_feed_is_the_players_projection(seeded_db, client, auth_headers):
    """The sheet reads the campaign feed through the character, with hidden rolls withheld."""
    char_id = _character(seeded_db["db_path"], 1)
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])
    from routes.table import insert_roll

    db = _db(seeded_db["db_path"])
    try:
        insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[11],
                    label="secret", hidden=True)
        insert_roll(db, campaign_id=camp_id, who="dm", expression="1d20", results=[3],
                    label="open", hidden=False)
    finally:
        db.close()

    resp = client.get(f"/api/character/{char_id}/feed", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["campaign"]["id"] == camp_id
    labels = [r["label"] for r in data["rolls"]]
    assert labels == ["open"], "the sheet must not be shown a hidden roll"

    # and the stream is scoped to the character's owner
    bystander = _other_user(seeded_db["db_path"])
    assert client.get(f"/api/character/{char_id}/feed", headers=_hdr(bystander)).status_code == 404
    assert client.get(f"/api/character/{char_id}/feed/stream",
                      headers=_hdr(bystander)).status_code == 404


def test_the_sheet_feed_is_absent_without_a_campaign(seeded_db, client, auth_headers):
    char_id = _character(seeded_db["db_path"], 1, "Loner")

    data = client.get(f"/api/character/{char_id}/feed", headers=auth_headers).json()
    assert data == {"rolls": [], "campaign": None}


def test_the_sheet_mounts_a_read_only_feed():
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    html = (repo / "templates" / "sheet.html").read_text()
    js = (repo / "static" / "sheet.js").read_text()
    assert "sheetRollFeedCard" in html and "sheetRollFeedList" in html
    assert "initSheetRollFeed();" in js, "the sheet never mounts its feed"
    assert "publish: false" in js, "the sheet must not publish rolls into the table feed"


# ── Slice 4: state events, so the sheet's HP poll can stop being the mechanism ──────

def test_publish_character_state_reaches_the_characters_campaign(seeded_db):
    from routes.table import publish_character_state
    from services import events

    char_id = _character(seeded_db["db_path"], 1)
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    q = events.subscribe(camp_id)
    try:
        assert publish_character_state(char_id, hp_current=7, hp_max=30) is True
        event = q.get_nowait()
        assert event["kind"] == "hp"
        assert event["character_id"] == char_id
        assert event["hp_current"] == 7 and event["hp_max"] == 30
        assert event["character_name"] == "Rowan"
    finally:
        events.unsubscribe(camp_id, q)


def test_publish_character_state_is_a_noop_without_a_campaign(seeded_db):
    from routes.table import publish_character_state

    char_id = _character(seeded_db["db_path"], 1, "Loner")
    assert publish_character_state(char_id, hp_current=3) is False


def test_changing_hp_on_the_sheet_publishes_a_state_event(seeded_db, client, auth_headers):
    """The write that actually moves a character's HP is /update — that is what must publish."""
    from services import events

    char_id = _character(seeded_db["db_path"], 1)
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    q = events.subscribe(camp_id)
    try:
        resp = client.post(f"/api/character/{char_id}/update", headers=auth_headers,
                           json={"hp_current": 4})
        assert resp.status_code == 200, resp.text
        event = q.get_nowait()
        assert event["kind"] == "hp" and event["character_id"] == char_id
        assert event["hp_current"] == 4

        # a save that does not touch HP must not wake anybody up
        assert client.post(f"/api/character/{char_id}/update", headers=auth_headers,
                           json={"notes": "just some notes"}).status_code == 200
        assert q.qsize() == 0
    finally:
        events.unsubscribe(camp_id, q)


def test_the_sheet_poll_is_demoted_to_a_safety_net():
    """Text-level guard: with the stream open the 2s timer must not be the mechanism."""
    import pathlib

    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "sheet.js").read_text()
    assert "SHEET_POLL_FAST_MS = 2000" in js and "SHEET_POLL_SAFE_MS = 30000" in js
    assert "on('open', function () { startSheetPolling(SHEET_POLL_SAFE_MS); })" in js
    assert "on('error', function () { startSheetPolling(SHEET_POLL_FAST_MS); })" in js
    assert "on('hp'" in js, "nothing listens for the HP event the route publishes"


def test_every_stream_flushes_a_first_byte_before_anything_else(seeded_db):
    """Regression: with no first byte, EventSource stays CONNECTING on a quiet stream and the
    client's `open` never fires — which is how the sheet's poll demotion silently never ran."""
    from routes.table import stream_frames

    camp_id = _campaign(seeded_db["db_path"], 1)
    frames = _drain(stream_frames(camp_id, last_id=0, heartbeat=5), 1)
    assert frames[0].startswith("retry: 3000"), "the stream must flush something immediately"
    assert frames[0].strip().endswith(": ok")


def test_a_state_event_reaches_the_stream_without_an_id(seeded_db):
    """Regression: dropping non-roll events in the stream made the sheet's HP go stale the
    moment its poll was demoted. A state event must arrive AND must not carry a roll id."""
    import asyncio

    from routes.table import stream_frames
    from services import events

    camp_id = _campaign(seeded_db["db_path"], 1)

    async def _go():
        agen = stream_frames(camp_id, last_id=0, heartbeat=0.05, include_hidden=False)
        first = await agen.__anext__()                    # the opening flush
        assert first.startswith("retry: 3000")
        events.publish(camp_id, {"kind": "hp", "character_id": 78, "hp_current": 37})
        frame = await agen.__anext__()
        await agen.aclose()
        return first, frame

    _, frame = asyncio.run(_go())
    payload = json.loads(_data_of(frame))
    assert payload["kind"] == "hp" and payload["hp_current"] == 37
    assert not frame.startswith("id:"), "a state event must not set Last-Event-ID"
    assert "id:" not in frame.split("data:")[0]


def test_roll_frames_still_carry_their_id(seeded_db):
    """The other half: drops of roll ids would break the reconnect replay."""
    from routes.table import frame_for

    frame = frame_for({"kind": "roll", "id": 41, "expression": "1d20", "total": 12})
    assert frame.startswith("id: 41\n")
    assert _data_of(frame).startswith('{"kind": "roll"')


# ── Slice 6: the events a DM tracker mirrors ────────────────────────────────────────

def _subscribed(seeded_db, camp_id):
    from services import events

    return events, events.subscribe(camp_id)


def test_conditions_and_summons_publish_the_sheet_kind(seeded_db, client, auth_headers):
    """The DM's tracker mirrors conditions and summons as well as HP: a change it cannot hear
    about is a screen that goes stale the moment its poll is demoted."""
    from services import events

    char_id = _character(seeded_db["db_path"], 1)
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    q = events.subscribe(camp_id)
    try:
        assert client.post(f"/api/character/{char_id}/conditions",
                           json={"name": "Prone"}, headers=auth_headers).status_code == 200
        first = q.get_nowait()
        assert first["kind"] == "sheet" and first["character_id"] == char_id

        assert client.request("DELETE", f"/api/character/{char_id}/conditions",
                              json={"name": "Prone"}, headers=auth_headers).status_code == 200
        assert q.get_nowait()["kind"] == "sheet"

        created = client.post(f"/api/character/{char_id}/summons", json={"name": "Wolf", "hp_max": 11},
                              headers=auth_headers)
        assert created.status_code == 200, created.text
        assert q.get_nowait()["kind"] == "sheet"

        assert client.post(f"/api/character/{char_id}/update-summon-hp",
                           json={"summon_idx": 0, "hp_current": 4},
                           headers=auth_headers).status_code == 200
        event = q.get_nowait()
        assert event["kind"] == "sheet", "summon damage is exactly what the tracker mirrors"

        # a save that only touches notes still says nothing: no listener needs that
        assert client.post(f"/api/character/{char_id}/update", json={"notes": "hi"},
                           headers=auth_headers).status_code == 200
        assert q.qsize() == 0
    finally:
        events.unsubscribe(camp_id, q)


def test_hp_still_wins_the_kind_when_a_save_moves_both(seeded_db, client, auth_headers):
    from services import events

    char_id = _character(seeded_db["db_path"], 1)
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    q = events.subscribe(camp_id)
    try:
        client.post(f"/api/character/{char_id}/update",
                    json={"hp_current": 9, "conditions": ["Prone"]}, headers=auth_headers)
        assert q.qsize() == 1, "one save is one event, even when it moves HP and conditions"
        assert q.get_nowait()["kind"] == "hp", "HP is the more specific kind and wins"
    finally:
        events.unsubscribe(camp_id, q)


def test_a_sheet_event_carries_no_roll_id():
    """The frame contract again, from the other side: a state event that carried an id would
    poison Last-Event-ID and make the next reconnect replay the whole feed."""
    import pathlib

    from routes.table import frame_for

    frame = frame_for({"kind": "sheet", "character_id": 7})
    assert not frame.startswith("id:")
    assert _data_of(frame) == '{"kind": "sheet", "character_id": 7}'


def test_the_tracker_poll_is_demoted_and_wired_to_the_stream():
    """Text guard: the DM tracker must take its updates from the campaign stream it already
    opens for the roll feed, and keep a fast poll only as the fallback."""
    import pathlib

    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "dm_tools.js").read_text()
    assert "COMBAT_POLL_FAST_MS = 2000" in js and "COMBAT_POLL_SAFE_MS = 30000" in js
    assert "setInterval(syncPlayerAndSummonHP, 2000)" not in js, "the hard-coded 2s timer is gone"
    assert "setInterval(syncPlayerAndSummonHP, interval || COMBAT_POLL_FAST_MS)" in js
    assert "window.RollFeed.on('hp', _refreshTrackerIfTracked)" in js
    assert "window.RollFeed.on('sheet', _refreshTrackerIfTracked)" in js
    assert "on('open', function () { startCombatPolling(COMBAT_POLL_SAFE_MS); })" in js
    assert "on('error', function () { startCombatPolling(COMBAT_POLL_FAST_MS); })" in js
    assert "function _tracksCharacter(" in js, "the tracker must not refetch for strangers' sheets"


def test_a_player_hp_change_repaints_the_trackers_panel():
    """The players loop used to update its data without flagging a repaint, so the DM's panel
    kept the old number until something unrelated re-rendered. The initiative track holds hp
    inputs and still waits for a participant change, so the two flags stay separate."""
    import pathlib

    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "dm_tools.js").read_text()
    assert "let changedPlayers = false;" in js
    assert "if (changedPlayers) renderPlayersPanel();" in js
    assert "if (changed) {\n      renderInitiativeTrack();" in js, (
        "the initiative track must still wait for a participant change")


# ── Slice 7: the tracker's damage has to land on the sheet ──────────────────────────

def test_a_dm_can_move_the_hp_of_a_character_in_their_campaign(seeded_db, client, auth_headers):
    """The player's sheet belongs to the player, so the DM's authority is the campaign roster."""
    import sqlite3

    from services import events

    char_id = _character(seeded_db["db_path"], 2)          # a PLAYER's character, not the DM's
    camp_id = _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    q = events.subscribe(camp_id)
    try:
        r = client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": 12},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["hp_current"] == 12

        db = sqlite3.connect(seeded_db["db_path"])
        db.row_factory = sqlite3.Row
        assert db.execute("SELECT hp_current FROM characters WHERE id=?", (char_id,)).fetchone()["hp_current"] == 12
        db.close()

        event = q.get_nowait()
        assert event["kind"] == "hp" and event["character_id"] == char_id, (
            "the player's own sheet must hear about the damage")
    finally:
        events.unsubscribe(camp_id, q)


def test_a_dm_cannot_touch_a_character_outside_their_campaigns(seeded_db, client, auth_headers):
    import sqlite3

    stranger = _character(seeded_db["db_path"], 2, "Not Mine")
    r = client.post("/api/dm/character-hp", json={"character_id": stranger, "hp_current": 1},
                    headers=auth_headers)
    assert r.status_code == 404, "404, like every other ownership check: no confirming it exists"

    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT hp_current FROM characters WHERE id=?", (stranger,)).fetchone()[0] != 1
    db.close()


def test_the_hp_the_dm_sends_is_bounded_and_never_negative(seeded_db, client, auth_headers):
    char_id = _character(seeded_db["db_path"], 2)
    _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])

    assert client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": -50},
                       headers=auth_headers).json()["hp_current"] == 0
    assert client.post("/api/dm/character-hp", json={"character_id": char_id, "hp_current": "nope"},
                       headers=auth_headers).status_code == 400
    assert client.post("/api/dm/character-hp", json={"hp_current": 5},
                       headers=auth_headers).status_code == 400


def test_a_dm_can_move_a_summons_hp_in_their_campaign(seeded_db, client, auth_headers):
    import json as _j
    import sqlite3

    char_id = _character(seeded_db["db_path"], 2)
    _campaign_with_roster(seeded_db["db_path"], 1, [{"id": char_id, "name": "Rowan"}])
    db = sqlite3.connect(seeded_db["db_path"])
    db.execute("UPDATE characters SET summons=? WHERE id=?",
               (_j.dumps([{"name": "Wolf", "hp_current": 11, "hp_max": 11}]), char_id))
    db.commit()
    db.close()

    r = client.post("/api/dm/summon-hp",
                    json={"character_id": char_id, "summon_idx": 0, "hp_current": 3},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    db = sqlite3.connect(seeded_db["db_path"])
    stored = _j.loads(db.execute("SELECT summons FROM characters WHERE id=?", (char_id,)).fetchone()[0])
    db.close()
    assert stored[0]["hp_current"] == 3, "the tracker wounding a summon must stick"

    assert client.post("/api/dm/summon-hp",
                       json={"character_id": char_id, "summon_idx": 9, "hp_current": 3},
                       headers=auth_headers).status_code == 400


def test_the_tracker_no_longer_posts_to_a_route_that_does_not_exist():
    """The bug this slice exists for: `/api/update` was never a route, so every damage the DM
    applied to a player in the tracker was a silent 404 that the client's catch swallowed."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    js = (repo / "static" / "dm_tools.js").read_text()
    assert "'/api/update'" not in js, "the dead endpoint is back"
    assert "postDmHp('/api/dm/character-hp'" in js
    assert "postDmHp('/api/dm/summon-hp'" in js
    # and a failure must be LOUD: that silence is what hid this for so long
    assert "function reportCombatError(" in js and "reportCombatError('HP not saved: '" in js
    # every URL the tracker posts to must exist in the app
    import re
    urls = set(re.findall(r"postDmHp\('([^']+)'", js))
    routes_src = (repo / "routes").glob("**/*.py")
    backend = "\n".join(f.read_text() for f in routes_src)
    for url in urls:
        assert url.strip('"') in backend or url in backend, f"{url} is not a route"
