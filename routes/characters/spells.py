"""Spell/combat management routes — add spell, magic initiate, prepared,
available spells, AI selection, attunement, feature uses.

Extracted from routes/characters/all.py (2026-07-31). Imports helpers
from main / data / services.leveling / schemas only — never from
all.py (avoids circulars).
"""

import json

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from main import (
    get_db, require_user, _require_owned, _equipped_names, _normalize_equipped,
    PREPARED_CASTERS, SPELL_DICE, SRD_SPELLS, SUBCLASS_FEATURES,
)
from data import SPELLS_KNOWN_CASTERS
from routes.schemas import AddSpell
from services.leveling import (
    MAGIC_INITIATE_CLASSES,
    _scaled_dice_display,
    get_spell_slots,
    get_srd_spells_for_class,
)

router = APIRouter()
@router.post("/api/character/{char_id}/add-spell", response_class=JSONResponse)
async def add_spell(char_id: int, request: Request, body: AddSpell):
    """Add a spell to a character's spell list."""
    user = require_user(request)
    db = get_db()
    db.execute("INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
               (char_id, body.name, body.level, int(body.prepared), body.slots_max or 0, 0))
    db.commit()
    sp_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": sp_id})


# ── Magic Initiate feat configuration ──────────────────────────────────────
@router.get("/api/character/{char_id}/magic-initiate", response_class=JSONResponse)
async def get_magic_initiate(char_id: int, request: Request):
    """Return Magic Initiate configuration: class, spells, usage state."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    asi_hist = json.loads(char.get("asi_history", "[]"))
    for entry in asi_hist:
        if entry.get("type") == "feat" and entry.get("feat") == "magic_initiate":
            return JSONResponse(entry.get("magic_initiate", {}))
    return JSONResponse({})


@router.post("/api/character/{char_id}/magic-initiate", response_class=JSONResponse)
async def save_magic_initiate(char_id: int, request: Request):
    """Save Magic Initiate choices: class, cantrips, level-1 spell. Auto-adds spells to spellbook."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    
    chosen_class = data.get("class", "")
    chosen_cantrips = data.get("cantrips", [])
    chosen_spell = data.get("spell", "")
    
    if chosen_class not in MAGIC_INITIATE_CLASSES:
        db.close()
        return JSONResponse({"error": f"Invalid class: {chosen_class}"}, status_code=400)
    
    # Update asi_history with magic_initiate config
    asi_hist = json.loads(char.get("asi_history", "[]"))
    spellcasting_ability = {
        "Bard": "charisma", "Sorcerer": "charisma", "Warlock": "charisma",
        "Cleric": "wisdom", "Druid": "wisdom",
        "Wizard": "intelligence",
    }.get(chosen_class, "intelligence")
    found = False
    for entry in asi_hist:
        if entry.get("type") == "feat" and entry.get("feat") == "magic_initiate":
            entry["magic_initiate"] = {
                "class": chosen_class,
                "cantrips": chosen_cantrips,
                "spell": chosen_spell,
                "used": data.get("used", False),
                "spellcasting_ability": spellcasting_ability,
            }
            found = True
            break
    if not found:
        db.close()
        return JSONResponse({"error": "Character does not have Magic Initiate feat"}, status_code=400)
    
    # Auto-add spells to character spellbook (remove old Magic Initiate spells first)
    db.execute("DELETE FROM character_spells WHERE character_id = ? AND source = 'Magic Initiate'", (char_id,))
    
    for c_name in chosen_cantrips:
        db.execute(
            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used, source) VALUES (?,?,?,?,?,?,?)",
            (char_id, c_name, 0, 1, 0, 0, "Magic Initiate"))
    if chosen_spell:
        db.execute(
            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used, source) VALUES (?,?,?,?,?,?,?)",
            (char_id, chosen_spell, 1, 1, 1, 0, "Magic Initiate"))
    
    db.execute("UPDATE characters SET asi_history = ? WHERE id = ?", (json.dumps(asi_hist), char_id))
    db.commit()
    db.close()
    
    return JSONResponse({"ok": True, "magic_initiate": {
        "class": chosen_class,
        "cantrips": chosen_cantrips,
        "spell": chosen_spell,
        "used": data.get("used", False),
        "spellcasting_ability": spellcasting_ability,
    }})


