"""Sheet writes: the save path (`POST /character/{char_id}`), the
ASI and expertise edits, and the one place that publishes + records undo.

Split out of the single-module sheet.py; behaviour and URLs are unchanged.
"""

import json
from core.db import get_db
from core.security import _require_owned, require_user
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from routes.characters.helpers import load_character
from routes.schemas import EditASI, UpdateCharacter
from services.leveling import get_expertise_count, get_expertise_options, parse_class_levels

router = APIRouter()

# ── The save path ──────────────────────────────────────────────────────────

def _publish_change(char_id: int, kind: str = "sheet", **fields) -> None:
    """Warn the character's table that this sheet moved. Never raises: a sheet save must not fail
    because the feed is having a bad day. Imported inside the call — routes.table imports main and
    this module is imported by the characters package, so a module-scope import is a cycle
    (Pitfall 16)."""
    try:
        from routes.table import publish_character_state

        publish_character_state(char_id, kind=kind, **fields)
    except Exception:
        pass
    try:
        # A token on a map is a second screen showing this character, and the map stream listens on
        # the MAP's scope, so a campaign event does not reach it: without this poke the players'
        # screen holds the old HP bar and the old conditions until an unrelated map change.
        from routes.maps import poke_maps_for_character

        poke_maps_for_character(char_id)
    except Exception:
        pass


@router.post("/api/character/{char_id}/update", response_class=JSONResponse)
def update_character(char_id: int, request: Request, body: UpdateCharacter):
    user = require_user(request)
    # Build data dict from model (extra fields pass through)
    data = body.model_dump(exclude_none=True) | (body.model_extra or {})
    # The model allows extras, so most numeric cells reach here untyped: clamp them to the bounds
    # the DM's NPC editor and character creation share (services/validation).
    from services.validation import clamp_fields

    data = clamp_fields(data)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    allowed = {
        # Core stats
        "hp_current","hp_max","temp_hp","ac","notes","death_saves_success","death_saves_fail",
        "hit_dice_used","strength","dexterity","constitution","intelligence","wisdom","charisma",
        "level","proficiency_bonus","speed","hit_dice","inspiration","exhaustion","passive_perception",
        # Identity
                "name","race","subrace","class_name","subclass","background","alignment",
                "personality", "backstory", "journal",
        "skills","save_proficiencies","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
        "languages","features","inventory","spell_slots_used","equipped","feature_data","attacks_data",
        "damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities",
        "attuned_items", "expertise_skills", "fighting_style",
        "cp",
        "gp",
        "dragonborn_ancestry",
        "summons",
        "background_data",
        "personality_data",
    }
    updates = {}
    for k, v in data.items():
        if k in allowed:
            updates[k] = v

    if "inventory" in updates:
        import logging as _invlog
        _invlog.getLogger(__name__).info(
            f"INVENTORY SAVE char={char_id} user={user['id']} "
            f"value_len={len(str(updates['inventory']))} "
            f"sample={str(updates['inventory'])[:200]}"
        )

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values())
        # Only the owner (or an admin) may write; everyone else's old
        # `WHERE id=? AND user_id=?` was a silent no-op, so it stays one.
        if load_character(db, user, char_id, "owner", columns="id"):
            db.execute(f"UPDATE characters SET {sets} WHERE id=?", vals + [char_id])

    # Spell slot updates
    if "spells" in data:
        for sp in data["spells"]:
            db.execute("UPDATE character_spells SET slots_used=? WHERE id=? AND character_id=?",
                       (sp.get("slots_used", 0), sp.get("id"), char_id))
    # Remember what this changed, while the connection is still open: one tap puts it back
    # (services/undo). The pre-update row is the "before"; only restorable columns are kept, and a
    # failure here must never cost the save that is already committed.
    from services.undo import record_safe

    record_safe(db, user["id"], "sheet",
                "sheet: " + ", ".join(sorted(updates)[:4]),
                [{"character_id": char_id,
                  "before": {k: row[k] for k in updates},
                  "after": {k: updates[k] for k in updates}}])
    db.commit()
    db.close()

    # Live state: the table should not have to poll to see this sheet move. The DM's tracker
    # mirrors conditions and summons as well as HP, so those count too — a change it cannot hear
    # about is a screen that goes stale the moment its own poll is demoted.
    if {"hp_current", "hp_max", "temp_hp"} & set(updates):
        # Damage is the difference between the value before and after THIS write: only the write
        # path can see it, and it decides whether a concentrating character owes a Constitution
        # save (PHB p.203) — so the DC rides along with the event instead of being guessed at.
        try:
            from routes.table import publish_hp_change

            publish_hp_change(char_id, row["hp_current"],
                              updates.get("hp_current", row["hp_current"]),
                              concentration=row["concentration"] or "")
        except Exception:
            # Saving the sheet must never fail because the feed is having a bad day.
            pass
    elif "summons" in updates:
        # Only summons can arrive here: `conditions` is not in this route's allowed set, and the
        # condition routes publish their own change (a branch that claimed to handle it would be
        # a lie the next reader has to disprove).
        _publish_change(char_id, "sheet")
    return JSONResponse({"ok": True})

