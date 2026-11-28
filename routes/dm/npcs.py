"""NPC library: list, detail, create/update/delete, portraits, and AI NPC building."""

import json
import random

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from core.data import ALIGNMENTS, CLASSES, FLEXIBLE_ASI_RACES, RACE_NAMES, RACES, _load_manual_json
from core.db import get_db
from core.features import (
    enrich_features,
    get_caster_type,
    get_racial_trait_effects,
    get_spell_slots,
)
from core.security import _is_admin, require_user
from data import SUBCLASS_LEVELS
from routes.characters import _build_character, _extract_json
from routes.characters.ai_routes import _calculate_ac, _pick_skills
from routes.characters.sheet import (
    allocate_ability_scores,
    calc_hp,
    get_equipment_for_level,
    get_spells_for_level,
)
from services.leveling import PROFICIENCY_BONUS, get_class_features
from services.rules import ability_mod
from services.schema import hydrate_json_columns
from services.sources import clean_source_display
from services.validation import clamp_fields, clamp_int

from ._shared import NPC_LIST_COLS, _manual_npc_hp, call_ai, user_where

router = APIRouter()


def _npc_int(data: dict, key: str, default: int) -> int:
    """One numeric NPC cell: coerced to int (form posts send strings) and clamped to its bounds.

    `int(data.get("hp_max", 10))` raised on anything unparseable — a 500 for a typo in the DM's
    NPC form — and let `hp_max: 1e9` through unchecked, which the initiative maths and the
    encounter HP bar then divided by. Bounds and defaults live in services/validation.
    """
    try:
        n = int(float(data.get(key, default)))
    except (TypeError, ValueError):
        n = default
    return clamp_int(key, n)

