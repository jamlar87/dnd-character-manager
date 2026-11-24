"""Campaigns: CRUD, roster membership, notes and XP."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.data import _load_manual_json
from core.db import get_db
from core.features import get_caster_type, get_spell_slots
from core.security import _require_owned, require_user
from services import roster
from services.rules import ability_mod
from services.schema import HAS_PORTRAIT_SQL

from ._shared import _manual_npc_hp, user_where

router = APIRouter()


@router.post("/api/dm/campaign/create", response_class=JSONResponse)
async def dm_campaign_create(request: Request):
    """Create a new campaign."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_campaigns (user_id, name, description, party_level, party_size, notes, quests, locations, characters)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New Campaign"),
        data.get("description", ""),
        int(data.get("party_level", 1)),
        int(data.get("party_size", 4)),
        data.get("notes", ""),
        json.dumps(data.get("quests", [])),
        json.dumps(data.get("locations", [])),
        json.dumps(data.get("characters", [])),
    ))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return JSONResponse({"id": cid, "ok": True})

@router.get("/api/dm/campaigns", response_class=JSONResponse)
def dm_campaigns_list(request: Request):
    """List all campaigns with live character stats."""
    user = require_user(request)
    db = get_db()
    where, params = user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_campaigns {where} ORDER BY created_at DESC", params
    ).fetchall()]

    # Parse every roster first, then resolve all the members in ONE query — this
    # used to issue a SELECT per character, per campaign (N+1).
    rosters = []
    all_ids = set()
    for camp in rows:
        try:
            char_ids = json.loads(camp.get("characters") or "[]")
        except (json.JSONDecodeError, TypeError):
            camp["characters"] = []
            continue
        rosters.append((camp, char_ids))
        for entry in char_ids:
            cid = entry.get("id") if isinstance(entry, dict) else entry
            if cid is not None:
                all_ids.add(cid)

    chars_by_id: dict = {}
    if all_ids:
        id_list = list(all_ids)
        for r in db.execute(f"""
            SELECT id, name, race, subrace, class_name, subclass, level,
                   hp_current, hp_max, temp_hp, ac, strength, dexterity, constitution,
                   intelligence, wisdom, charisma, proficiency_bonus, speed,
                   inspiration, exhaustion, passive_perception, hit_dice, hit_dice_used,
                   {HAS_PORTRAIT_SQL}
            FROM characters WHERE id IN ({",".join("?" * len(id_list))})
        """, id_list).fetchall():
            chars_by_id[r["id"]] = r
            # `id = ?` used to accept a roster id stored as a string (column affinity
            # coerced it); a dict lookup must do that coercion itself.
            chars_by_id[str(r["id"])] = r

    # Enrich each campaign's characters with live data from the characters table
    for camp, char_ids in rosters:
        enriched = []
        for entry in char_ids:
            cid = entry.get("id") if isinstance(entry, dict) else entry
            row = chars_by_id.get(cid)
            if row:
                ch = dict(row)
                # Compute modifiers
                mods = {s: ability_mod(ch[s]) for s in
                        ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
                ch["modifiers"] = mods
                ch["status"] = entry.get("status", "active") if isinstance(entry, dict) else "active"
                ch["subrace"] = ch.get("subrace") or ""
                ch["subclass"] = ch.get("subclass") or ""
                # Get spell slots if caster
                cls = ch.get("class_name", "")
                lvl = ch.get("level", 1)
                if cls and get_caster_type(cls) != "none":
                    slots = get_spell_slots(cls, lvl)
                    ch["spell_slots"] = slots.get("by_level", {})
                else:
                    ch["spell_slots"] = {}
                enriched.append(ch)

        camp["characters"] = enriched

        # Parse NPCs JSON
        try:
            camp["npcs"] = json.loads(camp.get("npcs") or "[]")
        except (json.JSONDecodeError, TypeError):
            camp["npcs"] = []

    db.close()
    return JSONResponse({"campaigns": rows})

@router.post("/api/dm/campaign/{camp_id}/update", response_class=JSONResponse)
async def dm_campaign_update(camp_id: int, request: Request):
    """Update campaign — supports field updates and array mutations."""
    user = require_user(request)
    data = await request.json()
    allowed = {"name","description","party_level","party_size","notes","session_notes","quests","locations","characters"}
    updates = {}

    # Handle add/remove quest operations
    if "addQuest" in data:
        new_quest = json.loads(data["addQuest"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            quests = json.loads(row["quests"] or "[]")
            quests.append(new_quest)
            updates["quests"] = json.dumps(quests)
        db.close()
    elif "removeQuest" in data:
        idx = int(data["removeQuest"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            quests = json.loads(row["quests"] or "[]")
            if 0 <= idx < len(quests):
                quests.pop(idx)
            updates["quests"] = json.dumps(quests)
        db.close()

    # Handle add/remove location operations
    if "addLocation" in data:
        new_loc = json.loads(data["addLocation"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            locs = json.loads(row["locations"] or "[]")
            locs.append(new_loc)
            updates["locations"] = json.dumps(locs)
        db.close()
    elif "removeLocation" in data:
        idx = int(data["removeLocation"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            locs = json.loads(row["locations"] or "[]")
            if 0 <= idx < len(locs):
                locs.pop(idx)
            updates["locations"] = json.dumps(locs)
        db.close()

    # Regular field updates
    for k, v in data.items():
        if k in allowed and k not in ("quests", "locations"):
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v
        elif k in allowed and k in ("quests", "locations") and k not in updates:
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v

    if updates:
        db = get_db()
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [camp_id, user["id"]]
        db.execute(f"UPDATE dm_campaigns SET {sets} WHERE id=? AND user_id=?", vals)
        if db.total_changes == 0:
            db.close()
            return JSONResponse({"error": "Campaign not found"}, status_code=404)
        db.commit()
        db.close()
    return JSONResponse({"ok": True})

@router.post("/api/dm/campaign/{camp_id}/delete", response_class=JSONResponse)
def dm_campaign_delete(camp_id: int, request: Request):
    """Delete a campaign."""
    user = require_user(request)
    db = get_db()
    # Verify ownership
    row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not row:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    db.execute("DELETE FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@router.post("/api/dm/campaign/{camp_id}/add-character", response_class=JSONResponse)
async def dm_campaign_add_character(camp_id: int, request: Request):
    """Add a character to a campaign."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    db = get_db()
    # Verify campaign ownership
    camp = _require_owned(db, user, "dm_campaigns", camp_id)
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    # A campaign may only list a character its owner has made readable to other accounts.
    # Checking existence alone let any registered user POST a sequential victim id into their
    # own campaign and then read the whole sheet via /character/{id}?dm_preview=1 — the campaign
    # listing IS DM authority (`_user_dms_character`). `shared` is the owner's own "Public"
    # toggle on the sheet; an unshared character stays private and looks nonexistent here.
    char_row = db.execute(
        "SELECT id, name, class_name, level, race FROM characters"
        " WHERE id=? AND (user_id=? OR shared=1)",
        (char_id, user["id"])).fetchone()
    if not char_row:
        db.close()
        return JSONResponse({"error": "Character not found"}, status_code=404)

    chars = json.loads(camp["characters"] or "[]")
    # Don't add duplicates
    if any(c.get("id") == char_id for c in chars):
        db.close()
        return JSONResponse({"ok": True, "message": "Already added"})
    chars.append({
        "id": char_id,
        "name": char_row["name"],
        "class_name": char_row["class_name"],
        "level": char_row["level"],
        "race": char_row["race"],
        "status": "active",
    })
    db.execute("UPDATE dm_campaigns SET characters=? WHERE id=?", (json.dumps(chars), camp_id))
    # The join table is what a JOIN reads; leaving it behind is how it stayed empty while
    # five campaigns had rosters. Mirror the write here so both agree.
    roster.sync(db, camp_id, chars)
    db.commit()
    db.close()
    # The audit trail: who put this character on the roster, and which campaign.
    try:
        from services.eventlog import log_event

        log_event("roster_add", campaign_id=int(camp_id),
                  actor=str(user.get("email") or user.get("id") or ""),
                  target=f"char:{char_id}",
                  before={"on_roster": False},
                  after={"on_roster": True, "name": char_row["name"],
                         "class_name": char_row["class_name"], "level": char_row["level"]})
    except Exception:  # noqa: BLE001 - auditing must never break the roster write
        pass
    return JSONResponse({"ok": True})

@router.post("/api/dm/campaign/{camp_id}/remove-character", response_class=JSONResponse)
async def dm_campaign_remove_character(camp_id: int, request: Request):
    """Remove a character from a campaign."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    chars = json.loads(camp["characters"] or "[]")
    chars = [c for c in chars if c.get("id") != char_id]
    db.execute("UPDATE dm_campaigns SET characters=? WHERE id=?", (json.dumps(chars), camp_id))
    roster.sync(db, camp_id, chars)
    db.commit()
    db.close()
    try:
        from services.eventlog import log_event

        log_event("roster_remove", campaign_id=int(camp_id),
                  actor=str(user.get("email") or user.get("id") or ""),
                  target=f"char:{char_id}",
                  before={"on_roster": True}, after={"on_roster": False})
    except Exception:  # noqa: BLE001 - auditing must never break the roster write
        pass
    return JSONResponse({"ok": True})

@router.post("/api/dm/campaign/{camp_id}/add-npc", response_class=JSONResponse)
async def dm_campaign_add_npc(camp_id: int, request: Request):
    """Add an NPC to a campaign."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id)
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    # Fetch NPC details
    npc_data = None
    if npc_id < 0:
        # Manual NPC — look up from JSON
        manual_npcs = _load_manual_json("npcs.json")
        idx = -npc_id - 1
        if 0 <= idx < len(manual_npcs):
            mn = manual_npcs[idx]
            hp = _manual_npc_hp(mn)
            npc_data = {
                "id": npc_id,
                "name": mn.get("name", "Unknown"),
                "race": mn.get("race", "Unknown"),
                "class_name": mn.get("class_name", ""),
                "subclass": mn.get("subclass", ""),
                "level": mn.get("level", 1),
                "hp_current": hp,
                "hp_max": hp,
                "ac": mn.get("ac", mn.get("armor_class", 10)),
                "is_enemy": 0,
                "role": mn.get("role", "NPC"),
                "alignment": mn.get("alignment", ""),
                "notes": "",
                "faction": "",
                "xp_reward": mn.get("xp_reward", 0),
            }
    else:
        npc_row = db.execute(
            "SELECT id, name, race, class_name, subclass, level, hp_current, hp_max, ac, is_enemy, role, alignment, notes, faction, xp_reward FROM dm_npcs WHERE id=?",
            (npc_id,)
        ).fetchone()
        if npc_row:
            npc_data = dict(npc_row)
            npc_data["notes"] = ""  # Per-campaign notes (separate from NPC's own notes)

    if not npc_data:
        db.close()
        return JSONResponse({"error": "NPC not found"}, status_code=404)

    npcs = json.loads(camp["npcs"] or "[]")
    if any(n.get("id") == npc_id for n in npcs):
        db.close()
        return JSONResponse({"ok": True, "message": "Already added"})
    npcs.append(npc_data)
    db.execute("UPDATE dm_campaigns SET npcs=? WHERE id=?", (json.dumps(npcs), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@router.post("/api/dm/campaign/{camp_id}/remove-npc", response_class=JSONResponse)
async def dm_campaign_remove_npc(camp_id: int, request: Request):
    """Remove an NPC from a campaign."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    npcs = json.loads(camp["npcs"] or "[]")
    npcs = [n for n in npcs if n.get("id") != npc_id]
    db.execute("UPDATE dm_campaigns SET npcs=? WHERE id=?", (json.dumps(npcs), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@router.post("/api/dm/campaign/{camp_id}/update-npc-notes", response_class=JSONResponse)
async def dm_campaign_update_npc_notes(camp_id: int, request: Request):
    """Update DM notes for an NPC in a campaign."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    notes = data.get("notes", "")
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    npcs = json.loads(camp["npcs"] or "[]")
    for n in npcs:
        if n.get("id") == npc_id:
            n["notes"] = notes
            break
    db.execute("UPDATE dm_campaigns SET npcs=? WHERE id=?", (json.dumps(npcs), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

def _campaign_owned(db, user, camp_id: int):
    row = db.execute("SELECT * FROM dm_campaigns WHERE id = ? AND user_id = ?",
                     (camp_id, user["id"])).fetchone()
    return dict(row) if row else None

def _campaign_roster_ids(db, camp: dict) -> list:
    """The characters a campaign covers: the join table first, then the campaign's own list."""
    ids = []
    try:
        ids = [int(r["character_id"]) for r in db.execute(
            "SELECT character_id FROM dm_campaign_characters WHERE campaign_id = ?",
            (camp["id"],))]
    except Exception:
        ids = []
    if not ids:
        try:
            ids = [int(c["id"]) for c in json.loads(camp.get("characters") or "[]") if c.get("id")]
        except (json.JSONDecodeError, TypeError, ValueError):
            ids = []
    return sorted(set(ids))

@router.get("/api/dm/campaign/{camp_id}/xp", response_class=JSONResponse)
def campaign_xp(camp_id: int, request: Request):
    """Who has how much, and the ledger behind it."""
    from services.leveling import xp_progress

    user = require_user(request)
    db = get_db()
    try:
        camp = _campaign_owned(db, user, camp_id)
        if not camp:
            return JSONResponse({"error": "Not found"}, status_code=404)
        ids = _campaign_roster_ids(db, camp)
        characters = []
        if ids:
            qs = ",".join("?" * len(ids))
            for r in db.execute(
                    "SELECT id, name, xp, level FROM characters WHERE id IN (" + qs + ") ORDER BY name",
                    tuple(ids)):
                characters.append({"id": r["id"], "name": r["name"], "xp": r["xp"] or 0,
                                   "level": r["level"] or 1,
                                   "progress": xp_progress(r["xp"], r["level"])})
        log = [dict(r) for r in db.execute(
            "SELECT id, amount, note, recipients, created_at, COALESCE(reversed,0) AS reversed "
            "FROM dm_xp_log "
            "WHERE campaign_id = ? ORDER BY id DESC LIMIT 20", (camp_id,))]
        return JSONResponse({"ok": True, "characters": characters, "log": log})
    finally:
        db.close()

@router.post("/api/dm/campaign/{camp_id}/award-xp", response_class=JSONResponse)
async def award_xp(camp_id: int, request: Request):
    """Give the party XP, and say so on every screen that shows one of them.

    The whole roster by default: XP is a party thing at most tables, and a subset is a
    `character_ids` away when one is needed. Every recipient hears about it through the same event
    the rest of this work runs on, so a sheet open on a player's phone updates by itself.
    """
    user = require_user(request)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)
    try:
        amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "amount must be a whole number"}, status_code=400)
    if amount == 0:
        return JSONResponse({"error": "amount must not be zero"}, status_code=400)
    note = str(data.get("note") or "").strip()[:200]

    db = get_db()
    try:
        camp = _campaign_owned(db, user, camp_id)
        if not camp:
            return JSONResponse({"error": "Not found"}, status_code=404)
        ids = _campaign_roster_ids(db, camp)
        wanted = data.get("character_ids")
        if isinstance(wanted, list) and wanted:
            keep = {int(x) for x in wanted if str(x).isdigit()}
            chosen = [i for i in ids if i in keep]
            if not chosen:
                # Falling back to the whole party here would hand out XP nobody asked for: the
                # caller named characters and none of them are on this campaign.
                return JSONResponse({"error": "None of those characters are in this campaign"},
                                    status_code=400)
            ids = chosen
        if not ids:
            return JSONResponse({"error": "No characters in this campaign"}, status_code=400)

        qs = ",".join("?" * len(ids))
        xp_before = {r["id"]: (r["xp"] or 0) for r in db.execute(
            "SELECT id, xp FROM characters WHERE id IN (" + qs + ")", tuple(ids))}
        db.execute("UPDATE characters SET xp = MAX(0, COALESCE(xp,0) + ?) WHERE id IN (" + qs + ")",
                   tuple([amount] + ids))
        log_cur = db.execute("INSERT INTO dm_xp_log (campaign_id, user_id, amount, note, recipients) "
                             "VALUES (?, ?, ?, ?, ?)",
                             (camp_id, user["id"], amount, note, len(ids)))
        from services.undo import record_safe

        record_safe(db, user["id"], "xp",
                   f"{amount:+d} XP to {len(ids)} character" + ("" if len(ids) == 1 else "s")
                   + (f" ({note})" if note else ""),
                   [{"character_id": cid,
                     "before": {"xp": was},
                     "after": {"xp": max(0, was + amount)}} for cid, was in xp_before.items()],
                    meta={"xp_log_id": log_cur.lastrowid})
        db.commit()
    finally:
        db.close()

    from routes.table import publish_character_state

    for cid in ids:
        try:
            publish_character_state(cid, kind="sheet")      # every recipient's sheet, live
        except Exception:
            pass
        try:
            from routes.maps import poke_maps_for_character

            poke_maps_for_character(cid)
        except Exception:
            pass
    # The audit trail: the XP award, per recipient, with the totals before and after.
    try:
        from services.eventlog import log_event

        for cid in ids:
            was = xp_before.get(cid, 0)
            log_event("xp_award", campaign_id=int(camp_id),
                      actor=str(user.get("email") or user.get("id") or ""),
                      target=f"char:{cid}",
                      before={"xp": was}, after={"xp": max(0, was + amount)},
                      )
    except Exception:  # noqa: BLE001 - auditing must never break the award
        pass
    return JSONResponse({"ok": True, "awarded": amount, "recipients": len(ids)})
