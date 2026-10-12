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
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from main import get_db, require_user, _require_owned, _render

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


def frame_for(payload: dict) -> str:
    """One SSE frame on the DEFAULT event name, so the client dispatches on `payload.kind`.

    Only rolls carry an `id:`. A state event with an id would set Last-Event-ID to a roll id
    that never existed, and the next reconnect would replay the whole feed.
    """
    body = json.dumps(payload, ensure_ascii=False)
    rid = payload.get("id") if payload.get("kind", "roll") == "roll" else None
    return f"{'id: %s\n' % rid if rid else ''}data: {body}\n\n"


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
        # Flush a first byte immediately. Without it the client stays in CONNECTING until the
        # first heartbeat, so `open` never fires on a quiet stream — and everything keyed off
        # "the stream is live" (the sheet demoting its poll, a status line) silently never runs.
        # `retry:` also tells EventSource how long to wait before reconnecting.
        yield "retry: 3000\n: ok\n\n"
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
            if event.get("kind") == "roll":
                if not include_hidden and event.get("hidden"):
                    continue
            # Everything else the campaign publishes (an hp change, say) is forwarded as-is:
            # dropping it here is how the sheet's HP silently stopped updating when its poll was
            # demoted. There is one stream per campaign and the client dispatches on kind.
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


# ── the player key ──────────────────────────────────────────────────────────────────

def campaign_by_key(db, key) -> dict | None:
    """The campaign a player key belongs to, or None.

    A blank key is NEVER a match: every campaign starts with '' and a naive comparison
    would hand the whole table to anyone who asked with no key at all.
    """
    wanted = (key or "").strip()
    if not wanted:
        return None
    row = db.execute("SELECT * FROM dm_campaigns WHERE player_key = ?", (wanted,)).fetchone()
    if not row:
        return None
    row = dict(row)
    stored = (row.get("player_key") or "").strip()
    if not stored or not secrets.compare_digest(stored, wanted):
        return None
    return row