@router.post("/api/character/{char_id}/combat-notes", response_class=JSONResponse)
async def save_combat_notes(char_id: int, request: Request):
    """Save combat notes for a character."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    data = await request.json()
    notes = (data.get("notes") or "").strip()
    db.execute("UPDATE characters SET combat_notes = ? WHERE id = ?", (notes, char_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@router.post("/api/character/{char_id}/magic-initiate/use", response_class=JSONResponse)
async def toggle_magic_initiate_use(char_id: int, request: Request):
    """Toggle the 1/day Magic Initiate spell usage."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    asi_hist = json.loads(char.get("asi_history", "[]"))
    for entry in asi_hist:
        if entry.get("type") == "feat" and entry.get("feat") == "magic_initiate":
            mi = entry.setdefault("magic_initiate", {})
            mi["used"] = not mi.get("used", False)
            break
    db.execute("UPDATE characters SET asi_history = ? WHERE id = ?", (json.dumps(asi_hist), char_id))
    db.commit()
    db.close()
    return JSONResponse({"used": mi.get("used", False)})


@router.post("/api/character/{char_id}/magic-initiate/reset", response_class=JSONResponse)
async def reset_magic_initiate_use(char_id: int, request: Request):
    """Reset all Magic Initiate 1/day uses to available (called on long rest)."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    asi_hist = json.loads(char.get("asi_history", "[]"))
    for entry in asi_hist:
        if entry.get("type") == "feat" and entry.get("feat") == "magic_initiate":
            mi = entry.setdefault("magic_initiate", {})
            mi["used"] = False
    db.execute("UPDATE characters SET asi_history = ? WHERE id = ?", (json.dumps(asi_hist), char_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── Generic feat configuration ──────────────────────────────────────────
FEAT_SETUP_CHOICES = {
    "elemental_adept": {
        "label": "Choose a damage type",
        "field": "type",
        "choices": ["Acid", "Cold", "Fire", "Lightning", "Thunder"],
    },
    "ley_initiate": {
        "label": "Choose an ability to increase",
        "field": "ability",
        "choices": ["Intelligence", "Wisdom"],
    },
    "martial_adept": {
        "label": "Choose 2 Battle Master maneuvers",
        "field": "maneuvers",
        "kind": "maneuvers",
        "picks": 2,
    },
}


@router.post("/api/character/{char_id}/feat-config", response_class=JSONResponse)
async def save_feat_config(char_id: int, request: Request):
    """Save configuration for any feat that requires choices (Elemental Adept, etc.)."""
    user = require_user(request)
    data = await request.json()
    feat_key = data.get("feat", "")
    config = data.get("config", {})
    
    # Normalize feat key (handle both "ley_initiate" and "Ley Initiate")
    _nk = feat_key.lower().replace(" ", "_")
    if _nk not in FEAT_SETUP_CHOICES:
        return JSONResponse({"error": f"No setup needed for {feat_key}"}, status_code=400)
    
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    asi_hist = json.loads(char.get("asi_history", "[]"))
    
    found = False
    for entry in asi_hist:
        if entry.get("type") == "feat":
            _ef = entry.get("feat", "")
            # Normalize both for comparison (handle space vs underscore variants)
            if _ef.lower().replace(" ", "_") == _nk:
                entry["feat_config"] = config
                found = True
                break
    
    if not found:
        db.close()
        return JSONResponse({"error": f"Character does not have {feat_key} feat"}, status_code=400)
    
    db.execute("UPDATE characters SET asi_history = ? WHERE id = ?", (json.dumps(asi_hist), char_id))
    
    # For martial_adept: also save selected maneuvers to characters.maneuvers
    if _nk == "martial_adept" and config.get("maneuvers"):
        man_list = config["maneuvers"]
        if isinstance(man_list, list):
            existing_man = json.loads(char.get("maneuvers", "[]"))
            # Add any new maneuvers not already known
            for m in man_list:
                if m not in existing_man:
                    existing_man.append(m)
            db.execute("UPDATE characters SET maneuvers = ? WHERE id = ?", (json.dumps(existing_man), char_id))
    
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "config": config})


@router.get("/api/character/{char_id}/available-spells", response_class=JSONResponse)
async def available_spells(char_id: int, request: Request):
    """Return spells this character can learn — filtered by class, level, race, subclass.
    Excludes spells already known."""
    import re
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    class_name = char["class_name"] or ""
    subclass = char["subclass"] or ""
    race_name = char["race"] or ""
    subrace = char["subrace"] or ""
    # Allow override for level-up preview (still at old level in DB)
    level = int(request.query_params.get("level", 0)) or max(1, int(char.get("level", 1) or 1))

    # Get already-known spell names
    known_rows = db.execute("SELECT spell_name FROM character_spells WHERE character_id = ?",
                            (char_id,)).fetchall()
    known_names = {r[0].lower() for r in known_rows}
    db.close()

    # Determine max spell level this character can cast
    max_spell_level = 0
    cantrips_ok = True
    if class_name in SPELLS_KNOWN_CASTERS or class_name in PREPARED_CASTERS or class_name == "Warlock":
        slots = get_spell_slots(class_name, level)
        if class_name == "Warlock":
            max_spell_level = slots.get("slot_level", 0) if slots else 0
        elif slots and slots.get("by_level"):
            max_spell_level = max((int(lvl) for lvl, cnt in slots["by_level"].items() if cnt > 0), default=0)

    # Build available spells list
    available = []
    seen = set()

    # 1. Class spell list
    cls_spells = get_srd_spells_for_class(class_name, level)
    for spell in SRD_SPELLS:
        name = spell.get("name", "")
        if not name or name.lower() in known_names:
            continue
        classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
        if class_name.lower() not in classes:
            continue
        slvl = int(spell.get("level", 0) or 0)
        if slvl > 0 and slvl > max_spell_level:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            available.append({
                "name": name,
                "level": slvl,
                "school": (spell.get("school") or {}).get("name", "") if isinstance(spell.get("school"), dict) else "",
                "casting_time": spell.get("casting_time", ""),
                "range": spell.get("range", ""),
                "duration": spell.get("duration", ""),
                "ritual": spell.get("ritual", False),
                "concentration": spell.get("concentration", False),
                "description": " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else (spell.get("description", "") or ""),
                "source": "class",
                "book": spell.get("source", ""),
            })

    # 2. Subclass bonus spells (e.g. Death Domain Spells, Oathbreaker Spells)
    if subclass and subclass in SUBCLASS_FEATURES:
        sc_feats = SUBCLASS_FEATURES[subclass]
        for sc_lvl, feat_names in sc_feats.items():
            for fn in feat_names:
                if "spell" not in fn.lower():
                    continue
                # Find the full spell description from SRD
                for spell in SRD_SPELLS:
                    sname = spell.get("name", "")
                    if not sname or sname.lower() in known_names:
                        continue
                    # Check if this spell is mentioned in subclass spell list
                    # Match from the SUBCLASS_FEATURES descriptions
                    key = sname.lower()
                    if key not in seen:
                        slvl = int(spell.get("level", 0) or 0)
                        if slvl > 0 and slvl > max_spell_level:
                            continue
                        # Check if this spell appears in subclass domain spells
                        found = False
                        for feat_name2 in feat_names:
                            if "spell" in feat_name2.lower():
                                found = True
                                break
                        if found and sc_lvl <= level:
                            seen.add(key)
                            available.append({
                                "name": sname,
                                "level": slvl,
                                "school": (spell.get("school") or {}).get("name", "") if isinstance(spell.get("school"), dict) else "",
                                "casting_time": spell.get("casting_time", ""),
                                "range": spell.get("range", ""),
                                "duration": spell.get("duration", ""),
                                "ritual": spell.get("ritual", False),
                                "concentration": spell.get("concentration", False),
                                "description": " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else (spell.get("description", "") or ""),
                                "source": f"subclass ({subclass})",
                                "book": spell.get("source", ""),
                            })

    # 3. Race spells (e.g. Tiefling: Hellish Rebuke, Darkness; Drow: Faerie Fire)
    # Subrace-gated spells (keyed by subrace name, resolved first)
    subrace_innate_spells = {
        # Elf subraces
        "High Elf": {0: ["any wizard cantrip"]},
        "Dark Elf (Drow)": {1: ["faerie fire"], 2: ["darkness"]},
        # Dwarf subraces
        "Duergar": {1: ["enlarge/reduce"], 2: ["invisibility"]},
        # Gnome subraces
        "Forest Gnome": {0: ["minor illusion"]},
        "Deep Gnome": {1: ["disguise self"], 2: ["nondetection"]},
        # Gith subraces
        "Githyanki": {0: ["mage hand"], 1: ["jump"], 2: ["misty step"]},
        "Githzerai": {0: ["mage hand"], 1: ["shield"], 2: ["detect thoughts"]},
        # Tiefling variant subrace innate spells (override base tiefling)
        "Asmodeus": {1: ["hellish rebuke"], 2: ["darkness"]},          # PHB default
        "Mephistopheles": {1: ["burning hands"], 2: ["flame blade"]},   # SCAG p.118
        "Zariel": {1: ["searing smite"], 2: ["branding smite"]},       # SCAG p.118
        "Dispater": {1: ["disguise self"], 2: ["invisibility"]},       # SCAG p.118
        "Fierna": {1: ["charm person"], 2: ["suggestion"]},            # SCAG p.118
        "Glasya": {1: ["disguise self"], 2: ["invisibility"]},         # SCAG p.118
        "Levistus": {0: ["ray of frost"], 2: ["darkness"]},              # SCAG p.118
        "Mammon": {0: ["mage hand"], 2: ["arcane lock"]},              # SCAG p.118
    }
    # Standalone-race innate spells (keyed by race name)
    race_innate_spells = {
        "tiefling": {1: ["hellish rebuke"], 2: ["darkness"]},  # fallback if no subrace
        "firbolg": {0: ["detect magic"], 1: ["disguise self"]},
        "yuan-ti pureblood": {0: ["poison spray"], 1: ["animal friendship"], 2: ["suggestion"]},
        "aasimar": {0: ["light"], 1: ["lesser restoration"]},
    }
    # Resolve spells: subrace-specific takes priority, then race-level
    if subrace and subrace in subrace_innate_spells:
        innate_spells = subrace_innate_spells[subrace]
    else:
        race_key = race_name.lower()
        innate_spells = race_innate_spells.get(race_key, {})
    for req_lvl, spell_names in innate_spells.items():
            if req_lvl > level:
                continue
            for sname in spell_names:
                key = sname.lower()
                if key in known_names or key in seen:
                    continue
                seen.add(key)
                # Look up in SRD
                srd = next((s for s in SRD_SPELLS if s.get("name","").lower() == key), None)
                available.append({
                    "name": sname,
                    "level": req_lvl if req_lvl > 0 else 0,
                    "school": (srd.get("school") or {}).get("name", "") if srd and isinstance(srd.get("school"), dict) else "",
                    "casting_time": srd.get("casting_time", "") if srd else "",
                    "range": srd.get("range", "") if srd else "",
                    "duration": srd.get("duration", "") if srd else "",
                    "ritual": srd.get("ritual", False) if srd else False,
                    "concentration": srd.get("concentration", False) if srd else False,
                    "description": " ".join(srd.get("desc", [])) if srd and isinstance(srd.get("desc"), list) else "Racial innate spell",
                    "source": f"race ({race_name})",
                    "book": srd.get("source", "") if srd else "",
                })

    # Sort: cantrips first, then by level, then by name
    available.sort(key=lambda s: (s["level"], s["name"].lower()))

    # Enrich with spell dice indicators (same as character sheet badges)
    for s in available:
        dice_info = SPELL_DICE.get(s["name"].lower())
        if dice_info:
            s["dice"] = _scaled_dice_display(dice_info, level)
            s["dice_healing"] = bool(dice_info.get("healing"))
            s["dice_ac"] = bool(dice_info.get("ac_bonus"))
            s["dice_buff"] = bool(dice_info.get("buff"))

    return JSONResponse(available)


@router.post("/api/character/{char_id}/ai-spells", response_class=JSONResponse)
async def ai_select_spells(char_id: int, request: Request):
    """AI-assisted spell selection — auto-picks optimal spells based on
    class, subclass, level, and race. Respects preparation limits for
    prepared casters and spells-known limits for known casters."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    class_name = char["class_name"] or ""
    subclass = char["subclass"] or ""
    level = max(1, int(char.get("level", 1) or 1))

    # Get existing spells — include id for potential UPDATE on prepared casters
    known_rows = db.execute("SELECT id, spell_name, spell_level, prepared FROM character_spells WHERE character_id = ?",
                            (char_id,)).fetchall()
    known_names = {r[1].lower() for r in known_rows}

    # Determine limits
    caster_class = class_name
    if caster_class not in PREPARED_CASTERS and caster_class not in SPELLS_KNOWN_CASTERS and caster_class != "Warlock":
        db.close()
        return JSONResponse({"added": 0, "message": f"{class_name} is not a spellcaster"})

    is_prepared = caster_class in PREPARED_CASTERS
    sc_mod = max(1, ((char.get("wisdom", 10) - 10) // 2) if caster_class in ("Cleric", "Druid") else
                    ((char.get("charisma", 10) - 10) // 2) if caster_class in ("Paladin",) else
                    ((char.get("intelligence", 10) - 10) // 2))  # Wizard

    # Calculate limits
    if is_prepared:
        prep_max = level + sc_mod  # Cleric/Druid/Paladin/Wizard: level + mod
        if caster_class == "Paladin":
            prep_max = max(1, (level // 2) + sc_mod)  # Paladin: half level + CHA
        current_prepared = sum(1 for r in known_rows if r[3] == 1 and (r[2] or 0) > 0)  # exclude cantrips
        can_add = max(0, prep_max - current_prepared)
    else:
        # Spells known
        spells_known = _spells_known_for_class(caster_class, level)
        current_known = sum(1 for r in known_rows if (r[2] or 0) > 0)  # exclude cantrips
        can_add = max(0, spells_known - current_known)

    if can_add <= 0:
        db.close()
        return JSONResponse({"added": 0, "message": "Already at spell limit"})

    # Get available spell slots (for max spell level filtering)
    slots = get_spell_slots(caster_class, level)
    max_spell_level = 0
    if caster_class == "Warlock":
        max_spell_level = slots.get("slot_level", 0) if slots else 0
    elif slots and slots.get("by_level"):
        max_spell_level = max((int(lvl) for lvl, cnt in slots["by_level"].items() if cnt > 0), default=0)

    # Build SRD lookup by name for scoring existing spells
    srd_by_name = {}
    for spell in SRD_SPELLS:
        name = spell.get("name", "")
        if name:
            srd_by_name[name.lower()] = spell

    # Score function (used for both existing and new candidates)
    def _score(spell_name: str, slvl: int, spell: dict) -> int:
        name_lower = spell_name.lower()
        score = 0
        # Class-specific priority lists
        priorities = CLASS_SPELL_PRIORITIES.get(caster_class, {}).get(slvl, [])
        if name_lower in [s.lower() for s in priorities]:
            score += 100
        elif any(p.lower() in name_lower for p in priorities):
            score += 50

        # Subclass synergy (domain spells etc.)
        if subclass and subclass in SUBCLASS_FEATURES:
            sc_feats = SUBCLASS_FEATURES[subclass]
            for feat_names in sc_feats.values():
                for fn in feat_names:
                    if "spell" in fn.lower():
                        score += 30

        # General quality heuristics
        desc = " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else (spell.get("description", "") or "")
        if "heal" in desc.lower() or "restore" in desc.lower():
            score += 15
        if "damage" in desc.lower() and "d" in desc.lower():
            score += 10
        if spell.get("ritual"):
            score += 20

        return score

    # Build candidate pool
    candidates = []

    if is_prepared:
        # Source 1: existing known spells that are NOT prepared — toggle them on
        for r in known_rows:
            if r[3] == 0 and (r[2] or 0) > 0:  # not prepared, non-cantrip
                name = r[1]
                name_lower = name.lower()
                srd_spell = srd_by_name.get(name_lower)
                if srd_spell:
                    candidates.append({"name": name, "level": r[2], "spell": srd_spell, "is_new": False, "known_id": r[0], "name_lower": name_lower})
                else:
                    # No SRD data — use a minimal placeholder for scoring
                    candidates.append({"name": name, "level": r[2], "spell": {"name": name, "level": r[2], "desc": [], "ritual": False}, "is_new": False, "known_id": r[0], "name_lower": name_lower})

    # Source 2: SRD spells not yet known (for both prepared and known casters)
    for spell in SRD_SPELLS:
        name = spell.get("name", "")
        if not name or name.lower() in known_names:
            continue
        classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
        if caster_class.lower() not in classes:
            continue
        slvl = int(spell.get("level", 0) or 0)
        if slvl > 0 and slvl > max_spell_level:
            continue
        candidates.append({"name": name, "level": slvl, "spell": spell, "is_new": True, "known_id": None, "name_lower": name.lower()})

    # Score all candidates
    for item in candidates:
        sp = item["spell"]
        item["score"] = _score(item["name"], item["level"], sp)
        item["name"] = sp.get("name", item["name"])
        item["school"] = (sp.get("school") or {}).get("name", "") if isinstance(sp.get("school"), dict) else ""

    # Sort: best score first, then higher level, then alphabetical
    candidates.sort(key=lambda x: (-x["score"], -x["level"], x["name"].lower()))

    # Pick top spells up to limit
    selected = candidates[:can_add]

    # Apply: UPDATE existing unprepared spells as prepared, INSERT new ones
    prepared_count = 0
    added_count = 0
    for item in selected:
        if is_prepared and not item["is_new"]:
            # Mark existing spell as prepared
            db.execute("UPDATE character_spells SET prepared=1 WHERE id=?", (item["known_id"],))
            prepared_count += 1
        else:
            # Insert new spell
            db.execute(
                "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
                (char_id, item["name"], item["level"], 1 if is_prepared else 0, 0, 0))
            added_count += 1

    db.commit()
    db.close()

    total = prepared_count + added_count
    names = [s["name"] for s in selected]
    parts = []
    if prepared_count:
        parts.append(f"{prepared_count} spells prepared")
    if added_count:
        parts.append(f"{added_count} new spells learned")
    msg = f"{class_name} L{level}" + (f" ({subclass})" if subclass else "") + " — " + ", ".join(parts)

    return JSONResponse({
        "added": total,
        "spells": names,
        "prepared": prepared_count,
        "new": added_count,
        "message": msg,
    })


# Class-specific priority spell picks (PHB 2014, curated by tier)
CLASS_SPELL_PRIORITIES = {
    "Cleric": {
        1: ["Bless", "Healing Word", "Guiding Bolt", "Shield of Faith"],
        2: ["Spiritual Weapon", "Aid", "Lesser Restoration", "Silence"],
        3: ["Spirit Guardians", "Revivify", "Mass Healing Word"],
        4: ["Banishment", "Death Ward", "Guardian of Faith"],
        5: ["Mass Cure Wounds", "Flame Strike", "Greater Restoration"],
        6: ["Heal", "Harm", "Word of Recall"],
        7: ["Divine Word", "Fire Storm", "Resurrection"],
        8: ["Holy Aura", "Antimagic Field"],
        9: ["Mass Heal", "Gate", "True Resurrection"],
    },
    "Wizard": {
        1: ["Mage Armor", "Shield", "Magic Missile", "Find Familiar", "Detect Magic"],
        2: ["Misty Step", "Web", "Mirror Image", "Invisibility"],
        3: ["Fireball", "Counterspell", "Fly", "Haste", "Dispel Magic"],
        4: ["Polymorph", "Dimension Door", "Greater Invisibility", "Banishment"],
        5: ["Wall of Force", "Animate Objects", "Cone of Cold"],
        6: ["Disintegrate", "Chain Lightning", "Globe of Invulnerability"],
        7: ["Forcecage", "Teleport", "Delayed Blast Fireball"],
        8: ["Maze", "Demiplane", "Power Word Stun"],
        9: ["Wish", "Meteor Swarm", "Time Stop", "Prismatic Wall"],
    },
    "Druid": {
        1: ["Entangle", "Goodberry", "Faerie Fire", "Healing Word"],
        2: ["Spike Growth", "Moonbeam", "Pass Without Trace", "Heat Metal"],
        3: ["Conjure Animals", "Call Lightning", "Plant Growth"],
        4: ["Conjure Woodland Beings", "Ice Storm", "Polymorph"],
        5: ["Wall of Stone", "Mass Cure Wounds", "Awaken"],
        6: ["Heal", "Sunbeam", "Transport via Plants"],
        7: ["Fire Storm", "Reverse Gravity"],
        8: ["Feeblemind", "Sunburst"],
        9: ["Shapechange", "Storm of Vengeance"],
    },
    "Bard": {
        1: ["Healing Word", "Dissonant Whispers", "Faerie Fire", "Tasha's Hideous Laughter"],
        2: ["Suggestion", "Invisibility", "Shatter"],
        3: ["Hypnotic Pattern", "Dispel Magic", "Slow"],
        4: ["Polymorph", "Dimension Door", "Greater Invisibility"],
        5: ["Animate Objects", "Hold Monster", "Mass Cure Wounds"],
        6: ["Otto's Irresistible Dance", "Mass Suggestion"],
        7: ["Forcecage", "Teleport"],
        8: ["Dominate Monster", "Power Word Stun"],
        9: ["Foresight", "True Polymorph"],
    },
    "Sorcerer": {
        1: ["Shield", "Magic Missile", "Burning Hands"],
        2: ["Scorching Ray", "Misty Step", "Mirror Image"],
        3: ["Fireball", "Haste", "Counterspell", "Fly"],
        4: ["Polymorph", "Dimension Door", "Greater Invisibility"],
        5: ["Cone of Cold", "Animate Objects"],
        6: ["Chain Lightning", "Disintegrate"],
        7: ["Delayed Blast Fireball", "Reverse Gravity"],
        8: ["Sunburst", "Power Word Stun"],
        9: ["Wish", "Meteor Swarm"],
    },
    "Warlock": {
        1: ["Hex", "Armor of Agathys", "Hellish Rebuke"],
        2: ["Misty Step", "Darkness", "Hold Person"],
        3: ["Fireball", "Counterspell", "Fly", "Hunger of Hadar"],
        4: ["Dimension Door", "Banishment", "Shadow of Moil"],
        5: ["Synaptic Static", "Hold Monster"],
    },
    "Paladin": {
        1: ["Bless", "Shield of Faith", "Divine Favor", "Cure Wounds"],
        2: ["Find Steed", "Aid", "Lesser Restoration"],
        3: ["Aura of Vitality", "Revivify", "Crusader's Mantle"],
        4: ["Find Greater Steed", "Death Ward", "Aura of Life"],
        5: ["Destructive Wave", "Banishing Smite", "Raise Dead"],
    },
    "Ranger": {
        1: ["Hunter's Mark", "Goodberry", "Absorb Elements", "Hail of Thorns"],
        2: ["Pass Without Trace", "Spike Growth", "Silence"],
        3: ["Conjure Animals", "Lightning Arrow", "Plant Growth"],
        4: ["Guardian of Nature", "Conjure Woodland Beings"],
        5: ["Swift Quiver", "Steel Wind Strike"],
    },
}

def _spells_known_for_class(class_name: str, level: int) -> int:
    """PHB 2014 spells known progression."""
    known = {
        "Bard":    {1:4,2:5,3:6,4:7,5:8,6:9,7:10,8:11,9:12,10:14,11:15,12:15,13:16,14:18,15:19,16:19,17:20,18:22,19:22,20:22},
        "Sorcerer":{1:2,2:3,3:4,4:5,5:6,6:7,7:8,8:9,9:10,10:11,11:12,12:12,13:13,14:13,15:14,16:14,17:15,18:15,19:15,20:15},
        "Warlock": {1:2,2:3,3:4,4:5,5:6,6:7,7:8,8:9,9:10,10:10,11:11,12:11,13:12,14:12,15:13,16:13,17:14,18:14,19:15,20:15},
        "Ranger":  {2:2,3:3,4:3,5:4,6:4,7:5,8:5,9:6,10:6,11:7,12:7,13:8,14:8,15:9,16:9,17:10,18:10,19:11,20:11},
    }
    return known.get(class_name, {}).get(level, 0)


@router.post("/api/character/{char_id}/toggle-prepared", response_class=JSONResponse)
async def toggle_prepared(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    spell_id = data.get("id")
    prepared = 1 if data.get("prepared", False) else 0
    db = get_db()
    row = db.execute(
        "SELECT id FROM character_spells WHERE id=? AND character_id=?",
        (spell_id, char_id)
    ).fetchone()
    if not row:
        db.close()
        return JSONResponse({"error": "Spell not found"}, status_code=404)
    db.execute("UPDATE character_spells SET prepared=? WHERE id=?",
               (prepared, spell_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@router.post("/api/character/{char_id}/unlearn-spell", response_class=JSONResponse)
async def unlearn_spell(char_id: int, request: Request):
    """Delete a spell from the character's spellbook."""
    user = require_user(request)
    data = await request.json()
    spell_id = data.get("id")
    if not spell_id:
        return JSONResponse({"error": "Missing spell id"}, status_code=400)
    db = get_db()
    row = db.execute(
        "SELECT id, spell_name FROM character_spells WHERE id=? AND character_id=?",
        (spell_id, char_id)
    ).fetchone()
    if not row:
        db.close()
        return JSONResponse({"error": "Spell not found"}, status_code=404)
    name = row["spell_name"]
    db.execute("DELETE FROM character_spells WHERE id=?", (spell_id,))
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "spell_name": name, "message": f"Unlearned {name}"})


@router.get("/api/character/{char_id}/gp", response_class=JSONResponse)
async def get_character_gp(char_id: int, request: Request):
    """Return just the gp value for a character (lightweight)."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT gp FROM characters WHERE id=? AND user_id=?", (char_id, user["id"])).fetchone()
    db.close()
    if not row:
        return JSONResponse({"gp": 0})
    return JSONResponse({"gp": row["gp"] or 0})


@router.post("/api/character/{char_id}/toggle-attune", response_class=JSONResponse)
async def toggle_attune(char_id: int, request: Request):
    """Toggle attunement for an equipped item. Max 3 attuned items (PHB p.138)."""
    user = require_user(request)
    data = await request.json()
    item_name = data.get("item", "").strip()
    if not item_name:
        return JSONResponse({"error": "Missing item name"}, status_code=400)

    db = get_db()
    row = db.execute(
        "SELECT attuned_items, equipped, user_id FROM characters WHERE id = ?",
        (char_id,)
    ).fetchone()
    if not row:
        db.close()
        return JSONResponse({"error": "Character not found"}, status_code=404)

    attuned = json.loads(row[0] or "[]")
    equipped = _normalize_equipped(json.loads(row[1] or "[]"))
    item_lower = item_name.lower()

    # Check if item is equipped
    eq_names = _equipped_names(equipped)
    if item_name not in eq_names:
        db.close()
        return JSONResponse({"error": "Item is not equipped"}, status_code=400)

    # Check if item requires attunement
    props = ITEM_PROPERTIES.get(item_lower, {})
    if not props.get("requires_attunement"):
        db.close()
        return JSONResponse({"error": "This item does not require attunement"}, status_code=400)

    if item_name in attuned:
        # Break attunement
        attuned.remove(item_name)
        action = "broken"
    else:
        # Attune — check slot limit (max 3)
        if len(attuned) >= 3:
            db.close()
            return JSONResponse({"error": "Attunement slots full (max 3, PHB p.138). Break attunement to another item first."}, status_code=400)
        attuned.append(item_name)
        action = "attuned"

    db.execute("UPDATE characters SET attuned_items = ? WHERE id = ?",
               (json.dumps(attuned), char_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "action": action, "attuned": attuned,
                         "slots_used": len(attuned)})


@router.post("/api/character/{char_id}/use-feature", response_class=JSONResponse)
async def use_feature(char_id: int, request: Request):
    """Use one charge of a limited-use feature. Returns updated uses count."""
    user = require_user(request)
    data = await request.json()
    feat_name = data.get("name", "")
    db = get_db()
    row = db.execute(
        "SELECT feature_data, user_id FROM characters WHERE id = ?", (char_id,)
    ).fetchone()
    if not row or row["user_id"] != user["id"]:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    features = json.loads(row["feature_data"] or "[]")
    new_uses = 0
    for feat in features:
        if feat.get("name") == feat_name:
            current = feat.get("uses", 0)
            if current > 0:
                feat["uses"] = current - 1
            new_uses = feat["uses"]
            break
    db.execute(
        "UPDATE characters SET feature_data = ? WHERE id = ?",
        (json.dumps(features), char_id),
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "uses": new_uses})

@router.post("/api/character/{char_id}/reset-features", response_class=JSONResponse)
async def reset_features(char_id: int, request: Request):
    """Reset all limited-use features to max (e.g., after long rest)."""
    user = require_user(request)
    data = await request.json()
    recharge_filter = data.get("recharge", None)
    db = get_db()
    row = db.execute(
        "SELECT feature_data, user_id FROM characters WHERE id = ?", (char_id,)
    ).fetchone()
    if not row or row["user_id"] != user["id"]:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    features = json.loads(row["feature_data"] or "[]")
    for feat in features:
        if "uses_max" not in feat or "recharge" not in feat:
            continue
        if recharge_filter and feat["recharge"] != recharge_filter:
            continue
        feat["uses"] = feat["uses_max"]
    db.execute(
        "UPDATE characters SET feature_data = ? WHERE id = ?",
        (json.dumps(features), char_id),
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True})