@router.post("/api/dm/npc/create", response_class=JSONResponse)
async def dm_npc_create(request: Request):
    """Create a new NPC (or enemy)."""
    user = require_user(request)
    data = await request.json()
    # A portrait picked while creating an NPC must survive the create call.
    _portrait = (data.get("portrait_url") or "").strip()
    if _portrait:
        from services.images import normalize_portrait
        # Image resize is CPU work; keep it off the event loop.
        _portrait, err = await run_in_threadpool(normalize_portrait, _portrait, max_px=1024)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    db = get_db()
    _hp_max = _npc_int(data, "hp_max", 10)
    cur = db.execute("""
        INSERT INTO dm_npcs (user_id, name, race, class_name, subclass, level, is_enemy, is_party_npc,
            strength, dexterity, constitution, intelligence, wisdom, charisma,
            hp_max, hp_current, ac, speed, proficiency_bonus, hit_dice,
            skills, features, inventory, notes, alignment, role, faction, xp_reward, portrait_url)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New NPC"),
        data.get("race", "Human"),
        data.get("class_name", ""),
        data.get("subclass", ""),
        _npc_int(data, "level", 1),
        1 if data.get("is_enemy") else 0,
        1 if data.get("is_party_npc") else 0,
        _npc_int(data, "strength", 10),
        _npc_int(data, "dexterity", 10),
        _npc_int(data, "constitution", 10),
        _npc_int(data, "intelligence", 10),
        _npc_int(data, "wisdom", 10),
        _npc_int(data, "charisma", 10),
        _hp_max,
        _npc_int(data, "hp_current", _hp_max),
        _npc_int(data, "ac", 10),
        _npc_int(data, "speed", 30),
        _npc_int(data, "proficiency_bonus", 2),
        data.get("hit_dice", "1d8"),
        json.dumps(data.get("skills", [])),
        json.dumps(data.get("features", [])),
        json.dumps(data.get("inventory", [])),
        data.get("notes", ""),
        data.get("alignment", "True Neutral"),
        data.get("role", "NPC"),
        data.get("faction", ""),
        _npc_int(data, "xp_reward", 0),
        _portrait,
    ))
    db.commit()
    npc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": npc_id, "ok": True})

@router.get("/api/dm/npcs", response_class=JSONResponse)
def dm_npcs_list(request: Request):
    """List all DM's NPCs."""
    user = require_user(request)
    db = get_db()
    where, params = user_where(user)
    # NPC id -1 named __sentinel__ is the pack import's placeholder: it exists so encounter and campaign
    # rows always have a non-null NPC to point at. It is not a creature, and in the palette it listed as
    # a real entry that placed a blank token; generate_portraits.py already skips it by name.
    #
    # The guard needs its OWN clause: `_user_where` returns '' for an admin (who sees everything), and
    # appending "AND ..." to an empty clause is `SELECT * FROM dm_npcs  AND ...` - a syntax error that
    # took the whole list down with a 500 rather than filtering one row.
    guard = "COALESCE(name, '') <> '__sentinel__'"
    clause = f"{where} AND {guard}" if where.strip() else f"WHERE {guard}"
    rows = [dict(r) for r in db.execute(
        f"SELECT {NPC_LIST_COLS} FROM dm_npcs {clause} ORDER BY is_enemy DESC, name", params
    ).fetchall()]
    db.close()
    for r in rows:
        hydrate_json_columns(r)
        # Boolean only — the base64 blob was never selected in the first place.
        r["has_portrait"] = bool(r.get("has_portrait"))
    # Merge manual NPCs from extracted data
    manual_npcs = _load_manual_json("npcs.json")
    for i, mn in enumerate(manual_npcs):
        # Belt and braces: the sentinel is a database row (NPC id -1, created by the pack import and the
        # DM bootstrap so encounter rows always have a non-null NPC to point at), and the database query
        # above filters it. The current npcs.json carries no such entry, but the merge is where a second
        # copy would appear, and a creature-shaped placeholder in a search list is worth being sure
        # about. scripts/generate_portraits.py skips it by name for the same reason.
        if str(mn.get("name") or "").strip() == "__sentinel__":
            continue
        hp = _manual_npc_hp(mn)
        scores = mn.get("ability_scores", {})
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        rows.append({
            "id": -(i + 1),  # negative ID to distinguish manual NPCs
            "user_id": user["id"],
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
            "speed": mn.get("speed", "30 ft."),
            "ability_scores": mn.get("ability_scores", {}),
            "spellcasting": mn.get("spellcasting"),
            "skills": mn.get("skills", []),
            "features": mn.get("features", []),
            "actions": mn.get("actions", []),
            "reactions": mn.get("reactions", []),
            "legendary_actions": mn.get("legendary_actions", []),
            "saving_throws": mn.get("saving_throws", {}),
            "senses": mn.get("senses", ""),
            "languages": mn.get("languages", []),
            "damage_resistances": mn.get("damage_resistances", []),
            "damage_immunities": mn.get("damage_immunities", []),
            "condition_immunities": mn.get("condition_immunities", []),
            "challenge_rating": mn.get("challenge_rating"),
            "inventory": mn.get("equipment", []),
            "notes": mn.get("description", ""),
            "source": clean_source_display(mn.get("source"), "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "_narrative": not has_stats,
        })
    return JSONResponse({"npcs": rows})

@router.get("/api/dm/npc/{npc_id}", response_class=JSONResponse)
def dm_npc_detail(npc_id: int, request: Request):
    """Full NPC detail — DB NPCs and manual extracted NPCs."""
    user = require_user(request)

    # Manual NPCs use negative IDs
    if npc_id < 0:
        manual_npcs = _load_manual_json("npcs.json")
        idx = -(npc_id) - 1  # id -1 → idx 0, id -2 → idx 1
        if idx < 0 or idx >= len(manual_npcs):
            raise HTTPException(status_code=404, detail="Manual NPC not found")
        mn = manual_npcs[idx]
        scores = mn.get("ability_scores", {})
        hp = _manual_npc_hp(mn)
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        return JSONResponse({
            "id": npc_id,
            "user_id": user["id"],
            "name": mn.get("name", "Unknown"),
            "race": mn.get("race", "Unknown"),
            "class_name": mn.get("class_name", ""),
            "subclass": mn.get("subclass", ""),
            "level": mn.get("level", 1),
            "hp_current": hp,
            "hp_max": hp,
            "ac": mn.get("ac", mn.get("armor_class", 10)),
            "is_enemy": 0,
            "is_party_npc": 0,
            "role": mn.get("role", "NPC"),
            "alignment": mn.get("alignment", ""),
            "speed": mn.get("speed", "30 ft."),
            "skills": mn.get("skills", []),
            "features": mn.get("features", []),
            "inventory": mn.get("equipment", []),
            "notes": mn.get("description", ""),
            "source": clean_source_display(mn.get("source"), "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "strength": scores.get("strength", 0) if has_stats else 0,
            "dexterity": scores.get("dexterity", 0) if has_stats else 0,
            "constitution": scores.get("constitution", 0) if has_stats else 0,
            "intelligence": scores.get("intelligence", 0) if has_stats else 0,
            "wisdom": scores.get("wisdom", 0) if has_stats else 0,
            "charisma": scores.get("charisma", 0) if has_stats else 0,
            "proficiency_bonus": 2,
            "hit_dice": "1d8",
            "hit_dice_used": 0,
            "temp_hp": 0,
            "portrait_url": "",
            "faction": "",
            "_manual": True,
            "_narrative": not has_stats,
        })

    db = get_db()
    row = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="NPC not found")
    npc = dict(row)
    hydrate_json_columns(npc)
    # The editor needs a boolean + (for external links) the URL — never the
    # multi-MB base64 blob, which would ride along in every editor open.
    _p = (npc.get("portrait_url") or "").strip()
    npc["has_portrait"] = bool(_p)
    npc["portrait_url"] = _p if _p.startswith(("http://", "https://", "/")) else ""
    npc["portrait_is_data"] = _p.startswith("data:")
    return JSONResponse(npc)

@router.post("/api/dm/npc/{npc_id}/update", response_class=JSONResponse)
async def dm_npc_update(npc_id: int, request: Request):
    """Update NPC fields."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")

    allowed = {"name","race","class_name","subclass","level","strength","dexterity","constitution",
               "intelligence","wisdom","charisma","hp_max","hp_current","temp_hp","ac","speed",
               "proficiency_bonus","hit_dice","hit_dice_used","skills","features","inventory",
               "notes","alignment","role","faction","xp_reward","portrait_url",
               "is_enemy","is_party_npc"}
    updates = {}
    for k, v in data.items():
        if k in allowed:
            # Serialize list fields
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v
    # Same bounds as create: an HP max of 1e9 (or a negative level) must not reach the row.
    updates = clamp_fields(updates)

    if "portrait_url" in updates:
        from services.images import normalize_portrait
        # Image resize is CPU work; keep it off the event loop.
        updates["portrait_url"], err = await run_in_threadpool(normalize_portrait, updates["portrait_url"], max_px=1024)
        if err:
            db.close()
            return JSONResponse({"error": err}, status_code=400)

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [npc_id, user["id"]]
        db.execute(f"UPDATE dm_npcs SET {sets} WHERE id=? AND user_id=?", vals)
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@router.post("/api/dm/npc/{npc_id}/delete", response_class=JSONResponse)
def dm_npc_delete(npc_id: int, request: Request):
    """Delete an NPC."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_npcs WHERE id = ? AND user_id = ?", (npc_id, user["id"]))
    if db.total_changes == 0:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── NPC portraits ────────────────────────────────────────────────────────────
@router.get("/api/dm/npc/{npc_id}/portrait-image")
async def dm_npc_portrait_image(npc_id: int, request: Request, size: int | None = None):
    """Serve an NPC portrait as a real, cacheable image response.

    Mirrors /api/character/{id}/portrait-image: portraits are stored as base64
    data URLs, so every render site points here (with `?size=` for thumbnails)
    instead of inlining the blob. Scoped to the NPC's owner (or an admin) —
    404 for anyone else, exactly like a missing NPC.
    """
    from services.images import decode_data_url, thumb_for_blob
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT user_id, portrait_url FROM dm_npcs WHERE id = ?",
                     (npc_id,)).fetchone()
    db.close()
    if not row or not (_is_admin(user) or row["user_id"] == user["id"]):
        raise HTTPException(status_code=404, detail="NPC not found")

    src = (row["portrait_url"] or "").strip()
    if src.startswith("data:"):
        decoded = decode_data_url(src)
        if not decoded:
            raise HTTPException(status_code=404, detail="No portrait stored")
        blob, media = decoded
        if size is not None:
            # encoding inline would block the event loop for every other request in flight
            thumb = await run_in_threadpool(thumb_for_blob, blob, size)
            if thumb:
                blob, media = thumb
        return Response(content=blob, media_type=media,
                        headers={"Cache-Control": ("private, max-age=60" if size is not None else "private, max-age=86400")})
    if src:
        # External URL — send the browser straight there rather than proxying.
        return RedirectResponse(src, status_code=307)
    raise HTTPException(status_code=404, detail="No portrait stored")

