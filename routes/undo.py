"""Undo endpoints: what the last action was, and putting it back.

Player-facing writes are unaffected: this is scoped to the user who made the change, and the routes
that call it already authorised them.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main import get_db, require_user

router = APIRouter()


def _publish(restored: list) -> None:
    """Tell every screen that shows one of these characters, through the channel the rest of the
    app already uses. A silent undo would leave the player's sheet showing the damage that the DM
    just took back."""
    from routes.maps import poke_maps_for_character
    from routes.table import publish_character_state

    for item in restored or []:
        cid = item.get("character_id")
        if not cid:
            continue
        values = item.get("values") or {}
        # The numbers go with the event (a client should not need a second read), but not through
        # publish_hp_change: that path computes damage and a concentration DC, and an undo is the
        # removal of a change, not a hit.
        payload = {k: v for k, v in values.items() if k in ("hp_current", "hp_max", "temp_hp")}
        try:
            publish_character_state(cid, kind="hp" if payload else "sheet", **payload)
        except Exception:
            pass
        try:
            poke_maps_for_character(cid)
        except Exception:
            pass


@router.get("/api/undo/peek", response_class=JSONResponse)
async def undo_peek(request: Request):
    """What would be undone, so the button can name it instead of saying "undo"."""
    from services.undo import peek

    user = require_user(request)
    db = get_db()
    try:
        entry = peek(db, user["id"])
        return JSONResponse({"ok": True, "entry": entry})
    finally:
        db.close()


@router.post("/api/undo", response_class=JSONResponse)
async def do_undo(request: Request):
    """Put the newest action back.

    409 when the row has moved on since (the honest answer: undoing would overwrite newer work) and
    404 when there is nothing to undo. Neither is an error worth a 500.
    """
    from services.undo import undo

    user = require_user(request)
    db = get_db()
    try:
        result = undo(db, user["id"])
        meta = result.get("meta") or {}
        if result.get("ok") and result.get("kind") == "xp" and meta.get("xp_log_id"):
            # The ledger documents what happened, so it keeps the row — and says it was put back.
            # A ledger that contradicts the totals is worse than no ledger at all.
            db.execute("UPDATE dm_xp_log SET reversed = 1 WHERE id = ?", (meta["xp_log_id"],))
            db.commit()
    finally:
        db.close()

    if result.get("ok"):
        _publish(result.get("restored") or [])
        return JSONResponse(result)
    reason = result.get("reason")
    status = 404 if reason == "empty" else (409 if reason == "stale" else 400)
    return JSONResponse(result, status_code=status)
