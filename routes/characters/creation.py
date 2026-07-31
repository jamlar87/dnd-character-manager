"""Character creation routes — /create page, create API, NPC→character
conversion, starting spells, name generators.

Extracted from routes/characters/all.py (2026-07-31). Imports helpers
from main / data / services.leveling / schemas only — never from
all.py (avoids circulars).
"""

import json
import re
import random
import sys
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse

from main import (
    get_db, require_user, _render, _get_source_slug_map,
    RACES, RACE_NAMES, CLASSES, BACKGROUNDS, BACKGROUND_SOURCES, ALIGNMENTS,
    FLEXIBLE_ASI_RACES, SUBASIS, SKILL_ABILITIES, ALL_SKILLS,
    DRACONIC_ANCESTRIES, PREPARED_CASTERS, SPELLS_KNOWN_CASTERS,
    SUBCLASS_FEATURES, SUBCLASS_FEATURE_REPLACEMENTS, STARTING_EQUIPMENT,
    SRD_SPELLS, SPELL_DICE, EXPERTISE_LEVELS,
    METAMAGIC_LEVELS, METAMAGIC_PICKS, METAMAGIC_OPTIONS,
    INVOCATION_LEVELS, INVOCATION_PICKS, INVOCATION_OPTIONS,
    PACT_BOON_LEVELS, PACT_BOON_OPTIONS, MANEUVER_LEVELS, MANEUVER_OPTIONS,
    TOTEM_SPIRIT_OPTIONS, HUNTERS_PREY_OPTIONS, FAVORED_ENEMY_OPTIONS,
    FAVORED_TERRAIN_OPTIONS, INFUSION_OPTIONS,
    get_racial_trait_effects, enrich_features, get_spell_slots,
)
from services.leveling import (
    _build_racial_limited_features, _scaled_dice_display, get_class_features,
    get_spell_slots, enrich_features, _manual_races_raw,
    DOMAIN_SPELLS, WARLOCK_EXPANDED_SPELLS_BY_LEVEL,
    FIGHTING_STYLES, FIGHTING_STYLE_OPTIONS,
    FAVORED_ENEMY_LEVELS, FAVORED_TERRAIN_LEVELS, HUNTERS_PREY_LEVELS,
    INFUSION_LEVELS, INFUSION_PICKS, MAGICAL_SECRETS_LEVELS,
    MAGICAL_SECRETS_PICKS, MANEUVER_LEVELS, MANEUVER_PICKS,
    SUBCLASS_PROFICIENCIES, TOTEM_SPIRIT_LEVELS, TOTEM_SPIRIT_TIER_LABELS,
    PREPARED_CASTERS, SPELLS_KNOWN_CASTERS, SPELL_DICE, SRD_SPELLS,
    SUBCLASS_FEATURES, EXPERTISE_LEVELS,
)
from routes.schemas import CreateCharacter
from routes.characters.ai_routes import _calculate_attacks

router = APIRouter()
@router.get("/create", response_class=HTMLResponse)
async def create_character_page(request: Request):
    require_user(request)
    return _render("create.html", request=request,
        races=RACES, subasis=SUBASIS, classes=CLASSES,
        all_skills=ALL_SKILLS, skill_abilities=SKILL_ABILITIES,
        backgrounds=BACKGROUNDS, alignments=ALIGNMENTS,
        background_sources=BACKGROUND_SOURCES,
        draconic_ancestries=DRACONIC_ANCESTRIES,
        race_names=RACE_NAMES, expertise_levels=EXPERTISE_LEVELS,
        flexible_asi_races=list(FLEXIBLE_ASI_RACES),
        fighting_style_options=FIGHTING_STYLE_OPTIONS,
        fighting_styles=FIGHTING_STYLES,
        metamagic_options=METAMAGIC_OPTIONS, metamagic_levels=METAMAGIC_LEVELS, metamagic_picks=METAMAGIC_PICKS,
        invocation_options=INVOCATION_OPTIONS, invocation_levels=INVOCATION_LEVELS, invocation_picks=INVOCATION_PICKS,
        pact_boon_options=PACT_BOON_OPTIONS, pact_boon_levels=PACT_BOON_LEVELS,
        maneuver_options=MANEUVER_OPTIONS, maneuver_levels=MANEUVER_LEVELS, maneuver_picks=MANEUVER_PICKS,
        magical_secrets_levels=MAGICAL_SECRETS_LEVELS, magical_secrets_picks=MAGICAL_SECRETS_PICKS,
        totem_spirit_options=TOTEM_SPIRIT_OPTIONS, totem_spirit_levels=TOTEM_SPIRIT_LEVELS, totem_spirit_tier_labels=TOTEM_SPIRIT_TIER_LABELS,
        hunters_prey_options=HUNTERS_PREY_OPTIONS, hunters_prey_levels=HUNTERS_PREY_LEVELS,
        favored_enemy_options=FAVORED_ENEMY_OPTIONS, favored_enemy_levels=FAVORED_ENEMY_LEVELS,
        favored_terrain_options=FAVORED_TERRAIN_OPTIONS, favored_terrain_levels=FAVORED_TERRAIN_LEVELS,
        infusion_options=INFUSION_OPTIONS, infusion_levels=INFUSION_LEVELS, infusion_picks=INFUSION_PICKS,
        source_map_json=json.dumps(_get_source_slug_map()),
        subclass_feature_replacements=SUBCLASS_FEATURE_REPLACEMENTS)