def _detect_creation_gaps(npc: dict) -> list[dict]:
    """Given an NPC dict (from DB or manual), detect what's missing for full build.
    Returns list of gap dicts e.g. {field: 'class_name', label: 'Class', type: 'class_picker'}."""
    gaps = []
    race = npc.get("race", "").strip()
    subrace = npc.get("subrace", "").strip()
    class_name = npc.get("class_name", "").strip()
    subclass = npc.get("subclass", "").strip()
    level = int(npc.get("level", 1) or 1)

    # 1. Race must be a known PHB-equivalent
    if race not in RACES and race not in RACE_NAMES:
        gaps.append({"field": "race", "label": "Race (PHB equivalent)", "type": "race_picker",
                      "current": race, "note": f"\"{race}\" not in PHB — pick closest match"})

    # 2. Subrace needed? If race has subraces & none set & race is known
    if race in RACES and not subrace:
        race_data = RACES.get(race, {})
        race_subraces = race_data.get("subraces", [])
        if race_subraces:
            gaps.append({"field": "subrace", "label": "Subrace", "type": "subrace_picker",
                          "options": race_subraces, "note": f"{race} has subraces: pick one"})

    # 3. Class required
    if not class_name:
        gaps.append({"field": "class_name", "label": "Class", "type": "class_picker",
                      "note": "NPC has no class — pick one"})
    elif class_name not in CLASSES:
        gaps.append({"field": "class_name", "label": "Class (PHB equivalent)", "type": "class_picker",
                      "current": class_name, "note": f"\"{class_name}\" not in PHB — pick closest match"})
    else:
        if class_name in SUBCLASS_LEVELS:
            sc_info = SUBCLASS_LEVELS[class_name]
            if level >= sc_info["level"] and not subclass:
                gaps.append({"field": "subclass", "label": f"Subclass ({sc_info['label']})",
                              "type": "subclass_picker", "options": sc_info.get("options", []),
                              "note": f"L{level} {class_name} needs a subclass (pick or skip)"})

    # Abilities: all 6 must be non-zero
    scores = npc.get("ability_scores", {})
    if isinstance(scores, str):
        try: scores = json.loads(scores)
        except: scores = {}
    for abil in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        val = scores.get(abil, npc.get(abil, 0))
        try: val = int(val)
        except: val = 0
        if val == 0:
            gaps.append({"field": f"ability_scores.{abil}", "label": abil.capitalize(),
                          "type": "ability_input", "note": f"{abil.capitalize()} score is 0"})

    # Half-Elf / Custom Lineage: need asi_picks
    if race in FLEXIBLE_ASI_RACES:
        gaps.append({"field": "asi_picks", "label": "Ability Score Picks", "type": "asi_picks",
                      "note": f"{race} needs ability score bonuses assigned"})

    return gaps

