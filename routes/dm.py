"""DM Tools routes — monsters, NPCs, encounters, campaigns, traps."""

from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
import sqlite3, json, math, random, re, urllib.parse
from pathlib import Path
from datetime import datetime

from main import get_db, require_user, _render, get_current_user
from main import _user_where
from main import RACES, CLASSES, SUBCLASS_FEATURES, LIMITED_USE, BACKGROUNDS
from main import _load_manual_json, _get_named_item_types, _get_source_slug_map
from main import enrich_features, get_caster_type, get_spell_slots, _search_manuals, MANUAL_TRAPS
from routes.characters import _load_monster_cache
from summon_templates import SUMMON_TEMPLATES

router = APIRouter()


@router.get("/dm-tools", response_class=HTMLResponse)
async def dm_tools(request: Request):
    """DM Tools main page — encounter builder, NPC manager, monster lookup."""
    user = require_user(request)
    db = get_db()

    # Load monsters from SRD cache
    all_monsters = _load_monster_cache()
    monsters_by_env = {}
    for m in all_monsters:
        m_type = m.get("type", "other")
        monsters_by_env.setdefault(m_type, []).append(m)

    # Load DM's NPCs (DB)
    npcs = [dict(r) for r in db.execute(
        "SELECT * FROM dm_npcs WHERE user_id = ? ORDER BY is_enemy DESC, name",
        (user["id"],)
    ).fetchall()]

    # Merge manual NPCs from extracted data
    manual_npcs = _load_manual_json("npcs.json")
    for i, mn in enumerate(manual_npcs):
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
        try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
        except: hp = 10
        # Detect narrative NPCs (no combat stat block)
        scores = mn.get("ability_scores", {})
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        is_narrative = not has_stats and str(hp_raw).lower() in ("unknown", "?", "0", "")
        npcs.append({
            "id": -(i + 1),  # negative ID = manual NPC
            "user_id": user["id"],
            "name": mn.get("name", "Unknown"),
            "race": mn.get("race", "Unknown"),
            "class_name": mn.get("class_name", ""),
            "subclass": mn.get("subclass", ""),
            "level": mn.get("level", 1),
            "hp_current": hp,
            "hp_max": hp,
            "ac": mn.get("ac", mn.get("armor_class", 10)) if has_stats else 10,
            "is_enemy": 0,
            "is_party_npc": 0,
            "role": mn.get("role", "NPC"),
            "alignment": mn.get("alignment", ""),
            "speed": mn.get("speed", "30 ft."),
            "skills": json.dumps(mn.get("skills", [])),
            "features": json.dumps(mn.get("features", [])),
            "inventory": json.dumps(mn.get("equipment", [])),
            "notes": mn.get("description", ""),
            "source": mn.get("source", "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "strength": scores.get("strength", 0) if has_stats else 0,
            "dexterity": scores.get("dexterity", 0) if has_stats else 0,
            "constitution": scores.get("constitution", 0) if has_stats else 0,
            "intelligence": scores.get("intelligence", 0) if has_stats else 0,
            "wisdom": scores.get("wisdom", 0) if has_stats else 0,
            "charisma": scores.get("charisma", 0) if has_stats else 0,
            "proficiency_bonus": 2, "hit_dice": "1d8", "hit_dice_used": 0,
            "temp_hp": 0, "portrait_url": "", "faction": "",
            "_manual": True,
            "_narrative": is_narrative,
        })

    # Load encounters
    encounters = [dict(r) for r in db.execute(
        "SELECT * FROM dm_encounters WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Load campaigns
    campaigns = [dict(r) for r in db.execute(
        "SELECT * FROM dm_campaigns WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Parse JSON fields
    for npc in npcs:
        for f in ("skills", "features", "inventory"):
            try: npc[f] = json.loads(npc[f])
            except (json.JSONDecodeError, TypeError): npc[f] = []
    for c in campaigns:
        for f in ("quests", "locations", "characters"):
            try: c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError): c[f] = []

    # Monster types for filtering
    monster_types = sorted(monsters_by_env.keys())
    # Challenge rating options
    cr_ranges = [(0, 0.25), (0.5, 2), (3, 5), (6, 10), (11, 16), (17, 30)]

    db.close()
    # Merge custom traps with manual traps
    all_traps = list(MANUAL_TRAPS)
    try:
        db2 = get_db()
        custom_traps = [dict(r) for r in db2.execute(
            "SELECT * FROM dm_custom_traps WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],)
        ).fetchall()]
        db2.close()
        for ct in custom_traps:
            all_traps.append({
                "name": ct["name"],
                "type": ct["type"],
                "danger": ct["danger"],
                "trigger": ct["trigger"] or "",
                "detection": {
                    "dc": ct["detection_dc"] or 10,
                    "skill": ct["detection_skill"] or "Perception",
                    "detail": ct["detection_detail"] or ""
                },
                "disarm": {
                    "dc": ct["disarm_dc"],
                    "method": ct["disarm_method"] or "",
                    "detail": ct["disarm_detail"] or ""
                },
                "effect": ct["effect"] or "",
                "save_dc": ct["save_dc"],
                "save_ability": ct["save_ability"] or "",
                "damage": ct["damage"] or "",
                "damage_type": ct["damage_type"] or "",
                "area": ct["area"] or "",
                "description": ct["description"] or "",
                "source": "Custom",
                "_custom_id": ct["id"],
                "_custom": True
            })
    except Exception:
        pass
    return _render("dm_tools.html", request=request,
                   monsters=all_monsters, monster_types=monster_types,
                   cr_ranges=cr_ranges, npcs=npcs,
                   encounters=encounters, campaigns=campaigns,
                   traps=all_traps,
                   named_item_types=_get_named_item_types(),
                   source_map_json=json.dumps(_get_source_slug_map()),
                   summon_templates=SUMMON_TEMPLATES)


@router.get("/api/dm/monster/{index}", response_class=JSONResponse)
async def dm_monster_detail(index: str, request: Request):
    """Full monster detail from SRD cache."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    for m in all_monsters:
        if m.get("index", "").lower() == index.lower():
            return JSONResponse(_enrich_monster(m))
    raise HTTPException(status_code=404, detail="Monster not found")


def _enrich_monster(m: dict) -> dict:
    """Enrich monster data with computed saves, skills, and action tags."""
    import copy
    m = copy.deepcopy(m)
    
    # Ensure flat ability scores
    scores = m.get("ability_scores", {})
    stat_map = {"str":"strength","strength":"strength","dex":"dexterity","dexterity":"dexterity",
                "con":"constitution","constitution":"constitution","int":"intelligence",
                "intelligence":"intelligence","wis":"wisdom","wisdom":"wisdom",
                "cha":"charisma","charisma":"charisma"}
    for k, v in scores.items():
        target = stat_map.get(k.lower().strip())
        if target and target not in m:
            try: m[target] = int(v)
            except: m[target] = 10
    for stat in ("strength","dexterity","constitution","intelligence","wisdom","charisma"):
        m.setdefault(stat, 10)
    if "ability_scores" in m:
        del m["ability_scores"]  # clean up now-useless dict
    
    # Proficiency bonus
    cr_val = m.get("challenge_rating", 0)
    try: cr_val = float(cr_val)
    except: cr_val = 0
    pb = int(m.get("proficiency_bonus", 0) or max(2, 2 + int((cr_val - 1) / 4)))
    
    # Compute saving throws if not in proficiencies
    has_saves = any(
        p.get("proficiency", {}).get("name", "").startswith("Saving Throw")
        for p in m.get("proficiencies", [])
    ) if m.get("proficiencies") else False
    
    if not has_saves:
        m.setdefault("proficiencies", [])
        # Check for manual saving_throws field
        raw_saves = m.get("saving_throws", {})
        if isinstance(raw_saves, dict) and raw_saves:
            for save_key, save_val in raw_saves.items():
                try: save_val = int(save_val)
                except: save_val = 0
                m["proficiencies"].append({
                    "proficiency": {"name": f"Saving Throw: {save_key.upper()}"},
                    "value": save_val
                })
        else:
            # Compute from ability scores
            save_stats = {"STR":"strength","DEX":"dexterity","CON":"constitution",
                           "INT":"intelligence","WIS":"wisdom","CHA":"charisma"}
            for save_name, stat_key in save_stats.items():
                stat_val = m.get(stat_key, 10)
                save_bonus = (stat_val - 10) // 2
                m["proficiencies"].append({
                    "proficiency": {"name": f"Saving Throw: {save_name}"},
                    "value": save_bonus
                })
    
    # Compute skills from manual data
    raw_skills = m.get("skills", {})
    if isinstance(raw_skills, dict) and raw_skills:
        m.setdefault("proficiencies", [])
        skill_abilities = {
            "acrobatics":"dexterity","animal handling":"wisdom","arcana":"intelligence",
            "athletics":"strength","deception":"charisma","history":"intelligence",
            "insight":"wisdom","intimidation":"charisma","investigation":"intelligence",
            "medicine":"wisdom","nature":"intelligence","perception":"wisdom",
            "performance":"charisma","persuasion":"charisma","religion":"intelligence",
            "sleight of hand":"dexterity","stealth":"dexterity","survival":"wisdom",
        }
        for skill_name, bonus in raw_skills.items():
            try: bonus = int(bonus)
            except: continue
            m["proficiencies"].append({
                "proficiency": {"name": f"Skill: {skill_name.title()}"},
                "value": bonus
            })
        del m["skills"]  # cleanup
    
    # Ensure reactions array
    m.setdefault("reactions", [])
    
    # Ensure lair_actions
    m.setdefault("lair_actions", [])
    m.setdefault("lair_desc", "")
    m.setdefault("legendary_desc", "")
    
    return m


@router.get("/api/dm/monsters", response_class=JSONResponse)
async def dm_monster_list(request: Request):
    """List monsters with optional filters."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    return JSONResponse({"count": len(all_monsters), "monsters": all_monsters})


@router.get("/api/dm/monsters/search", response_class=JSONResponse)
async def dm_monster_search(request: Request, q: str = "", type: str = "", cr_min: float = 0, cr_max: float = 30, cr: str = ""):
    """Search/filter monsters by name, type, and CR range."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    results = []

    # Parse CR from string param as alternative
    if cr:
        try:
            cr_f = float(cr)
            cr_min = cr_f
            cr_max = cr_f
        except (TypeError, ValueError):
            pass

    for m in all_monsters:
        name = m.get("name", "").lower()
        m_type = m.get("type", "").lower()
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            m_cr = 0

        if q and q.lower() not in name:
            continue
        if type and type.lower() != m_type:
            continue
        if m_cr < cr_min or m_cr > cr_max:
            continue
        # Flatten for display
        results.append({
            "index": m["index"],
            "name": m["name"],
            "type": m.get("type", ""),
            "size": m.get("size", ""),
            "armor_class": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hit_points": m.get("hit_points", 0),
            "challenge_rating": m.get("challenge_rating", 0),
            "xp": m.get("xp", 0),
            "source": m.get("source", ""),
        })

    return JSONResponse({"count": len(results), "monsters": results, "total": len(all_monsters)})


@router.get("/api/dm/monsters/by-cr", response_class=JSONResponse)
async def dm_monsters_by_cr(request: Request):
    """Grouped monsters by CR tier for encounter building."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    tiers = {
        "trivial": [m for m in all_monsters if _monster_cr_sort_key(m) <= 0.25],
        "low": [m for m in all_monsters if 0.5 <= _monster_cr_sort_key(m) <= 2],
        "medium": [m for m in all_monsters if 3 <= _monster_cr_sort_key(m) <= 5],
        "high": [m for m in all_monsters if 6 <= _monster_cr_sort_key(m) <= 10],
        "deadly": [m for m in all_monsters if 11 <= _monster_cr_sort_key(m) <= 16],
        "legendary": [m for m in all_monsters if _monster_cr_sort_key(m) >= 17],
    }
    result = {}
    for tier, monsters in tiers.items():
        result[tier] = [{
            "index": m["index"], "name": m["name"], "cr": m.get("challenge_rating", 0),
            "type": m.get("type", ""), "size": m.get("size", ""),
            "hp": m.get("hit_points", 0), "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "source": m.get("source", ""),
        } for m in sorted(monsters, key=_monster_cr_sort_key)]
    return JSONResponse(result)


# ── DM Tools: NPC Management ─────────────────────────────────────────────

@router.post("/api/dm/npc/create", response_class=JSONResponse)
async def dm_npc_create(request: Request):
    """Create a new NPC (or enemy)."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_npcs (user_id, name, race, class_name, subclass, level, is_enemy, is_party_npc,
            strength, dexterity, constitution, intelligence, wisdom, charisma,
            hp_max, hp_current, ac, speed, proficiency_bonus, hit_dice,
            skills, features, inventory, notes, alignment, role, faction, xp_reward)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New NPC"),
        data.get("race", "Human"),
        data.get("class_name", ""),
        data.get("subclass", ""),
        int(data.get("level", 1)),
        1 if data.get("is_enemy") else 0,
        1 if data.get("is_party_npc") else 0,
        int(data.get("strength", 10)),
        int(data.get("dexterity", 10)),
        int(data.get("constitution", 10)),
        int(data.get("intelligence", 10)),
        int(data.get("wisdom", 10)),
        int(data.get("charisma", 10)),
        int(data.get("hp_max", 10)),
        int(data.get("hp_current", int(data.get("hp_max", 10)))),
        int(data.get("ac", 10)),
        int(data.get("speed", 30)),
        int(data.get("proficiency_bonus", 2)),
        data.get("hit_dice", "1d8"),
        json.dumps(data.get("skills", [])),
        json.dumps(data.get("features", [])),
        json.dumps(data.get("inventory", [])),
        data.get("notes", ""),
        data.get("alignment", "True Neutral"),
        data.get("role", "NPC"),
        data.get("faction", ""),
        int(data.get("xp_reward", 0)),
    ))
    db.commit()
    npc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": npc_id, "ok": True})


@router.get("/api/dm/npcs", response_class=JSONResponse)
async def dm_npcs_list(request: Request):
    """List all DM's NPCs."""
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_npcs {where} ORDER BY is_enemy DESC, name", params
    ).fetchall()]
    db.close()
    for r in rows:
        for f in ("skills", "features", "inventory"):
            try: r[f] = json.loads(r[f])
            except: r[f] = []
    # Merge manual NPCs from extracted data
    manual_npcs = _load_manual_json("npcs.json")
    for i, mn in enumerate(manual_npcs):
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
        try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
        except: hp = 10
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
            "source": mn.get("source", "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "_narrative": not has_stats,
        })
    return JSONResponse({"npcs": rows})


