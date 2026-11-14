"""Encounter and campaign table widgets."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import json
from core.security import _require_owned, require_user
from core.db import _json_list, get_db

router = APIRouter()


# ── Table widgets (counters and timers kept next to the initiative order) ──────────────
# Atlas VTT keeps small widgets — a "fear" counter, a round timer — beside the tracker so the
# DM never leaves the fight to update them. They are plain data here: an encounter's own
# widgets, and a campaign's, which the DM sees on that campaign's page. (Encounters carry no
# campaign_id column, so the two scopes stay separate rather than guessing a link.)
WIDGET_TYPES = {"counter", "timer"}

WIDGET_MAX = 24

def _clean_widgets(raw) -> list[dict]:
    """Validate a widget list from the client.

    Anything unrecognised is dropped rather than stored: widgets are re-rendered on every
    fight, so one bad entry would be re-sent forever. Values are clamped, not rejected — a
    counter pinned at 999 is a stuck counter, not an attack.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:WIDGET_MAX]:
        if not isinstance(item, dict):
            continue
        wtype = str(item.get("type") or "counter").lower()
        if wtype not in WIDGET_TYPES:
            wtype = "counter"
        try:
            value = int(item.get("value") or 0)
        except (TypeError, ValueError):
            value = 0
        try:
            seconds = int(item.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        out.append({
            "id": str(item.get("id") or "")[:40],
            "name": str(item.get("name") or "Widget")[:24],
            "icon": str(item.get("icon") or "⭐")[:4],
            "type": wtype,
            "value": max(-999, min(999, value)),
            "seconds": max(0, min(24 * 3600, seconds)),
            "color": str(item.get("color") or "")[:16],
        })
    return out

def _widgets_of(db, table: str, row_id: int) -> list[dict]:
    row = db.execute(f"SELECT widgets FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return _clean_widgets(_json_list(row["widgets"])) if row else []

def _save_widgets(db, table: str, row_id: int, data: dict) -> list[dict]:
    widgets = _clean_widgets(data.get("widgets"))
    db.execute(f"UPDATE {table} SET widgets = ? WHERE id = ?", (json.dumps(widgets), row_id))
    db.commit()
    return widgets

@router.get("/api/dm/encounter/{enc_id}/widgets", response_class=JSONResponse)
def dm_encounter_widgets(enc_id: int, request: Request):
    """The widgets on one encounter."""
    user = require_user(request)
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_encounters", enc_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse({"widgets": _widgets_of(db, "dm_encounters", enc_id)})
    finally:
        db.close()

@router.post("/api/dm/encounter/{enc_id}/widgets", response_class=JSONResponse)
async def dm_save_encounter_widgets(enc_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_encounters", enc_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        widgets = _save_widgets(db, "dm_encounters", enc_id, data)
        return JSONResponse({"ok": True, "widgets": widgets, "count": len(widgets)})
    finally:
        db.close()

@router.get("/api/dm/campaign/{camp_id}/widgets", response_class=JSONResponse)
def dm_campaign_widgets(camp_id: int, request: Request):
    """The widgets on one campaign."""
    user = require_user(request)
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_campaigns", camp_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse({"widgets": _widgets_of(db, "dm_campaigns", camp_id)})
    finally:
        db.close()

@router.post("/api/dm/campaign/{camp_id}/widgets", response_class=JSONResponse)
async def dm_save_campaign_widgets(camp_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    try:
        if not _require_owned(db, user, "dm_campaigns", camp_id):
            return JSONResponse({"error": "Not found"}, status_code=404)
        widgets = _save_widgets(db, "dm_campaigns", camp_id, data)
        return JSONResponse({"ok": True, "widgets": widgets, "count": len(widgets)})
    finally:
        db.close()
