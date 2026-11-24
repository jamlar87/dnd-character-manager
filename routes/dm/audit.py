"""The audit trail, read back: a campaign's timeline as JSON.

The trail is written by `services.eventlog.log_event` from every write path.
This module is the campaign-side read: one campaign, the rows that describe
what changed and when.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.db import get_db
from routes.characters.audit import DEFAULT_LIMIT, MAX_LIMIT, event_rows

router = APIRouter()


@router.get("/api/campaign/{camp_id}/timeline.json", response_class=JSONResponse)
def campaign_timeline(camp_id: int, request: Request, limit: int = DEFAULT_LIMIT,
                      kind: str = ""):
    """Every logged change for one campaign, oldest first."""
    from core.security import _is_admin, require_user

    # An anonymous caller gets a JSON 404 rather than a redirect to the login
    # page: this endpoint answers JSON, and it always answers JSON.
    try:
        user = require_user(request)
    except HTTPException:
        user = None
    db = get_db()
    try:
        row = db.execute("SELECT id, user_id FROM dm_campaigns WHERE id = ?", (camp_id,)).fetchone()
        if not row:
            return JSONResponse({"error": "campaign not found"}, status_code=404)
        if user is None:
            return JSONResponse({"error": "campaign not found"}, status_code=404)
        if not _is_admin(user) and int(row["user_id"]) != int(user["id"]):
            return JSONResponse({"error": "campaign not found"}, status_code=404)
        limit = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
        events = event_rows(db, "campaign_id = ?", (int(camp_id),), kind=kind, limit=limit)
        return JSONResponse({
            "campaign_id": int(camp_id),
            "count": len(events),
            "events": events,
        })
    finally:
        db.close()
