"""The audit trail, read back: a character's history as JSON.

The trail is written by `services.eventlog.log_event` from every save path.
This module is the read side: one character, one campaign, the rows that
describe what changed and when.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.db import get_db

router = APIRouter()

# A history page that returns everything is a page nobody reads. The cap keeps
# the JSON answer small enough to render on a phone.
MAX_LIMIT = 2000
DEFAULT_LIMIT = 500


def _loads(value):
    """A stored JSON column back as an object, never as a string."""
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, (dict, list)) else {}


def event_rows(db, where_sql: str, params: tuple, kind: str = "", limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Rows from event_log, oldest first, as JSON-ready dicts."""
    sql = ("SELECT id, campaign_id, kind, actor, target, before, after, ts "
           f"FROM event_log WHERE {where_sql}")
    args = list(params)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY id ASC LIMIT ?"
    args.append(int(limit))
    out = []
    for r in db.execute(sql, tuple(args)):
        out.append({
            "id": int(r["id"]),
            "campaign_id": int(r["campaign_id"]),
            "kind": r["kind"],
            "actor": r["actor"] or "",
            "target": r["target"] or "",
            "before": _loads(r["before"]),
            "after": _loads(r["after"]),
            "ts": r["ts"],
        })
    return out


@router.get("/api/character/{char_id}/history.json", response_class=JSONResponse)
def character_history(char_id: int, request: Request, limit: int = DEFAULT_LIMIT,
                      kind: str = ""):
    """Every logged change for one character, oldest first.

    The character's own events are the rows whose target is its name or
    `char:<id>` — the two forms the write paths use.
    """
    from core.security import require_user
    from services.eventlog import character_campaign_id

    # An anonymous caller gets the same answer as a stranger: 404, not a
    # redirect to the login page. A JSON endpoint that answers with HTML is a
    # surprise for every client that reads it.
    try:
        user = require_user(request)
    except HTTPException:
        user = None
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, name, user_id, shared FROM characters WHERE id = ?", (char_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"error": "character not found"}, status_code=404)
        # The audit trail is the character's own history: its owner, anyone the
        # owner shared it with, an admin, and a DM whose campaign lists it.
        if not _may_read(db, user, row):
            return JSONResponse({"error": "character not found"}, status_code=404)
        limit = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
        name = row["name"] or ""
        events = event_rows(
            db,
            "(target = ? OR target = ?)",
            (name, f"char:{int(row['id'])}"),
            kind=kind,
            limit=limit,
        )
        return JSONResponse({
            "character_id": int(row["id"]),
            "name": name,
            "campaign_id": character_campaign_id(db, int(row["id"])),
            "count": len(events),
            "events": events,
        })
    finally:
        db.close()


def _may_read(db, user: dict | None, row) -> bool:
    """True when `user` may see this character's audit trail."""
    from core.security import _is_admin, _user_dms_character

    if user is None:
        # Nobody signed in: only a character its owner published is readable.
        return int(row["shared"] or 0) == 1
    if _is_admin(user):
        return True
    if int(row["user_id"]) == int(user["id"]):
        return True
    if int(row["shared"] or 0) == 1:
        return True
    try:
        return _user_dms_character(db, user, int(row["id"]))
    except Exception:  # noqa: BLE001 - a failed check must not open the door
        return False
