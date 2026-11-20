"""Live-session JSON endpoints: attacks, summons, conditions, HP sync,
charge tracking, portraits, delete and share.

Split out of the single-module sheet.py; behaviour and URLs are unchanged.
"""

import base64
import json
import time
from datetime import datetime

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from core.db import get_db
from core.features import _build_character_attacks, _build_charged_item_attacks, _resolve_item_key
from core.security import _require_owned, require_user
from routes.characters.helpers import load_character

from .update import _publish_change

router = APIRouter()


# ── Routes: Live Session API ───────────────────────────────────────────────

@router.get("/api/character/{char_id}/attacks", response_class=JSONResponse)
def get_attacks(char_id: int, request: Request):
    """Return current weapon attacks for Actions tab refresh."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    db.close()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    char = dict(row)
    # Parse JSON fields
    for field in ["inventory", "equipped", "weapon_proficiencies", "save_proficiencies",
                  "attacks_data", "feature_data", "skills", "tool_proficiencies",
                  "armor_proficiencies", "languages", "features", "damage_resistances",
                  "damage_immunities", "damage_vulnerabilities", "condition_immunities",
                  "attuned_items"]:
        if isinstance(char.get(field), str):
            try: char[field] = json.loads(char[field])
            except: pass
    attacks = _build_character_attacks(char)
    return JSONResponse({"attacks": attacks})

@router.get("/api/character/{char_id}/summons", response_class=JSONResponse)
def get_summons(char_id: int, request: Request):
    """Return character's active summons for combat tab integration."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    db.close()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        summons = json.loads(row["summons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        summons = []
    return JSONResponse({"summons": summons, "char_name": row["name"]})

