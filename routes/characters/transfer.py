"""Character export/import routes (JSON).

Export dumps a full character (all sheet fields + spells + relationships)
as JSON. Import recreates it under the current user — used for backup,
account transfer, and sharing builds. Whitelist-based: only known
characters-table columns are accepted, user_id is always the importer.
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response

from main import get_db, require_user, _require_owned

router = APIRouter()

# All editable columns on the characters table (excludes id/user_id/timestamps).
# Import accepts exactly this set — anything else is dropped.
_CHAR_FIELDS = [
    "name", "race", "subrace", "class_name", "subclass", "level", "background",
    "alignment", "strength", "dexterity", "constitution", "intelligence",
    "wisdom", "charisma", "hp_max", "hp_current", "temp_hp", "ac", "speed",
    "proficiency_bonus", "hit_dice", "hit_dice_used", "death_saves_success",
    "death_saves_fail", "skills", "tool_proficiencies", "weapon_proficiencies",
    "armor_proficiencies", "languages", "features", "inventory", "equipped",
    "notes", "personality", "backstory", "feature_data", "attacks_data",
    "spell_slot_data", "passive_perception", "inspiration", "exhaustion",
    "portrait_url", "portrait_prompt", "save_proficiencies",
    "damage_resistances", "damage_immunities", "damage_vulnerabilities",
    "condition_immunities", "background_data", "spell_slots_used",
    "class_levels", "attuned_items", "dragonborn_ancestry", "cp", "gp",
    "expertise_skills", "fighting_style", "metamagic", "invocations",
    "pact_boon", "maneuvers", "magical_secrets", "totem_spirits",
    "hunters_prey", "infusions", "asi_history", "combat_notes",
    "metamagic_history", "summons", "conditions", "favored_enemies",
    "favored_terrains", "personality_data", "journal", "advantage_map",
]

_EXPORT_VERSION = 1


@router.get("/api/character/{char_id}/export", response_class=Response)
async def character_export(char_id: int, request: Request):
    """Full character JSON: sheet fields + spells + relationships."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    payload = {
        "version": _EXPORT_VERSION,
        "character": {k: char.get(k) for k in _CHAR_FIELDS if k in char},
    }

    spells = db.execute(
        "SELECT spell_name, spell_level, prepared, slots_max, slots_used, source "
        "FROM character_spells WHERE character_id = ? ORDER BY spell_level, spell_name",
        (char_id,),
    ).fetchall()
    if spells:
        payload["spells"] = [dict(s) for s in spells]

    rels = db.execute(
        "SELECT name, relationship_type, description, prompt, npc_data, ai_generated "
        "FROM character_relationships WHERE character_id = ? AND user_id = ? "
        "ORDER BY created_at DESC",
        (char_id, user["id"]),
    ).fetchall()
    if rels:
        payload["relationships"] = [dict(r) for r in rels]

    db.close()

    fname = "".join(c for c in (char.get("name") or "character") if c.isalnum() or c in " -_").strip()
    return Response(
        content=__import__("json").dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}.json"'},
    )


@router.post("/api/character/import", response_class=JSONResponse)
async def character_import(request: Request):
    """Import a character exported via /export. Recreates under current user."""
    user = require_user(request)
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not isinstance(raw, dict) or not isinstance(raw.get("character"), dict):
        return JSONResponse({"error": "Missing character object"}, status_code=400)

    data = raw["character"]

    # Minimal validation
    name = str(data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Character must have a name"}, status_code=422)
    if len(name) > 100:
        return JSONResponse({"error": "Name too long (max 100 chars)"}, status_code=422)
    if not str(data.get("race") or "").strip():
        return JSONResponse({"error": "Character must have a race"}, status_code=422)
    if not str(data.get("class_name") or "").strip():
        return JSONResponse({"error": "Character must have a class"}, status_code=422)

    # Whitelist + type-normalize
    updates = {}
    for k in _CHAR_FIELDS:
        if k in data:
            v = data[k]
            if k in ("level", "strength", "dexterity", "constitution",
                     "intelligence", "wisdom", "charisma", "hp_max", "hp_current",
                     "temp_hp", "ac", "speed", "proficiency_bonus",
                     "passive_perception", "inspiration", "exhaustion", "cp", "gp"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = 0 if k not in ("hp_max", "hp_current", "ac", "speed", "level") else 0
            updates[k] = v

    if not updates.get("level"):
        updates["level"] = 1

    db = get_db()
    cols = ", ".join(updates.keys())
    marks = ", ".join("?" for _ in updates)
    cur = db.execute(
        f"INSERT INTO characters ({cols}, user_id) VALUES ({marks}, ?)",
        list(updates.values()) + [user["id"]],
    )
    new_id = cur.lastrowid

    # Spells
    for sp in raw.get("spells") or []:
        if not isinstance(sp, dict) or not sp.get("spell_name"):
            continue
        db.execute(
            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                str(sp["spell_name"])[:200],
                int(sp.get("spell_level") or 0),
                int(bool(sp.get("prepared"))),
                int(sp.get("slots_max") or 0),
                int(sp.get("slots_used") or 0),
                str(sp.get("source") or "")[:100],
            ),
        )

    # Relationships
    for rel in raw.get("relationships") or []:
        if not isinstance(rel, dict) or not rel.get("name"):
            continue
        db.execute(
            "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, prompt, npc_data, ai_generated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                user["id"],
                str(rel["name"])[:100],
                str(rel.get("relationship_type") or "")[:50],
                str(rel.get("description") or "")[:2000],
                str(rel.get("prompt") or "")[:4000],
                str(rel.get("npc_data") or "")[:4000],
                int(bool(rel.get("ai_generated"))),
            ),
        )

    db.commit()
    db.close()
    return JSONResponse({"id": new_id, "name": name, "ok": True})