def _npc_to_character_data(npc: dict) -> dict:
    """Map NPC dict → character creation data dict for _build_character."""
    scores = npc.get("ability_scores", {})
    if isinstance(scores, str):
        try: scores = json.loads(scores)
        except: scores = {}
    abilities = {}
    for abil in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        abilities[abil] = int(scores.get(abil, npc.get(abil, 10)) or 10)

    skills = npc.get("skills", [])
    if isinstance(skills, str):
        try: skills = json.loads(skills)
        except: skills = []

    inventory = npc.get("inventory", npc.get("equipment", []))
    if isinstance(inventory, str):
        try: inventory = json.loads(inventory)
        except: inventory = []

    features = npc.get("features", [])
    if isinstance(features, str):
        try: features = json.loads(features)
        except: features = []

    notes = npc.get("notes", npc.get("description", ""))

    return {
        "name": npc.get("name", "Unknown NPC"),
        "race": npc.get("race", "Human"),
        "subrace": npc.get("subrace", ""),
        "class_name": npc.get("class_name", ""),
        "subclass": npc.get("subclass", ""),
        "level": int(npc.get("level", 1) or 1),
        "abilities": abilities,
        "skills": skills,
        "equipment": inventory,
        "alignment": npc.get("alignment", "True Neutral"),
        "background": npc.get("role", ""),
        "personality": "",
        "backstory": notes,
        "features": features,
        # Carried through the gap-fill round trip so the built character can
        # inherit the NPC's portrait (see _apply_portrait_to_built_character).
        "portrait_url": (npc.get("portrait_url") or "").strip(),
    }

