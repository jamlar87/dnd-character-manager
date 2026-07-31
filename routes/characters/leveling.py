"""Level-up / de-level routes — progression API.

Extracted from routes/characters/all.py (2026-07-31). Imports helpers
from main / data / services.leveling / schemas only — never from
all.py (avoids circulars).
"""

import json
import random
import re

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from main import (
    get_db, require_user, _require_owned,
    ABILITY_NAMES, ASI_LEVELS, CLASSES, EXPERTISE_LEVELS, FEATS,
    FEAT_BY_NAME, HTTPException, INVOCATION_LEVELS, INVOCATION_OPTIONS,
    INVOCATION_PICKS, MULTICLASS_PREREQS, PREPARED_CASTERS, SRD_LEVELS,
    SRD_SPELLS, SUBCLASS_FEATURE_REPLACEMENTS, SUBCLASS_LEVELS,
)
from data import (
    ALL_SKILLS, FAVORED_ENEMY_OPTIONS, FAVORED_TERRAIN_OPTIONS,
    HUNTERS_PREY_OPTIONS, INFUSION_OPTIONS, LANGUAGES,
    MANEUVER_OPTIONS, METAMAGIC_OPTIONS,
    PACT_BOON_OPTIONS, SPELLS_KNOWN_CASTERS,
    SUBCLASS_LEVELS, TOTEM_SPIRIT_OPTIONS,
)
from services.leveling import (
    enrich_features, get_caster_type, get_spell_slots,
    get_character_spell_slots, get_class_features, get_expertise_count,
    get_multiclass_proficiencies, get_spells_known_max,
    get_srd_spells_for_class, meets_multiclass_prereq, parse_class_levels,
    total_level,
    # Progression core helpers
    ABILITY_PRIORITY, PROFICIENCY_BONUS, SUBCLASS_PROFICIENCIES,
    _build_racial_limited_features, _deduplicate_multiclass_features,
    # Level-up data constants (moved from all.py)
    DOMAIN_SPELLS, FAVORED_ENEMY_LEVELS, FAVORED_TERRAIN_LEVELS,
    FIGHTING_STYLES, FIGHTING_STYLE_LEVELS, FIGHTING_STYLE_OPTIONS,
    HUNTERS_PREY_LEVELS, INFUSION_LEVELS, INFUSION_PICKS,
    MAGICAL_SECRETS_LEVELS, MAGICAL_SECRETS_PICKS, MANEUVER_PICKS,
    TOTEM_SPIRIT_LEVELS, TOTEM_SPIRIT_TIER_LABELS,
    WARLOCK_EXPANDED_SPELLS_BY_LEVEL,
    # Choice-system constants (shadow data.py versions)
    METAMAGIC_LEVELS, METAMAGIC_PICKS,
    INVOCATION_LEVELS, INVOCATION_PICKS,
    PACT_BOON_LEVELS, MANEUVER_LEVELS, CANTRIPS_PROGRESSION,
)
from routes.schemas import ApplyLevelUp

router = APIRouter()
def _meets_feat_prereq(prereq: str, char: dict, abilities: dict, feat_name: str = "") -> tuple[bool, str]:
    """Check if character meets a feat prerequisite. Returns (met, reason)."""
    if not prereq or not prereq.strip():
        return True, ""
    p = prereq.strip()
    
    # ── OCR corruption fixes ──
    ocr_fixes = {
        "Proliciency": "Proficiency", "mediuJ1Jarmor": "medium armor",
        "Dexlerily": "Dexterity", "OI' higher": "or higher",
        "Thc abilily lo casl aI leasl one spell": "The ability to cast at least one spell",
        "The abilily lo casl aI leasl one spell": "The ability to cast at least one spell",
        "Thc abilily": "The ability", "lo casl": "to cast", "aI leasl": "at least",
        "abilily": "ability",
    }
    for bad, good in ocr_fixes.items():
        p = p.replace(bad, good)
    
    # ── Warlock invocations (not real feats) ──
    invocation_terms = ["eldritch blast", "Pact of the Blade", "Pact of the Chain",
                        "Pact of the Tome", "Pact of the Talisman"]
    invocation_names = {"Agonizing Blast", "Armor of Shadows", "Ascendant Step",
        "Beast Speech", "Beguiling Influence", "Bewitching Whispers",
        "Book of Ancient Secrets", "Chains of Carceri", "Devil's Sight",
        "Dreadful Word", "Eldritch Sight", "Eldritch Spear", "Eyes of the Rune Keeper",
        "Fiendish Vigor", "Gaze of Two Minds", "Gift of the Depths",
        "Gift of the Ever-Living Ones", "Grasp of Hadar", "Investment of the Chain Master",
        "Lance of Lethargy", "Lifedrinker", "Maddening Hex", "Mask of Many Faces",
        "Master of Myriad Forms", "Minions of Chaos", "Mire the Mind",
        "Misty Visions", "One with Shadows", "Otherworldly Leap",
        "Protection of the Talisman", "Rebuke of the Talisman", "Relentless Hex",
        "Repelling Blast", "Sculptor of Flesh", "Shroud of Shadow",
        "Sign of Ill Omen", "Thief of Five Fates", "Thirsting Blade",
        "Tomb of Levistus", "Trickster's Escape", "Undying Servitude",
        "Visions of Distant Realms", "Voice of the Chain Master",
        "Whispers of the Grave", "Witch Sight"}
    if any(t.lower() in p.lower() for t in invocation_terms):
        return False, "Warlock invocation (not a feat)"
    # Also check by name (invocations from feats.json)
    if feat_name in invocation_names:
        return False, "Warlock invocation (not a feat)"
    
    # ── Ability score: "Dexterity 13 or higher" OR "Dexterity 13+" ──
    m = re.match(r'^(\w+)\s+(\d+)\s*(?:or\s+higher|\+)$', p)
    if m:
        abil = m.group(1).lower()
        needed = int(m.group(2))
        current = abilities.get(abil, 10)
        return current >= needed, ("" if current >= needed else f"{m.group(1)} {current}/{needed}")
    
    # ── Ability A or B: "Intelligence or Wisdom 13 or higher" OR "... 13+" ──
    m = re.match(r'^(\w+)\s+or\s+(\w+)\s+(\d+)\s*(?:or\s+higher|\+)$', p)
    if m:
        a1, a2, needed = m.group(1).lower(), m.group(2).lower(), int(m.group(3))
        ok = abilities.get(a1, 10) >= needed or abilities.get(a2, 10) >= needed
        return ok, ("" if ok else f"{m.group(1)} or {m.group(2)} {needed}")
    
    # ── Level only: "7th level", "12th level", "5th level" ──
    m = re.match(r'^(\d+)(?:th|rd|nd|st)\s+level$', p)
    if m:
        needed = int(m.group(1))
        current = total_level(parse_class_levels(char))
        return current >= needed, ("" if current >= needed else f"Level {needed}")
    
    # ── Level + feature: "12th level, Pact of the Blade feature" ──
    m = re.match(r'^(\d+)(?:th|rd|nd|st)\s+level,\s+(.+)$', p)
    if m:
        needed = int(m.group(1))
        current = total_level(parse_class_levels(char))
        return current >= needed, ("" if current >= needed else f"Level {needed}")
    
    # ── Multi-race: "Elf or half-elf", "Dwarf or a Small race", "Half-elf, half-orc, or human" ──
    race_aliases = {
        "half-elf": "half-elf", "half-orc": "half-orc", "half-orc": "half-orc",
        "human": "human", "elf": "elf", "dwarf": "dwarf", "halfling": "halfling",
        "gnome": "gnome", "dragonborn": "dragonborn", "tiefling": "tiefling",
        "aasimar": "aasimar", "goliath": "goliath", "firbolg": "firbolg",
        "kenku": "kenku", "lizardfolk": "lizardfolk", "tabaxi": "tabaxi",
        "triton": "triton", "goblin": "goblin", "hobgoblin": "hobgoblin",
        "bugbear": "bugbear", "kobold": "kobold", "orc": "orc",
        "yuan-ti": "yuan-ti pureblood", "changeling": "changeling",
        "shifter": "shifter", "warforged": "warforged", "kalashtar": "kalashtar",
        "centaur": "centaur", "minotaur": "minotaur", "loxodon": "loxodon",
        "vedalken": "vedalken", "simic hybrid": "simic hybrid",
        "tortle": "tortle", "aarakocra": "aarakocra", "genasi": "genasi",
        "gith": "gith",
    }
    char_race = (char.get("race") or "").lower()
    char_subrace = (char.get("subrace") or "").lower()
    
    # "Small race" check
    small_races = {"halfling", "gnome", "goblin", "kobold"}
    if "small race" in p.lower():
        ok = char_race in small_races
        return ok, ("Small race required" if not ok else "")
    
    # Multi-race pattern: "Elf or half-elf", "Half-elf, half-orc, or human"
    race_words = re.split(r',\s*|\s+or\s+', p.lower())
    race_matches = []
    for rw in race_words:
        rw = rw.strip().rstrip('.')
        if rw in race_aliases:
            race_matches.append(race_aliases[rw])
    
    if race_matches:
        # Check if character matches any
        for rm in race_matches:
            if char_race == rm:
                return True, ""
            # Check subrace if parent race matches (e.g. "elf" matches "high elf")
            if char_subrace and rm in char_subrace:
                return True, ""
        return False, f"Race: {p}"
    
    # ── Single race with subrace: "Elf (high elf)" ──
    m = re.match(r'^(\w[\w\s]*?)(?:\s*\((.+)\))?$', p)
    if m and not any(w in p.lower() for w in ['cast', 'spell', 'armor', 'proficiency', 'level', 'renown']):
        race_name = m.group(1).strip().lower()
        subrace = m.group(2).strip().lower() if m.group(2) else None
        if race_name in race_aliases:
            race_name = race_aliases[race_name]
        if subrace:
            ok = char_race == race_name and char_subrace == subrace
            return ok, (f"Race: {m.group(1)} ({m.group(2)})" if not ok else "")
        else:
            ok = char_race == race_name
            return ok, (f"Race: {m.group(1)}" if not ok else "")
    
    # ── Spellcasting ──
    if 'cast' in p.lower() and 'spell' in p.lower():
        caster_type = get_caster_type(char.get("class_name", ""))
        ok = caster_type != "none"
        return ok, ("Requires spellcasting" if not ok else "")
    
    # ── Armor proficiency ──
    if 'proficiency' in p.lower() and 'armor' in p.lower():
        profs_raw = char.get("armor_proficiencies", "")
        profs = [x.strip().lower() for x in profs_raw.split(",")] if profs_raw else []
        if 'medium' in p.lower():
            ok = any('medium' in x for x in profs)
            return ok, ("Medium armor proficiency" if not ok else "")
        if 'heavy' in p.lower():
            ok = any('heavy' in x for x in profs)
            return ok, ("Heavy armor proficiency" if not ok else "")
        if 'light' in p.lower():
            ok = any('light' in x for x in profs) or True  # most classes have light
            return ok, ("Light armor proficiency" if not ok else "")
        return False, f"Armor proficiency: {p}"
    
    # ── Renown / guild — block (not campaign-independent) ──
    if 'renown' in p.lower():
        return False, "Campaign-specific (Renown)"
    
    # ── Default: allow ──
    return True, ""


