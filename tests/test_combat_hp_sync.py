"""Combat HP sync: the per-tick read must not run on the event loop.

/api/sync-combat-hp fires from every open sheet on every combat tick. The sqlite
queries and xp_progress math are blocking, so they are handed to a worker thread;
these tests pin that contract and the payload it returns.
"""

import asyncio
import json
import sqlite3
import threading
import time
from typing import Any


class _FakeRequest:
    """Only what the handler touches: JSON body (require_user is patched out)."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _req(payload) -> Any:
    """The handler only needs cookies-free JSON; require_user is patched out."""
    return _FakeRequest(payload)


def _body(resp) -> dict:
    raw = resp.body
    text = raw if isinstance(raw, str) else bytes(raw).decode()
    return json.loads(text)


def _character(db_path, user_id=1, name="Rowan", **overrides):
    cols = {"user_id": user_id, "name": name, "race": "Human", "class_name": "Fighter",
            "level": 3, "hp_current": 21, "hp_max": 27, "xp": 2700,
            "summons": json.dumps([{"name": "Wolf", "hp": 11}]),
            "conditions": json.dumps([{"name": "Prone"}])}
    cols.update(overrides)
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            f"INSERT INTO characters ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            tuple(cols.values()),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def test_the_blocking_read_runs_off_the_event_loop_thread(seeded_db, monkeypatch):
    """A slow read must not stop the loop: the heartbeat keeps ticking meanwhile."""
    from routes.characters import sheet as sheet_routes

    char_id = _character(seeded_db["db_path"])
    monkeypatch.setattr(sheet_routes, "require_user", lambda request: {"id": 1})

    seen = {}
    real_impl = sheet_routes._sync_combat_hp_impl

    def slow_impl(user, char_ids):
        seen["thread"] = threading.get_ident()
        time.sleep(0.05)  # stand-in for the sqlite round trips
        return real_impl(user, char_ids)

    monkeypatch.setattr(sheet_routes, "_sync_combat_hp_impl", slow_impl)

    ticks = 0

    async def run():
        nonlocal ticks

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        try:
            return await sheet_routes.sync_combat_hp(_req({"char_ids": [char_id]}))
        finally:
            hb.cancel()

    loop_thread = threading.get_ident()
    resp = asyncio.run(run())

    assert seen["thread"] != loop_thread, "the reader still ran on the event loop thread"
    assert ticks >= 5, f"event loop stalled during the read (only {ticks} heartbeats)"

    body = _body(resp)
    assert body["characters"][str(char_id)]["hp_current"] == 21


def test_payload_and_envelope_are_unchanged(seeded_db, monkeypatch):
    """Keys the sheet already reads: name, hp, summons, conditions, xp/level/progress."""
    from routes.characters import sheet as sheet_routes

    char_id = _character(seeded_db["db_path"])
    foreign_id = _character(seeded_db["db_path"], user_id=2, name="NotMine")
    monkeypatch.setattr(sheet_routes, "require_user", lambda request: {"id": 1})

    resp = asyncio.run(sheet_routes.sync_combat_hp(
        _req({"char_ids": [char_id, foreign_id, 999999]})))
    body = _body(resp)

    assert set(body) == {"characters"}
    assert set(body["characters"]) == {str(char_id)}, "only owned rows may come back"

    info = body["characters"][str(char_id)]
    assert set(info) == {"name", "hp_current", "hp_max", "summons", "conditions",
                         "xp", "level", "progress"}
    assert info["name"] == "Rowan"
    assert info["hp_max"] == 27
    assert info["summons"] == [{"name": "Wolf", "hp": 11}]
    assert info["conditions"] == [{"name": "Prone"}]
    assert info["xp"] == 2700 and info["level"] == 3
    assert info["progress"]["ready"] is True and info["progress"]["next_at"] == 6500


def test_empty_ids_short_circuit_without_touching_the_reader(seeded_db, monkeypatch):
    from routes.characters import sheet as sheet_routes

    monkeypatch.setattr(sheet_routes, "require_user", lambda request: {"id": 1})

    def boom(user, char_ids):
        raise AssertionError("the reader must not run for an empty tick")

    monkeypatch.setattr(sheet_routes, "_sync_combat_hp_impl", boom)

    resp = asyncio.run(sheet_routes.sync_combat_hp(_req({"char_ids": []})))
    assert _body(resp) == {"characters": {}}
