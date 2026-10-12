"""The table channel: a campaign-scoped roll feed shared by the DM and the players.

Two ways in, deliberately asymmetric:

* the DM's own session (`/api/campaign/{id}/…`) — full feed, may mark a roll hidden;
* a campaign player key (`/api/table/{key}/…`) — roll only, and never sees a hidden roll.

`roll_log` is the durable history and the SSE streams are its fast path, so anything that
reconnects replays the gap instead of depending on having been connected. See
`.hermes/plans/2026-10-12_table-channel.md`.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main import get_db, require_user, _require_owned

router = APIRouter()

# How many rolls a campaign keeps. The feed is a table aid, not an archive: 500 is several
# sessions of play, and pruning keeps the read on the hot path an index scan.
FEED_LIMIT = 500

# The only thing a roll may be. Matched against the WHOLE expression, so `1d20; --` is prose,
# not dice. Bounds mirror static/dice.js (count <= 100, sides <= 1000).
ROLL_RE = re.compile(r"^(\d{0,3})d(\d{1,3})(?:\s*([+-])\s*(\d{1,3}))?$", re.IGNORECASE)

MAX_EXPRESSION_LEN = 32


def parse_expression(expression: str) -> tuple[int, int, int]:
    """(count, sides, modifier) for a dice expression, or ValueError.

    The single authority on what a roll may be: the client's own parse is a convenience for
    display, never trusted for what gets stored.
    """
    expr = (expression or "").strip()
    if not expr or len(expr) > MAX_EXPRESSION_LEN:
        raise ValueError("not a dice expression")
    m = ROLL_RE.match(expr)
    if not m:
        raise ValueError("not a dice expression")
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    mod = 0
    if m.group(3) and m.group(4):
        mod = (1 if m.group(3) == "+" else -1) * int(m.group(4))
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        raise ValueError("dice out of range")
    return count, sides, mod


def _clean_results(results, count: int, sides: int) -> list[int]:
    """The dice the client says it rolled: right number of them, each a face that exists."""
    if not isinstance(results, (list, tuple)):
        raise ValueError("results must be a list")
    if len(results) != count:
        raise ValueError("results do not match the expression")
    faces = []
    for value in results:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("results must be integers")
        if value < 1 or value > sides:
            raise ValueError("die face out of range")
        faces.append(value)
    return faces


def row_to_dict(row) -> dict:
    """A roll_log row as the feed renders it: results decoded, `hidden` a real bool."""
    data = dict(row)
    try:
        data["results"] = json.loads(data.get("results") or "[]")
    except (TypeError, ValueError):
        data["results"] = []
    data["hidden"] = bool(data.get("hidden"))
    return data


def insert_roll(db, *, campaign_id: int, expression: str, results, who: str = "player",
                character_id=None, character_name: str = "", source: str = "",
                label: str = "", hidden: bool = False) -> dict:
    """Validate, store and prune one roll. Returns the stored row.

    The total is computed HERE from the claimed dice — a client-sent total is not accepted,
    so the feed can never show a total its own dice do not add up to.
    """
    count, sides, mod = parse_expression(expression)
    faces = _clean_results(results, count, sides)
    total = sum(faces) + mod

    if character_id and not character_name:
        row = db.execute("SELECT name FROM characters WHERE id = ?", (int(character_id),)).fetchone()
        if row:
            character_name = row["name"] or ""
        else:
            character_id = None

    cur = db.execute(
        "INSERT INTO roll_log (campaign_id, character_id, character_name, who, source, label,"
        " expression, results, total, hidden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(campaign_id), character_id, (character_name or "")[:80], who[:16], (source or "")[:32],
         (label or "")[:120], expression.strip(), json.dumps(faces), total, 1 if hidden else 0),
    )
    # Prune in the same transaction as the insert: the feed's bound is a property of the write,
    # not of a maintenance job somebody has to remember to run.
    db.execute(
        "DELETE FROM roll_log WHERE campaign_id = ? AND id NOT IN "
        "(SELECT id FROM roll_log WHERE campaign_id = ? ORDER BY id DESC LIMIT ?)",
        (int(campaign_id), int(campaign_id), FEED_LIMIT),
    )
    db.commit()
    stored = db.execute("SELECT * FROM roll_log WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row_to_dict(stored)


def recent_rolls(db, campaign_id: int, limit: int = 50, *, include_hidden: bool = True) -> list[dict]:
    """Newest first. `include_hidden=False` is the player projection."""
    limit = max(1, min(int(limit or 50), FEED_LIMIT))
    sql = "SELECT * FROM roll_log WHERE campaign_id = ?"
    params: list = [int(campaign_id)]
    if not include_hidden:
        sql += " AND hidden = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [row_to_dict(r) for r in db.execute(sql, params).fetchall()]


# ── the stream ──────────────────────────────────────────────────────────────────────

# A reconnect should not dump the whole feed at a client that missed a moment. Anything
# beyond this many missed rolls means "reload the page", which reads /rolls.
REPLAY_MAX = 200

HEARTBEAT_SECONDS = 20.0


def frame_for(roll: dict) -> str:
    """One SSE frame. `data` is JSON built by json.dumps, so a label can never break the frame."""
    payload = json.dumps({**roll, "kind": "roll"}, ensure_ascii=False)
    return f"id: {roll.get('id', 0)}\nevent: roll\ndata: {payload}\n\n"


def replay_gap(db, campaign_id: int, last_id: int, *, include_hidden: bool = True) -> list[dict]:
    """The rolls a reconnecting client missed, oldest first."""
    if not last_id or int(last_id) <= 0:
        return []
    sql = "SELECT * FROM roll_log WHERE campaign_id = ? AND id > ?"
    params: list = [int(campaign_id), int(last_id)]
    if not include_hidden:
        sql += " AND hidden = 0"
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(REPLAY_MAX)
    return [row_to_dict(r) for r in db.execute(sql, params).fetchall()]


async def stream_frames(campaign_id: int, *, last_id: int = 0, heartbeat: float = HEARTBEAT_SECONDS,
                        include_hidden: bool = True, keep_alive=None):
    """Yield SSE frames for one campaign: the missed gap first, then live events.

    `keep_alive` is an optional callable returning False to stop the stream — the player
    stream uses it to notice that its key was revoked while the connection was open.
    """
    import asyncio

    from services import events

    db = get_db()
    try:
        gap = replay_gap(db, campaign_id, last_id, include_hidden=include_hidden)
    finally:
        db.close()

    q = events.subscribe(campaign_id)
    try:
        for roll in gap:
            yield frame_for(roll)
        while True:
            if keep_alive is not None and not keep_alive():
                return
            try:
                event = await asyncio.wait_for(q.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                # A comment frame keeps intermediaries (and Cloudflare's 100s idle timer) happy.
                yield ": hb\n\n"
                continue
            if event.get("kind") != "roll":
                continue
            if not include_hidden and event.get("hidden"):
                continue
            yield frame_for(event)
    finally:
        events.unsubscribe(campaign_id, q)


@router.get("/api/campaign/{camp_id}/stream")
async def campaign_stream(camp_id: int, request: Request):
    """The DM's live feed for one campaign (rolls and, later, state pokes)."""
    from fastapi.responses import StreamingResponse

    user = require_user(request)
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_campaigns", camp_id):
            return JSONResponse({"error": "Campaign not found"}, status_code=404)
    finally:
        db.close()

    try:
        last_id = int(request.headers.get("last-event-id") or 0)
    except (TypeError, ValueError):
        last_id = 0

    return StreamingResponse(
        stream_frames(camp_id, last_id=last_id),
        media_type="text/event-stream",
        headers={
            # no-transform matters: a compressing proxy would buffer the stream and the feed
            # would arrive in bursts.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )



# ── DM side ─────────────────────────────────────────────────────────────────────────

@router.post("/api/campaign/{camp_id}/roll", response_class=JSONResponse)
async def campaign_roll(camp_id: int, request: Request):
    """The DM rolls into the feed. Hidden rolls are stored and withheld from players."""
    from services import events

    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        camp = _require_owned(db, user, "dm_campaigns", camp_id)
        if not camp:
            return JSONResponse({"error": "Campaign not found"}, status_code=404)
        try:
            row = insert_roll(
                db,
                campaign_id=camp_id,
                expression=str(data.get("expression") or ""),
                results=data.get("results"),
                who="dm",
                character_id=data.get("character_id"),
                character_name=str(data.get("character_name") or ""),
                source=str(data.get("source") or "dm-tools"),
                label=str(data.get("label") or ""),
                hidden=bool(data.get("hidden")),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        db.close()
    # Published only after the commit: a subscriber must never be shown a roll a crash
    # could still have lost.
    events.publish(camp_id, {"kind": "roll", **row})
    return JSONResponse({"ok": True, "roll": row})


@router.get("/api/campaign/{camp_id}/rolls", response_class=JSONResponse)
async def campaign_rolls(camp_id: int, request: Request, limit: int = 50):
    """Feed history — what a fresh page or a resynced client reads."""
    user = require_user(request)
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_campaigns", camp_id):
            return JSONResponse({"error": "Campaign not found"}, status_code=404)
        return JSONResponse({"rolls": recent_rolls(db, camp_id, limit)})
    finally:
        db.close()