def _apply_portrait_to_built_character(char_id: int, portrait_url) -> bool:
    """Give a freshly built character the source NPC's portrait.

    Only fills an empty portrait, so a character that already has one is never
    overwritten. Stored verbatim (the editor already downscaled it).
    """
    src = (portrait_url or "").strip() if isinstance(portrait_url, str) else ""
    if not src:
        return False
    from services.images import normalize_portrait
    src, err = normalize_portrait(portrait_url, max_px=1024)
    if err or not src:
        return False
    try:
        db = get_db()
        db.execute(
            "UPDATE characters SET portrait_url = ? WHERE id = ? "
            "AND (portrait_url IS NULL OR portrait_url = '')",
            (src, char_id))
        changed = db.total_changes > 0
        db.commit()
        db.close()
        return changed
    except Exception:  # pragma: no cover - defensive
        return False

@router.post("/api/dm/npc/{npc_id}/build-to-character", response_class=JSONResponse)
def dm_npc_build_to_character(npc_id: int, request: Request):
    """Build a full player character from an NPC. Detects gaps and returns them."""
    user = require_user(request)
    npc = None

    # Manual NPC (negative ID)
    if npc_id < 0:
        manual_npcs = _load_manual_json("npcs.json")
        idx = -npc_id - 1
        if 0 <= idx < len(manual_npcs):
            mn = manual_npcs[idx]
            scores = mn.get("ability_scores", {})
            has_stats = scores and any(v != 0 and v is not None for v in scores.values())
            npc = {
                "name": mn.get("name", "Unknown"),
                "race": mn.get("race", ""),
                "subrace": mn.get("subrace", ""),
                "class_name": mn.get("class_name", ""),
                "subclass": mn.get("subclass", ""),
                "level": mn.get("level", 1),
                "ability_scores": scores if has_stats else {},
                "skills": mn.get("skills", []),
                "features": mn.get("features", []),
                "inventory": mn.get("equipment", []),
                "alignment": mn.get("alignment", ""),
                "notes": mn.get("description", ""),
                "role": mn.get("role", "NPC"),
            }
    else:
        db = get_db()
        row = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                         (npc_id, user["id"])).fetchone()
        db.close()
        if row:
            npc = dict(row)

    if not npc:
        raise HTTPException(status_code=404, detail="NPC not found")

    gaps = _detect_creation_gaps(npc)
    char_data = _npc_to_character_data(npc)

    if gaps:
        return JSONResponse({"ok": False, "gaps": gaps, "npc_data": char_data,
                              "npc_name": npc.get("name", "Unknown"),
                              "message": f"Need {len(gaps)} more field(s) before building"})

    try:
        char_id, name = _build_character(char_data, user["id"])
        _apply_portrait_to_built_character(char_id, npc.get("portrait_url"))
        return JSONResponse({"ok": True, "character_id": char_id, "name": name,
                              "message": f"Built {name} as a full character"})
    except (ValueError, KeyError) as e:
        return JSONResponse({"ok": False, "error": str(e),
                              "message": "Build failed — fill in gaps manually"}, status_code=400)