@router.get("/api/dm/npc/{npc_id}", response_class=JSONResponse)
async def dm_npc_detail(npc_id: int, request: Request):
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
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
        try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
        except: hp = 10
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
            "source": mn.get("source", "Manual"),
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
    for f in ("skills", "features", "inventory"):
        try: npc[f] = json.loads(npc[f])
        except: npc[f] = []
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

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [npc_id, user["id"]]
        db.execute(f"UPDATE dm_npcs SET {sets} WHERE id=? AND user_id=?", vals)
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@router.post("/api/dm/npc/{npc_id}/delete", response_class=JSONResponse)
async def dm_npc_delete(npc_id: int, request: Request):
    """Delete an NPC."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_npcs WHERE id = ? AND user_id = ?", (npc_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


def _build_party_profile(db, campaign_id: int) -> dict | None:
    """Build a combat profile of all characters in a campaign for AI counter-play.
    Returns {characters: [...], summary: {...}} or None if no characters."""
    rows = db.execute("""
        SELECT c.* FROM characters c
        JOIN dm_campaign_characters cc ON cc.campaign_id = ? AND cc.character_id = c.id
        WHERE cc.status = 'active'
    """, (campaign_id,)).fetchall()

    # Fallback: read from campaign's characters JSON column
    if not rows:
        camp = db.execute("SELECT characters FROM dm_campaigns WHERE id=?", (campaign_id,)).fetchone()
        if camp and camp["characters"]:
            try:
                char_entries = json.loads(camp["characters"])
                char_ids = [e["id"] for e in char_entries if isinstance(e, dict) and e.get("status", "active") == "active"]
                if char_ids:
                    placeholders = ",".join("?" * len(char_ids))
                    rows = db.execute(
                        f"SELECT * FROM characters WHERE id IN ({placeholders})", char_ids
                    ).fetchall()
            except (json.JSONDecodeError, KeyError):
                pass

    if not rows:
        return None

    chars = []
    total_level = 0
    ability_mods = {"STR": [], "DEX": [], "CON": [], "INT": [], "WIS": [], "CHA": []}
    all_resistances = set()
    all_immunities = set()
    all_vulns = set()
    all_cond_immunities = set()

    for r in rows:
        c = dict(r)
        # Parse JSON fields
        for f in ("skills", "features", "equipped", "attuned_items",
                   "save_proficiencies", "damage_resistances", "damage_immunities",
                   "damage_vulnerabilities", "condition_immunities", "asi_history"):
            val = c.get(f)
            if isinstance(val, str) and val:
                try: c[f] = json.loads(val)
                except: pass
            if c.get(f) is None:
                c[f] = []

        ac = c.get("ac", 10)
        hp = c.get("hp_current", c.get("hp_max", 10)) or c.get("hp_max", 10)
        lvl = c.get("level", 1)
        total_level += lvl

        scores = {
            "STR": c.get("strength", 10), "DEX": c.get("dexterity", 10),
            "CON": c.get("constitution", 10), "INT": c.get("intelligence", 10),
            "WIS": c.get("wisdom", 10), "CHA": c.get("charisma", 10),
        }
        mods = {k: (v - 10) // 2 for k, v in scores.items()}

        for ab in ability_mods:
            ability_mods[ab].append(mods[ab])

        saves = {s.replace("_save", "").upper(): mods.get(s.replace("_save", "").upper(), 0)
                 for s in (c.get("save_proficiencies", []) or [])}
        # Add prof bonus to proficient saves
        prof = c.get("proficiency_bonus", 2)
        for s in saves:
            saves[s] = mods.get(s, 0) + prof

        # Collect defenses
        res = [r for r in (c.get("damage_resistances", []) or []) if r]
        imm = [r for r in (c.get("damage_immunities", []) or []) if r]
        vul = [r for r in (c.get("damage_vulnerabilities", []) or []) if r]
        cond_imm = [r for r in (c.get("condition_immunities", []) or []) if r]
        all_resistances.update(res)
        all_immunities.update(imm)
        all_vulns.update(vul)
        all_cond_immunities.update(cond_imm)

        # Spellcasting check
        spells = db.execute(
            "SELECT spell_name, spell_level FROM character_spells WHERE character_id=? AND prepared=1",
            (c["id"],)
        ).fetchall()
        spell_names = [s[0] for s in spells] if spells else []

        chars.append({
            "name": c.get("name", ""),
            "race": c.get("race", ""),
            "class": c.get("class_name", ""),
            "subclass": c.get("subclass", ""),
            "level": lvl,
            "ac": ac,
            "hp": hp,
            "scores": {k: v for k, v in scores.items()},
            "mods": mods,
            "saves": saves,
            "resistances": res,
            "immunities": imm,
            "vulnerabilities": vul,
            "condition_immunities": cond_imm,
            "spells": spell_names,
            "features": [f.get("name", f) if isinstance(f, dict) else f
                        for f in (c.get("features", []) or [])],
        })

    avg_level = round(total_level / len(chars), 1)
    # Find party's weakest saves (lowest avg modifier)
    weakest_saves = sorted(ability_mods.items(), key=lambda x: sum(x[1])/len(x[1]))[:2]
    strongest_saves = sorted(ability_mods.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:2]

    summary = {
        "size": len(chars),
        "avg_level": avg_level,
        "total_level": total_level,
        "weakest_saves": [w[0] for w in weakest_saves],
        "strongest_saves": [s[0] for s in strongest_saves],
        "collective_resistances": sorted(all_resistances - all_immunities - all_vulns),
        "collective_immunities": sorted(all_immunities),
        "collective_vulnerabilities": sorted(all_vulns),
        "condition_immunities": sorted(all_cond_immunities),
    }
    return {"characters": chars, "summary": summary}


@router.get("/api/dm/ai/party-profile", response_class=JSONResponse)
async def dm_ai_party_profile(request: Request):
    """Return a combat profile summary of all characters in a campaign.
    Used by the AI Encounter Builder frontend to show party preview."""
    user = require_user(request)
    cid = request.query_params.get("campaign_id", "")
    if not cid:
        return JSONResponse({"profile": None})
    db = get_db()
    profile = _build_party_profile(db, int(cid))
    # Fallback: always include campaign's stored party_level/party_size
    camp = db.execute("SELECT party_level, party_size FROM dm_campaigns WHERE id=?", (int(cid),)).fetchone()
    fallback = {"party_level": camp["party_level"] if camp else 1, "party_size": camp["party_size"] if camp else 4}
    db.close()
    return JSONResponse({"profile": profile, "campaign": fallback})


@router.post("/api/dm/ai/build-encounter", response_class=JSONResponse)
async def dm_ai_build_encounter(request: Request):
    """AI-suggested encounter composition based on party size/level, environment, and difficulty.
    When campaign_id is provided, loads all party characters and tailors the encounter
    to their stats — exploiting weak saves, bypassing resistances, and countering strengths."""
    user = require_user(request)
    db = get_db()
    data = await request.json()
    party_level = int(data.get('party_level', 5))
    party_size = int(data.get('party_size', 4))
    environment = data.get('environment', 'dungeon')
    difficulty = data.get('difficulty', 'medium')
    theme = data.get('theme', '')
    tone = data.get('tone', '')
    target_cr_raw = data.get('target_cr', '')
    campaign_id = data.get('campaign_id', '')

    # Determine effective CR range for filtering
    encounter_type = data.get('encounter_type', 'skirmish')
    if target_cr_raw:
        try:
            if '/' in target_cr_raw:
                parts = target_cr_raw.split('/')
                target_cr = float(parts[0]) / float(parts[1])
            else:
                target_cr = float(target_cr_raw)
        except (ValueError, ZeroDivisionError):
            target_cr = None
    else:
        target_cr = None

    # Effective level for XP budget lookup
    if target_cr is not None:
        eff_level = min(20, max(1, int(target_cr * 2) + 1))
    else:
        eff_level = party_level

    # XP budgets (DMG p.82 — Adjusted XP thresholds for party of N)
    # Per-character thresholds for medium encounters at each level
    MEDIUM_XP = {1: 50, 2: 100, 3: 150, 4: 250, 5: 500, 6: 600, 7: 750, 8: 900,
                 9: 1100, 10: 1200, 11: 1600, 12: 2000, 13: 2200, 14: 2500,
                 15: 2800, 16: 3200, 17: 3900, 18: 4200, 19: 4900, 20: 5700}
    HARD_XP = {1: 75, 2: 150, 3: 225, 4: 375, 5: 750, 6: 900, 7: 1100, 8: 1400,
               9: 1600, 10: 1900, 11: 2400, 12: 3000, 13: 3400, 14: 3800,
               15: 4300, 16: 4800, 17: 5900, 18: 6300, 19: 7300, 20: 8500}
    DEADLY_XP = {1: 100, 2: 200, 3: 400, 4: 500, 5: 1100, 6: 1400, 7: 1700, 8: 2100,
                 9: 2400, 10: 2800, 11: 3600, 12: 4500, 13: 5100, 14: 5700,
                 15: 6400, 16: 7200, 17: 8800, 18: 9500, 19: 10900, 20: 12700}

    xp_budgets = {"easy": MEDIUM_XP, "medium": MEDIUM_XP, "hard": HARD_XP, "deadly": DEADLY_XP}
    if difficulty == "easy":
        xp_per_char = int(MEDIUM_XP.get(eff_level, 500) * 0.5)
    else:
        xp_per_char = xp_budgets.get(difficulty, MEDIUM_XP).get(eff_level, 500)
    xp_budget = xp_per_char * party_size

    # Load monsters
    all_monsters = _load_monster_cache()

    # CR range for filtering (computed once, not per-monster)
    if encounter_type == "swarm":
        max_cr = party_level
        min_cr = 0.125
    elif target_cr is not None:
        max_cr = target_cr + 3
        min_cr = max(0, target_cr - 2)
    else:
        max_cr = party_level + 2
        min_cr = max(0, (party_level // 2) - 1)  # L1→0, L5→1, L7→2, L11→4, L15→6, L20→8

    # Filter by environment/theme hints
    candidates = []
    for m in all_monsters:
        m_type = m.get("type", "").lower()
        m_name = m.get("name", "").lower()
        m_env = m.get("environment", [])
        if isinstance(m_env, list):
            m_env = [e.lower() for e in m_env]

        # Environment/climate matching
        env_match = True
        if environment and environment.lower() not in ("any", ""):
            env_keywords = {
                "dungeon": ["dungeon", "underdark", "cavern", "underground"],
                "forest": ["forest", "woodland", "jungle"],
                "mountain": ["mountain", "hill", "peak"],
                "coastal": ["coastal", "coast", "ocean", "sea", "water"],
                "swamp": ["swamp", "marsh", "wetland"],
                "arctic": ["arctic", "tundra", "ice", "cold", "frozen"],
                "desert": ["desert", "sandy", "arid"],
                "grassland": ["grassland", "plain", "savanna"],
                "urban": ["urban", "city", "town", "settlement"],
                "underdark": ["underdark", "cavern", "underground", "dark"],
                "planar": ["planar", "outer", "inner", "abyss", "heaven"],
            }
            env_words = env_keywords.get(environment.lower(), [environment.lower()])
            # Check monster type alignment against environment
            type_env_map = {
                "aberration": ["underdark", "dungeon"],
                "beast": ["forest", "grassland", "mountain", "arctic", "desert", "coastal", "swamp"],
                "celestial": ["planar"],
                "construct": ["dungeon", "urban"],
                "dragon": ["mountain", "coastal", "forest", "swamp", "arctic", "desert"],
                "elemental": ["planar", "mountain"],
                "fey": ["forest", "grassland"],
                "fiend": ["planar", "underdark"],
                "giant": ["mountain", "hill", "grassland"],
                "humanoid": ["urban", "dungeon", "grassland", "mountain", "forest", "arctic", "desert", "swamp", "coastal"],
                "monstrosity": ["underdark", "dungeon", "forest", "mountain", "swamp", "desert"],
                "ooze": ["underdark", "dungeon", "swamp"],
                "plant": ["forest", "swamp", "jungle"],
                "undead": ["underdark", "dungeon", "urban", "swamp"],
            }
            suitable = type_env_map.get(m_type, [])
            # Expanded: allow thematic pairings across all environments
            if environment.lower() not in ("any", ""):
                # "forest" undead: haunted woods, druid groves with blights
                cross_env = {
                    "undead": ["forest", "mountain", "desert", "coastal", "arctic"],
                    "fey": ["swamp", "mountain", "underdark"],
                    "fiend": ["swamp", "desert", "forest"],
                    "aberration": ["forest", "swamp", "mountain", "urban"],
                    "elemental": ["desert", "coastal", "forest", "swamp"],
                    "ooze": ["forest", "mountain", "desert", "coastal"],
                    "plant": ["mountain", "coastal"],
                    "monstrosity": ["coastal", "urban", "arctic"],
                    "dragon": ["underdark", "urban", "grassland"],
                }
                extra = cross_env.get(m_type, [])
                if not any(e in env_words or e in suitable or e in extra for e in env_words) and not any(kw in m_name for kw in env_words):
                    env_match = False

        # ── Negative environment filter: exclude clearly wrong creatures ──
        if env_match and environment.lower() not in ("any", "", "coastal", "swamp", "underdark"):
            # For land-based environments, exclude aquatic creatures
            aquatic_keywords = ["dolphin", "whale", "shark", "crab", "octopus", "eel", "jellyfish",
                               "squid", "seal", "sea ", "coral", "ray ", "manta", "crocodile",
                               "turtle", "toad", "frog", "axolot", "merfolk", "merrow", "water ",
                               "koi", "piranha", "giant sea", "reef", "platypus", "beaver", "otter",
                               "hippopotamus", "rhinoceros"]
            if any(kw in m_name for kw in aquatic_keywords):
                env_match = False

        # ── Also filter by tags ──
        m_tags = m.get("tags", [])
        if isinstance(m_tags, list):
            if env_match:
                # If monster is tagged "aquatic" and environment isn't aquatic, exclude
                if "aquatic" in m_tags and environment.lower() not in ("coastal", "swamp", "underdark", "any"):
                    env_match = False

        if not env_match:
            continue

        # CR filtering: split into combat candidates and atmosphere (CR 0)
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            continue

        if m_cr > max_cr or (m_cr < min_cr and m_cr > 0.125):
            continue

        m_xp = _xp_for_cr(m_cr)
        if m_xp == 0 and m_cr != 0:
            continue

        # Skip mounts and vehicles — DM can add manually
        m_tags = m.get("tags", [])
        if "mount" in m_tags or "vehicle" in m_tags:
            continue

        entry = {
            "index": m["index"], "name": m["name"], "cr": m_cr, "xp": m_xp,
            "type": m_type, "size": m.get("size", ""),
            "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hp": m.get("hit_points", 0),
            "source": m.get("source", ""),
        }

        # Theme-based filtering
        theme_match = True
        if theme:
            theme_lower = theme.lower()
            theme_map = {
                "undead": ["undead", "skeleton", "zombie", "ghost", "wraith", "lich", "vampire",
                           "wight", "spect", "shadow", "ghoul", "mummy", "revenant", "banshee",
                           "death", "necro", "bone", "spirit", "haunt"],
                "dragon": ["dragon", "wyrm", "drake", "wyvern"],
                "demon": ["demon", "fiend", "devil", "abyssal", "infernal", "hell", "imp"],
                "goblin": ["goblin", "hobgoblin", "bugbear"],
                "orc": ["orc", "ogre", "troll", "goblinoid"],
                "beast": ["beast", "wolf", "bear", "cat", "snake", "spider", "hawk", "eagle",
                          "rat", "bat", "owl", "lizard", "crocodile", "shark", "dinosaur"],
                "cult": ["cult", "fanatic", "priest", "acolyte", "cultist", "archmage", "mage"],
                "bandit": ["bandit", "thug", "scout", "assassin", "spy", "veteran", "berserker",
                           "guard", "noble", "commoner"],
                "elemental": ["elemental", "fire", "water", "earth", "air", "mephit", "genie"],
                "giant": ["giant", "ogre", "troll", "ettin", "cyclops"],
                "fey": ["fey", "sprite", "pixie", "dryad", "satyr", "hag", "centaur"],
                "celestial": ["celestial", "angel", "deva", "planetar", "solar", "unicorn", "pegasus"],
                "construct": ["construct", "golem", "animated", "armor", "homunculus", "shield guardian"],
                "aberration": ["aberration", "beholder", "mind flayer", "aboleth", "slaad", "nothic"],
                "ooze": ["ooze", "slime", "jelly", "pudding", "cube"],
                "plant": ["plant", "treant", "shambling", "blight", "vine", "fungus"],
                "monstrosity": ["monstrosity", "chimera", "manticore", "owlbear", "basilisk",
                                "bulette", "roper", "rust monster", "yeti"],
                "swarm": ["swarm", "horde", "pack"],
            }
            theme_tags = None
            for k, v in theme_map.items():
                if k in theme_lower:
                    theme_tags = v
                    break
            if theme_tags:
                theme_match = any(
                    t in m_type or t in m_name
                    or any(t in tag.lower() for tag in m.get("tags", []) if isinstance(tag, str))
                    for t in theme_tags
                )
                # Also check the monster's tags
                for tag in m.get("tags", []) if isinstance(m.get("tags"), list) else []:
                    tag_lower = tag.lower() if isinstance(tag, str) else ""
                    if any(t in tag_lower for t in theme_tags):
                        theme_match = True
                        break

        if not theme_match:
            continue

        candidates.append(entry)

    # AI composition suggestion — send all candidates so AI has full choice
    if not candidates:
        for m in all_monsters:
            try:
                m_cr = float(m.get("challenge_rating", 0))
            except (TypeError, ValueError):
                continue
            if m_cr > max_cr or (m_cr < min_cr and m_cr > 0.125):
                continue
            if m_cr < 0.125:
                continue
            m_xp = _xp_for_cr(m_cr)
            if m_xp == 0:
                continue
            candidates.append({
                "index": m["index"], "name": m["name"], "cr": m_cr, "xp": m_xp,
                "type": m.get("type", "").lower(), "size": m.get("size", ""),
                "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
                "hp": m.get("hit_points", 0),
                "source": m.get("source", ""),
            })
    # Shuffle to vary AI picks, then sort by CR descending for readability
    random.shuffle(candidates)
    candidates.sort(key=lambda c: c["cr"], reverse=True)

    # Build party profile if campaign selected
    party_profile = None
    campaign_name = ""
    if campaign_id:
        try:
            cid = int(campaign_id)
            party_profile = _build_party_profile(db, cid)
            if party_profile:
                # Also get campaign name
                camp_row = db.execute(
                    "SELECT name FROM dm_campaigns WHERE id=?", (cid,)
                ).fetchone()
                if camp_row:
                    campaign_name = camp_row["name"]
                # Override party_size/party_level with actual values
                party_size = party_profile["summary"]["size"]
                party_level = max(1, int(party_profile["summary"]["avg_level"]))
                xp_budget = xp_per_char * party_size
        except (ValueError, TypeError):
            pass

    cr_info = f"Target CR: {target_cr_raw}" if target_cr_raw else f"Party: {party_size} level {party_level}"

    # Build party-specific prompt section
    party_section = ""
    if party_profile:
        chars = party_profile["characters"]
        s = party_profile["summary"]
        party_section = f"\n--- PARTY PROFILE (tailor encounter to counter these characters) ---\n"
        party_section += f"Campaign: {campaign_name}. {s['size']} characters, avg level {s['avg_level']}.\n"
        for ch in chars:
            saves_str = ", ".join(f"{k}+{v}" for k, v in sorted(ch["saves"].items()))
            subclass_str = f" ({ch['subclass']})" if ch.get("subclass") else ""
            party_section += (
                f"  {ch['name']} — {ch['race']} L{ch['level']} {ch['class']}"
                f"{subclass_str} | "
                f"AC{ch['ac']} HP{ch['hp']} | "
                f"Saves: {saves_str}\n"
            )
            if ch["resistances"]:
                party_section += f"    Resists: {', '.join(ch['resistances'])}\n"
            if ch["immunities"]:
                party_section += f"    Immune: {', '.join(ch['immunities'])}\n"
            if ch["vulnerabilities"]:
                party_section += f"    Vulnerable: {', '.join(ch['vulnerabilities'])}\n"
            if ch["spells"]:
                spell_preview = ", ".join(ch["spells"][:8])
                if len(ch["spells"]) > 8:
                    spell_preview += f" +{len(ch['spells']) - 8} more"
                party_section += f"    Spells: {spell_preview}\n"
            if ch["features"]:
                feat_str = ", ".join(str(f) for f in ch["features"][:5])
                if len(ch["features"]) > 5:
                    feat_str += f" +{len(ch['features']) - 5} more"
                party_section += f"    Features: {feat_str}\n"
        party_section += (
            f"\nParty summary: weakest saves = {', '.join(s['weakest_saves'])}, "
            f"strongest = {', '.join(s['strongest_saves'])}.\n"
        )
        if s["collective_resistances"]:
            party_section += f"Shared resistances: {', '.join(s['collective_resistances'])}.\n"
        if s["collective_immunities"]:
            party_section += f"Shared immunities: {', '.join(s['collective_immunities'])} — AVOID these damage types.\n"
        if s["collective_vulnerabilities"]:
            party_section += f"Shared vulnerabilities: {', '.join(s['collective_vulnerabilities'])} — EXPLOIT these.\n"
        if s["condition_immunities"]:
            party_section += f"Condition immunities: {', '.join(s['condition_immunities'])} — DON'T rely on these conditions.\n"
        party_section += (
            "COUNTER-PLAY STRATEGY: target weakest saves, bypass resistances, "
            "use attacks the party is vulnerable to, and pick monsters that counter "
            "the party's spellcasters and tanks. Make this encounter tactically challenging.\n"
        )

    # Boss rotation: track recently used bosses for this campaign
    boss_rotation_context = ""
    if campaign_id:
        try:
            recent = db.execute("""
                SELECT name, combat_state FROM dm_encounters
                WHERE campaign_id=? AND status='complete' AND combat_state IS NOT NULL
                ORDER BY updated_at DESC LIMIT 5
            """, (int(campaign_id),)).fetchall()
            if recent:
                boss_rotation_context = "\nRECENTLY USED BOSSES (avoid repeating these):\n"
                for r in recent:
                    cs = r["combat_state"]
                    try:
                        if isinstance(cs, str):
                            cs = json.loads(cs)
                        if isinstance(cs, dict):
                            monsters = cs.get("monsters", []) or cs.get("npcs", [])
                            boss_rotation_context += f"- {r['name']}: {', '.join(m[:30] for m in monsters[:3])}\n"
                    except:
                        pass
                boss_rotation_context += "Choose DIFFERENT monsters than those listed above. Be creative.\n"
        except:
            pass

    # ── Budget guidance: compute target raw XP and per-role budgets ──
    # DMG p.83 multiplier depends on expected monster count
    if encounter_type == "swarm":
        expected_mult = 2.5  # DMG p.83: 7-10 creatures
        expected_count_hint = "10 creatures"
        target_raw = xp_budget / expected_mult if xp_budget > 0 else 500
        minion_budget = int(target_raw / 10)  # per-creature budget
        boss_budget = 0
        elite_budget = 0
    elif encounter_type == "solo_lair":
        expected_mult = 1.0
        expected_count_hint = "1-3 creatures"
        boss_budget = int(xp_budget / expected_mult * 0.85) if xp_budget > 0 else 500
        elite_budget = 0
        minion_budget = int(xp_budget / expected_mult * 0.15) if xp_budget > 0 else 50
    else:
        expected_mult = 2.0  # skirmish, ambush, social: target 3-6
        expected_count_hint = "3-6 creatures"
        boss_budget = int(xp_budget / expected_mult * 0.45) if xp_budget > 0 else 500
        elite_budget = int(xp_budget / expected_mult * 0.25) if xp_budget > 0 else 200
        minion_budget = int(xp_budget / expected_mult * 0.12) if xp_budget > 0 else 50

    target_raw = xp_budget / expected_mult if xp_budget > 0 else 500

    def _fit_label(xp_val, budget_target):
        """Label how well a monster's XP fits a role budget."""
        if budget_target <= 0:
            return ""
        ratio = xp_val / budget_target
        if 0.5 <= ratio <= 1.4:
            return "PERFECT"
        elif 0.2 <= ratio < 0.5:
            return "CHEAP"
        elif ratio < 0.2:
            return "VERY CHEAP"
        elif 1.4 < ratio <= 2.2:
            return "PRICEY"
        else:
            return "TOO EXPENSIVE"

    # Categorize candidates by role budget fit
    boss_pool = []
    elite_pool = []
    minion_pool = []

    for c in candidates:
        xp = c["xp"]
        cr = c["cr"]
        # Exclude CR 0 creatures (bats, crabs, etc.) — they're not combat threats
        is_trivial = (cr == 0 and xp <= 10)
        if encounter_type == "swarm":
            if not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)
        elif encounter_type == "solo_lair":
            if cr >= party_level - 1:
                boss_pool.append(c)
            if not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)
        else:
            if boss_budget > 0 and xp >= boss_budget * 0.3 and xp <= boss_budget * 3.0:
                boss_pool.append(c)
            if elite_budget > 0 and xp >= elite_budget * 0.2 and xp <= elite_budget * 2.5:
                elite_pool.append(c)
            if minion_budget > 0 and not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)

    boss_pool.sort(key=lambda c: abs(c["xp"] - boss_budget) if boss_budget > 0 else c["xp"])
    elite_pool.sort(key=lambda c: abs(c["xp"] - elite_budget) if elite_budget > 0 else c["xp"])
    minion_pool.sort(key=lambda c: c["xp"])

    # Build role-labeled candidate lists for the prompt
    def _fmt_cr(cr_val):
        """Format CR value for display: 0.125 → 1/8, 0.25 → 1/4, etc."""
        if cr_val == 0.125: return "1/8"
        if cr_val == 0.25: return "1/4"
        if cr_val == 0.5: return "1/2"
        if cr_val == int(cr_val): return str(int(cr_val))
        return str(cr_val)

    def _cand_line(c, budget_target):
        label = _fit_label(c["xp"], budget_target) if budget_target > 0 else ""
        idx = c.get("index", c["name"].lower().replace(" ", "-"))
        return f"  [{idx}] {c['name']} | CR {_fmt_cr(c['cr'])} | {c['xp']} XP | {c['type']} | AC{c['ac']} HP{c['hp']} | {label}"

    boss_lines = "\n".join(_cand_line(c, boss_budget) for c in boss_pool[:15]) if boss_pool else "  (none available)"
    elite_lines = "\n".join(_cand_line(c, elite_budget) for c in elite_pool[:15]) if elite_pool else "  (none available)"
    minion_lines = "\n".join(_cand_line(c, minion_budget) for c in minion_pool[:18]) if minion_pool else "  (none available)"

    # Build budget guidance lines
    budget_lines = [f"BUDGET: {xp_budget} adjusted XP (DMG p.82 {difficulty} threshold)",
                    f"  Target raw XP: ~{int(target_raw)} (×{expected_mult} for {expected_count_hint})",
                    f"  Fill to ≥85% of budget — do NOT leave XP unused."]
    if boss_budget:
        budget_lines.append(f"  Boss target: ~{boss_budget} XP")
    if elite_budget:
        budget_lines.append(f"  Elite target: ~{elite_budget} XP each")
    if minion_budget:
        budget_lines.append(f"  Minion target: ≤{minion_budget} XP each" if encounter_type != "swarm" else f"  Per creature target: ~{minion_budget} XP (×10 to fill)")
    budget_section = "\n".join(budget_lines)

    # ── Tier-based scaling guidance ──
    tier_guides = {
        "low": "Tier 1 (L1-4): Keep it simple. 1-2 monster types max. Clear threat roles. No legendary actions. Focus on basic tactics — flanking, cover, simple terrain. Party has few resources; don't grind them down with 6+ creatures.",
        "mid": "Tier 2 (L5-10): Medium complexity. 2-4 monster types. Add synergy between types. Start using multi-attack monsters, spellcasters. Terrain and positioning matter. Party has extra attack and L3 spells; they can handle tactical challenges.",
        "high": "Tier 3 (L11-16): High complexity. 3-5 monster types. Include legendary/lair actions. Use resistances, immunities, and legendary saves. Multi-phase fight structure. Party has powerful magic and features; the encounter must challenge resource management.",
        "epic": "Tier 4 (L17-20): Maximum complexity. Multi-phase boss encounters. Legendary + lair actions. Environmental hazards that shift each round. Minion waves. Punish saves, bypass resistances. Party is demigod-level; anything less is a speed bump.",
    }
    tier_key = "low" if party_level <= 4 else "mid" if party_level <= 10 else "high" if party_level <= 16 else "epic"
    tier_guide = tier_guides[tier_key]

    # ── Archetype-specific design guides ──
    archetype_guides = {
        "skirmish": (
            "DESIGN CONCEPT: Think about monster synergy. Why do these specific creatures fight together? "
            "What's their combined tactical approach? (e.g., brute front line + ranged support + flanker). "
            "Pick 2-4 monster types that complement each other — a primary threat and creatures that enable it."
        ),
        "swarm": (
            "DESIGN CONCEPT: A horde encounter where numbers are the threat. Pick 1-2 creature types "
            "appropriate for the environment. Each creature should be weak individually (CR 0.125 to CR 2). "
            "Describe how the swarm attacks — does it surround, ambush from above, surge in waves? "
            "No boss. Role is always 'minion'."
        ),
        "ambush": (
            "DESIGN CONCEPT: The environment is the first enemy. The ambushers use terrain, surprise, "
            "and first-round alpha strike. Pick 2-4 creatures, at least one with Stealth proficiency "
            "or surprise ability. Tactics must describe: ambush setup, trigger, first-round plan, "
            "and fallback plan if surprise fails."
        ),
        "solo_lair": (
            "DESIGN CONCEPT: One legendary monster with lair actions. CR should be 2-5 above party level "
            "(solo monsters need the action economy advantage). Pick exactly 1 boss + 0-2 minions "
            "as lair guards. Tactics: lair action rotation, terrain hazards, escape route. "
            "Description: set up the lair — size, hazards, environment features."
        ),
        "rival_faction": (
            "DESIGN CONCEPT: Three-way fight. Two rival groups (2-3 creatures each) that hate each other "
            "more than the party — at least initially. E.g., goblins vs hobgoblins, cultists vs guards. "
            "In JSON use roles 'faction_a' and 'faction_b'. Tactics: How each faction fights AND when they "
            "might switch targets or flee."
        ),
        "social_combat": (
            "DESIGN CONCEPT: Negotiation first, violence second. Pick 1-3 intelligent creatures "
            "that have something the party wants (information, passage, treasure). "
            "Description: the social tension. Tactics: what they want + what happens if combat starts."
        ),
    }
    guide = archetype_guides.get(encounter_type, archetype_guides["skirmish"])

    # Build role-labeled candidate section
    role_section = ""
    if encounter_type != "swarm" and boss_pool:
        role_section += f"\nBOSS CANDIDATES (target ~{boss_budget} XP):\n{boss_lines}\n"
    if encounter_type not in ("swarm", "solo_lair") and elite_pool:
        role_section += f"\nELITE CANDIDATES (target ~{elite_budget} XP each):\n{elite_lines}\n"
    if minion_pool:
        role_section += f"\nMINION CANDIDATES (≤{minion_budget} XP each):\n{minion_lines}\n"

    # ── Phase 1: Algorithm picks monsters (AI can't do this reliably) ──
    # Pick boss from boss pool, or CR-appropriate candidates if pool empty
    fb_pool = boss_pool if boss_pool else (
        [c for c in candidates if abs(c["cr"] - party_level) <= 1 and c["cr"] >= 1]
        or candidates[:20])

    boss = random.choice(fb_pool) if fb_pool else None
    # Safety: validate boss is budget-appropriate (should be from pool, but guard)
    if boss and boss_budget > 0 and boss["xp"] > boss_budget * 3.5:
        print(f"[AI Encounter] Safety: boss {boss['name']} ({boss['xp']} XP > {boss_budget*3.5}) over budget — re-picking from filtered pool")
        budget_ok = [c for c in fb_pool if c["xp"] <= boss_budget * 3.0]
        boss = random.choice(budget_ok) if budget_ok else None
    picks = []
    if boss:
        picks.append({**boss, "role": "boss", "_suggested_count": 1})
        # Minions: prefer same-type from minion_pool
        if minion_pool:
            same_type = [c for c in minion_pool if c["type"] == boss["type"]]
            other = [c for c in minion_pool if c["index"] != boss["index"]]
            random.shuffle(same_type)
            random.shuffle(other)
            minion_picks = (same_type + other)[:3]
            for m in minion_picks:
                picks.append({**m, "role": "minion", "_suggested_count": 2 if m["cr"] <= 0.5 else 1})
    composition, xp_total = _assign_encounter_counts(picks, xp_budget, encounter_type) if picks else ([], 0)

    # Build composition summary for AI
    comp_name_list = []
    seen_names = set()
    for c in composition:
        n = c.get("name", "?")
        if n not in seen_names:
            seen_names.add(n)
            comp_name_list.append(f"{c.get('count', 1)}× {n}")
    monster_str = ", ".join(comp_name_list) or "various creatures"

    # ── Phase 2: AI writes narrative for the actual composition ──
    ai_prompt = f"""Write flavor text for a D&D 5e encounter:
Difficulty: {difficulty.upper()} | Setting: {environment} | Type: {encounter_type}
Party: {cr_info}
Monsters: {monster_str}
{tier_guide}{party_section}{boss_rotation_context}

Write a vivid scene description that sets up WHY these specific monsters are here and
how they work together. Then describe their tactics and any dynamic element.

Return ONLY valid JSON:
{{"name": "short evocative encounter name",
  "description": "vignette setting the scene and why these creatures are together",
  "tactics": "2-3 sentences: terrain use, opening combo, how monsters adapt when hurt",
  "dynamic": "1 sentence about what changes mid-fight (reinforcements, enrage, terrain shift, morale)"}}"""
    
    print(f"[AI Encounter] Phase 1: {monster_str} — calling AI for flavor")
    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    ai = _extract_json(text) if text else None
    if ai:
        print(f"[AI Encounter] Phase 2: name={bool(ai.get('name'))} desc={bool(ai.get('description'))} tactics={bool(ai.get('tactics'))}")
    else:
        print(f"[AI Encounter] Phase 2 FAILED, using generic flavor")

    name = (ai.get("name") or f"{environment.title()} Encounter") if ai else f"{environment.title()} Encounter"
    desc = ((ai.get("description") or "") if ai else "") or f"A {difficulty} encounter in a {environment} setting with {monster_str}."
    tactics = (ai.get("tactics") or "") if ai else ""
    dynamic = (ai.get("dynamic") or "") if ai else ""

    print(f"[AI Encounter] Final composition: {monster_str}")

    # Calculate adjusted XP multiplier (DMG p.83 — Encounter Multipliers)
    total_monsters = sum(c.get("count", 1) for c in composition)
    if total_monsters == 1: mult = 1.0
    elif total_monsters == 2: mult = 1.5
    elif 3 <= total_monsters <= 6: mult = 2.0
    elif 7 <= total_monsters <= 10: mult = 2.5
    elif 11 <= total_monsters <= 14: mult = 3.0
    else: mult = 4.0
    adjusted_xp = int(xp_total * mult)
    budget_pct = int((adjusted_xp / xp_budget * 100)) if xp_budget > 0 else 100

    return JSONResponse({
        "name": name or (ai.get("name") or f"{environment.title()} Encounter") if ai else f"{environment.title()} Encounter",
        "description": desc or f"A {difficulty} encounter in a {environment} setting.",
        "tactics": tactics or "",
        "dynamic": dynamic,
        "composition": composition,
        "xp": {"raw_total": xp_total, "adjusted": adjusted_xp, "budget": xp_budget, "budget_pct": budget_pct},
        "difficulty": difficulty.capitalize(),
        "party": {"level": party_level, "size": party_size},
    })


