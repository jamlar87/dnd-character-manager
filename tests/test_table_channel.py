"""Table channel: shared roll feed + live push.

Tests are grouped by the slice they came from. The live stream itself is proven against a
real HTTP client in the slice-1 verification (TestClient streaming cannot prove delivery
timing or the edge behaviour), so what lives here is the contract: schema, bus, write path,
projection and scope.
"""

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
        frames = _drain(stream_frames(camp_id, last_id=first["id"], heartbeat=0.05), 1)
    finally:
        db.close()

    payload = _json.loads(_data_of(frames[0]))
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
        frames = _drain(stream_frames(camp_id, last_id=0, heartbeat=0.05), 1)
    finally:
        db.close()

    assert frames[0].strip() == ": hb"     # the first thing out is a heartbeat


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

    async def _first_frame():
        async for chunk in resp.body_iterator:
            return chunk
        return ""

    frame = asyncio.run(_first_frame())
    assert _json.loads(_data_of(frame))["id"] == second["id"]