@router.get("/api/character/{char_id}/level-up-info", response_class=JSONResponse)
async def level_up_info(char_id: int, request: Request):
    """Return everything needed for the level-up wizard. Supports multi-level via ?target=N."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    
    cl = parse_class_levels(char)
    current_level = total_level(cl)
    cls = char.get("class_name", "Fighter")  # primary class for backward compat
    subclass = char.get("subclass", "")  # for expertise/maneuvers/secrets/totem/hunters_prey lookups
    
    # Parse target level (default next level, cap at 20)
    target_level = int(request.query_params.get("target", current_level + 1))
    target_level = max(current_level + 1, min(target_level, 20))
    
    if current_level >= 20:
        return JSONResponse({"error": "Already max level (20)"}, status_code=400)
    
    # Which class to level? Default: primary class, or specified via ?class_to_level=
    class_to_level = request.query_params.get("class_to_level", cls)
    if class_to_level not in cl:
        # If specified class isn't in the character's class list, it's a multiclass attempt
        # Check prerequisites
        abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
        if not meets_multiclass_prereq(abilities, class_to_level):
            prereqs = MULTICLASS_PREREQS.get(class_to_level, {})
            return JSONResponse({
                "error": f"Prerequisites not met for {class_to_level}: {prereqs}",
                "prerequisites": prereqs,
                "abilities": abilities,
            }, status_code=400)
    
    # Multiclass eligible classes (for UI)
    multiclass_options = []
    if target_level > current_level:
        abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
        for mc_class in MULTICLASS_PREREQS:
            if mc_class not in cl:
                meets = meets_multiclass_prereq(abilities, mc_class)
                profs = get_multiclass_proficiencies(mc_class)
                prereqs = MULTICLASS_PREREQS.get(mc_class, {})
                # Show all classes — eligible and ineligible (grayed out)
                missing = {}
                if not meets:
                    for stat, val in prereqs.items():
                        abil = abilities.get(stat.lower(), 10)
                        if abil < val:
                            missing[stat] = f"{abil}/{val}"
                multiclass_options.append({
                    "class": mc_class,
                    "prerequisites": prereqs,
                    "proficiencies": profs,
                    "available": meets,
                    "missing": missing,
                })
    
    cls = class_to_level  # the class gaining a level
    class_level = cl.get(cls, 0)  # current level IN that class
    
    # HP info (new class HD + CON mod)
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    hp_options = {"hit_die": f"d{hd}", "con_mod": con_mod, "average": avg_hp, "max_roll": hd + con_mod}
    
    # Accumulate per-level gains
    levels_gained = []
    all_features = []
    levels_gained_count = target_level - current_level
    new_class_level = class_level + levels_gained_count  # level in this class after level-up
    
    for offset in range(1, levels_gained_count + 1):
        char_lvl = current_level + offset
        cls_lvl = class_level + offset  # level IN the class being leveled
        old_f = get_class_features(cls, cls_lvl - 1, char.get("subclass", ""))
        new_f = get_class_features(cls, cls_lvl, char.get("subclass", ""))
        gained = [f for f in new_f if f not in old_f]
        is_asi = cls_lvl in ASI_LEVELS.get(cls, [])
        levels_gained.append({
            "level": char_lvl,
            "class_level": cls_lvl,
            "class_name": cls,
            "features": gained,
            "is_asi": is_asi,
        })
        all_features.extend(gained)
    
    # ASI levels — these are the character levels where ASIs fire
    asi_levels = [lvl["level"] for lvl in levels_gained if lvl["is_asi"]]
    
    # Pre-compute ASI info for each ASI level
    asi_infos = {}
    abilities = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    # Collect abilities already increased at prior ASI levels
    asi_hist = json.loads(char.get("asi_history", "[]"))
    prev_plus2 = set()
    prev_plus1 = set()
    prev_by_ability = {}  # ability → [levels]
    for entry in asi_hist:
        if entry.get("type") == "asi":
            for ab, amt in entry.get("choices", {}).items():
                ab_tc = ab.capitalize()  # Normalize to title case (Dexterity)
                prev_by_ability.setdefault(ab_tc, []).append(entry["level"])
                if amt == 2:
                    prev_plus2.add(ab_tc)
                elif amt == 1:
                    prev_plus1.add(ab_tc)
    for lvl in asi_levels:
        asi_infos[str(lvl)] = {
            "level": lvl,
            "abilities": dict(abilities),  # snapshot
            "max_20": [a for a in ABILITY_NAMES if abilities[a] >= 20],
            "would_exceed_20": [a for a in ABILITY_NAMES if abilities[a] >= 19],  # +2 would push 19→21
            "previous_plus2": list(prev_plus2),
            "previous_plus1": list(prev_plus1),
            "previous_by_ability": {ab: lvls for ab, lvls in prev_by_ability.items()},
        }
    
    # Collect feats the character has already taken (via ASI or class feature)
    already_taken_keys: set[str] = set()
    for entry in asi_hist:
        if entry.get("type") == "feat":
            already_taken_keys.add(entry.get("feat", ""))
    # Also check class-granted feats in feature_data (e.g. Warlock invocations,
    # Fighting Initiate from Fighter/Ranger bonus feat, etc.)
    feat_data = json.loads(char.get("feature_data", "[]"))
    for f in feat_data:
        fn = f.get("name", "") if isinstance(f, dict) else str(f)
        fn_lower = fn.lower()
        for fk, fv in FEATS.items():
            if fv["name"].lower() == fn_lower and fk not in already_taken_keys:
                already_taken_keys.add(fk)
    
    # Feats — filter by prereqs, eligible first
    feats_available = []
    feats_ineligible = []
    char_abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
    for key, feat in FEATS.items():
        prereq = feat.get("prereq") or feat.get("prerequisite", "")
        # Already taken beats prereq — if they already have it, it's ineligible
        if key in already_taken_keys:
            meets = False
            reason = "Already taken"
        else:
            meets, reason = _meets_feat_prereq(prereq, char, char_abilities, feat.get("name", ""))
        entry = {
            "key": key, "name": feat["name"],
            "desc": feat.get("desc") or feat.get("description", ""),
            "asi": feat.get("asi"), "prereq": prereq,
            "source": feat.get("source", ""),
            "eligible": meets,
            "reason": reason,
        }
        if meets:
            feats_available.append(entry)
        else:
            feats_ineligible.append(entry)
    feats_available.extend(feats_ineligible)  # eligible first, then ineligible
    
    # Subclass
    subclass_info = None
    sc = SUBCLASS_LEVELS.get(cls)
    if sc and class_level < sc["level"] <= new_class_level and not char.get("subclass"):
        descs = CLASSES.get(cls, {}).get("subclass_descs", {})
        all_options = CLASSES.get(cls, {}).get("subclasses", sc["options"])
        subclass_info = {
            "level": sc["level"],
            "label": sc["label"],
            "options": all_options,
            "descriptions": {opt: descs.get(opt, "") for opt in all_options},
        }
    
    # Map of subclass → bonus proficiency picker info
    subclass_bonus_map = {}
    for sc_name, sc_profs in SUBCLASS_PROFICIENCIES.items():
        if "skill_profs" in sc_profs and sc_profs["skill_profs"] == []:
            subclass_bonus_map[sc_name] = {
                "type": "skills",
                "count": 3 if "Lore" in sc_name else 2,
                "label": "Bonus Proficiencies",
                "options": ALL_SKILLS,
            }
        if "languages" in sc_profs:
            subclass_bonus_map[sc_name] = {
                "type": "languages",
                "count": sc_profs["languages"],
                "label": "Bonus Languages",
                "options": LANGUAGES,
            }
    
    # Proficiency bonus
    old_pb = PROFICIENCY_BONUS.get(current_level, 2)
    new_pb = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Spell changes (aggregate across all levels)
    spell_info = None
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            old_slots = get_spell_slots(cls, current_level)
            new_slots = get_spell_slots(cls, target_level)
        except:
            old_slots = {}; new_slots = {}
        spell_info = {
            "class_name": cls,
            "caster_type": caster_type,
            "spellcasting_ability": _spellcasting_ability(cls),
            "old_slots": old_slots, "new_slots": new_slots,
        }
        # Compute which slot levels are newly available (for UI messaging)
        if old_slots and new_slots:
            old_by = old_slots.get("by_level", {})
            new_by = new_slots.get("by_level", {})
            new_levels = []
            for lvl in range(1, 10):
                if new_by.get(str(lvl), 0) > 0 and old_by.get(str(lvl), 0) == 0:
                    new_levels.append(lvl)
            if new_levels:
                spell_info["new_slot_levels"] = new_levels
        # Spells known (Bard, Sorcerer, Warlock, Ranger)
        if cls in SPELLS_KNOWN_CASTERS:
            old_known = get_spells_known_max(cls, current_level)
            new_known = get_spells_known_max(cls, target_level)
            spell_info["spells_known_change"] = max(0, new_known - old_known)
        # Cantrips — use SRD class_levels data for accurate per-class progression
        if caster_type in ("full", "pact") or cls == "Cleric":
            old_cantrips = get_cantrips_known_max(cls, current_level)
            new_cantrips = get_cantrips_known_max(cls, target_level)
            spell_info["cantrips_change"] = max(0, new_cantrips - old_cantrips)
    
    # Expertise
    expertise_info = None
    exp_data = EXPERTISE_LEVELS.get(subclass) or EXPERTISE_LEVELS.get(cls)
    if exp_data:
        exp_levels = exp_data.get("levels", [])
        new_exp_levels = [l for l in exp_levels if class_level < l <= new_class_level]
        if new_exp_levels:
            char_skills = json.loads(char.get("skills", "[]"))
            char_subclass = char.get("subclass", "")
            old_count = get_expertise_count(cls, class_level, char_subclass)
            new_count = get_expertise_count(cls, new_class_level, char_subclass)
            exp_options = get_expertise_options(cls, char_subclass, char_skills)
            expertise_info = {
                "levels": new_exp_levels,
                "picks_gained": new_count - old_count,
                "level_count": new_count,
                "options": exp_options,
            }
    
    # Fighting Style — check if this class gets one at any of the gained levels
    fighting_style_info = None
    fs_level = FIGHTING_STYLE_LEVELS.get(cls)
    if fs_level and class_level < fs_level <= new_class_level:
        options = FIGHTING_STYLE_OPTIONS.get(cls, [])
        fighting_style_info = {
            "level": fs_level,
            "options": [{"key": k, "name": FIGHTING_STYLES[k]["name"], "desc": FIGHTING_STYLES[k]["desc"]} for k in options],
        }
    
    # Metamagic — Sorcerer L3/10/17
    metamagic_info = None
    metamagic_levels_list = METAMAGIC_LEVELS.get(cls, [])
    new_meta_levels = [l for l in metamagic_levels_list if class_level < l <= new_class_level]
    if new_meta_levels:
        existing = json.loads(char.get("metamagic", "[]"))
        metamagic_info = {
            "levels": new_meta_levels,
            "picks_per_level": {str(l): METAMAGIC_PICKS.get(l, 0) for l in new_meta_levels},
            "options": [{"key": k, "name": v["name"], "desc": v["desc"]} for k,v in METAMAGIC_OPTIONS.items()],
            "existing": existing,
        }
    
    # Eldritch Invocations — Warlock L2/5/7/9/12/15/18
    invocation_info = None
    invocation_levels_list = INVOCATION_LEVELS.get(cls, [])
    new_inv_levels = [l for l in invocation_levels_list if class_level < l <= new_class_level]
    if new_inv_levels:
        existing = json.loads(char.get("invocations", "[]"))
        total_picks_before = sum(INVOCATION_PICKS.get(l,0) for l in invocation_levels_list if l <= class_level)
        total_picks_after = sum(INVOCATION_PICKS.get(l,0) for l in invocation_levels_list if l <= new_class_level)
        pact_boon = char.get("pact_boon", "")
        options = []
        for k,v in INVOCATION_OPTIONS.items():
            req = v.get("prereq","")
            ok = not req or pact_boon in req
            if ok:
                options.append({"key":k,"name":v["name"],"desc":v["desc"],"level":v["level"],"prereq":req})
        invocation_info = {
            "levels": new_inv_levels,
            "picks_gained": total_picks_after - total_picks_before,
            "total_picks": total_picks_after,
            "options": options,
            "existing": existing,
        }
    
    # Pact Boon — Warlock L3
    pact_boon_info = None
    pb_level = PACT_BOON_LEVELS.get(cls)
    if pb_level and class_level < pb_level <= new_class_level and not char.get("pact_boon"):
        pact_boon_info = {
            "level": pb_level,
            "options": [{"key": k, "name": v["name"], "desc": v["desc"]} for k,v in PACT_BOON_OPTIONS.items()],
        }
    
    # Battle Master Maneuvers — Fighter subclass, L3/7/10/15
    maneuver_info = None
    man_levels_list = MANEUVER_LEVELS.get(subclass if subclass else char.get("subclass",""), [])
    new_man_levels = [l for l in man_levels_list if class_level < l <= new_class_level]
    if new_man_levels:
        existing = json.loads(char.get("maneuvers", "[]"))
        total_before = sum(MANEUVER_PICKS.get(l,0) for l in man_levels_list if l <= class_level)
        total_after = sum(MANEUVER_PICKS.get(l,0) for l in man_levels_list if l <= new_class_level)
        maneuver_info = {
            "levels": new_man_levels,
            "picks_gained": total_after - total_before,
            "total_known": total_after,
            "options": [{"key":k,"name":v["name"],"desc":v["desc"]} for k,v in MANEUVER_OPTIONS.items()],
            "existing": existing,
        }
    
    # Magical Secrets — Bard L10/14/18, Lore Bard L6
    magical_secrets_info = None
    ms_source = subclass if subclass in MAGICAL_SECRETS_LEVELS else cls
    ms_levels_list = MAGICAL_SECRETS_LEVELS.get(ms_source, [])
    new_ms_levels = [l for l in ms_levels_list if class_level < l <= new_class_level]
    if new_ms_levels:
        existing = json.loads(char.get("magical_secrets", "[]"))
        total_picks = sum(MAGICAL_SECRETS_PICKS.get(l,0) for l in new_ms_levels)
        # Build all-spells list for cross-class picking
        all_spell_opts = []
        for cls_k in SRD_LEVELS:
            spells = get_srd_spells_for_class(cls_k.title(), 20)
            for sp in spells.get("cantrips",[]):
                all_spell_opts.append({"key":sp["name"],"name":sp["name"],"level":"Cantrip","class":cls_k.title()})
            for lvl,lst in spells.get("spells",{}).items():
                for sp in lst:
                    all_spell_opts.append({"key":sp["name"],"name":sp["name"],"level":f"L{lvl}","class":cls_k.title()})
        magical_secrets_info = {
            "levels": new_ms_levels,
            "picks_per_level": {str(l): MAGICAL_SECRETS_PICKS.get(l,2) for l in new_ms_levels},
            "options": all_spell_opts,
            "existing": existing,
        }
    
    # Totem Spirit — Barbarian Totem Warrior L3/6/14
    totem_info = None
    totem_levels_list = TOTEM_SPIRIT_LEVELS.get(subclass if subclass else char.get("subclass",""), [])
    new_totem_levels = [l for l in totem_levels_list if class_level < l <= new_class_level]
    if new_totem_levels:
        existing = json.loads(char.get("totem_spirits","{}"))
        totem_info = {
            "levels": new_totem_levels,
            "labels": {str(l): TOTEM_SPIRIT_TIER_LABELS.get(l,f"L{l}") for l in new_totem_levels},
            "options": [{"key":k,"name":v["name"],"desc":v["desc"]} for k,v in TOTEM_SPIRIT_OPTIONS.items()],
            "existing": existing,
        }
    
    # Hunter's Prey — Ranger Hunter L3
    hunters_prey_info = None
    hp_level = HUNTERS_PREY_LEVELS.get(subclass if subclass else char.get("subclass",""))
    if hp_level and class_level < hp_level <= new_class_level and not char.get("hunters_prey"):
        hunters_prey_info = {
            "level": hp_level,
            "options": [{"key":k,"name":v["name"],"desc":v["desc"]} for k,v in HUNTERS_PREY_OPTIONS.items()],
        }
    
    # Artificer Infusions — L2
    infusion_info = None
    # Ranger Favored Enemy — L1/6/14
    favored_enemy_info = None
    fe_levels_list = FAVORED_ENEMY_LEVELS.get(cls, [])
    new_fe_levels = [l for l in fe_levels_list if class_level < l <= new_class_level]
    _fe_replaced = SUBCLASS_FEATURE_REPLACEMENTS.get(subclass, [])
    if new_fe_levels and 'favored_enemy' not in _fe_replaced:
        existing = json.loads(char.get("favored_enemies", "[]"))
        picks_count = len([l for l in fe_levels_list if l <= new_class_level])
        gained = picks_count - len(existing)
        if gained > 0:
            favored_enemy_info = {
                "levels": new_fe_levels,
                "picks_gained": gained,
                "total_picks": picks_count,
                "options": [{"key":k,"name":v["name"],"desc":v["desc"]} for k,v in FAVORED_ENEMY_OPTIONS.items()],
                "existing": existing,
            }

    # Ranger Favored Terrain — L1/6/10
    favored_terrain_info = None
    ft_levels_list = FAVORED_TERRAIN_LEVELS.get(cls, [])
    new_ft_levels = [l for l in ft_levels_list if class_level < l <= new_class_level]
    if new_ft_levels and 'favored_terrain' not in _fe_replaced:
        existing = json.loads(char.get("favored_terrains", "[]"))
        picks_count = len([l for l in ft_levels_list if l <= new_class_level])
        gained = picks_count - len(existing)
        if gained > 0:
            favored_terrain_info = {
                "levels": new_ft_levels,
                "picks_gained": gained,
                "total_picks": picks_count,
                "options": [{"key":k,"name":v["name"],"desc":v["desc"]} for k,v in FAVORED_TERRAIN_OPTIONS.items()],
                "existing": existing,
            }

    inf_level = INFUSION_LEVELS.get(cls)
    if inf_level and class_level < inf_level <= new_class_level:
        existing = json.loads(char.get("infusions","[]"))
        total_known = INFUSION_PICKS.get(inf_level,4)
        infusion_info = {
            "level": inf_level,
            "options": [{"key":k,"name":v["name"],"desc":v["desc"]} for k,v in INFUSION_OPTIONS.items()],
            "total_known": total_known,
            "existing": existing,
        }    
    return JSONResponse({
        "class_name": cls,
        "current_level": current_level,
        "class_level": class_level,
        "target_level": target_level,
        "levels": levels_gained,
        "all_features": all_features,
        "hp": hp_options,
        "asi": asi_infos.get(str(target_level)) if asi_infos else None,
        "asi_levels": asi_levels,
        "asi_info": asi_infos,
        "feats": feats_available,
        "subclass": subclass_info,
        "subclass_bonus_map": subclass_bonus_map,
        "proficiency_bonus": {"old": old_pb, "new": new_pb, "changed": old_pb != new_pb},
        "spells": spell_info,
        "has_subclass": bool(char.get("subclass")),
        "expertise": expertise_info,
        "fighting_style": fighting_style_info,
        "metamagic": metamagic_info,
        "invocations": invocation_info,
        "pact_boon": pact_boon_info,
        "maneuvers": maneuver_info,
        "magical_secrets": magical_secrets_info,
        "totem_spirit": totem_info,
        "hunters_prey": hunters_prey_info,
        "favored_enemy": favored_enemy_info,
        "favored_terrain": favored_terrain_info,
        "infusions": infusion_info,
        "multiclass": {
            "class_levels": cl,
            "class_to_level": class_to_level,
            "is_multiclass": class_to_level not in cl or len(cl) > 1,
            "options": multiclass_options,
        },
    })


def _feat_prereq_met(char: dict, prereq: str) -> bool:
    """Check if a character meets a feat prerequisite string."""
    if not prereq:
        return True
    if "Strength" in prereq:
        raw = prereq.replace("Strength ", "").replace("+", "").strip()
        try:
            return char.get("strength", 10) >= int(raw)
        except ValueError:
            pass
    if "Dexterity" in prereq:
        raw = prereq.replace("Dexterity ", "").replace("+", "").strip()
        try:
            return char.get("dexterity", 10) >= int(raw)
        except ValueError:
            pass
    if "Intelligence" in prereq:
        raw = prereq.replace("Intelligence ", "").replace("+", "").strip()
        try:
            return char.get("intelligence", 10) >= int(raw)
        except ValueError:
            pass
    if "Wisdom" in prereq:
        raw = prereq.replace("Wisdom ", "").replace("+", "").strip()
        try:
            return char.get("wisdom", 10) >= int(raw)
        except ValueError:
            pass
    if "Charisma" in prereq:
        raw = prereq.replace("Charisma ", "").replace("+", "").strip()
        try:
            return char.get("charisma", 10) >= int(raw)
        except ValueError:
            pass
    if "Constitution" in prereq:
        raw = prereq.replace("Constitution ", "").replace("+", "").strip()
        try:
            return char.get("constitution", 10) >= int(raw)
        except ValueError:
            pass
    # Spellcasting prereq
    if "spell" in prereq.lower() or "cast" in prereq.lower():
        caster = get_caster_type(char.get("class_name", ""))
        return caster != "none"
    if "armor" in prereq.lower():
        armors = char.get("armor_proficiencies", [])
        if isinstance(armors, str):
            armors = json.loads(armors)
        return prereq.split(" ")[0].lower() in [a.lower() for a in armors]
    return True


def _spellcasting_ability(class_name: str) -> str:
    spell_map = {
        "Bard": "Charisma", "Cleric": "Wisdom", "Druid": "Wisdom",
        "Paladin": "Charisma", "Ranger": "Wisdom", "Sorcerer": "Charisma",
        "Warlock": "Charisma", "Wizard": "Intelligence",
    }
    return spell_map.get(class_name, "Wisdom")


@router.post("/api/character/{char_id}/apply-level-up", response_class=JSONResponse)
async def apply_level_up(char_id: int, request: Request, body: ApplyLevelUp):
    """Apply all level-up choices across potentially multiple levels."""
    user = require_user(request)
    data = body.model_dump(exclude_none=True) | (body.model_extra or {})
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    
    cl = parse_class_levels(char)
    old_total = total_level(cl)
    target_level = int(data.get("target_level", old_total + 1))
    target_level = max(old_total + 1, min(target_level, 20))
    
    # Which class gains the level?
    class_to_level = data.get("class_to_level", char.get("class_name", "Fighter"))
    is_multiclass = class_to_level not in cl
    
    if is_multiclass:
        abilities = {a.lower(): char.get(a.lower(), 10) for a in ABILITY_NAMES}
        if not meets_multiclass_prereq(abilities, class_to_level):
            db.close()
            return JSONResponse({"error": f"Prerequisites not met for {class_to_level}"}, status_code=400)
    
    updates = {"level": target_level}
    changes = []
    
    # Track cumulative ability scores (start from current, apply ASIs in level order)
    cumulative = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    
    # Process levels in order — apply ASIs at each level BEFORE computing HP for that level
    levels_gained = target_level - old_total

    hp_choices = data.get("hp_choices", {})
    # Also support flat hp choice from frontend (legacy format)
    hp_flat = data.get("hp")
    hp_custom = data.get("hp_custom")
    if not hp_choices and hp_flat:
        for offset in range(levels_gained):
            lvl_str = str(old_total + offset + 1)
            hp_choices[lvl_str] = hp_flat
    asi_choices = data.get("asi_choices", {})
    # Also accept flat asi from frontend (single-level form)
    asi_flat = data.get("asi")
    if not asi_choices and asi_flat:
        lvl_str = str(target_level)
        asi_choices[lvl_str] = asi_flat
    feat_asi_choices = data.get("feat_asi_choices", {})
    # Also accept flat feat_asi_choice from frontend
    feat_asi_flat = data.get("feat_asi_choice")
    if not feat_asi_choices and feat_asi_flat:
        lvl_str = str(target_level)
        feat_asi_choices[lvl_str] = feat_asi_flat

    total_hp_gain = 0
    retro_hp = 0
    last_con_mod = (char.get("constitution", 10) - 10) // 2
    hd = CLASSES.get(class_to_level, {}).get("hd", 8)
    
    for offset in range(levels_gained):
        lvl_num = old_total + offset + 1
        lvl_str = str(lvl_num)
        if lvl_str in asi_choices:
            choice = asi_choices[lvl_str]
            if isinstance(choice, dict):
                for ability, increase in choice.items():
                    ab_tc = ability.capitalize()  # Normalize "dexterity" → "Dexterity"
                    new_val = cumulative.get(ab_tc, 10) + increase
                    if new_val > 20:
                        raise HTTPException(400, f"{ab_tc} would exceed 20 ({cumulative.get(ab_tc, 10)} + {increase} = {new_val})")
                    cumulative[ab_tc] = new_val
                    updates[ability.lower()] = cumulative[ab_tc]
                    changes.append(f"L{lvl_str}: {ability} +{increase}")
            elif isinstance(choice, str) and choice.startswith("feat:"):
                feat_key = choice[5:]
                feat = FEAT_BY_NAME.get(feat_key.lower(), {})
                changes.append(f"L{lvl_str}: Feat — {feat.get('name', feat_key)}")
                feat_asi = feat.get("asi")
                if feat_asi:
                    chosen_abi = feat_asi_choices.get(lvl_str)
                    if chosen_abi:
                        ab_tc = chosen_abi.capitalize()
                        if ab_tc in ABILITY_NAMES:
                            cumulative[ab_tc] = cumulative.get(ab_tc, 10) + feat_asi["amount"]
                            updates[chosen_abi.lower()] = cumulative[ab_tc]
        
        # Now compute HP with current CON (includes this level's ASI if any)
        con_mod = (cumulative.get("Constitution", 10) - 10) // 2
        # Retroactive CON HP (PHB p.177): when CON mod rises at this level,
        # every PRIOR level gains the delta (not just levels before the jump).
        if con_mod > last_con_mod:
            retro_hp += (lvl_num - 1) * (con_mod - last_con_mod)
        last_con_mod = con_mod
        choice = hp_choices.get(lvl_str, "average")
        if choice == "custom" and hp_custom is not None:
            hp_gain = int(hp_custom) + con_mod
        elif choice == "max":
            hp_gain = hd + con_mod
        elif choice == "roll":
            hp_gain = random.randint(1, hd) + con_mod
        else:
            hp_gain = (hd // 2) + 1 + con_mod
        total_hp_gain += hp_gain
    
    updates["hp_max"] = char.get("hp_max", 10) + total_hp_gain
    updates["hp_current"] = updates["hp_max"]  # Full heal on level up
    changes.append(f"HP +{total_hp_gain}")
    
    # Apply retroactive CON HP accumulated above
    if retro_hp > 0:
        updates["hp_max"] += retro_hp
        updates["hp_current"] += retro_hp
        changes.append(f"CON mod +{(cumulative.get('Constitution', 10) - 10) // 2 - (char.get('constitution', 10) - 10) // 2}: +{retro_hp} HP (retroactive)")
    
    # Tough feat: +2 HP per character level (PHB p.170)
    tough_level = None
    for lvl_str, choice in asi_choices.items():
        if isinstance(choice, str) and choice == "feat:tough":
            tough_level = int(lvl_str)
            break
    if tough_level:
        tough_hp = tough_level * 2
        updates["hp_max"] += tough_hp
        updates["hp_current"] += tough_hp
        changes.append(f"Tough feat: +{tough_hp} HP")
    
    # Subclass
    subclass_choice = data.get("subclass")
    if subclass_choice:
        updates["subclass"] = subclass_choice
        changes.append(f"Subclass: {subclass_choice}")
        # Wire subclass-granted proficiencies into DB columns
        sc_profs = SUBCLASS_PROFICIENCIES.get(subclass_choice, {})
        for col, key in [("armor_proficiencies","armor_profs"), ("weapon_proficiencies","weapon_profs"),
                          ("tool_proficiencies","tool_profs")]:
            for v in sc_profs.get(key, []):
                current = json.loads(char.get(col, "[]"))
                if v not in current:
                    current.append(v)
                    updates[col] = json.dumps(current)
                    changes.append(f"Proficiency: {v}")
        # Handle chosen bonus proficiencies (skills/languages from picker)
        bonus_choices = data.get("subclass_bonus", [])
        if bonus_choices:
            bonus_map = SUBCLASS_PROFICIENCIES.get(subclass_choice, {})
            if "skill_profs" in bonus_map:
                current_skills = json.loads(char.get("skills", "[]"))
                for v in bonus_choices:
                    if v not in current_skills:
                        current_skills.append(v)
                updates["skills"] = json.dumps(current_skills)
                changes.append(f"Skills: {', '.join(bonus_choices)}")
            if "languages" in bonus_map:
                current_langs = json.loads(char.get("languages", "[]"))
                for v in bonus_choices:
                    if v not in current_langs:
                        current_langs.append(v)
                updates["languages"] = json.dumps(current_langs)
                changes.append(f"Languages: {', '.join(bonus_choices)}")
    
    # Update class_levels
    new_cl = dict(cl)
    new_cl[class_to_level] = new_cl.get(class_to_level, 0) + levels_gained
    updates["class_levels"] = json.dumps(new_cl)
    # Keep class_name as primary for backward compat
    if not is_multiclass:
        updates["class_name"] = class_to_level
    
    # Proficiency bonus
    updates["proficiency_bonus"] = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Fighting Style
    fs_choice = data.get("fighting_style")
    if fs_choice:
        updates["fighting_style"] = fs_choice
        fs_name = FIGHTING_STYLES.get(fs_choice, {}).get("name", fs_choice)
        changes.append(f"Fighting Style: {fs_name}")
    
    # Features — rebuild from all classes, preserve racial features
    all_features = []
    for cls_n, cls_lvl in new_cl.items():
        sub = updates.get("subclass", char.get("subclass", ""))
        features = get_class_features(cls_n, cls_lvl, sub)
        # Wrap string features as dicts for dedup
        wrapped = [{"name": f, "source_class": cls_n} if isinstance(f, str) else dict(f, source_class=cls_n) for f in features]
        all_features.extend(wrapped)
    all_features = _deduplicate_multiclass_features(all_features, new_cl)
    # Unwrap back to strings for DB storage
    all_feature_names = [f["name"] if isinstance(f, dict) else str(f) for f in all_features]
    # Preserve racial limited-use features (they aren't class features)
    race = char.get("race", "")
    subrace = char.get("subrace", "")
    racial_features = _build_racial_limited_features(race, subrace, target_level)
    for rf in racial_features:
        # Strip "L{N}: " prefix for comparison
        rf_name = rf.split(": ", 1)[1] if ": " in rf else rf
        if not any(fn == rf_name for fn in all_feature_names):
            all_feature_names.append(rf)
    updates["features"] = json.dumps(all_feature_names)
    
    # Enriched feature_data
    mods = {a: (cumulative.get(a, 10) - 10) // 2 for a in ABILITY_NAMES}
    eff_subclass = updates.get("subclass", char.get("subclass", ""))
    enriched = enrich_features(all_feature_names, class_name=class_to_level, level=target_level, mods=mods, class_levels=new_cl, subclass=eff_subclass)
    updates["feature_data"] = json.dumps(enriched)

    # ── Unarmored Movement (Monk) & Fast Movement (Barbarian) ──
    um_bonus = 0
    for fn in all_feature_names:
        if "Unarmored Movement" in fn:
            # Look up the bonus from SRD data
            srd_levels = SRD_LEVELS.get("monk", [])
            for entry in srd_levels:
                if entry.get("level") == target_level:
                    um_bonus = entry.get("class_specific", {}).get("unarmored_movement", 0)
                    break
            break
    base_speed = char.get("speed", 30)
    try: base_speed = int(base_speed)
    except: base_speed = 30
    updates["speed"] = base_speed + um_bonus

    # Spell slots — multiclass-aware
    char_copy = dict(char)
    char_copy["class_levels"] = json.dumps(new_cl)
    char_copy["level"] = target_level
    spell_slots = get_character_spell_slots(char_copy)
    updates["spell_slot_data"] = json.dumps(spell_slots)
    updates["spell_slots_used"] = json.dumps({})  # Fresh slots after level up
    
    # Expertise — merge new picks with existing
    exp_picks = data.get("expertise_skills", [])
    if exp_picks:
        current_exp = json.loads(char.get("expertise_skills", "[]"))
        for pick in exp_picks:
            if pick not in current_exp:
                current_exp.append(pick)
        updates["expertise_skills"] = json.dumps(current_exp)
        changes.append(f"Expertise: {', '.join(exp_picks)}")
    
    # ASI history — record what was chosen at each ASI level
    if asi_choices:
        current_asi = json.loads(char.get("asi_history", "[]"))
        for lvl_str, choice in asi_choices.items():
            entry = {"level": int(lvl_str)}
            if isinstance(choice, dict):
                entry["type"] = "asi"
                entry["choices"] = choice  # {"dexterity": 2} or {"dexterity": 1, "wisdom": 1}
            elif isinstance(choice, str) and choice.startswith("feat:"):
                entry["type"] = "feat"
                entry["feat"] = choice[5:]
                feat_name = FEAT_BY_NAME.get(choice[5:].lower(), {}).get("name", choice[5:])
                changes.append(f"Feat: {feat_name}")
            current_asi.append(entry)
        updates["asi_history"] = json.dumps(current_asi)
    
    
    # ── 8 Choice Systems: apply picks from level-up ──
    # Metamagic — validate against PHB limits
    meta_picks = data.get("metamagic", [])
    if meta_picks:
        current_meta = json.loads(char.get("metamagic", "[]"))
        meta_history = json.loads(char.get("metamagic_history", "[]"))
        # Support both flat array (backward compat) and per-level dict
        if isinstance(meta_picks, dict):
            # New format: {"3": ["careful_spell", "twinned_spell"], "10": ["quickened_spell"]}
            for lvl_str, picks in meta_picks.items():
                lvl = int(lvl_str)
                for pick in picks:
                    if pick not in current_meta:
                        current_meta.append(pick)
                # Record in history
                existing_entry = next((e for e in meta_history if e["level"] == lvl), None)
                if existing_entry:
                    for pick in picks:
                        if pick not in existing_entry["choices"]:
                            existing_entry["choices"].append(pick)
                else:
                    meta_history.append({"level": lvl, "choices": list(picks)})
        else:
            # Old format: flat list — infer levels from order (backward compat)
            for pick in meta_picks:
                if pick not in current_meta:
                    current_meta.append(pick)
            # Build history from picks_per_level info
            meta_levels_list = METAMAGIC_LEVELS.get(class_to_level, [])
            new_meta_levels = [l for l in meta_levels_list if class_level < l <= target_level]
            pick_idx = 0
            for lvl in sorted(new_meta_levels):
                count = METAMAGIC_PICKS.get(lvl, 0)
                lvl_picks = meta_picks[pick_idx:pick_idx + count]
                if lvl_picks:
                    existing_entry = next((e for e in meta_history if e["level"] == lvl), None)
                    if existing_entry:
                        for pick in lvl_picks:
                            if pick not in existing_entry["choices"]:
                                existing_entry["choices"].append(pick)
                    else:
                        meta_history.append({"level": lvl, "choices": list(lvl_picks)})
                pick_idx += count
        # Enforce PHB limit: sum of picks at or below target level
        meta_levels_list = METAMAGIC_LEVELS.get(class_to_level, [])
        total_allowed = sum(METAMAGIC_PICKS.get(l, 0) for l in meta_levels_list if l <= target_level)
        if len(current_meta) > total_allowed:
            current_meta = current_meta[:total_allowed]
        updates["metamagic"] = json.dumps(current_meta)
        updates["metamagic_history"] = json.dumps(meta_history)
        # Flatten for display
        all_new = []
        if isinstance(meta_picks, dict):
            for picks in meta_picks.values():
                all_new.extend(picks)
        else:
            all_new = meta_picks
        changes.append(f"Metamagic: {', '.join(all_new)}")
    
    # Eldritch Invocations
    inv_picks = data.get("invocations", [])
    if inv_picks:
        current_inv = json.loads(char.get("invocations", "[]"))
        for pick in inv_picks:
            if pick not in current_inv:
                current_inv.append(pick)
        updates["invocations"] = json.dumps(current_inv)
        inv_names = [INVOCATION_OPTIONS.get(p,{}).get("name",p) for p in inv_picks]
        changes.append(f"Invocations: {', '.join(inv_names)}")
    
    # Pact Boon
    pb_choice = data.get("pact_boon")
    if pb_choice:
        updates["pact_boon"] = pb_choice
        pb_name = PACT_BOON_OPTIONS.get(pb_choice, {}).get("name", pb_choice)
        changes.append(f"Pact Boon: {pb_name}")
    
    # Battle Master Maneuvers
    man_picks = data.get("maneuvers", [])
    if man_picks:
        current_man = json.loads(char.get("maneuvers", "[]"))
        for pick in man_picks:
            if pick not in current_man:
                current_man.append(pick)
        updates["maneuvers"] = json.dumps(current_man)
        man_names = [MANEUVER_OPTIONS.get(p,{}).get("name",p) for p in man_picks]
        changes.append(f"Maneuvers: {', '.join(man_names)}")
    
    # Magical Secrets
    ms_picks = data.get("magical_secrets", [])
    if ms_picks:
        current_ms = json.loads(char.get("magical_secrets", "[]"))
        for pick in ms_picks:
            if pick not in current_ms:
                current_ms.append(pick)
        updates["magical_secrets"] = json.dumps(current_ms)
        changes.append(f"Magical Secrets: {', '.join(ms_picks)}")
    
    # Totem Spirit
    totem_picks = data.get("totem_spirits", {})
    if totem_picks:
        current_totems = json.loads(char.get("totem_spirits", "{}"))
        for lvl, choice in totem_picks.items():
            current_totems[str(lvl)] = choice
        updates["totem_spirits"] = json.dumps(current_totems)
        totem_names = [TOTEM_SPIRIT_OPTIONS.get(v,{}).get("name",v) for v in totem_picks.values()]
        changes.append(f"Totem: {', '.join(totem_names)}")
    
    # Hunter's Prey
    hp_choice = data.get("hunters_prey")
    if hp_choice:
        updates["hunters_prey"] = hp_choice
        hp_name = HUNTERS_PREY_OPTIONS.get(hp_choice, {}).get("name", hp_choice)
        changes.append(f"Hunter's Prey: {hp_name}")
    
    # Favored Enemy
    fe_picks = data.get("favored_enemies", [])
    if fe_picks:
        current_fe = json.loads(char.get("favored_enemies", "[]"))
        for pick in fe_picks:
            if pick not in current_fe:
                current_fe.append(pick)
        updates["favored_enemies"] = json.dumps(current_fe)
        fe_names = [FAVORED_ENEMY_OPTIONS.get(p,{}).get("name",p) for p in fe_picks]
        changes.append(f"Favored Enemy: {', '.join(fe_names)}")

    # Favored Terrain
    ft_picks = data.get("favored_terrains", [])
    if ft_picks:
        current_ft = json.loads(char.get("favored_terrains", "[]"))
        for pick in ft_picks:
            if pick not in current_ft:
                current_ft.append(pick)
        updates["favored_terrains"] = json.dumps(current_ft)
        ft_names = [FAVORED_TERRAIN_OPTIONS.get(p,{}).get("name",p) for p in ft_picks]
        changes.append(f"Favored Terrain: {', '.join(ft_names)}")

    # Artificer Infusions
    inf_picks = data.get("infusions", [])
    if inf_picks:
        current_inf = json.loads(char.get("infusions", "[]"))
        for pick in inf_picks:
            if pick not in current_inf:
                current_inf.append(pick)
        updates["infusions"] = json.dumps(current_inf)
        inf_names = [INFUSION_OPTIONS.get(p,{}).get("name",p) for p in inf_picks]
        changes.append(f"Infusions: {', '.join(inf_names)}")
    
    # Batch-add spells selected during level-up
    spell_choices = data.get("spells", [])
    if spell_choices:
        for sp in spell_choices:
            db.execute(
                "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
                (char_id, sp.get("name", ""), sp.get("level", 0), 0, 0, 0))
        # Enrich with SRD data for the changes log
        spell_names = [sp.get("name", "") for sp in spell_choices]
        changes.append(f"Spells: {', '.join(spell_names)}")
    
    # Prepared casters: auto-add newly available class spells from higher slots
    if class_to_level in PREPARED_CASTERS:
        old_slots_data = get_spell_slots(class_to_level, old_total)
        new_slots_data = get_spell_slots(class_to_level, target_level)
        old_max = 0
        new_max = 0
        if old_slots_data and old_slots_data.get("by_level"):
            old_max = max((int(lvl) for lvl, cnt in old_slots_data["by_level"].items() if cnt > 0), default=0)
        if new_slots_data and new_slots_data.get("by_level"):
            new_max = max((int(lvl) for lvl, cnt in new_slots_data["by_level"].items() if cnt > 0), default=0)
        if new_max > old_max:
            # Get already-known spell names
            known = {r[0].lower() for r in db.execute(
                "SELECT spell_name FROM character_spells WHERE character_id = ?", (char_id,)
            ).fetchall()}
            added = 0
            seen = set()
            for spell in SRD_SPELLS:
                name = spell.get("name", "")
                if not name or name.lower() in seen or name.lower() in known:
                    continue
                seen.add(name.lower())
                sp_level = spell.get("level", 0)
                if sp_level < 1 or sp_level > new_max:
                    continue
                classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
                if class_to_level.lower() not in classes:
                    continue
                db.execute(
                    "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
                    (char_id, name, sp_level, 0, 0, 0))
                added += 1
            if added:
                changes.append(f"Spellbook: +{added} new spells (up to L{new_max})")
    
    # Auto-prepare domain spells (always prepared, PHB p.58/85) — covers creation + level-up
    sub = updates.get("subclass", char.get("subclass", ""))
    if sub and sub in DOMAIN_SPELLS:
        ds_lower = [s.lower() for s in DOMAIN_SPELLS[sub]]
        for sp in db.execute(
            "SELECT id, spell_name FROM character_spells WHERE character_id = ?", (char_id,)
        ).fetchall():
            if sp[1].lower() in ds_lower:
                db.execute("UPDATE character_spells SET prepared = 1 WHERE id = ?", (sp[0],))
    
    # Auto-insert Warlock expanded spells — new patron tiers unlocked on level-up
    if sub and sub in WARLOCK_EXPANDED_SPELLS_BY_LEVEL:
        # Determine new Warlock level after this level-up
        current_warlock_lvl = cl.get("Warlock", 0) if "Warlock" in cl else (
            int(char.get("level", 0)) if char.get("class_name") == "Warlock" else 0
        )
        new_warlock_lvl = current_warlock_lvl + (1 if class_to_level == "Warlock" else 0)
        expanded = WARLOCK_EXPANDED_SPELLS_BY_LEVEL[sub]
        existing = {r[0].lower() for r in db.execute(
            "SELECT spell_name FROM character_spells WHERE character_id = ?", (char_id,)
        ).fetchall()}
        for patron_lvl, spell_names in expanded.items():
            if new_warlock_lvl >= patron_lvl:
                for sname in spell_names:
                    if sname.lower() not in existing:
                        slvl = 0
                        for spell in SRD_SPELLS:
                            if spell.get("name", "").lower() == sname.lower():
                                slvl = spell.get("level", 0)
                                break
                        db.execute(
                            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used, source) VALUES (?,?,?,?,?,?,?)",
                            (char_id, sname, slvl, 1, 0, 0, "Expanded Spell List"))
                        existing.add(sname.lower())
                        changes.append(f"Patron spell: {sname}")
    
    # Hit dice — per class (e.g. "3d10 + 2d8")
    hd_parts = []
    for cls_n, cls_lvl in new_cl.items():
        cls_hd = CLASSES.get(cls_n, {}).get("hd", 8)
        hd_parts.append(f"{cls_lvl}d{cls_hd}")
    updates["hit_dice"] = " + ".join(hd_parts)
    
    # Grant multiclass proficiencies
    if is_multiclass:
        profs = get_multiclass_proficiencies(class_to_level)
        cur_weapons = json.loads(char.get("weapon_proficiencies", "[]") or "[]")
        cur_armor = json.loads(char.get("armor_proficiencies", "[]") or "[]")
        if profs.get("weapons"):
            for w in profs["weapons"].split(","):
                if w.strip() not in cur_weapons:
                    cur_weapons.append(w.strip())
            updates["weapon_proficiencies"] = json.dumps(cur_weapons)
            changes.append(f"Multiclass {class_to_level}: gained weapon proficiencies")
        if profs.get("armor"):
            for a in profs["armor"].split(","):
                if a.strip() not in cur_armor:
                    cur_armor.append(a.strip())
            updates["armor_proficiencies"] = json.dumps(cur_armor)
            changes.append(f"Multiclass {class_to_level}: gained armor proficiencies")
    
    # Apply updates
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [char_id]
    db.execute(f"UPDATE characters SET {set_clauses} WHERE id = ?", values)
    db.commit()
    db.close()
    
    return JSONResponse({
        "ok": True,
        "new_level": target_level,
        "changes": changes,
    })

# ── De-Level (rollback) ──────────────────────────────────────────────

@router.get("/api/character/{char_id}/de-level-info", response_class=JSONResponse)
async def de_level_info(char_id: int, request: Request):
    """Return what the character would look like at a lower level.
    
    For multiclass characters, accepts an optional ``class_name`` query param.
    If omitted and the character is multiclassed, returns ``needs_class_selection``
    with the list of available classes.
    """
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    
    cl = parse_class_levels(char)
    is_multiclass = len(cl) > 1
    current_level = char.get("level", 1)
    
    # Determine which class to de-level
    requested_class = request.query_params.get("class_name", "")
    
    if is_multiclass and not requested_class:
        # Return class options so the frontend can ask the user
        options = [{"name": c, "level": l} for c, l in sorted(cl.items(), key=lambda x: -x[1])]
        return JSONResponse({
            "needs_class_selection": True,
            "multiclass_options": options,
            "current_level": current_level,
            "class_levels": cl,
        })
    
    cls = requested_class if requested_class else char.get("class_name", "Fighter")
    target_level = int(request.query_params.get("target_level", request.query_params.get("target", current_level - 1)))
    target_level = max(1, min(target_level, current_level - 1))
    
    if current_level <= 1:
        return JSONResponse({"error": "Already at level 1"}, status_code=400)
    
    # Compute class-specific levels for multiclass correctness
    class_level = cl.get(cls, current_level)  # level in this class before
    levels_lost = current_level - target_level
    new_class_level = max(0, class_level - levels_lost)
    
    # Check we aren't trying to de-level below 1 in the chosen class
    if new_class_level <= 0 and sum(l for c, l in cl.items() if c != cls) < 1:
        return JSONResponse({"error": f"Cannot de-level {cls} below level 1 — it's the only class."}, status_code=400)
    
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    
    # Features lost (at current but not at target)
    old_features = get_class_features(cls, new_class_level, char.get("subclass", ""))
    new_features = get_class_features(cls, class_level, char.get("subclass", ""))
    features_lost = [f for f in new_features if f not in old_features]
    
    # Get what target-level features look like
    target_features = get_class_features(cls, new_class_level, char.get("subclass", ""))
    
    # ASI levels being rolled back
    lost_asi_levels = [lvl for lvl in range(new_class_level + 1, class_level + 1) if lvl in ASI_LEVELS.get(cls, [])]
    
    # Current ability scores
    abilities = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    
    # Subclass note
    subclass_note = None
    sc = SUBCLASS_LEVELS.get(cls)
    current_subclass = char.get("subclass", "")
    if sc and current_subclass and sc["level"] > new_class_level:
        # Only warn if the subclass belongs to this class
        valid_subs = CLASSES.get(cls, {}).get("subclasses", []) + sc.get("options", [])
        if not valid_subs or current_subclass in valid_subs:
            subclass_note = f"{sc['label']}: {current_subclass} (chosen at L{sc['level']} — will be cleared since target class level < L{sc['level']})"
    
    # Proficiency
    old_pb = PROFICIENCY_BONUS.get(current_level, 2)
    new_pb = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Spell changes
    spell_info = None
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            old_slots = get_spell_slots(cls, class_level)
            new_slots = get_spell_slots(cls, new_class_level)
        except:
            old_slots = {}; new_slots = {}
        spell_info = {
            "caster_type": caster_type,
            "old_slots": old_slots, "new_slots": new_slots,
        }
    
    # HP estimate: subtract average per level rolled back
    estimated_hp = max(1, char.get("hp_max", 10) - levels_lost * avg_hp)

    # Suggested ability reversion: assume +2 to primary ability per lost ASI
    primary_ability = ABILITY_PRIORITY.get(cls, ["dexterity"])[0].capitalize()
    suggested_abilities = dict(abilities)
    for _ in lost_asi_levels:
        suggested_abilities[primary_ability] = max(8, suggested_abilities.get(primary_ability, 10) - 2)

    return JSONResponse({
        "class_name": cls,
        "current_level": current_level,
        "target_level": target_level,
        "levels_lost": levels_lost,
        "features_lost": features_lost,
        "target_features": target_features,
        "hp_estimate": estimated_hp,
        "hp_per_level_avg": avg_hp,
        "hit_die": f"d{hd}",
        "current_abilities": abilities,
        "suggested_abilities": suggested_abilities,
        "lost_asi_levels": lost_asi_levels,
        "subclass": char.get("subclass", ""),
        "subclass_note": subclass_note,
        "proficiency_bonus": {"old": old_pb, "new": new_pb, "changed": old_pb != new_pb},
        "spells": spell_info,
        "class_levels": cl,
    })


@router.post("/api/character/{char_id}/de-level", response_class=JSONResponse)
async def apply_de_level(char_id: int, request: Request):
    """Roll back the character to a lower level."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    cls = data.get("class_to_level", char.get("class_name", "Fighter"))
    old_level = char.get("level", 1)
    target_level = int(data.get("target_level", old_level - 1))
    target_level = max(1, min(target_level, old_level - 1))
    
    # Compute class-specific levels for multiclass correctness
    cl = parse_class_levels(char)
    old_class_level = cl.get(cls, old_level)  # level in this class before
    levels_lost = old_level - target_level
    new_class_level = max(0, old_class_level - levels_lost)  # level in this class after
    is_multiclass = len(cl) > 1
    # Build the post-delevel class_levels dict
    new_cl = dict(cl)
    new_cl[cls] = max(0, new_cl.get(cls, 0) - levels_lost)
    if new_cl[cls] <= 0:
        del new_cl[cls]
    
    updates = {"level": target_level}
    changes = []
    
    # HP: use user-provided value or estimate
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    estimated_hp = max(1, char.get("hp_max", 10) - (old_level - target_level) * avg_hp)
    new_hp = int(data.get("hp_max", estimated_hp))
    new_hp = max(1, new_hp)  # Floor at 1
    updates["hp_max"] = new_hp
    updates["hp_current"] = new_hp
    changes.append(f"HP set to {new_hp}")
    
    # Ability scores: user-specified, auto-reverted, or keep current
    ability_updates = data.get("abilities", {})
    if not ability_updates:
        # Auto-revert ASIs: assume +2 to primary ability per lost ASI level
        lost_asi = [lvl for lvl in range(new_class_level + 1, old_class_level + 1) if lvl in ASI_LEVELS.get(cls, [])]
        if lost_asi:
            primary_ab = ABILITY_PRIORITY.get(cls, ["dexterity"])[0].capitalize()
            ability_updates = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
            for _ in lost_asi:
                ability_updates[primary_ab] = max(8, ability_updates.get(primary_ab, 10) - 2)
    for a in ABILITY_NAMES:
        key = a.lower()
        if a in ability_updates:
            updates[key] = int(ability_updates[a])
            if updates[key] != char.get(key, 10):
                changes.append(f"{a}: {char.get(key, 10)} → {updates[key]}")
    
    # Subclass: keep if already had it at target, or clear if gained in lost levels
    sc = SUBCLASS_LEVELS.get(cls)
    if sc and char.get("subclass") and sc["level"] > new_class_level:
        # Only clear subclass if it was gained during the levels being lost
        if sc["level"] <= old_class_level:
            # Verify the subclass actually belongs to this class — prevents clearing
            # e.g. "Swashbuckler" (Rogue) when de-leveling Fighter
            current_sub = char.get("subclass", "")
            valid_subs = CLASSES.get(cls, {}).get("subclasses", []) + sc.get("options", [])
            if valid_subs and current_sub not in valid_subs:
                changes.append(f"Subclass '{current_sub}' kept (belongs to different class than {cls})")
            else:
                updates["subclass"] = ""
                changes.append(f"Subclass cleared ({current_sub})")
    
    # Expertise: revert picks from levels being lost
    current_exp = json.loads(char.get("expertise_skills", "[]"))
    if current_exp:
        new_count = get_expertise_count(cls, new_class_level, char.get("subclass", ""))
        # Trim to the correct count for target level
        if len(current_exp) > new_count:
            kept = current_exp[:new_count]
            lost = [s for s in current_exp if s not in kept]
            updates["expertise_skills"] = json.dumps(kept)
            changes.append(f"Expertise lost: {', '.join(lost)}")
    
    # Fighting Style: clear if reverting past the level it was gained
    fs_level = FIGHTING_STYLE_LEVELS.get(cls)
    if fs_level and fs_level > new_class_level and char.get("fighting_style"):
        updates["fighting_style"] = ""
        changes.append(f"Fighting Style cleared ({char.get('fighting_style')})")
    
    # ASI history: remove entries for lost levels
    current_asi = json.loads(char.get("asi_history", "[]"))
    if current_asi:
        kept_asi = [e for e in current_asi if e.get("level", 99) <= target_level]
        if len(kept_asi) < len(current_asi):
            updates["asi_history"] = json.dumps(kept_asi)
            lost_count = len(current_asi) - len(kept_asi)
            changes.append(f"ASI history: removed {lost_count} entr{'y' if lost_count == 1 else 'ies'}")
    
    
    # ── 8 Choice Systems: revert on de-level ──
    # Metamagic — trim to picks allowed at target level
    meta_levels_list = METAMAGIC_LEVELS.get(cls, [])
    current_meta = json.loads(char.get("metamagic", "[]"))
    meta_history = json.loads(char.get("metamagic_history", "[]"))
    if current_meta and meta_levels_list:
        total_allowed = sum(METAMAGIC_PICKS.get(l,0) for l in meta_levels_list if l <= new_class_level)
        if len(current_meta) > total_allowed:
            kept = current_meta[:total_allowed]
            lost_meta = [m for m in current_meta if m not in kept]
            updates["metamagic"] = json.dumps(kept)
            # Also trim metamagic_history — remove entries for levels above target
            kept_history = [e for e in meta_history if e["level"] <= new_class_level]
            # Also trim choices in kept levels to match total_allowed
            # Rebuild from history entries
            all_from_history = []
            for e in sorted(kept_history, key=lambda x: x["level"]):
                all_from_history.extend(e["choices"])
            if len(all_from_history) > total_allowed:
                # Trim last entry's choices
                excess = len(all_from_history) - total_allowed
                for e in reversed(kept_history):
                    while excess > 0 and e["choices"]:
                        e["choices"].pop()
                        excess -= 1
                    if excess == 0:
                        break
                # Remove empty entries
                kept_history = [e for e in kept_history if e["choices"]]
            updates["metamagic_history"] = json.dumps(kept_history)
            changes.append(f"Metamagic lost: {', '.join(lost_meta)}")
    elif meta_history:
        # Even if current_meta is empty, clean up history for levels above target
        kept_history = [e for e in meta_history if e["level"] <= new_class_level]
        if len(kept_history) != len(meta_history):
            updates["metamagic_history"] = json.dumps(kept_history)
    
    # Eldritch Invocations — trim to total picks at target
    inv_levels_list = INVOCATION_LEVELS.get(cls, [])
    current_inv = json.loads(char.get("invocations", "[]"))
    if current_inv and inv_levels_list:
        total_inv = sum(INVOCATION_PICKS.get(l,0) for l in inv_levels_list if l <= new_class_level)
        if len(current_inv) > total_inv:
            kept = current_inv[:total_inv]
            lost_inv = [i for i in current_inv if i not in kept]
            updates["invocations"] = json.dumps(kept)
            changes.append(f"Invocations lost: {', '.join(lost_inv)}")
    
    # Pact Boon — clear if reverting past L3
    pb_level = PACT_BOON_LEVELS.get(cls)
    if pb_level and pb_level > new_class_level and char.get("pact_boon"):
        updates["pact_boon"] = ""
        changes.append(f"Pact Boon cleared ({char.get('pact_boon')})")
    
    # Battle Master Maneuvers — trim at each threshold
    man_sub = char.get("subclass", "")
    man_levels_list = MANEUVER_LEVELS.get(man_sub, [])
    current_man = json.loads(char.get("maneuvers", "[]"))
    if current_man and man_levels_list:
        total_man = sum(MANEUVER_PICKS.get(l,0) for l in man_levels_list if l <= new_class_level)
        if len(current_man) > total_man:
            kept = current_man[:total_man]
            lost_man = [m for m in current_man if m not in kept]
            updates["maneuvers"] = json.dumps(kept)
            changes.append(f"Maneuvers lost: {', '.join(lost_man)}")
    
    # Magical Secrets — trim per level thresholds
    ms_source = char.get("subclass","") if char.get("subclass","") in MAGICAL_SECRETS_LEVELS else cls
    ms_levels_list = MAGICAL_SECRETS_LEVELS.get(ms_source, [])
    current_ms = json.loads(char.get("magical_secrets", "[]"))
    if current_ms and ms_levels_list:
        total_ms = sum(MAGICAL_SECRETS_PICKS.get(l,0) for l in ms_levels_list if l <= new_class_level)
        if len(current_ms) > total_ms:
            kept = current_ms[:total_ms]
            lost_ms = [s for s in current_ms if s not in kept]
            updates["magical_secrets"] = json.dumps(kept)
            changes.append(f"Magical Secrets lost: {', '.join(lost_ms)}")
    
    # Totem Spirit — remove entries above target level
    totem_sub = char.get("subclass", "")
    totem_levels_list = TOTEM_SPIRIT_LEVELS.get(totem_sub, [])
    current_totems = json.loads(char.get("totem_spirits", "{}"))
    if current_totems and totem_levels_list:
        kept_totems = {k:v for k,v in current_totems.items() if int(k) <= new_class_level}
        if len(kept_totems) < len(current_totems):
            updates["totem_spirits"] = json.dumps(kept_totems)
            lost = {k:v for k,v in current_totems.items() if k not in kept_totems}
            changes.append(f"Totem spirits lost at levels: {', '.join(lost.keys())}")
    
    # Hunter's Prey — clear if reverting past L3
    hp_level = HUNTERS_PREY_LEVELS.get(char.get("subclass",""))
    if hp_level and hp_level > new_class_level and char.get("hunters_prey"):
        updates["hunters_prey"] = ""
        changes.append(f"Hunter's Prey cleared ({char.get('hunters_prey')})")
    
    # Infusions — clear if reverting past L2
    # Favored Enemy — trim to level threshold
    fe_levels_list2 = FAVORED_ENEMY_LEVELS.get(cls, [])
    current_fe = json.loads(char.get("favored_enemies", "[]"))
    if current_fe and fe_levels_list2:
        total_fe = len([l for l in fe_levels_list2 if l <= new_class_level])
        if len(current_fe) > total_fe:
            lost_fe = current_fe[total_fe:]
            kept = current_fe[:total_fe]
            updates["favored_enemies"] = json.dumps(kept)
            fe_names = [FAVORED_ENEMY_OPTIONS.get(p,{}).get("name",p) for p in lost_fe]
            changes.append(f"Favored Enemy lost: {', '.join(fe_names)}")

    # Favored Terrain — trim to level threshold
    ft_levels_list2 = FAVORED_TERRAIN_LEVELS.get(cls, [])
    current_ft = json.loads(char.get("favored_terrains", "[]"))
    if current_ft and ft_levels_list2:
        total_ft = len([l for l in ft_levels_list2 if l <= new_class_level])
        if len(current_ft) > total_ft:
            lost_ft = current_ft[total_ft:]
            kept = current_ft[:total_ft]
            updates["favored_terrains"] = json.dumps(kept)
            ft_names = [FAVORED_TERRAIN_OPTIONS.get(p,{}).get("name",p) for p in lost_ft]
            changes.append(f"Favored Terrain lost: {', '.join(ft_names)}")

    inf_level = INFUSION_LEVELS.get(cls)
    if inf_level and inf_level > new_class_level and char.get("infusions"):
        updates["infusions"] = "[]"
        changes.append("Infusions cleared")
    
    # Proficiency
    updates["proficiency_bonus"] = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Features rebuild — per-class for multiclass
    all_features = []
    for cls_n, cls_lvl in new_cl.items():
        sub = updates.get("subclass", char.get("subclass", ""))
        features = get_class_features(cls_n, cls_lvl, sub)
        wrapped = [{"name": f, "source_class": cls_n} if isinstance(f, str) else dict(f, source_class=cls_n) for f in features]
        all_features.extend(wrapped)
    all_features = _deduplicate_multiclass_features(all_features, new_cl)
    all_feature_names = [f["name"] if isinstance(f, dict) else str(f) for f in all_features]
    updates["features"] = json.dumps(all_feature_names)
    
    # Feature data rebuild
    final_mods = {}
    for a in ABILITY_NAMES:
        key = a.lower()
        val = updates.get(key, char.get(key, 10))
        final_mods[a] = (val - 10) // 2
    eff_sub = updates.get("subclass", char.get("subclass", ""))
    enriched = enrich_features(all_feature_names, class_name=cls, level=target_level, mods=final_mods, subclass=eff_sub)
    updates["feature_data"] = json.dumps(enriched)

    # ── Unarmored Movement (Monk) speed bonus ──
    um_bonus = 0
    for fn in all_feature_names:
        if "Unarmored Movement" in fn:
            srd_levels = SRD_LEVELS.get("monk", [])
            for entry in srd_levels:
                if entry.get("level") == target_level:
                    um_bonus = entry.get("class_specific", {}).get("unarmored_movement", 0)
                    break
            break
    base_speed = char.get("speed", 30)
    try: base_speed = int(base_speed)
    except: base_speed = 30
    updates["speed"] = base_speed + um_bonus

    # Spell slots
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            new_slots = get_spell_slots(cls, target_level)
            updates["spell_slot_data"] = json.dumps(new_slots)
        except:
            pass
    
    # Hit dice
    updates["hit_dice"] = f"{target_level}d{hd}"

    # Class levels — update correctly for multiclass
    cl = parse_class_levels(char)
    new_cl = {}
    for cls_name, cls_lvl in cl.items():
        if cls_name == cls:
            new_cl[cls_name] = max(0, new_class_level)
        else:
            new_cl[cls_name] = cls_lvl
    # Remove classes that dropped to 0
    new_cl = {k: v for k, v in new_cl.items() if v > 0}
    if not new_cl:
        new_cl = {cls: 1}  # safety floor
    updates["class_levels"] = json.dumps(new_cl)
    # Update total level
    new_total = sum(new_cl.values())
    updates["level"] = new_total
    # Update class_name: use highest-level class, ties go to first taken
    best_cls = cls
    best_lvl = 0
    for cn, clv in new_cl.items():
        if clv > best_lvl:
            best_cls = cn
            best_lvl = clv
    updates["class_name"] = best_cls

    # Apply
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [char_id]
    db.execute(f"UPDATE characters SET {set_clauses} WHERE id = ?", values)
    db.commit()
    db.close()
    
    return JSONResponse({
        "ok": True,
        "new_level": target_level,
        "changes": changes,
    })