@router.post("/api/dm/ai/build-npc", response_class=JSONResponse)
async def dm_ai_build_npc(request: Request):
    """AI-generated NPC with full build. Uses same PHB-grounded engine as character builder."""
    user = require_user(request)
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
    mods = {ability: (abilities[ability] - 10) // 2 for ability in abilities}
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

    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
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


# ── AI Trap Builder ───────────────────────────────────────────────────────────

@router.post("/api/dm/ai/build-trap", response_class=JSONResponse)
async def dm_ai_build_trap(request: Request):
    """AI-generated custom trap following DMG/UA trap creation guidelines."""
    user = require_user(request)
    data = await request.json()
    danger = data.get("danger", "dangerous")
    trap_type = data.get("type", "mechanical")
    theme = data.get("theme", "")
    location = data.get("location", "")
    party_level = data.get("party_level", "")

    # DMG presets for the AI to reference
    danger_guidelines = {
        "setback": "DC 10-11, attack +3-5, 1d10 damage, minor inconvenience — a trip wire that tangles, a minor poison that sickens for an hour",
        "dangerous": "DC 12-15, attack +6-8, 2d10 damage, likely to injure — collapsing floor, scything blade, glyph of warding",
        "deadly": "DC 16-20, attack +9-12, 4d10+ damage, could kill — rolling boulder trap, sphere of annihilation, prismatic wall"
    }
    guidelines = danger_guidelines.get(danger, danger_guidelines["dangerous"])
    level_note = f"\nParty level: ~{party_level}" if party_level else ""

    prompt = f"""Design a creative D&D 5e trap following these guidelines:

Type: {trap_type}
Danger: {danger} ({guidelines}){level_note}
Theme/location: {theme or 'any'}{' — ' + location if location else ''}

Return a single JSON object with these keys:
{{
  "name": "Short evocative trap name (4-6 words)",
  "trigger": "What activates it (1 sentence)",
  "detection_dc": <int, appropriate for {danger}>,
  "detection_skill": "Perception or Investigation",
  "detection_detail": "What observant characters notice (1 sentence)",
  "disarm_dc": <int, same as detection_dc or slightly higher>,
  "disarm_method": "How to disable (e.g. 'Dexterity (thieves\\' tools)', 'Arcana', 'Strength')",
  "disarm_detail": "What happens when disarmed successfully (1 sentence)",
  "effect": "Full mechanical description with save DC, damage, and consequences (2-3 sentences)",
  "save_dc": <int, appropriate for {danger}>,
  "save_ability": "Dexterity, Constitution, Wisdom, etc.",
  "damage": "e.g. '2d10' or '4d10+5'",
  "damage_type": "piercing, fire, poison, etc.",
  "area": "e.g. '10 ft. square' or '30 ft. line'",
  "description": "Flavor text and DM tips (1-2 sentences)"
}}

Use the DMG guidelines for DCs and damage. Make the trap feel unique and cinematic.
Keep within the danger level bounds — don't overpower a setback trap or underpower a deadly one."""

    text = await _call_gemini(prompt) or await _call_openrouter(prompt) or await _call_ollama(prompt)
    ai = _extract_json(text) if text else None

    if not ai:
        # Fallback: generate a basic trap from the danger presets
        presets = {
            "setback": {"name": f"{theme or 'Simple'} {trap_type.title()} Trap", "save_dc": 10, "damage": "1d10",
                        "trigger": "Pressure plate or trip wire", "effect": "A minor hazard is triggered."},
            "dangerous": {"name": f"{theme or 'Hidden'} {trap_type.title()} Trap", "save_dc": 14, "damage": "2d10",
                          "trigger": "A concealed mechanism activates", "effect": "A dangerous hazard strikes the party."},
            "deadly": {"name": f"{theme or 'Devastating'} {trap_type.title()} Trap", "save_dc": 18, "damage": "4d10",
                       "trigger": "An intricate trigger mechanism fires", "effect": "A lethal hazard threatens to destroy everything in range."},
        }
        preset = presets.get(danger, presets["dangerous"])
        ai = preset

    return JSONResponse({
        "name": ai.get("name", f"{danger.title()} {trap_type.title()} Trap"),
        "trigger": ai.get("trigger", ""),
        "detection_dc": ai.get("detection_dc", 10),
        "detection_skill": ai.get("detection_skill", "Perception"),
        "detection_detail": ai.get("detection_detail", ""),
        "disarm_dc": ai.get("disarm_dc", ai.get("detection_dc", 10)),
        "disarm_method": ai.get("disarm_method", f"Dexterity (thieves' tools)"),
        "disarm_detail": ai.get("disarm_detail", ""),
        "effect": ai.get("effect", ""),
        "save_dc": ai.get("save_dc", 15),
        "save_ability": ai.get("save_ability", "Dexterity"),
        "damage": ai.get("damage", ""),
        "damage_type": ai.get("damage_type", ""),
        "area": ai.get("area", ""),
        "description": ai.get("description", ""),
        "danger": danger,
        "type": trap_type,
    })


# ── DM Tools: Manual Search ───────────────────────────────────────────────
@router.post("/api/dm/search-manuals", response_class=JSONResponse)
async def dm_search_manuals(request: Request):
    """Full-text search across all D&D reference manuals (cached PDF text)."""
    user = require_user(request)
    data = await request.json()
    query = (data.get("query", "") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"results": [], "error": "Query too short"})

    results = _search_manuals(query, max_results=25)
    return JSONResponse({"results": results, "query": query, "total": len(results)})