def roster_entries(camp: dict) -> list[dict]:
    """This campaign's roster as the picker shows it, alphabetically (services.text.alpha_key
    is the app's collation — `.lower()` sorts differently for names with punctuation)."""
    from services.text import alpha_key

    try:
        entries = json.loads(camp.get("characters") or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            char_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        out.append({"id": char_id, "name": str(entry.get("name") or "Unnamed")})
    out.sort(key=lambda r: alpha_key(r["name"]))
    return out


def roster_ids(camp: dict) -> set[int]:
    """Character ids on this campaign's roster.

    Read from the campaign's own `characters` JSON, not the `dm_campaign_characters`
    join table: that table is EMPTY on the live database while campaigns are full.
    """
    return {row["id"] for row in roster_entries(camp)}


def roster_name(camp: dict, character_id: int) -> str:
    for row in roster_entries(camp):
        if row["id"] == int(character_id):
            return row["name"]
    return ""


@router.get("/api/campaign/{camp_id}/player-key", response_class=JSONResponse)
async def campaign_player_key(camp_id: int, request: Request):
    """The DM's share key for the players' devices. Creates one on first ask."""
    user = require_user(request)
    db = get_db()
    try:
        camp = _require_owned(db, user, "dm_campaigns", camp_id)
        if not camp:
            return JSONResponse({"error": "Campaign not found"}, status_code=404)
        key = (camp.get("player_key") or "").strip()
        if not key:
            key = secrets.token_hex(16)
            db.execute("UPDATE dm_campaigns SET player_key = ? WHERE id = ?", (key, camp_id))
            db.commit()
        return JSONResponse({"ok": True, "player_key": key, "url": f"/table/{key}"})
    finally:
        db.close()


@router.post("/api/campaign/{camp_id}/player-key", response_class=JSONResponse)
async def campaign_player_key_set(camp_id: int, request: Request):
    """Rotate the key, or revoke it (send an empty key) to close the table's devices."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_campaigns", camp_id):
            return JSONResponse({"error": "Campaign not found"}, status_code=404)
        wanted = str(data.get("key") or "").strip()
        key = wanted[:64] if wanted else ""
        db.execute("UPDATE dm_campaigns SET player_key = ? WHERE id = ?", (key, camp_id))
        db.commit()
        return JSONResponse({"ok": True, "player_key": key, "url": f"/table/{key}" if key else ""})
    finally:
        db.close()


# ── the player surface ──────────────────────────────────────────────────────────────

# A player device is a browser that keeps a tab open; it is not a client to be trusted with
# throughput. 20 rolls in 10 seconds is far above table pace and stops a stuck loop.
PLAYER_ROLL_LIMIT = 20
PLAYER_ROLL_WINDOW = 10.0

_roll_times: dict[str, list[float]] = {}


def player_roll_allowed(key: str, *, now: float | None = None) -> bool:
    """Token bucket per key. Prunes as it goes, so the map cannot grow unbounded."""
    import time as _time

    now = _time.monotonic() if now is None else now
    recent = [t for t in _roll_times.get(key, []) if now - t < PLAYER_ROLL_WINDOW]
    if len(recent) >= PLAYER_ROLL_LIMIT:
        _roll_times[key] = recent
        return False
    recent.append(now)
    _roll_times[key] = recent
    return True


def key_still_valid(camp_id: int, key: str) -> bool:
    """Re-check a key against the row — the stream calls this so a rotation closes it."""
    db = get_db()
    try:
        row = db.execute("SELECT player_key FROM dm_campaigns WHERE id = ?", (camp_id,)).fetchone()
        if not row:
            return False
        stored = (row["player_key"] or "").strip()
        return bool(stored) and secrets.compare_digest(stored, (key or "").strip())
    finally:
        db.close()


@router.get("/api/table/{key}/stream")
async def table_stream(key: str, request: Request):
    """The players' live feed: hidden rolls are filtered out server-side."""
    from fastapi.responses import StreamingResponse

    db = get_db()
    try:
        camp = campaign_by_key(db, key)
        if not camp:
            return JSONResponse({"error": "Not found"}, status_code=404)
        camp_id = camp["id"]
    finally:
        db.close()

    try:
        last_id = int(request.headers.get("last-event-id") or 0)
    except (TypeError, ValueError):
        last_id = 0

    return StreamingResponse(
        stream_frames(camp_id, last_id=last_id, include_hidden=False,
                      keep_alive=lambda: key_still_valid(camp_id, key)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/api/table/{key}/roll", response_class=JSONResponse)
async def table_roll(key: str, request: Request):
    """A player rolls. The only write a key grants, and it must name a roster character."""
    from services import events

    data = await request.json()
    if not player_roll_allowed((key or "").strip()):
        return JSONResponse({"error": "Too many rolls — take a breath."}, status_code=429)

    db = get_db()
    try:
        camp = campaign_by_key(db, key)
        if not camp:
            return JSONResponse({"error": "Not found"}, status_code=404)
        camp_id = camp["id"]

        try:
            character_id = int(data.get("character_id") or 0)
        except (TypeError, ValueError):
            character_id = 0
        if not character_id:
            return JSONResponse({"error": "Pick a character first"}, status_code=400)
        if character_id not in roster_ids(camp):
            return JSONResponse({"error": "That character is not at this table"}, status_code=400)

        try:
            row = insert_roll(
                db,
                campaign_id=camp_id,
                expression=str(data.get("expression") or ""),
                results=data.get("results"),
                # A player roll is a player roll: neither the DM's name nor a hidden roll can
                # be claimed from this path, whatever the payload says.
                who="player",
                character_id=character_id,
                character_name=roster_name(camp, character_id),
                source="player-view",
                label=str(data.get("label") or ""),
                hidden=False,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        db.close()

    events.publish(camp_id, {"kind": "roll", **row})
    return JSONResponse({"ok": True, "roll": row})



@router.get("/table/{key}", response_class=HTMLResponse)
async def table_page(key: str, request: Request):
    """The players' surface: pick a character, roll, watch the same feed the DM sees.

    Authorised by the campaign key alone — this is the tablet nobody signs into. It can read
    the projected feed and post rolls, and nothing else.
    """
    db = get_db()
    try:
        camp = campaign_by_key(db, key)
        if not camp:
            raise HTTPException(status_code=404, detail="Not found")
        roster = roster_entries(camp)
        campaign_name = camp.get("name") or "Campaign"
    finally:
        db.close()

    return _render("table.html", request=request, campaign={"name": campaign_name},
                   roster=roster, key=key)


# ── the sheet's own view of the feed ────────────────────────────────────────────────
# A player holding a sheet is not the campaign's DM, so /api/campaign/{id}/stream is not
# theirs to open. Membership is provable from their own character, so these two routes
# authorise on the character and then project the campaign feed WITHOUT hidden rolls —
# exactly the players' view, reached from the screen they already have open.

def campaign_for_character(db, char_id: int) -> dict | None:
    """The campaign whose roster holds this character, if any.

    Mirrors /api/character/{id}/campaign: the join table first, then the campaign's own
    `characters` JSON, because the join table is EMPTY on the live database.
    """
    row = db.execute(
        "SELECT c.* FROM dm_campaigns c JOIN dm_campaign_characters cc ON cc.campaign_id = c.id "
        "WHERE cc.character_id = ?", (int(char_id),)).fetchone()
    if row:
        return dict(row)
    for camp in db.execute("SELECT * FROM dm_campaigns").fetchall():
        camp = dict(camp)
        if int(char_id) in roster_ids(camp):
            return camp
    return None


@router.get("/api/character/{char_id}/feed", response_class=JSONResponse)
async def character_feed(char_id: int, request: Request, limit: int = 50):
    """The last rolls at this character's table (hidden ones are not in this projection)."""
    user = require_user(request)
    db = get_db()
    try:
        if not _require_owned(db, user, "characters", char_id):
            return JSONResponse({"error": "Character not found"}, status_code=404)
        camp = campaign_for_character(db, char_id)
        if not camp:
            return JSONResponse({"rolls": [], "campaign": None})
        return JSONResponse({"rolls": recent_rolls(db, camp["id"], limit, include_hidden=False),
                             "campaign": {"id": camp["id"], "name": camp.get("name") or ""}})
    finally:
        db.close()


@router.get("/api/character/{char_id}/feed/stream")
async def character_feed_stream(char_id: int, request: Request):
    """The live feed for a character's table, authorised by the sheet's own owner."""
    from fastapi.responses import StreamingResponse

    user = require_user(request)
    db = get_db()
    try:
        if not _require_owned(db, user, "characters", char_id):
            return JSONResponse({"error": "Character not found"}, status_code=404)
        camp = campaign_for_character(db, char_id)
        if not camp:
            return JSONResponse({"error": "No campaign for this character"}, status_code=404)
        camp_id = camp["id"]
    finally:
        db.close()

    try:
        last_id = int(request.headers.get("last-event-id") or 0)
    except (TypeError, ValueError):
        last_id = 0

    return StreamingResponse(
        stream_frames(camp_id, last_id=last_id, include_hidden=False),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── state events ────────────────────────────────────────────────────────────────────
# The sheet used to poll every 2 s for its own HP, because the DM's combat tracker keeps
# player HP as a SNAPSHOT inside the encounter (combat_state.player_participants) and never
# writes characters.hp_current. What the poll actually catches is the character row changing
# underneath the open page — the player's own second tab, a rest, a level-up. That is what
# these events carry, so the timer can stop being the mechanism.

def concentration_dc(damage: int) -> int:
    """PHB p.203: DC 10 or half the damage taken, whichever is higher."""
    return max(10, int(damage) // 2)


def publish_hp_change(char_id: int, old_hp, new_hp, *, concentration: str = "") -> bool:
    """Warn the table about an HP write — and, when damage lands on a concentrating character, say
    what save it owes.

    The DC only means anything if someone can compute it, and the damage is the difference between
    the value before and after the write: that is knowledge only the write path has. Sending it
    with the event means the player's sheet can prompt with the right number, and the rules stay on
    one side of the wire (the client is not asked to know PHB p.203).
    """
    damage = 0
    try:
        if old_hp is not None and new_hp is not None:
            damage = max(0, int(old_hp) - int(new_hp))
    except (TypeError, ValueError):
        damage = 0

    fields = {"damage": damage}
    if damage and (concentration or "").strip():
        fields["concentration"] = concentration.strip()
        fields["concentration_dc"] = concentration_dc(damage)
    return publish_character_state(char_id, kind="hp", hp_current=new_hp, **fields)


def publish_character_state(char_id: int, *, kind: str = "hp", **fields) -> bool:
    """Tell the character's table that their sheet changed. No campaign means nobody listens.

    Two kinds, both "something on this sheet moved, go and read it": `hp` when hit points changed
    and `sheet` for everything else a tracker mirrors (conditions, summons, concentration). Clients
    refresh the same way for either — the kind is there so a listener can tell what to say out
    loud, not to decide whether to bother reading. Extra fields ride along for listeners that need
    the rules rather than just the news (`damage`, `concentration_dc`).
    """
    from services import events

    db = get_db()
    try:
        camp = campaign_for_character(db, char_id)
        if not camp:
            return False
        camp_id = camp["id"]
        name = roster_name(camp, char_id)
    finally:
        db.close()

    event = {"kind": kind, "character_id": int(char_id), "character_name": name}
    event.update({k: v for k, v in fields.items() if v is not None})
    events.publish(camp_id, event)
    return True
