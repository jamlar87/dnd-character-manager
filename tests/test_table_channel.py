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