@router.post("/api/dm/search-manuals/summarize", response_class=JSONResponse)
async def dm_search_manuals_summarize(request: Request):
    """AI-powered research summary: search manuals, then distill with LLM."""
    user = require_user(request)
    data = await request.json()
    query = (data.get("query", "") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"summary": "", "error": "Query too short"})

    results = _search_manuals(query, max_results=30)

    if not results:
        return JSONResponse({"summary": "No matches found across any reference manuals.", "results": []})

    # Build a research context from the search results
    context_blocks = []
    for r in results:
        page_str = f" (est. p.{r['page']})" if r.get("page") else ""
        context_blocks.append(f"[{r['book']}{page_str}] {r['snippet']}")
    research_text = "\n\n".join(context_blocks[:4000])  # cap for token budget

    ai_prompt = f"""You are a D&D 5e rules researcher. The user searched the reference manuals for:
\"{query}\"

Below are the relevant excerpts found across the manuals. Synthesize them into a comprehensive, well-organized summary. Include:
1. A clear, concise answer to what was searched
2. Key rules, mechanics, and page references where available
3. Any nuances, edge cases, or related rules
4. Differences between sources if applicable (e.g. PHB vs DMG vs XGE)

Format the response in plain text with clear section breaks. Keep it grounded in the excerpts — don't invent rules not present.

RESEARCH EXCERPTS:
{research_text}

RULES SUMMARY:"""

    summary = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    if not summary:
        # Fallback: return raw results without AI
        return JSONResponse({"summary": None, "results": results, "note": "AI unavailable — raw results shown"})

    return JSONResponse({"summary": summary.strip(), "results": results, "query": query})