@router.post("/api/dm/npc/build-from-gaps", response_class=JSONResponse)
async def dm_npc_build_from_gaps(request: Request):
    """Complete NPC→character build with user-filled gaps."""
    user = require_user(request)
    data = await request.json()
    char_data = data.get("char_data", {})
    gap_values = data.get("gap_values", {})

    for field, value in gap_values.items():
        if field.startswith("ability_scores."):
            abil = field.split(".")[1]
            if "abilities" not in char_data:
                char_data["abilities"] = {}
            char_data["abilities"][abil] = int(value)
        elif field == "asi_picks":
            char_data["asi_picks"] = value if isinstance(value, list) else [value]
        else:
            char_data[field] = value

    if not char_data.get("name"):
        return JSONResponse({"error": "Name required"}, status_code=400)
    if not char_data.get("race"):
        return JSONResponse({"error": "Race required"}, status_code=400)
    if not char_data.get("class_name"):
        return JSONResponse({"error": "Class required"}, status_code=400)

    try:
        char_id, name = _build_character(char_data, user["id"])
        _apply_portrait_to_built_character(char_id, char_data.get("portrait_url"))
        return JSONResponse({"ok": True, "character_id": char_id, "name": name,
                              "message": f"Built {name} as a full character"})
    except (ValueError, KeyError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

@router.post("/api/dm/ai/build-npc", response_class=JSONResponse)
async def dm_ai_build_npc(request: Request):
    """AI-generated NPC with full build. Uses same PHB-grounded engine as character builder."""
    require_user(request)
    data = await request.json()
    race = data.get('race', 'Human')
    subrace = data.get('subrace', '')
    class_name = data.get('class_name', 'Fighter')
    subclass = data.get('subclass', '')
    is_enemy = data.get('is_enemy', False)
    personality_hint = data.get('personality_hint', '')
    role_hint = data.get('role', 'NPC')
    target_cr_raw = data.get('target_cr', '')

    # If target CR provided, auto-derive level; otherwise use manual level
    if target_cr_raw:
        try:
            if '/' in target_cr_raw:
                parts = target_cr_raw.split('/')
                target_cr = float(parts[0]) / float(parts[1])
            else:
                target_cr = float(target_cr_raw)
            # Rough CR→level mapping: L ≈ CR*2 + 1 (DMG p.274 approximation)
            level = min(20, max(1, int(target_cr * 2) + 1))
        except (ValueError, ZeroDivisionError):
            level = min(max(int(data.get('level', 1)), 1), 20)
            target_cr = None
    else:
        level = min(max(int(data.get('level', 1)), 1), 20)
        target_cr = None

    # Use the same build engine as PCs
    abilities = allocate_ability_scores(class_name, race, subrace)
    mods = {ability: ability_mod(abilities[ability]) for ability in abilities}
    pb = PROFICIENCY_BONUS.get(level, 2)
    con_mod = mods["constitution"]
    hp = calc_hp(class_name, level, con_mod)
    racial_eff = get_racial_trait_effects(race, subrace)
    ac = _calculate_ac(class_name, level, mods, racial_eff.get("natural_armor"))
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    saves = {ability: mod + (pb if ability in class_data.get("saves", []) else 0)
             for ability, mod in mods.items()}
    skills = _pick_skills(class_name, mods)
    equipment = get_equipment_for_level(class_name, level)
    raw_features = get_class_features(class_name, level, subclass)
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods, subclass=subclass)
    spells = get_spells_for_level(class_name, level) if get_caster_type(class_name) != "none" else {}
    spell_slots = get_spell_slots(class_name, level) if get_caster_type(class_name) != "none" else {}

    # AI flavor
    cr_note = f' (target CR {target_cr_raw})' if target_cr_raw else ''
    ai_prompt = f"""Generate a D&D 5e NPC concept for a {'villain/enemy' if is_enemy else 'friendly NPC'}{cr_note}.
Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name} L{level}{' — ' + subclass if subclass else ''}
Role: {role_hint}
{personality_hint}
Return: {{\"name\": \"NPC Name\", \"personality\": \"2 traits\", \"backstory\": \"1-2 sentences\", \"alignment\": \"one from PHB list\", \"faction\": \"group name or empty\"}}"""

    text = await call_ai(prompt=ai_prompt, label="npc-gen")
    ai = _extract_json(text) if text else None
    if ai:
        if ai.get("alignment") not in ALIGNMENTS:
            ai["alignment"] = random.choice(ALIGNMENTS)

    return JSONResponse({
        "name": ai.get("name", f"{race} {role_hint}") if ai else f"{race} {role_hint}",
        "personality": ai.get("personality", "") if ai else "",
        "backstory": ai.get("backstory", "") if ai else "",
        "alignment": ai.get("alignment", "True Neutral") if ai else "True Neutral",
        "faction": ai.get("faction", "") if ai else "",
        "build": {
            "level": level,
            "class": class_name,
            "subclass": subclass or None,
            "race": race,
            "subrace": subrace or None,
            "target_cr": target_cr_raw or None,
            "ability_scores": abilities,
            "modifiers": mods,
            "hit_points": hp,
            "armor_class": ac,
            "proficiency_bonus": pb,
            "saving_throws": saves,
            "skills": skills,
            "equipment": equipment,
            "features": enriched_features,
            "spells": spells.get("spells", {}),
            "cantrips": spells.get("cantrips", []),
            "spell_slots": spell_slots.get("by_level", {}),
            "hit_dice": f"{level}d{class_data['hd']}",
            "speed": RACES.get(race, RACES["Human"]).get("speed", 30),
        }
    })