@router.post("/api/character/{char_id}/edit-asi", response_class=JSONResponse)
def edit_asi_choice(char_id: int, request: Request, body: EditASI):
    """Edit a past ASI/feat choice for a given level."""
    user = require_user(request)
    if body.entry is None:
        return JSONResponse({"error": "Missing entry"}, status_code=400)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    char = dict(row)
    asi_history = json.loads(char.get("asi_history", "[]") or "[]")

    # Ensure entry has level set before saving
    body.entry["level"] = body.level

    # Replace the entry for this level, or append if not found
    # Also match old entries that lack a level field (legacy data)
    found = False
    for i, ae in enumerate(asi_history):
        if ae.get("level") == body.level:
            asi_history[i] = body.entry
            found = True
            break
    if not found:
        asi_history.append(body.entry)

    db.execute(
        "UPDATE characters SET asi_history=? WHERE id=?",
        (json.dumps(asi_history), char_id)
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "asi_history": asi_history})

@router.post("/api/character/{char_id}/edit-expertise", response_class=JSONResponse)
async def edit_expertise_choice(char_id: int, request: Request):
    """Edit expertise skill picks for this character."""
    user = require_user(request)
    data = await request.json()
    new_skills = data.get("expertise_skills", [])

    if not isinstance(new_skills, list):
        return JSONResponse({"error": "expertise_skills must be a list"}, status_code=400)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    char = dict(row)
    # Parse JSON fields
    char["skills"] = json.loads(char.get("skills", "[]") or "[]")
    char["expertise_skills"] = json.loads(char.get("expertise_skills", "[]") or "[]")

    # Compute valid options and count
    class_levels = parse_class_levels(char)
    all_options = []
    total_count = 0
    for cls, lvl in class_levels.items():
        ec = get_expertise_count(cls, lvl, char.get("subclass", "") if cls == char.get("class_name", "") else "")
        if ec > 0:
            total_count += ec
            eo = get_expertise_options(cls, char.get("subclass", "") if cls == char.get("class_name", "") else "",
                                       char["skills"])
            for opt in eo:
                if opt not in all_options:
                    all_options.append(opt)

    # Validate: must have exactly total_count picks
    if len(new_skills) != total_count:
        # Allow editing even if count differs (de-level scenarios), just warn
        pass

    # Validate each pick is in the options
    for sk in new_skills:
        if sk not in all_options and sk not in char["skills"]:
            db.close()
            return JSONResponse({"error": f"'{sk}' is not a valid expertise option"}, status_code=400)

    # Remove duplicates
    seen = set()
    unique_skills = []
    for sk in new_skills:
        if sk not in seen:
            seen.add(sk)
            unique_skills.append(sk)

    db.execute(
        "UPDATE characters SET expertise_skills=? WHERE id=?",
        (json.dumps(unique_skills), char_id)
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "expertise_skills": unique_skills})