# ── DM Tools: Encounter Management
# ── DM Tools: Encounter Management ───────────────────────────────────────

@router.post("/api/dm/encounter/create", response_class=JSONResponse)
async def dm_encounter_create(request: Request):
    """Create a new encounter."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_encounters (user_id, name, description, location, environment, difficulty, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New Encounter"),
        data.get("description", ""),
        data.get("location", ""),
        data.get("environment", ""),
        data.get("difficulty", "medium"),
        data.get("notes", ""),
    ))
    db.commit()
    enc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": enc_id, "ok": True})


@router.get("/api/dm/encounters", response_class=JSONResponse)
async def dm_encounters_list(request: Request):
    """List all encounters."""
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_encounters {where} ORDER BY created_at DESC", params
    ).fetchall()]
    # Count participants
    for r in rows:
        npcs = db.execute(
            "SELECT COUNT(*) as cnt FROM dm_encounter_npcs WHERE encounter_id = ?",
            (r["id"],)
        ).fetchone()
        r["npc_count"] = npcs["cnt"] if npcs else 0
    db.close()
    return JSONResponse({"encounters": rows})


@router.get("/api/dm/encounter/{enc_id}", response_class=JSONResponse)
async def dm_encounter_detail(enc_id: int, request: Request):
    """Full encounter detail with NPC participants."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404)
    enc = dict(row)
    # Get NPC participants
    participants = [dict(r) for r in db.execute("""
        SELECT en.*, n.name as npc_name, n.race, n.class_name, n.level, n.is_enemy,
               n.hp_max as npc_hp_max, n.ac as npc_ac, n.role, n.xp_reward
        FROM dm_encounter_npcs en
        LEFT JOIN dm_npcs n ON n.id = en.npc_id
        WHERE en.encounter_id = ?
        ORDER BY en.initiative DESC
    """, (enc_id,)).fetchall()]
    for p in participants:
        # Creature-only entries (npc_id = -1) use creature_data, not the sentinel NPC
        if p.get("npc_id") == -1 or not p.get("npc_name"):
            try: cd = json.loads(p.get("creature_data") or "{}")
            except: cd = {}
            p["npc_name"] = cd.get("name", "Unknown")
            p["race"] = cd.get("race", "")
            p["class_name"] = cd.get("class_name", "")
            p["level"] = cd.get("level", 1)
            p["is_enemy"] = cd.get("is_enemy", 0)
            p["npc_hp_max"] = cd.get("hp_max", p.get("hp_max", 10))
            p["npc_ac"] = cd.get("ac", p.get("ac", 10))
            p["role"] = cd.get("role", "")
            p["xp_reward"] = cd.get("xp_reward", 0)
            p["_monster_index"] = cd.get("_monster_index", "")
    # Compute spell slots for caster NPCs
    for p in participants:
        cls = p.get("class_name", "")
        lvl = p.get("level", 1)
        if cls and get_caster_type(cls) != "none":
            slots = get_spell_slots(cls, lvl)
            p["npc_spell_slots"] = slots.get("by_level", {})
        else:
            p["npc_spell_slots"] = {}
    enc["participants"] = participants
    xp_total = sum(p.get("xp_reward", 0) or 0 for p in participants)
    db.close()
    return JSONResponse({"encounter": enc, "xp_total": xp_total})