def _build_character(data: dict, user_id: int) -> tuple[int, str]:
    """Synchronous character builder. Takes creation dict + user_id, returns (char_id, name).
    Raises ValueError or KeyError on bad input — caller wraps for HTTP."""
    # Compute ability scores
    def _asi(base, bonuses):
        stats = {}
        for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
            stats[stat] = int(base.get(stat, 10) or 10) + int(bonuses.get(stat, 0) or 0)
        return stats

    race_name = data.get("race", "")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "")
    subclass = data.get("subclass", "")
    level = max(1, int(data.get("level", 1) or 1))
    name = data.get("name", "").strip()
    if not name or not race_name or not class_name:
        raise ValueError("Name, race, and class required")

    # Race ASIs
    race_data = RACES.get(race_name, {})
    race_asi = dict(race_data.get("asi", {}))
    if subrace and subrace in SUBASIS:
        for k, v in SUBASIS[subrace].items():
            race_asi[k] = race_asi.get(k, 0) + v
    # Half-Elf: +1 to two other abilities (user picks from data.asi_picks)
    asi_picks = data.get("asi_picks", [])
    if race_name == "Half-Elf" and len(asi_picks) == 2:
        for a in asi_picks:
            if a in race_asi:
                race_asi[a] = race_asi.get(a, 0) + 1
    # Custom Lineage: +2 to one ability (user picks from data.asi_picks)
    if race_name in FLEXIBLE_ASI_RACES and len(asi_picks) == 1:
        a = asi_picks[0]
        if a in race_asi:
            race_asi[a] = race_asi.get(a, 0) + 2

    stats = _asi(data.get("abilities", {}), race_asi)

    class_data = CLASSES.get(class_name, {})
    hd = class_data.get("hd", 8)
    hp = hd + (stats["constitution"] - 10) // 2
    hp_max = hp + (hd // 2 + 1 + (stats["constitution"] - 10) // 2) * (level - 1)

    prof_bonus = 2 + (level - 1) // 4
    ac_base = 10 + (stats["dexterity"] - 10) // 2

    # Generate build data (features, attacks, spell slots)
    build_features = get_class_features(class_name, level, subclass)
    _homebrew_feature_descs = {}  # name → description from NPC data (for homebrew fallback)
    if not build_features:
        # Homebrew class not in SRD — carry over NPC's original features
        build_features = data.get("features", [])
        if isinstance(build_features, str):
            try: build_features = json.loads(build_features)
            except: build_features = []
        if build_features:
            # Save original descriptions for later enrichment
            for _f in build_features:
                if isinstance(_f, dict) and _f.get('name'):
                    _homebrew_feature_descs[_f['name'].lower().strip()] = _f.get('description', '')
            # Normalize to string format: "L{lvl}: Name"
            # Level 1 assumed for raw NPC features (or try to extract level marker)
            build_features = [
                f"L{level}: {f['name']}" if isinstance(f, dict) and f.get('name')
                else f"L{level}: {f}" if isinstance(f, str) and not f.startswith("L")
                else f
                for f in build_features
            ]
    # Append ALL racial limited-use features (not just Dragonborn)
    racial_features = _build_racial_limited_features(race_name, data.get("subrace", ""), level)
    if not racial_features:
        # Manual race not in PHB — load all traits from manual data
        try:
            # Normalize race name through known aliases
            _lookup = race_name.lower().strip()
            _race_aliases = {
                "elves of mirkwood": "Mirkwood Elf",
                "hobbits of the shire": "Hobbit of the Shire",
                "hobbit of the shire": "Hobbit of the Shire",
                "high elves of rivendell": "High Elf of Rivendell",
            }
            _target = _race_aliases.get(_lookup, race_name)
            for _mrr in _manual_races_raw:
                if _mrr.get("name", "").lower() == _target.lower():
                    _existing_names = set()
                    for _ef in build_features:
                        _en = _ef.split(": ", 1)[1] if ": " in _ef else _ef
                        _existing_names.add(_en.lower().strip())
                    for _t in _mrr.get("traits", []):
                        _tname = _t.get("name", "").lower().strip()
                        # Always add manual race trait descriptions (overrides worse NPC cross-refs)
                        if _t.get('description'):
                            _homebrew_feature_descs[_tname] = _t.get('description', '')
                        if _tname not in _existing_names:
                            racial_features.append(f"L{level}: {_t['name']}")
                    break
        except Exception:
            pass
    build_features.extend(racial_features)
    enriched = enrich_features(build_features, class_name=class_name, level=level, mods={a: (stats[a] - 10) // 2 for a in stats}, subclass=subclass)
    # Patch descriptions for homebrew features (from NPC data or manual races)
    # Always override SRD enrichment — homebrew source data is authoritative for
    # manual races and NPC-original features (SRD may match wrong features like
    # "Night Vision" resolving to a subclass entry instead of the racial trait)
    if _homebrew_feature_descs:
        for _ef in enriched:
            _name = (_ef.get("name") or "").lower().strip()
            if _name in _homebrew_feature_descs:
                _ef["description"] = _homebrew_feature_descs[_name]
    build_attacks = _calculate_attacks(class_name, level,
        {a: (stats[a] - 10) // 2 for a in stats}, prof_bonus,
        data.get("equipment", []))

    # Spell slots from SRD data
    spell_slots = get_spell_slots(class_name, level)

    # Passive perception: 10 + WIS mod + proficiency if Perception proficient
    skills_list = data.get("skills", [])
    wis_mod = (stats["wisdom"] - 10) // 2
    passive = 10 + wis_mod + (prof_bonus if "Perception" in skills_list else 0)

    # Starting proficiencies from class (PHB 2014)
    def _parse_prof_list(raw):
        """Split comma-separated proficiency string into list, filtering empties."""
        if not raw:
            return []
        if isinstance(raw, list):
            return [p.strip() for p in raw if p.strip()]
        return [p.strip() for p in raw.split(",") if p.strip()]

    weapon_profs = _parse_prof_list(class_data.get("weapons", ""))
    armor_profs = _parse_prof_list(class_data.get("armor", ""))
    tool_profs = _parse_prof_list(class_data.get("tools", ""))
    save_profs = class_data.get("saves", [])

    # ── Racial trait effects (PHB 2014) ──
    racial_effects = get_racial_trait_effects(race_name, subrace,
                                               data.get("dragonborn_ancestry", ""))
    damage_resist = racial_effects["damage_resist"]
    condition_immune = racial_effects["condition_immune"]
    damage_immune = []
    damage_vuln = []

    # Merge racial proficiencies into class-granted ones
    for key, lst in [("armor_profs", armor_profs), ("weapon_profs", weapon_profs),
                      ("tool_profs", tool_profs), ("skill_profs", skills_list)]:
        for v in racial_effects.get(key, []):
            if v not in lst:
                lst.append(v)

    # Merge subclass-granted proficiencies (PHB domain/college/circle bonuses)
    subclass_profs = SUBCLASS_PROFICIENCIES.get(subclass, {})
    for key, lst in [("armor_profs", armor_profs), ("weapon_profs", weapon_profs),
                      ("tool_profs", tool_profs), ("skill_profs", skills_list)]:
        for v in subclass_profs.get(key, []):
            if v not in lst:
                lst.append(v)
    
    # Merge chosen subclass bonus picks (languages/skills from picker)
    bonus_choices = data.get("subclass_bonus", [])
    if bonus_choices:
        bonus_spec = SUBCLASS_PROFICIENCIES.get(subclass, {})
        if "skill_profs" in bonus_spec:
            for v in bonus_choices:
                if v not in skills_list:
                    skills_list.append(v)
        if "languages" in bonus_spec:
            languages_list = list(race_data.get("languages", ["Common"]))
            for v in bonus_choices:
                if v not in languages_list:
                    languages_list.append(v)
            race_data = dict(race_data)
            race_data["languages"] = languages_list

    # Racial speed override (Wood Elf: 35 ft)
    if racial_effects.get("speed"):
        race_data = dict(race_data)
        race_data["speed"] = racial_effects["speed"]

    # Racial natural armor (Tortle, Lizardfolk, etc.)
    natural_armor = racial_effects.get("natural_armor")
    if natural_armor:
        ac_base = natural_armor.get("base_ac", 17)
        # If armor allows DEX bonus (like Lizardfolk 13+DEX)
        max_dex = natural_armor.get("max_dex")
        if max_dex is not None:
            ac_base += min((stats["dexterity"] - 10) // 2, max_dex)

    # Merge background items into inventory (normalize to {name, qty} dicts)
    def _parse_item(item):
        """Parse item string: '4 Javelins' → {name: 'Javelin', qty: 4}.
        Strips leading quantity, trailing plural 's', and measurement units."""
        if isinstance(item, str):
            import re
            s = item.strip()
            m = re.match(r'^(\d+)\s+(.+)', s)
            if m:
                qty = int(m.group(1))
                rest = m.group(2)
                if not re.match(r'^(ft|lb|oz|gp|sp|cp|mi|yd|in|gal|feet|inch|mile|yard|pound|ounce)', rest, re.IGNORECASE):
                    # Strip trailing 's' for plurals (but not 'ss' endings like 'cross')
                    if rest.endswith('s') and not rest.endswith('ss') and len(rest) > 3:
                        rest = rest[:-1]
                    return {"name": rest, "qty": qty}
            # No quantity prefix — still singularize
            name = s
            if name.endswith('s') and not name.endswith('ss') and len(name) > 3:
                name = name[:-1]
            return {"name": name, "qty": 1}
        return item

    inventory = [_parse_item(item) for item in data.get("equipment", [])]
    bg_data_raw = data.get("background_data", "")
    if bg_data_raw and isinstance(bg_data_raw, dict):
        for item in bg_data_raw.get("items", []):
            inventory.append(_parse_item(item))

    db = get_db()
    cur = db.execute("""
        INSERT INTO characters (user_id, name, race, subrace, class_name, subclass,
        level, background, background_data, alignment, personality, backstory, strength, dexterity, constitution, intelligence,
        wisdom, charisma, hp_max, hp_current, ac, speed,
        proficiency_bonus, hit_dice, skills, features, languages, tool_proficiencies,
        weapon_proficiencies, armor_proficiencies, save_proficiencies, inventory, equipped,
        damage_resistances, damage_immunities, damage_vulnerabilities, condition_immunities,
        feature_data, attacks_data, spell_slot_data, passive_perception, dragonborn_ancestry, portrait_url, portrait_prompt, expertise_skills, fighting_style,
        metamagic, metamagic_history, invocations, pact_boon, maneuvers, magical_secrets, totem_spirits, hunters_prey, favored_enemies, favored_terrains, infusions, class_levels)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user_id, name, race_name, subrace, class_name, subclass, level,
        data.get("background",""), json.dumps(data.get("background_data","")), data.get("alignment",""), data.get("personality",""), data.get("backstory",""),
        stats["strength"], stats["dexterity"], stats["constitution"],
        stats["intelligence"], stats["wisdom"], stats["charisma"],
        hp_max, hp_max, ac_base, race_data.get("speed", 30),
        prof_bonus, f"1d{hd}",
        json.dumps(skills_list), json.dumps(build_features), json.dumps(race_data.get("languages",["Common"])),
        json.dumps(tool_profs), json.dumps(weapon_profs), json.dumps(armor_profs), json.dumps(save_profs),
        json.dumps(inventory), json.dumps([]),
        json.dumps(damage_resist), json.dumps(damage_immune), json.dumps(damage_vuln), json.dumps(condition_immune),
        json.dumps(enriched), json.dumps(build_attacks), json.dumps(spell_slots), passive,
        data.get("dragonborn_ancestry", ""),
        data.get("portrait_url", ""), data.get("portrait_prompt", ""),
        json.dumps(data.get("expertise_skills", [])),
        data.get("fighting_style", ""),
        json.dumps(data.get("metamagic", [])),
        json.dumps(data.get("metamagic_history", [])),
        json.dumps(data.get("invocations", [])),
        data.get("pact_boon", ""),
        json.dumps(data.get("maneuvers", [])),
        json.dumps(data.get("magical_secrets", [])),
        json.dumps(data.get("totem_spirits", {})),
        data.get("hunters_prey", ""),
        json.dumps(data.get("favored_enemies", [])),
        json.dumps(data.get("favored_terrains", [])),
        json.dumps(data.get("infusions", [])),
        json.dumps({class_name: level})
    ))
    char_id = cur.lastrowid
    db.commit()
    
    # Insert starting spells if provided
    spell_choices = data.get("spells", [])
    if spell_choices:
        for sp in spell_choices:
            sp_name = sp if isinstance(sp, str) else sp.get("name", "")
            sp_level = 0 if isinstance(sp, str) else sp.get("level", 0)
            db.execute(
                "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
                (char_id, sp_name, sp_level, 0, 0, 0))
        db.commit()

    # Prepared casters: auto-populate entire class spell list
    if class_name in PREPARED_CASTERS:
        slots = get_spell_slots(class_name, level)
        max_slot = 0
        if slots and slots.get("by_level"):
            max_slot = max((int(lvl) for lvl, cnt in slots["by_level"].items() if cnt > 0), default=0)
        added = 0
        seen = set()
        for spell in SRD_SPELLS:
            name = spell.get("name", "")
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            sp_level = spell.get("level", 0)
            if sp_level < 1 or sp_level > max_slot:
                continue  # Cantrips handled separately; only L1+ auto-loaded
            classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
            if class_name.lower() not in classes:
                continue
            db.execute(
                "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
                (char_id, name, sp_level, 0, 0, 0))
            added += 1
        db.commit()
    
    # Auto-prepare domain spells (always prepared, PHB p.58/85)
    if subclass and subclass in DOMAIN_SPELLS:
        ds_lower = [s.lower() for s in DOMAIN_SPELLS[subclass]]
        for sp in db.execute(
            "SELECT id, spell_name FROM character_spells WHERE character_id = ?", (char_id,)
        ).fetchall():
            if sp[1].lower() in ds_lower:
                db.execute("UPDATE character_spells SET prepared = 1 WHERE id = ?", (sp[0],))
        db.commit()
    
    # Auto-insert Warlock expanded spell list spells (always known, don't count against limit)
    if class_name == "Warlock" and subclass and subclass in WARLOCK_EXPANDED_SPELLS_BY_LEVEL:
        expanded = WARLOCK_EXPANDED_SPELLS_BY_LEVEL[subclass]
        existing = {r[0].lower() for r in db.execute(
            "SELECT spell_name FROM character_spells WHERE character_id = ?", (char_id,)
        ).fetchall()}
        for patron_lvl, spell_names in expanded.items():
            if level >= patron_lvl:
                for sname in spell_names:
                    if sname.lower() not in existing:
                        # Look up SRD spell level
                        slvl = 0
                        for spell in SRD_SPELLS:
                            if spell.get("name", "").lower() == sname.lower():
                                slvl = spell.get("level", 0)
                                break
                        db.execute(
                            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used, source) VALUES (?,?,?,?,?,?,?)",
                            (char_id, sname, slvl, 1, 0, 0, "Expanded Spell List"))
                        existing.add(sname.lower())
        db.commit()
    
    db.close()
    return (char_id, name)


@router.post("/api/character/create", response_class=JSONResponse)
async def api_create_character(request: Request):
    """Create a new character. Thin async wrapper around _build_character."""
    import traceback
    user = require_user(request)
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    # Validate core fields with Pydantic
    from pydantic import ValidationError
    try:
        body = CreateCharacter.model_validate(raw)
    except ValidationError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    try:
        char_id, name = _build_character(raw, user["id"])
        return JSONResponse({"id": char_id, "name": name})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        tb = traceback.format_exc()
        import sys
        print(f"ERROR creating character: {tb}", file=sys.stderr)
        with open("/tmp/char_create_error.log", "w") as _f:
            _f.write(tb)
        return JSONResponse({"error": f"Internal error: {e}"}, status_code=500)


_NPC_RACE_ALIASES: dict[str, str] = {
    "half-elf": "Half-Elf", "half elve": "Half-Elf", "half-elf (variant)": "Half-Elf",
    'half-orc': 'Half-Orc', 'half ore': 'Half-Orc', 'half-ore': 'Half-Orc',
    "half-elf (dmg)": "Half-Elf",
    "wood elf": "Wood Elf", "wood-elf": "Wood Elf", "wild elf": "Wood Elf",
    "high elf": "High Elf", "high-elf": "High Elf",
    "dark elf (drow)": "Dark Elf (Drow)", "dark elf": "Dark Elf (Drow)", "drow": "Dark Elf (Drow)",
    "moon elf": "Moon Elf", "moon-elf": "Moon Elf",
    "deep gnome (svirfneblin)": "Deep Gnome (Svirfneblin)", "svirfneblin": "Deep Gnome (Svirfneblin)",
    "rock gnome": "Rock Gnome",
    "forest gnome": "Forest Gnome",
    "lightfoot halfling": "Lightfoot Halfling", "lightfoot halflings": "Lightfoot Halfling",
    "stout halfling": "Stout Halfling", "strongheart halfling": "Stout Halfling",
    "hill dwarf": "Hill Dwarf",
    "mountain dwarf": "Mountain Dwarf",
    "shield dwarf": "Shield Dwarf",
    "fire genasi": "Fire Genasi", "genasi (fire)": "Fire Genasi",
    "water genasi": "Water Genasi", "genasi (water)": "Water Genasi",
    "air genasi": "Air Genasi", "genasi (air)": "Air Genasi",
    "earth genasi": "Earth Genasi", "genasi (earth)": "Earth Genasi",
    "dragonborn (bronze dragon ancestry)": "Dragonborn",
    "gold dragonborn": "Dragonborn",
    "ghostwise halflings": "Ghostwise Halfling",
    "tiefling (variant)": "Tiefling",
    "half-umbral dragon bugbear": "Bugbear",
    "goblin (custom)": "Goblin",
    "hobbit of the shire": "Hobbit of the Shire",
    "mirkwood elf": "Mirkwood Elf",
    "high elf of rivendell": "High Elf of Rivendell",
    "woodman": "Woodman",
    "bearfolk": "Bearfolk",
    "trollkin": "Trollkin",
    "erina": "Erina",
    "wyrd gnome": "Wyrd Gnome",
    "alseid": "Alseid",
    "satarre": "Satarre",
    "sidhe": "Sidhe",
    "shadow fey": "Shadow Fey",
    "elfmarked": "Elfmarked",
    "saurial": "Saurial",
    "vodyanoi": "Vodyanoi",
    "ratatosk": "Ratatosk",
    "umbral human": "Umbral Human",
    "gearforged": "Gearforged",
    "dorwinion": "Dorwinion",
    "easterling": "Easterling",
    "northman": "Northman",
    "hill-man": "Hill-man",
    "hill-man of rhudaur": "Hill-man",
}


def _normalize_npc_race(race_str: str) -> str:
    """Map an NPC race string to a canonical RACES key.
    Returns the matched RACES key, or the original string if no match."""
    if not race_str:
        return ""
    key = race_str.strip().lower()
    # Direct alias lookup
    if key in _NPC_RACE_ALIASES:
        return _NPC_RACE_ALIASES[key]
    # Strip parenthetical context: "Human (Barding)" → "Human"
    base = re.sub(r"\s*\(.*?\)", "", key).strip()
    if base != key and base in _NPC_RACE_ALIASES:
        return _NPC_RACE_ALIASES[base]
    # Try base against RACES keys directly
    for rk in RACES:
        if rk.lower() == base or rk.lower() == key:
            return rk
    # Try partial match: any RACES key that contains the base name
    for rk in RACES:
        if base in rk.lower() or rk.lower() in base:
            return rk
    return race_str  # No match found — return original for user to resolve


def _normalize_npc_class(class_str: str) -> tuple[str, str]:
    """Map an NPC class_name string to (canonical CLASSES key, extracted subclass).
    
    Examples:
      "Wizard (Necromancer)" → ("Wizard", "Necromancer")
      "bard" → ("Bard", "")
      "Fighter (Knight of the Black Fist)" → ("Fighter", "Knight of the Black Fist")
      "Monk (Way of the Sacred Fists) / Cleric" → ("Monk", "Way of the Sacred Fists")
    """
    if not class_str:
        return ("", "")
    key = class_str.strip()
    subclass = ""
    # Extract subclass from parenthetical: "ClassName (Subclass)" → ("ClassName", "Subclass")
    paren_match = re.match(r"^([^(]+)\s*\(([^)]+)\)", key)
    if paren_match:
        key = paren_match.group(1).strip()
        subclass = paren_match.group(2).strip()
    # Handle multi-class: "Fighter/Druid" → take the first class
    if "/" in key:
        key = key.split("/")[0].strip()
    # Case-insensitive match
    key_lower = key.lower()
    for ck in CLASSES:
        if ck.lower() == key_lower:
            return (ck, subclass)
    return (class_str, subclass)


@router.post("/api/build-from-npc", response_class=JSONResponse)
async def build_from_npc(request: Request):
    """Convert an NPC stat block into a playable character.
    Accepts NPC data (from npcs.json or user-supplied) and attempts to
    auto-detect race, class, subclass, level, ability scores, etc.
    
    Returns a dict with:
      - auto_detected: what the system could infer
      - gaps: fields the user needs to fill in
      - character_data: pre-filled build data ready for /api/character/create
    """
    user = require_user(request)
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    npc = raw.get("npc", {})
    if not npc or not npc.get("name"):
        return JSONResponse({"error": "NPC must have a 'name' field"}, status_code=400)

    # ── Auto-detect race ──
    race_str = npc.get("race", "")
    detected_race = _normalize_npc_race(race_str) if race_str else ""

    # ── Auto-detect class / subclass / level ──
    class_str = npc.get("class_name", "")
    detected_class, detected_subclass = _normalize_npc_class(class_str) if class_str else ("", "")
    level = npc.get("level", 1)

    # ── Parse hit points ──
    hp_raw = npc.get("hit_points", "")
    hp = 0
    if isinstance(hp_raw, str):
        hp_match = re.match(r"(\d+)", hp_raw)
        if hp_match:
            hp = int(hp_match.group(1))
    elif isinstance(hp_raw, (int, float)):
        hp = int(hp_raw)

    # ── Build gaps list ──
    gaps = []
    if not detected_race or detected_race == race_str:
        gaps.append({
            "field": "race",
            "label": "Race / Species",
            "original": race_str,
            "note": "Unknown race — select from available races" if race_str else "No race set",
        })
    if not detected_class or detected_class == class_str:
        gaps.append({
            "field": "class_name",
            "label": "Class",
            "original": class_str,
            "note": "Unknown class — select from available classes" if class_str else "No class set",
        })
    if not npc.get("alignment"):
        gaps.append({
            "field": "alignment",
            "label": "Alignment",
            "original": "",
            "note": "Pick an alignment",
        })
    # Subclass gap: class has subclasses but none detected
    if detected_class and detected_class in CLASSES:
        class_subclasses = CLASSES[detected_class].get("subclasses", [])
        if class_subclasses and not detected_subclass:
            gaps.append({
                "field": "subclass",
                "label": "Subclass",
                "original": "",
                "options": class_subclasses,
                "note": f"Select a subclass for {detected_class}",
            })
    elif detected_subclass:
        # Subclass was extracted from parenthetical but may not be registered
        if detected_subclass not in SUBCLASS_FEATURES:
            gaps.append({
                "field": "subclass",
                "label": "Subclass",
                "original": detected_subclass,
                "note": f"Subclass '{detected_subclass}' not found in library — may need manual entry",
            })

    # ── Build character data payload ──
    char_data = {
        "name": npc.get("name", ""),
        "race": detected_race,
        "class_name": detected_class,
        "subclass": detected_subclass,
        "level": level,
        "ability_scores": npc.get("ability_scores", {}),
        "hit_points": hp,
        "alignment": npc.get("alignment", ""),
        "skills": npc.get("skills", {}),
        "saving_throws": npc.get("saving_throws", {}),
        "features": npc.get("features", []),
        "equipment": npc.get("equipment", []),
        "spellcasting": npc.get("spellcasting"),
        "role": npc.get("role", "Ally"),
        "is_enemy": npc.get("is_enemy", False),
        "xp_reward": npc.get("xp_reward", 0),
        "source": npc.get("source", ""),
        "description": npc.get("description", ""),
    }

    # ── Build response with modal prompt data ──
    result = {
        "ok": True,
        "auto_detected": {
            "race": detected_race if detected_race and detected_race != race_str else None,
            "class": detected_class if detected_class and detected_class != class_str else None,
            "subclass": detected_subclass or None,
            "level": level,
            "has_ability_scores": bool(npc.get("ability_scores")),
            "has_skills": bool(npc.get("skills")),
            "has_features": bool(npc.get("features")),
            "has_equipment": bool(npc.get("equipment")),
            "has_spellcasting": bool(npc.get("spellcasting")),
        },
        "gaps": gaps,
        "character_data": char_data,
    }
    return JSONResponse(result)


@router.get("/api/spells/starting", response_class=JSONResponse)
async def starting_spells(request: Request, class_name: str = "", level: int = 1):
    """Return L1+spells available to a class at a given level. Public — no auth needed."""
    if class_name not in SPELLS_KNOWN_CASTERS and class_name not in PREPARED_CASTERS:
        return JSONResponse([])
    level = max(1, min(level, 20))
    max_slot = 0
    slots = get_spell_slots(class_name, level)
    if class_name == "Warlock":
        max_slot = slots.get("slot_level", 0) or 0
    elif slots and slots.get("by_level"):
        max_slot = max((int(lvl) for lvl, cnt in slots["by_level"].items() if cnt > 0), default=0)
    results = []
    seen = set()
    for spell in SRD_SPELLS:
        name = spell.get("name", "")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        sp_level = spell.get("level", 0)
        if sp_level > max_slot:
            continue
        classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
        if class_name.lower() not in classes:
            continue
        results.append({
            "name": name, "level": sp_level,
            "school": spell.get("school", {}).get("name", ""),
            "source": spell.get("source", "SRD"),
            "casting_time": spell.get("casting_time", ""),
            "range": spell.get("range", ""),
            "duration": spell.get("duration", ""),
            "concentration": spell.get("concentration", False),
            "ritual": spell.get("ritual", False),
            "description": " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else (spell.get("description", "") or ""),
            "book": spell.get("source", ""),
        })
    results.sort(key=lambda s: (s["level"], s["name"]))
    # Enrich with spell dice indicators (same as character sheet badges)
    for s in results:
        dice_info = SPELL_DICE.get(s["name"].lower())
        if dice_info:
            s["dice"] = _scaled_dice_display(dice_info, level)
            s["dice_healing"] = bool(dice_info.get("healing"))
            s["dice_ac"] = bool(dice_info.get("ac_bonus"))
            s["dice_buff"] = bool(dice_info.get("buff"))
    return JSONResponse(results)


def random_name(race: str, gender: str = "any") -> dict:
    """Generate a random name for the given race."""
    data = RACE_NAMES.get(race)
    if data:
        if gender == "any":
            gender = random.choice(["male", "female"])
        first = random.choice(data[gender])
        # Skip clan if empty or 30% random chance
        if not data.get("clan") or not data["clan"] or random.random() < 0.3:
            return {"name": first, "first": first, "clan": ""}
        clan = random.choice(data["clan"])
        return {"name": f"{first} {clan}", "first": first, "clan": clan}

    # Fallback: syllable-based generator for any race not in RACE_NAMES
    syllables = {
        "prefix": ["Al","Ar","Bal","Bel","Cal","Cel","Dar","Dor","El","Er","Far","Gal","Gar","Hel","Il","Jal","Kal",
                    "Kel","Kor","Lan","Lor","Mal","Mar","Mel","Mir","Mor","Nal","Nor","Or","Pal","Per","Quel","Ral",
                    "Rel","Ril","Sal","Sar","Sel","Sil","Tal","Tar","Tel","Thal","Ther","Tor","Ul","Val","Var","Vel",
                    "Ver","Vil","Vor","Wal","Wil","Xal","Yel","Yor","Zal","Zel","Zor"],
        "mid": ["a","ae","ai","an","ar","e","ea","ei","en","er","i","ia","ian","ien","il","in","ir","o","oa","on",
                 "or","u","ua","un","ur","y","yr"],
        "suffix": ["a","ac","ad","aer","al","an","ar","as","ath","en","er","es","eth","ian","ien","il","in","ion",
                    "ir","is","ith","on","or","os","uin","um","us","ya","yr"],
    }

    def _build(c):
        # Build a name with c components
        parts = [random.choice(syllables["prefix"])]
        for _ in range(c - 2):
            parts.append(random.choice(syllables["mid"]))
        parts.append(random.choice(syllables["suffix"]))
        # Post-process: join and normalize
        name = "".join(parts)
        # Capitalize
        return name[0].upper() + name[1:] if name else name

    if gender == "any":
        gender = "male"
    # Male names: 2-3 syllable parts, female: 2-4 with more vowels
    if gender == "female":
        n_parts = random.choice([3, 4])
    else:
        n_parts = random.choice([2, 3])
    first = _build(n_parts)
    # Sometimes add a clan-like second name
    if random.random() < 0.6:
        clan = _build(random.choice([2, 3]))
        return {"name": f"{first} {clan}", "first": first, "clan": clan}
    return {"name": first, "first": first, "clan": ""}


def random_equipment(class_name: str) -> list[str]:
    return STARTING_EQUIPMENT.get(class_name, ["Explorer's Pack", "Dagger"])


def _normalize_recharge(recharge: str) -> str:
    """Normalize racial trait recharge strings to canonical forms.
    
    Handles edge cases like 'combat', 'special', 'short or long rest'.
    Canonical values: 'short', 'long', 'combat', 'special', 'dawn'.
    """
    r = recharge.lower().strip()
    if "short" in r and "long" in r:
        return "short"  # "short or long rest" → short (recharges on either)
    if "short" in r:
        return "short"
    if "long" in r:
        return "long"
    if "combat" in r:
        return "combat"
    if "special" in r:
        return "special"
    if "dawn" in r:
        return "dawn"
    return r  # passthru unknown