@router.post("/api/character/{char_id}/summons", response_class=JSONResponse)
async def create_summon(char_id: int, request: Request):
    """Create a new summon for a character. Returns the created summon object."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        summons = json.loads(row["summons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        summons = []

    summon = {
        "id": "summon_" + str(int(time.time() * 1000)),
        "name": data.get("name", "Unnamed"),
        "form": data.get("form", ""),
        "category": data.get("category", "custom"),
        "source": data.get("source", "custom"),
        "ac": data.get("ac", 10),
        "hp_max": data.get("hp_max", 1),
        "hp_current": data.get("hp_current", data.get("hp_max", 1)),
        "size": data.get("size", "Medium"),
        "speed": data.get("speed", "30 ft."),
        "stats": data.get("stats", {}),
        "features": data.get("features", []),
        "attacks": data.get("attacks", []),
        "skills": data.get("skills", ""),
        "senses": data.get("senses", ""),
        "hp_note": data.get("hp_note", ""),
    }
    summons.append(summon)
    db.execute("UPDATE characters SET summons=? WHERE id=? AND user_id=?",
               (json.dumps(summons), char_id, user["id"]))
    db.commit()
    db.close()
    _publish_change(char_id, "sheet")            # a new summon appears in the DM's tracker
    return JSONResponse({"summon": summon, "total": len(summons)})

@router.post("/api/character/{char_id}/update-summon-hp", response_class=JSONResponse)
async def update_summon_hp(char_id: int, request: Request):
    """Update a single summon's HP by index. Used by combat page to sync back."""
    user = require_user(request)
    data = await request.json()
    idx = data.get("summon_idx")
    hp = data.get("hp_current")
    if idx is None or hp is None:
        return JSONResponse({"error": "Missing summon_idx or hp_current"}, status_code=400)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        summons = json.loads(row["summons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        summons = []
    if idx < 0 or idx >= len(summons):
        db.close()
        return JSONResponse({"error": "Invalid summon index"}, status_code=400)
    summons[idx]["hp_current"] = max(0, hp)
    db.execute("UPDATE characters SET summons=? WHERE id=? AND user_id=?",
               (json.dumps(summons), char_id, user["id"]))
    db.commit()
    db.close()
    _publish_change(char_id, "sheet")
    return JSONResponse({"ok": True, "hp_current": summons[idx]["hp_current"]})

# ── Conditions (CRUD) ─────────────────────────────────────────────
@router.get("/api/character/{char_id}/conditions", response_class=JSONResponse)
def get_conditions(char_id: int, request: Request):
    """Get active conditions for a character."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    db.close()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        conditions = json.loads(row["conditions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        conditions = []
    return JSONResponse({"conditions": conditions, "char_name": row["name"]})

@router.post("/api/character/{char_id}/concentration", response_class=JSONResponse)
async def set_concentration(char_id: int, request: Request):
    """What this character is concentrating on — one at a time, or clear it with an empty spell.

    Kept as state rather than a free-text condition because the rules act on it: damage forces a
    CON save whose DC depends on the damage, and losing it ends the spell.
    """
    user = require_user(request)
    data = await request.json()
    spell = str(data.get("spell") or "").strip()[:120]
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    db.execute("UPDATE characters SET concentration=? WHERE id=? AND user_id=?",
               (spell, char_id, user["id"]))
    db.commit()
    db.close()
    _publish_change(char_id, "sheet", concentration=spell or None)
    return JSONResponse({"ok": True, "concentration": spell})


def _record_conditions(db, user_id, char_id, before_json, after_json, what: str) -> None:
    """Conditions move as a whole blob, so that is what undo restores. Removing the wrong one is a
    two-second mistake at a table and there was no way back from it."""
    from services.undo import record_safe

    record_safe(db, user_id, "condition", what,
                [{"character_id": char_id,
                  "before": {"conditions": before_json},
                  "after": {"conditions": after_json}}])


@router.post("/api/character/{char_id}/conditions", response_class=JSONResponse)
async def add_condition(char_id: int, request: Request):
    """Add a condition to a character."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Condition name required"}, status_code=400)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        conditions = json.loads(row["conditions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        conditions = []
    # Don't add duplicate
    existing = [c for c in conditions if c.get("name","").lower() == name.lower()]
    if existing:
        db.close()
        return JSONResponse({"conditions": conditions, "duplicate": True})
    condition = {
        "name": name,
        "description": data.get("description", ""),
        "source": data.get("source", ""),
        "applied_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    conditions.append(condition)
    before_conditions = row["conditions"] or "[]"
    db.execute("UPDATE characters SET conditions=? WHERE id=? AND user_id=?",
               (json.dumps(conditions), char_id, user["id"]))
    _record_conditions(db, user["id"], char_id, before_conditions, json.dumps(conditions),
                       f"add {name}")
    db.commit()
    db.close()
    _publish_change(char_id, "sheet")
    return JSONResponse({"conditions": conditions, "added": condition})

@router.delete("/api/character/{char_id}/conditions", response_class=JSONResponse)
async def remove_condition(char_id: int, request: Request):
    """Remove a condition by name."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Condition name required"}, status_code=400)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        conditions = json.loads(row["conditions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        conditions = []
    conditions = [c for c in conditions if c.get("name","").lower() != name.lower()]
    before_conditions = row["conditions"] or "[]"
    db.execute("UPDATE characters SET conditions=? WHERE id=? AND user_id=?",
               (json.dumps(conditions), char_id, user["id"]))
    _record_conditions(db, user["id"], char_id, before_conditions, json.dumps(conditions),
                       f"remove {name}")
    db.commit()
    db.close()
    _publish_change(char_id, "sheet")
    return JSONResponse({"conditions": conditions, "removed": name})


def _sync_combat_hp_impl(user: dict, char_ids: list) -> dict:
    """Blocking half of /api/sync-combat-hp — runs in a worker thread so the
    sqlite reads + xp_progress math never stall the event loop."""
    db = get_db()
    result = {}
    from services.leveling import xp_progress

    try:
        for cid in char_ids:
            row = db.execute(
                "SELECT name, hp_current, hp_max, summons, conditions, xp, level FROM characters "
                "WHERE id=? AND user_id=?",
                (cid, user["id"])
            ).fetchone()
            if not row:
                continue
            try:
                summons = json.loads(row["summons"] or "[]")
            except (json.JSONDecodeError, TypeError):
                summons = []
            try:
                conditions = json.loads(row["conditions"] or "[]")
            except (json.JSONDecodeError, TypeError):
                conditions = []
            result[str(cid)] = {
                "name": row["name"],
                "hp_current": row["hp_current"],
                "hp_max": row["hp_max"],
                "summons": summons,
                "conditions": conditions,
                # XP rides with the same read the sheet already makes when the table moves: an award
                # published an event, and a client that could only re-read HP would sit on a stale
                # total until the page was reloaded.
                "xp": row["xp"] or 0,
                "level": row["level"] or 1,
                "progress": xp_progress(row["xp"], row["level"]),
            }
    finally:
        db.close()
    return result


@router.post("/api/sync-combat-hp", response_class=JSONResponse)
async def sync_combat_hp(request: Request):
    """Batch fetch HP + summons for characters in combat. Takes {char_ids: [...]}."""
    user = require_user(request)
    data = await request.json()
    char_ids = data.get("char_ids", [])
    if not char_ids:
        return JSONResponse({"characters": {}})
    result = await anyio.to_thread.run_sync(_sync_combat_hp_impl, user, char_ids)
    return JSONResponse({"characters": result})

@router.post("/api/character/{char_id}/spend-charge", response_class=JSONResponse)
async def spend_charge(char_id: int, request: Request):
    """Spend one or more charges from an equipped charged item."""
    user = require_user(request)
    data = await request.json()
    item_name = (data.get("name") or "").strip()
    amount = int(data.get("amount", 1))
    if amount < 1:
        amount = 1
    if not item_name:
        return JSONResponse({"error": "No item name"}, status_code=400)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    equipped = json.loads(char.get("equipped", "[]") or "[]")

    updated = False
    for item in equipped:
        if not isinstance(item, dict):
            continue
        if item.get("name", "").strip().lower() == item_name.lower():
            used = item.get("charges_used", 0)
            # Cap amount: don't go below 0 available
            info = _resolve_item_key(item_name.lower())
            max_charges = info.get("charges", 0) if info else 0
            available = max(0, max_charges - used)
            spend = min(amount, available)
            if spend < 1:
                item["charges_used"] = max_charges  # fully deplete
            else:
                item["charges_used"] = used + spend
            updated = True
            break

    if not updated:
        db.close()
        return JSONResponse({"error": "Item not found or not equipped"}, status_code=404)

    db.execute("UPDATE characters SET equipped=? WHERE id=? AND user_id=?",
               (json.dumps(equipped), char_id, user["id"]))
    db.commit()
    db.close()

    # Return updated charged items list
    char["equipped"] = equipped
    charged = _build_charged_item_attacks(char)
    return JSONResponse({"charged_items": charged, "item_name": item_name})


@router.post("/api/character/{char_id}/reload-charge", response_class=JSONResponse)
async def reload_charge(char_id: int, request: Request):
    """Reset charges_used to 0 for an equipped item (reload a firearm magazine)."""
    user = require_user(request)
    data = await request.json()
    item_name = (data.get("name") or "").strip()
    if not item_name:
        return JSONResponse({"error": "No item name"}, status_code=400)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    equipped = json.loads(char.get("equipped", "[]") or "[]")

    updated = False
    for item in equipped:
        if not isinstance(item, dict):
            continue
        if item.get("name", "").strip().lower() == item_name.lower():
            item["charges_used"] = 0
            updated = True
            break

    if not updated:
        db.close()
        return JSONResponse({"error": "Item not found or not equipped"}, status_code=404)

    db.execute("UPDATE characters SET equipped=? WHERE id=? AND user_id=?",
               (json.dumps(equipped), char_id, user["id"]))
    db.commit()
    db.close()

    char["equipped"] = equipped
    charged = _build_charged_item_attacks(char)
    return JSONResponse({"charged_items": charged, "item_name": item_name})

@router.post("/api/character/{char_id}/delete", response_class=JSONResponse)
def delete_character(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    # Child rows first — the delete used to touch only `characters`, leaving
    # orphaned character_spells (live DB had 10 rows for a long-gone character),
    # relationships and campaign-membership rows behind.
    db.execute("DELETE FROM character_spells WHERE character_id = ?", (char_id,))
    db.execute("DELETE FROM character_relationships WHERE character_id = ?", (char_id,))
    db.execute("DELETE FROM dm_campaign_characters WHERE character_id = ?", (char_id,))
    db.execute("DELETE FROM characters WHERE id = ?", (char_id,))

    # DM campaign rosters live in the dm_campaigns.characters JSON blob — drop
    # the id there too, or the roster keeps listing a character that is gone.
    for camp in db.execute("SELECT id, characters FROM dm_campaigns").fetchall():
        try:
            roster = json.loads(camp["characters"] or "[]")
        except (ValueError, TypeError):
            continue
        if not roster:
            continue
        kept = [c for c in roster
                if not ((isinstance(c, dict) and c.get("id") == char_id) or c == char_id)]
        if len(kept) != len(roster):
            db.execute("UPDATE dm_campaigns SET characters = ? WHERE id = ?",
                       (json.dumps(kept), camp["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


def _portrait_thumbnail(blob: bytes, size: int) -> tuple[bytes, str] | None:
    """Thin wrapper — the implementation is shared with the NPC route, and cached: the same portrait
    asked for at the same size must not be re-encoded on every request."""
    from services.images import thumb_for_blob
    return thumb_for_blob(blob, size)


@router.get("/api/character/{char_id}/portrait-image")
async def character_portrait_image(char_id: int, request: Request, size: int | None = None):
    """Serve a character portrait as a real, cacheable image response.

    Portraits are stored in `characters.portrait_url` as multi-MB base64 data
    URLs and used to be inlined straight into the sheet HTML — twice — so one
    sheet view shipped ~7 MB (measured live: Orla Harbak 7.5 MB). The template
    now points `data:` portraits at this endpoint instead; the stored value is
    untouched, so nothing needs migrating.

    `?size=N` returns a WebP thumbnail at most N px on the long edge (clamped to
    16..1024) for list/sidebar use; omit it for the full-size image.
    """
    user = require_user(request)
    db = get_db()
    row = load_character(db, user, char_id, "dm", columns="user_id, portrait_url, shared")
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Character not found")

    src = (row["portrait_url"] or "").strip()
    if src.startswith("data:"):
        try:
            header, b64 = src.split(",", 1)
            media = header[5:].split(";")[0] or "image/png"
            blob = base64.b64decode(b64)
        except Exception:
            raise HTTPException(status_code=404, detail="No portrait stored") from None
        if size is not None:
            from starlette.concurrency import run_in_threadpool
            thumb = await run_in_threadpool(_portrait_thumbnail, blob, size)
            if thumb:
                blob, media = thumb
        return Response(content=blob, media_type=media,
                        headers={"Cache-Control": ("private, max-age=60" if size is not None else "private, max-age=86400")})
    if src:
        # External URL — send the browser straight there rather than proxying.
        return RedirectResponse(src, status_code=307)
    raise HTTPException(status_code=404, detail="No portrait stored")


@router.post("/api/character/{char_id}/toggle-share", response_class=JSONResponse)
async def character_toggle_share(char_id: int, request: Request):
    """Toggle public sharing on a character (owner only)."""
    user = require_user(request)
    data = await request.json()
    shared = 1 if data.get("shared", False) else 0
    db = get_db()
    db.execute("UPDATE characters SET shared=? WHERE id=? AND user_id=?",
               (shared, char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "shared": bool(shared)})