@router.post("/api/dm/encounter/{enc_id}/add-npc", response_class=JSONResponse)
async def dm_encounter_add_npc(enc_id: int, request: Request):
    """Add an NPC to an encounter."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    # Verify encounter exists
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    # Verify NPC exists
    npc = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    if not npc:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")
    # Add to encounter
    init = int(data.get("initiative", 0))
    db.execute("""
        INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, ac)
        VALUES (?,?,?,?,?,?)
    """, (enc_id, npc_id, init, npc["hp_current"], npc["hp_max"], npc["ac"]))
    db.commit()
    en_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": en_id, "ok": True})


@router.post("/api/dm/encounter/{enc_id}/add-creature", response_class=JSONResponse)
async def dm_encounter_add_creature(enc_id: int, request: Request):
    """Add a monster or manual NPC to an encounter — doesn't need a dm_npcs row."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    init = int(data.get("initiative", 0))
    hp = int(data.get("hp", data.get("hp_current", 10)))
    hp_max = int(data.get("hp_max", hp))
    ac = int(data.get("ac", 10))
    creature_data = json.dumps({
        "name": data.get("name", "Unknown"),
        "race": data.get("race", ""),
        "class_name": data.get("class_name", ""),
        "level": data.get("level", 1),
        "is_enemy": data.get("is_enemy", 0),
        "hp_max": hp_max,
        "ac": ac,
        "role": data.get("role", ""),
        "xp_reward": data.get("xp_reward", 0),
        "_monster_index": data.get("_monster_index", ""),
        # Full stat block (from manual NPCs, SRD monsters)
        "ability_scores": data.get("ability_scores"),
        "spellcasting": data.get("spellcasting"),
        "features": data.get("features", []),
        "actions": data.get("actions", []),
        "skills": data.get("skills", {}),
        "saving_throws": data.get("saving_throws", {}),
        "speed": data.get("speed", ""),
        "alignment": data.get("alignment", ""),
        "description": data.get("description", ""),
        "equipment": data.get("equipment", []),
        "senses": data.get("senses", ""),
        "languages": data.get("languages", []),
        "damage_resistances": data.get("damage_resistances", []),
        "damage_immunities": data.get("damage_immunities", []),
        "condition_immunities": data.get("condition_immunities", []),
        "challenge_rating": data.get("challenge_rating"),
    })
    # Ensure a placeholder NPC exists for creature-only entries (FK constraint)
    sentinel = db.execute("SELECT id FROM dm_npcs WHERE id = -1").fetchone()
    if not sentinel:
        db.execute("INSERT INTO dm_npcs (id, user_id, name, race, class_name, level, hp_current, hp_max, ac) VALUES (-1, ?, '__sentinel__', '', '', 0, 1, 1, 10)", (user["id"],))
    db.execute("""
        INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, ac, creature_data)
        VALUES (?, -1, ?, ?, ?, ?, ?)
    """, (enc_id, init, hp, hp_max, ac, creature_data))
    db.commit()
    en_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": en_id, "ok": True})


@router.post("/api/dm/encounter/{enc_id}/remove-npc", response_class=JSONResponse)
async def dm_encounter_remove_npc(enc_id: int, request: Request):
    """Remove an NPC from an encounter."""
    user = require_user(request)
    data = await request.json()
    en_id = int(data.get("en_id", 0))
    db = get_db()
    db.execute("""
        DELETE FROM dm_encounter_npcs
        WHERE id = ? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id = ?)
    """, (en_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@router.post("/api/dm/encounter/{enc_id}/update-initiative", response_class=JSONResponse)
async def dm_encounter_update_init(enc_id: int, request: Request):
    """Update initiative, HP, defeated state, and individual spell slots for encounter NPCs."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    for entry in data.get("participants", []):
        en_id = int(entry.get("id", 0))
        init = int(entry.get("initiative", 0))
        defeated = 1 if entry.get("defeated", False) else 0
        spell_slots_used = entry.get("spell_slots_used", {})
        if isinstance(spell_slots_used, dict):
            spell_slots_used = json.dumps(spell_slots_used)
        if "hp_current" in entry:
            hp_cur = int(entry["hp_current"])
            set_parts = ["initiative=?", "hp_current=?"]
            set_vals = [init, hp_cur]
            if "defeated" in entry:
                set_parts.append("defeated=?")
                set_vals.append(defeated)
            if "spell_slots_used" in entry:
                set_parts.append("spell_slots_used=?")
                set_vals.append(spell_slots_used)
            set_vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(set_parts)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", set_vals)
        else:
            # Only update fields explicitly present (safe partial update)
            set_parts = ["initiative=?"]
            set_vals = [init]
            if "defeated" in entry:
                set_parts.append("defeated=?")
                set_vals.append(defeated)
            if "spell_slots_used" in entry:
                set_parts.append("spell_slots_used=?")
                set_vals.append(spell_slots_used)
            set_vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(set_parts)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", set_vals)

    # If a single participant should be updated (mark defeated / update HP)
    if "single" in data:
        s = data["single"]
        en_id = int(s.get("id", 0))
        updates = []
        vals = []
        if "hp_current" in s:
            updates.append("hp_current=?")
            vals.append(int(s["hp_current"]))
        if "defeated" in s:
            updates.append("defeated=?")
            vals.append(1 if s["defeated"] else 0)
        if "spell_slots_used" in s:
            up = s["spell_slots_used"]
            if isinstance(up, dict):
                up = json.dumps(up)
            updates.append("spell_slots_used=?")
            vals.append(up)
        if updates:
            vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(updates)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", vals)

    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@router.post("/api/dm/encounter/{enc_id}/roll-initiative", response_class=JSONResponse)
async def dm_encounter_roll_initiative(enc_id: int, request: Request):
    """Roll d20 + DEX modifier for all encounter participants. PHB p.189."""
    import random
    user = require_user(request)
    db = get_db()
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404)

    participants = [dict(r) for r in db.execute("""
        SELECT en.id as en_id, en.initiative, en.hp_current, en.hp_max, en.ac, en.defeated,
               en.creature_data,
               n.id as npc_id, n.name, n.race, n.class_name, n.level, n.is_enemy,
               n.dexterity, n.role, n.hp_max as npc_hp_max
        FROM dm_encounter_npcs en
        LEFT JOIN dm_npcs n ON n.id = en.npc_id
        WHERE en.encounter_id = ?
    """, (enc_id,)).fetchall()]

    # Override sentinel data for creature-only entries (npc_id = -1)
    for p in participants:
        if p.get("npc_id") == -1 or not p.get("name"):
            try: cd = json.loads(p.get("creature_data") or "{}")
            except: cd = {}
            p["name"] = cd.get("name", p.get("name") or "Unknown")
            p["race"] = cd.get("race", p.get("race") or "")
            p["class_name"] = cd.get("class_name", p.get("class_name") or "")
            p["level"] = cd.get("level", p.get("level") or 1)
            p["is_enemy"] = cd.get("is_enemy", p.get("is_enemy") or 0)
            p["npc_hp_max"] = cd.get("hp_max", p.get("npc_hp_max") or p.get("hp_max", 10))
            p["ac"] = cd.get("ac", p.get("ac") or 10)
            p["role"] = cd.get("role", p.get("role") or "")
            # Use creature DEX for initiative if available, otherwise default to 10
            if not p.get("dexterity"):
                p["dexterity"] = 10

    results = []
    for p in participants:
        dex_mod = (p.get("dexterity", 10) - 10) // 2
        roll = random.randint(1, 20)
        total = roll + dex_mod
        results.append({
            "en_id": p["en_id"],
            "npc_id": p["npc_id"],
            "name": p["name"],
            "race": p.get("race", ""),
            "class_name": p.get("class_name", ""),
            "level": p.get("level", 1),
            "is_enemy": p.get("is_enemy", 0),
            "role": p.get("role", ""),
            "ac": p.get("ac", 10),
            "hp_current": p["hp_current"],
            "hp_max": p["hp_max"],
            "defeated": p.get("defeated", 0),
            "dex_mod": dex_mod,
            "roll": roll,
            "initiative": total,
            "creature_data": p.get("creature_data"),
        })

    # Sort: alive first by initiative desc, then defeated
    results.sort(key=lambda r: (r["defeated"] or 0, -(r["initiative"])))

    # Save to DB
    for r in results:
        db.execute("UPDATE dm_encounter_npcs SET initiative=? WHERE id=?",
                   (r["initiative"], r["en_id"]))
    db.commit()
    db.close()
    return JSONResponse({"participants": results, "ok": True})


@router.post("/api/dm/encounter/{enc_id}/combat-state", response_class=JSONResponse)
async def dm_encounter_combat_state(enc_id: int, request: Request):
    """Save or load combat state (round, turn_index, participant order)."""
    user = require_user(request)
    db = get_db()
    enc = db.execute("SELECT id, combat_state FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404)

    data = await request.json()
    action = data.get("action", "load")

    if action == "save":
        state = json.dumps({
            "round": data.get("round", 1),
            "turn_index": data.get("turn_index", 0),
            "initiative_order": data.get("initiative_order", []),
            "benched_en_ids": data.get("benched_en_ids", []),
            "player_participants": data.get("player_participants", []),
            "campaign_id": data.get("campaign_id")
        })
        db.execute("UPDATE dm_encounters SET combat_state=? WHERE id=?", (state, enc_id))
        db.commit()
        db.close()
        return JSONResponse({"ok": True})

    # Load
    try:
        state = json.loads(enc["combat_state"] or "{}")
    except (json.JSONDecodeError, TypeError):
        state = {}
    db.close()
    return JSONResponse({
        "round": state.get("round", 1),
        "turn_index": state.get("turn_index", 0),
        "initiative_order": state.get("initiative_order", []),
        "benched_en_ids": state.get("benched_en_ids", []),
        "player_participants": state.get("player_participants", []),
        "campaign_id": state.get("campaign_id"),
        "ok": True
    })


@router.post("/api/dm/encounter/{enc_id}/update", response_class=JSONResponse)
async def dm_encounter_update(enc_id: int, request: Request):
    """Update encounter fields."""
    user = require_user(request)
    data = await request.json()
    allowed = {"name","description","location","environment","difficulty","status","notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        db = get_db()
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [enc_id, user["id"]]
        db.execute(f"UPDATE dm_encounters SET {sets} WHERE id=? AND user_id=?", vals)
        db.commit()
        db.close()
    return JSONResponse({"ok": True})


@router.post("/api/dm/encounter/{enc_id}/delete", response_class=JSONResponse)
async def dm_encounter_delete(enc_id: int, request: Request):
    """Delete an encounter."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_encounter_npcs WHERE encounter_id IN (SELECT id FROM dm_encounters WHERE id=? AND user_id=?)",
               (enc_id, user["id"]))
    db.execute("DELETE FROM dm_encounters WHERE id=? AND user_id=?", (enc_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── DM Tools: Campaign Management ────────────────────────────────────────

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
async def dm_campaigns_list(request: Request):
    """List all campaigns with live character stats."""
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_campaigns {where} ORDER BY created_at DESC", params
    ).fetchall()]

    # Enrich each campaign's characters with live data from the characters table
    for camp in rows:
        try:
            char_ids = json.loads(camp.get("characters") or "[]")
        except (json.JSONDecodeError, TypeError):
            camp["characters"] = []
            continue

        enriched = []
        for entry in char_ids:
            cid = entry.get("id") if isinstance(entry, dict) else entry
            row = db.execute("""
                SELECT id, name, race, subrace, class_name, subclass, level,
                       hp_current, hp_max, temp_hp, ac, strength, dexterity, constitution,
                       intelligence, wisdom, charisma, proficiency_bonus, speed,
                       inspiration, exhaustion, passive_perception, hit_dice, hit_dice_used
                FROM characters WHERE id=?
            """, (cid,)).fetchone()
            if row:
                ch = dict(row)
                # Compute modifiers
                mods = {s: (ch[s] - 10) // 2 for s in
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
        db.commit()
        db.close()
    return JSONResponse({"ok": True})


@router.post("/api/dm/campaign/{camp_id}/delete", response_class=JSONResponse)
async def dm_campaign_delete(camp_id: int, request: Request):
    """Delete a campaign."""
    user = require_user(request)
    db = get_db()
    # Verify ownership
    row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not row:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    db.execute("DELETE FROM dm_campaigns WHERE id=?", (camp_id,))
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
    # Verify character exists (any account — DMs can add any character)
    char_row = db.execute("SELECT id, name, class_name, level, race FROM characters WHERE id=?",
                          (char_id,)).fetchone()
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
    db.commit()
    db.close()
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
    db.commit()
    db.close()
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
            hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
            try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
            except: hp = 10
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


@router.get("/api/dm/user-characters", response_class=JSONResponse)
async def dm_user_characters(request: Request):
    """List user's characters (for campaign party picker)."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT id, name, race, class_name, level, subclass FROM characters ORDER BY name"
    ).fetchall()]
    db.close()
    return JSONResponse({"characters": rows})

