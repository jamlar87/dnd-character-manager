"""Character routes — create, sheet, level-up, spells, combat, relationships."""

from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import sqlite3, json, math, random, re, urllib.parse, os, httpx
from pathlib import Path
from datetime import datetime

from main import (
    get_db, get_current_user, require_user, _render,
    _user_where, _require_owned, _resolve_item_key, _resolve_armor_item,
    _resolve_source, _build_item_description, _build_charged_item_attacks,
    _build_inventory_attacks, _build_racial_traits, _normalize_equipped,
    _equipped_names, _load_json_cache, _parse_enhancement, _is_admin,
    _item_rarity_for_level, _user_filter,
)
from main import RACES, CLASSES, RACE_NAMES, SUBCLASS_FEATURES, LIMITED_USE
from main import BACKGROUNDS, BACKGROUND_SOURCES, ALIGNMENTS
from main import (
    FLEXIBLE_ASI_RACES, SUBASIS, SRD_FEATURES, SRD_MAGIC_ITEMS,
    ITEM_INDEX, ITEM_WEAPONS, ITEM_ARMOR, ITEM_WONDROUS, ITEM_RODS_STAVES_WANDS,
    ITEMS_BY_RARITY, SPELL_DICE, DATA_DIR,
)
from main import _load_manual_json
from main import get_racial_trait_effects, check_armor_proficiency_from_set, get_character_armor_profs
from main import load_manual_data
from main import SRD_LEVELS, SRD_SPELLS, _get_named_item_types, _get_source_slug_map
from main import _manual_races_raw, _manual_races_raw as _MANUAL_RACES_RAW
from data import (
    SPELLS_KNOWN_CASTERS, RACIAL_TRAIT_EFFECTS, FEATURE_ACTION_TYPES,
    ABILITY_NAMES, ALL_SKILLS, LANGUAGES, SKILL_ABILITIES, FEATS, FEAT_BY_NAME,
    FEATURE_DESCRIPTIONS, DRACONIC_ANCESTRIES, PREPARED_CASTERS,
    METAMAGIC_OPTIONS, METAMAGIC_LEVELS, METAMAGIC_PICKS,
    INVOCATION_OPTIONS, INVOCATION_LEVELS, INVOCATION_PICKS,
    PACT_BOON_OPTIONS, PACT_BOON_LEVELS,
    MANEUVER_OPTIONS, MANEUVER_LEVELS,
    TOTEM_SPIRIT_OPTIONS,
    HUNTERS_PREY_OPTIONS,
    FAVORED_ENEMY_OPTIONS,
    FAVORED_TERRAIN_OPTIONS,
    INFUSION_OPTIONS,
    SUBCLASS_LEVELS, EXPERTISE_LEVELS, STARTING_EQUIPMENT,
    SCALED_EQUIPMENT, RECOMMENDED_FEATS, MULTICLASS_PREREQS,
    MULTICLASS_PROFICIENCIES, BACKGROUND_INFO,
    ASI_LEVELS, FULL_CASTERS, HALF_CASTERS, PACT_CASTERS,
    RACIAL_TRAIT_DESCS,
)
from routes.schemas import CreateCharacter, AddSpell, EditASI, ApplyLevelUp, UpdateCharacter
from summon_templates import SUMMON_TEMPLATES

router = APIRouter()


# ── Routes: Character Creation Wizard ──────────────────────────────────────

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
        source_map_json=json.dumps(_get_source_slug_map()))

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
        metamagic, metamagic_history, invocations, pact_boon, maneuvers, magical_secrets, totem_spirits, hunters_prey, favored_enemies, favored_terrains, infusions)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        json.dumps(data.get("infusions", []))
    ))
    char_id = cur.lastrowid
    db.commit()
    
    # Insert starting spells if provided
    spell_choices = data.get("spells", [])
    if spell_choices:
        for sp in spell_choices:
            db.execute(
                "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
                (char_id, sp.get("name", ""), sp.get("level", 0), 0, 0, 0))
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
    
    db.close()
    return (char_id, name)


@router.post("/api/character/create", response_class=JSONResponse)
async def api_create_character(request: Request):
    """Create a new character. Thin async wrapper around _build_character."""
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


# ── NPC ↦ Character conversion helpers ──────────────────────────────────

# Maps NPC race strings → canonical RACES keys (case-insensitive lookup)
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

# Strip parenthetical context and case-fold for race matching
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


# ═══════════════════════════════════════════════════════════════════════
# Build a character from an NPC stat block
# ═══════════════════════════════════════════════════════════════════════

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


# ── Starting spells lookup (no character needed — creation wizard) ──────────

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

# ── DM Tools: Monster helpers ──────────────────────────────────────────────


from routes.characters.helpers import (
    MANUAL_MONSTERS, MANUAL_TRAPS,
    _load_monster_cache, _template_monster_entries,
    _normalize_manual_monster, _monster_cr_sort_key,
    _xp_for_cr, _encounter_mult, _assign_encounter_counts,
    _format_monster_action, _ensure_manual_cache,
    _extract_pdf, _fuzzy_variants, _search_manuals,
)


# DM routes moved to routes/dm.py — see startup event


# ── Campaign Team Items (extracted to campaign.py) ───────────────
from routes.characters.campaign import router as _campaign_router
router.include_router(_campaign_router)


# ── Routes: Character Sheet ────────────────────────────────────────────────

@router.get("/character/{char_id}", response_class=HTMLResponse)
async def character_sheet(char_id: int, request: Request):
    user = require_user(request)
    dm_preview = request.query_params.get("dm_preview", "0") == "1"
    db = get_db()
    if dm_preview or _is_admin(user):
        # DM preview or admin: allow viewing any character
        row = db.execute("SELECT * FROM characters WHERE id = ?",
                         (char_id,)).fetchone()
    else:
        row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?",
                         (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    for f in ("skills","features","inventory","equipped","languages","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
              "save_proficiencies","damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities",
              "expertise_skills", "asi_history", "metamagic_history",
              "metamagic", "invocations", "maneuvers", "magical_secrets", "infusions", "summons", "conditions", "favored_enemies", "favored_terrains"):
        try:
            char[f] = json.loads(char[f])
        except (json.JSONDecodeError, TypeError):
            char[f] = []
    # totem_spirits is a dict, not a list
    try:
        char["totem_spirits"] = json.loads(char.get("totem_spirits", "{}"))
    except (json.JSONDecodeError, TypeError):
        char["totem_spirits"] = {}
    # Enrich inventory items with descriptions from ITEM_INDEX
    for inv_item in char.get("inventory", []):
        if not isinstance(inv_item, dict):
            continue
        if not inv_item.get("description"):
            idx_entry = _resolve_item_key(inv_item.get("name", ""))
            if idx_entry:
                inv_item["description"] = idx_entry.get("description", "") or _build_item_description(idx_entry)
                inv_item["source"] = inv_item.get("source") or idx_entry.get("source", "")
        # Enrich with damage dice from ITEM_INDEX
        if not inv_item.get("dice"):
            idx_entry = _resolve_item_key(inv_item.get("name", ""))
            if idx_entry and idx_entry.get("dice"):
                inv_item["dice"] = idx_entry["dice"]
        # Flag items that grant a bonus action ability
        _desc_ba = (inv_item.get("description") or "").lower()
        inv_item["bonus_action"] = "bonus action" in _desc_ba
    # Normalize equipped to [{name, qty}] format (backward compat with old string-list format)
    char["equipped"] = _normalize_equipped(char["equipped"])
    # Enrich equipped items too
    for eq_item in char.get("equipped", []):
        if not isinstance(eq_item, dict):
            continue
        if not eq_item.get("description"):
            idx_entry = _resolve_item_key(eq_item.get("name", ""))
            if idx_entry:
                eq_item["description"] = idx_entry.get("description", "") or _build_item_description(idx_entry)
                eq_item["source"] = eq_item.get("source") or idx_entry.get("source", "")
        # Enrich with damage dice
        if not eq_item.get("dice"):
            idx_entry = _resolve_item_key(eq_item.get("name", ""))
            if idx_entry and idx_entry.get("dice"):
                eq_item["dice"] = idx_entry["dice"]
        # Flag items that grant a bonus action ability
        _desc_ba_eq = (eq_item.get("description") or "").lower()
        eq_item["bonus_action"] = "bonus action" in _desc_ba_eq
    # Load attuned_items
    try:
        char["attuned_items"] = json.loads(char.get("attuned_items") or "[]")
    except (json.JSONDecodeError, TypeError):
        char["attuned_items"] = []
    # Load enriched build data
    for f in ("feature_data", "attacks_data", "spell_slot_data"):
        try:
            char[f] = json.loads(char[f] or "[]")
        except (json.JSONDecodeError, TypeError):
            char[f] = [] if f != "spell_slot_data" else {}
    # Enrich existing feature_data with Channel Divinity sub-options and source (rebuild-safe)
    # First: normalize string-format features to dicts (legacy characters)
    if char["feature_data"] and isinstance(char["feature_data"][0], str):
        char["feature_data"] = enrich_features(
            char["feature_data"],
            class_name=char.get("class_name", ""),
            level=char.get("level", 1),
            subclass=char.get("subclass", ""),
        )
    _add_cd_sub_options(char["feature_data"])
    _add_source_to_features(char["feature_data"])
    # Enrich features with dice badge data from FEATS/RACES lookup
    _add_dice_to_features(char["feature_data"])
    # Inject Eldritch Invocation level cards for Warlocks (SRD only has L2)
    _add_invocation_levels(char["feature_data"], char.get("class_name", ""), char.get("level", 0))
    # Enrich ASI features with feat descriptions when a feat was taken
    if char.get("asi_history"):
        # Normalize: backfill missing level fields in asi_history
        # (legacy entries created before the edit-asi feature existed)
        _asi_levels = {int(f.get("level","L0").replace("L","") or "0") 
                       for f in char["feature_data"] 
                       if isinstance(f, dict) and "Ability Score Improvement" in f.get("name","")}
        _sorted_asis = sorted(_asi_levels)
        _pos = 0
        for _ae in char["asi_history"]:
            if _ae.get("level") is None and _pos < len(_sorted_asis):
                _ae["level"] = _sorted_asis[_pos]
            _pos += 1

        for _feat in char["feature_data"]:
            if not isinstance(_feat, dict):
                continue
            if "Ability Score Improvement" in _feat.get("name", ""):
                _lvl_str = _feat.get("level", "").replace("L", "")
                try:
                    _lvl_num = int(_lvl_str)
                except (ValueError, TypeError):
                    continue
                for _ae in char["asi_history"]:
                    if _ae.get("level") == _lvl_num and _ae.get("type") == "feat":
                        _fkey = _ae.get("feat", "")
                        # FEAT_BY_NAME uses space-separated keys, asi_history uses underscores
                        _finfo = FEAT_BY_NAME.get(_fkey.lower(), None)
                        if _finfo is None:
                            _finfo = FEAT_BY_NAME.get(_fkey.lower().replace("_", " "), {})
                        _feat["asi_feat_name"] = _finfo.get("name") or _fkey.replace("_", " ").title()
                        _feat["asi_feat_desc"] = _finfo.get("description", "") or _finfo.get("desc", "")
                        # Magic Initiate: pass spell config to frontend
                        if _fkey == "magic_initiate":
                            _mi = _ae.get("magic_initiate", {})
                            if _mi:
                                _feat["magic_initiate"] = _mi
                        # Generic feat config (Elemental Adept, Ley Initiate, etc.)
                        _fc = _ae.get("feat_config")
                        if _fc:
                            _feat["feat_config"] = _fc
                        # Martial Adept: resolve maneuver keys to display names
                        if _fkey == "martial_adept" and _fc and _fc.get("maneuvers"):
                            _feat["feat_config"] = dict(_fc)
                            _feat["feat_config"]["maneuver_names"] = [
                                MANEUVER_OPTIONS.get(m, {}).get("name", m.replace("_", " ").title())
                                for m in _fc["maneuvers"]
                            ]
                        break
    # Inject orphaned feats: asi_history feats with no matching ASI feature_data entry, or enrich existing
    for _ae in (char.get("asi_history") or []):
        if _ae.get("type") == "feat":
            _lvl = _ae.get("level", 0)
            _fkey = _ae.get("feat", "")
            _finfo = FEAT_BY_NAME.get(_fkey.lower(), None)
            if _finfo is None:
                _finfo = FEAT_BY_NAME.get(_fkey.lower().replace("_", " "), {})
            _feat_name = _finfo.get("name") or _fkey.replace("_", " ").title()
            _feat_desc = _finfo.get("description", "") or _finfo.get("desc", "")
            # Try to enrich existing ASI entry first
            _existing = None
            for _ef in char["feature_data"]:
                if isinstance(_ef, dict) and "Ability Score Improvement" in _ef.get("name", ""):
                    try:
                        if int(str(_ef.get("level", "0")).replace("L", "")) == _lvl:
                            _existing = _ef
                            break
                    except (ValueError, TypeError):
                        pass
            if _existing is not None:
                _existing["asi_feat_name"] = _feat_name
                _existing["asi_feat_desc"] = _feat_desc
                _existing["source"] = _existing.get("source") or _finfo.get("source", "")
            else:
                # Orphaned feat — create new entry
                _new_asi = {
                    "name": "Ability Score Improvement",
                    "level": f"L{_lvl}",
                    "description": FEATURE_DESCRIPTIONS.get("ability score improvement", ""),
                    "source": _finfo.get("source", ""),
                    "asi_feat_name": _feat_name,
                    "asi_feat_desc": _feat_desc,
                }
                if _fkey == "magic_initiate":
                    _mi = _ae.get("magic_initiate", {})
                    if _mi:
                        _new_asi["magic_initiate"] = _mi
                _fc = _ae.get("feat_config")
                if _fc:
                    _new_asi["feat_config"] = _fc
                char["feature_data"].append(_new_asi)
    # Enrich with pool_kind from LIMITED_USE (so existing characters get Lay on Hands HP pool)
    for _feat in char["feature_data"]:
        if isinstance(_feat, dict) and not _feat.get("pool_kind"):
            _key = _feat.get("name", "").lower()
            for lkey, lu in LIMITED_USE.items():
                if lkey in _key and lu.get("pool_kind"):
                    _feat["pool_kind"] = lu["pool_kind"]
                    break
        # Enrich missing uses_max/recharge from LIMITED_USE for all features
        if isinstance(_feat, dict) and not _feat.get("uses_max"):
            _fname_lower = _feat.get("name", "").lower()
            if _fname_lower in LIMITED_USE:
                _lu = LIMITED_USE[_fname_lower]
                _feat["uses_max"] = _lu.get("max", 1)
                _feat["uses"] = _lu.get("max", 1)
                if _lu.get("recharge"):
                    _feat["recharge"] = _lu["recharge"]
        # Enrich racial traits missing uses_max (set from uses if present)
        if isinstance(_feat, dict) and _feat.get("uses") and not _feat.get("uses_max"):
            _feat["uses_max"] = _feat["uses"]
        # Enrich action_type from FEATURE_ACTION_TYPES for all features
        if isinstance(_feat, dict) and not _feat.get("action_type"):
            _key = _feat.get("name", "").lower()
            import re as _re
            _clean_key = _re.sub(r'\s*\([^)]*\)\s*$', '', _key).strip()
            _action_info = FEATURE_ACTION_TYPES.get(_clean_key) or FEATURE_ACTION_TYPES.get(_key)
            # Fallback: composite Channel Divinity names (e.g. "channel divinity: abjure enemy | l3: channel divinity: vow of enmity")
            if not _action_info and "channel divinity" in _clean_key:
                _action_info = FEATURE_ACTION_TYPES.get("channel divinity")
            if _action_info:
                _feat["action_type"] = _action_info[0]
                _feat["action_desc"] = _action_info[1]
    # Inject missing racial limited-use features (they may not be in feature_data)
    _existing_names = {f.get("name", "").lower() for f in char["feature_data"] if isinstance(f, dict)}
    _race = char.get("race", "")
    _subrace = char.get("subrace", "")
    _char_level = char.get("level", 1)
    for _rf in _build_racial_limited_features(_race, _subrace, _char_level):
        _rf_name = _rf.split(": ", 1)[1] if ": " in _rf else _rf
        if _rf_name.lower() not in _existing_names:
            _new_feat = {
                "name": _rf_name,
                "level": str(_char_level),
                "description": RACIAL_TRAIT_DESCS.get(_rf_name, "") or "",
                "source": f"Race: {_race or 'Unknown'}",
                "uses": 1,
                "uses_max": 1,
            }
            # Enrich with LIMITED_USE data
            _rf_key = _rf_name.lower()
            if _rf_key in LIMITED_USE:
                _lu = LIMITED_USE[_rf_key]
                _new_feat["uses_max"] = _lu.get("max", 1)
                _new_feat["uses"] = _lu.get("max", 1)
                _new_feat["recharge"] = _lu.get("recharge", "long")
            # Enrich action_type
            _action_info = FEATURE_ACTION_TYPES.get(_rf_key, None)
            if _action_info:
                _new_feat["action_type"] = _action_info[0]
                _new_feat["action_desc"] = _action_info[1]
            char["feature_data"].append(_new_feat)
    # Fallback: features still without source inherit from class or subclass
    # For multiclass, assign per-class sources using SRD class level data
    # Build per-class feature→source map from SRD class_levels data
    _cl_data = parse_class_levels(char)
    _cls_sources = {}
    _feature_to_class = {}
    _subclass = char.get("subclass", "")
    _sub_source = ""
    _subclass_feature_names = set()
    if _subclass:
        for _cls in (_cl_data or {char.get("class_name","Fighter"): char.get("level",1)}):
            _src = CLASSES.get(_cls, {}).get("_subclass_sources", {}).get(_subclass, "")
            if _src:
                _sub_source = _src
                break
        if _subclass in SUBCLASS_FEATURES:
            for _lvl_feats in SUBCLASS_FEATURES[_subclass].values():
                _subclass_feature_names.update(_lvl_feats)
    for _cls in (_cl_data or {char.get("class_name","Fighter"): char.get("level",1)}):
        _cls_lower = _cls.lower()
        _cls_sources[_cls] = CLASSES.get(_cls, {}).get("source", "")
        for _entry in SRD_LEVELS.get(_cls_lower, []):
            for _f in _entry.get("features", []):
                _fname = _f.get("name", "")
                if _fname and _fname not in _feature_to_class:
                    _feature_to_class[_fname] = _cls
    # Primary class source as fallback
    _primary_source = CLASSES.get(char.get("class_name", "Fighter"), {}).get("source", "")
    # Assign sources
    for _feat in char["feature_data"]:
        if not _feat.get("source") or _feat.get("source") in ("SRD 5.1", "PHB 2014"):
            _fname = _feat.get("name", "")
            # Check subclass features first
            if _sub_source and _fname in _subclass_feature_names:
                _feat["source"] = _sub_source
            else:
                # Look up which class owns this feature via SRD class levels
                _owning_cls = _feature_to_class.get(_fname)
                if _owning_cls and _cls_sources.get(_owning_cls):
                    _feat["source"] = _cls_sources[_owning_cls]
                elif _primary_source:
                    _feat["source"] = _primary_source
    # Load background data
    # Load spell_slots_used
    try:
        char["spell_slots_used"] = json.loads(char.get("spell_slots_used") or "{}")
    except (json.JSONDecodeError, TypeError):
        char["spell_slots_used"] = {}
    try:
        char["background_data"] = json.loads(char["background_data"] or "")
    except (json.JSONDecodeError, TypeError):
        char["background_data"] = {}

    spells = [dict(r) for r in db.execute(
        "SELECT * FROM character_spells WHERE character_id = ? ORDER BY spell_level, spell_name",
        (char_id,)
    ).fetchall()]
    # Enrich spells with full SRD descriptions
    enrich_spells(spells, char.get("level"))
    # Split Magic Initiate spells out of the regular spell list
    mi_spells_data = [s for s in spells if s.get("source") == "Magic Initiate"]
    spells = [s for s in spells if s.get("source") != "Magic Initiate"]
    db.close()

    # Compute modifiers
    for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        char[f"{stat}_mod"] = (char[stat] - 10) // 2

    # Recalculate AC for natural armor races (Tortle, Lizardfolk, etc.)
    racial_effects = get_racial_trait_effects(
        char.get("race", ""), char.get("subrace", ""),
        char.get("dragonborn_ancestry", ""))
    natural_armor = racial_effects.get("natural_armor")
    natural_ac = None
    if natural_armor:
        na_base = natural_armor.get("base_ac", 17)
        max_dex = natural_armor.get("max_dex")
        natural_ac = na_base
        if max_dex is not None:
            natural_ac += min(char["dexterity_mod"], max_dex)

    # Calculate AC from equipped armor/shield (uses SRD equipment data)
    equipped_names = _equipped_names(char.get("equipped", []))
    armor_ac = None
    shield_bonus = 0
    for eq_name in equipped_names:
        eq_lower = eq_name.lower().strip()
        item = _resolve_armor_item(eq_lower)
        if not item:
            continue
        cat = (item.get("equipment_category") or {}).get("name", "")
        armor_cat = item.get("armor_category", "")
        if cat == "Armor" and armor_cat != "Shield":
            # Body armor: compute AC from its formula
            ac_data = item.get("armor_class", {})
            base = ac_data.get("base", 10)
            dex_flag = ac_data.get("dex_bonus", False)
            max_bonus = ac_data.get("max_bonus", None)
            dex_mod = char.get("dexterity_mod", 0)

            if dex_flag is True:
                computed = base + dex_mod
            elif isinstance(dex_flag, (int, float)) and dex_flag:
                cap = max_bonus if max_bonus is not None else dex_flag
                computed = base + min(dex_mod, cap)
            else:
                computed = base

            if armor_ac is None or computed > armor_ac:
                armor_ac = computed
        elif armor_cat == "Shield" or eq_lower == "shield":
            shield_bonus = 2

    # Determine final AC: armor > natural armor > base 10 + DEX
    if armor_ac is not None:
        char["ac"] = armor_ac + shield_bonus
    elif natural_ac is not None:
        char["ac"] = natural_ac + shield_bonus
    else:
        # No armor, no natural armor: 10 + DEX + shield
        char["ac"] = 10 + char.get("dexterity_mod", 0) + shield_bonus

    # ── Armor proficiency check (PHB p.144) ──
    char["armor_warnings"] = []
    char["shield_warning"] = None
    class_levels_data = parse_class_levels(char)
    profs = get_character_armor_profs(char, class_levels_data if len(class_levels_data) > 1 else None)
    if armor_ac is not None:
        # Determine which armor category was matched
        for eq_name in equipped_names:
            eq_lower = eq_name.lower().strip()
            item = _resolve_armor_item(eq_lower)
            if not item:
                continue
            cat = (item.get("equipment_category") or {}).get("name", "")
            armor_cat = item.get("armor_category", "")
            if cat == "Armor" and armor_cat != "Shield":
                check = check_armor_proficiency_from_set(profs, armor_cat)
                if not check["proficient"]:
                    char["armor_warnings"].append({
                        "item": eq_name,
                        "category": armor_cat,
                        "penalty": check["penalty"],
                        "source": check["source"],
                    })
                break  # Only check the best armor
    if shield_bonus > 0:
        shield_check = check_armor_proficiency_from_set(profs, "Shield")
        if not shield_check["proficient"]:
            char["shield_warning"] = {
                "penalty": shield_check["penalty"],
                "source": shield_check["source"],
            }

    # Merged save proficiencies (class-derived + user-toggled)
    class_saves = CLASSES.get(char.get("class_name",""), {}).get("saves", [])
    user_saves = char.get("save_proficiencies", [])
    saves_class = list(set(class_saves) | set(user_saves))

    # Build attacks from inventory weapons + existing attacks_data
    all_attacks = _build_inventory_attacks(char)
    # Build charged item cards (wands, staves, rods, etc.)
    charged_items = _build_charged_item_attacks(char)

    # Caster type detection (PHB rules) — multiclass aware
    class_name = char.get("class_name", "")
    level = char.get("level", 1)
    mods = {s: char.get(f"{s}_mod", 0) for s in
            ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
    
    if len(class_levels_data) > 1:
        # Multiclass: detect caster types present
        types = get_multiclass_caster_types(class_levels_data)
        has_full = types.get("full", 0) > 0
        has_half = types.get("half", 0) > 0
        has_pact = types.get("pact", 0) > 0
        has_any = has_full or has_half or has_pact
        if has_full and (has_half or has_pact):
            caster_type = "multiclass"
        elif has_half and has_pact:
            caster_type = "multiclass"
        elif has_full:
            caster_type = "full"
        elif has_half:
            caster_type = "half"
        elif has_pact:
            caster_type = "pact"
        else:
            caster_type = "none"
    else:
        caster_type = get_caster_type(class_name)
    
    sc_mod = get_spellcasting_mod(class_name, mods)
    prepared_max = get_prepared_max(class_name, level, sc_mod)
    spells_known_max = get_spells_known_max(class_name, level)
    cantrips_max = get_cantrips_known_max(class_name, level)

    # Feature-derived defenses (e.g. Rage → B/P/S resist)
    feature_defenses = []
    for fname in char.get("features", []):
        # Strip "LN: " prefix (features stored as "L1: Rage")
        bare_name = fname.split(": ", 1)[-1] if ": " in fname else fname
        fd = FEATURE_DEFENSES.get(bare_name)
        if fd:
            feature_defenses.append({"name": fname, **fd})

    # Item-granted effects (equipped + attuned)
    item_effects = compute_item_effects(
        _equipped_names(char.get("equipped", [])),
        char.get("attuned_items", []),
        char.get("inventory", [])
    )

    # Build attunement lookup for JS — which equipped/inventory items need attunement
    item_attunement_json = {}
    for inv_item in char.get("inventory", []):
        name = inv_item.get("name", "") if isinstance(inv_item, dict) else str(inv_item)
        if name.lower() in ITEM_ATTUNEMENT and ITEM_ATTUNEMENT[name.lower()]:
            item_attunement_json[name] = True
    for eq_name in _equipped_names(char.get("equipped", [])):
        if eq_name.lower() in ITEM_ATTUNEMENT and ITEM_ATTUNEMENT[eq_name.lower()]:
            item_attunement_json[eq_name] = True
    item_attunement_json = json.dumps(item_attunement_json)

    # Build a dict version for template use (checking attunement on equipped items)
    item_attunement_dict = {}
    for eq_name in _equipped_names(char.get("equipped", [])):
        if eq_name.lower() in ITEM_ATTUNEMENT and ITEM_ATTUNEMENT[eq_name.lower()]:
            item_attunement_dict[eq_name] = True

    # Check if character is in a campaign (JSON field + legacy table)
    db2 = get_db()
    campaign_row = db2.execute("""
        SELECT c.id, c.name FROM dm_campaigns c
        JOIN dm_campaign_characters cc ON cc.campaign_id = c.id
        WHERE cc.character_id = ?
    """, (char_id,)).fetchone()
    if not campaign_row:
        all_camps = db2.execute("SELECT id, name, characters FROM dm_campaigns").fetchall()
        for c in all_camps:
            try:
                chars = json.loads(c["characters"] or "[]")
                if any(ch.get("id") == char_id for ch in chars if isinstance(ch, dict)):
                    campaign_row = (c["id"], c["name"])
                    break
            except (json.JSONDecodeError, TypeError):
                pass
    db2.close()
    campaign_info = {"id": campaign_row[0], "name": campaign_row[1]} if campaign_row else None

    # Merge all resistance/immunity sources for edit-picker display values
    # Start with DB-stored + racial effects + feature-derived + item-granted
    racial_effects = get_racial_trait_effects(char.get("race", ""), char.get("subrace", ""),
                                              char.get("dragonborn_ancestry", ""))

    merged_resist = list(char.get("damage_resistances", []))
    for r in racial_effects["damage_resist"]:
        if r not in merged_resist:
            merged_resist.append(r)
    for fd in feature_defenses:
        for r in fd.get("resist", []):
            if r not in merged_resist:
                merged_resist.append(r)
    for r in item_effects.get("resist", []):
        if r not in merged_resist:
            merged_resist.append(r)

    merged_immune = list(char.get("damage_immunities", []))
    for fd in feature_defenses:
        for i in fd.get("immune", []):
            if i not in merged_immune:
                merged_immune.append(i)
    for i in item_effects.get("immune", []):
        if i not in merged_immune:
            merged_immune.append(i)

    # Condition immunities: DB-stored + racial effects
    merged_condition_immune = list(char.get("condition_immunities", []))
    for c in racial_effects["condition_immune"]:
        if c not in merged_condition_immune:
            merged_condition_immune.append(c)

    # Merge racial proficiencies for display (DB-stored + racial)
    merged_armor_profs = list(char.get("armor_proficiencies", []))
    for v in racial_effects["armor_profs"]:
        if v not in merged_armor_profs:
            merged_armor_profs.append(v)
    merged_weapon_profs = list(char.get("weapon_proficiencies", []))
    for v in racial_effects["weapon_profs"]:
        if v not in merged_weapon_profs:
            merged_weapon_profs.append(v)
    merged_tool_profs = list(char.get("tool_proficiencies", []))
    for v in racial_effects["tool_profs"]:
        if v not in merged_tool_profs:
            merged_tool_profs.append(v)

    # Compute invocations per level from the flat list + level/picks data
    invocations_by_level: dict[int, list[str]] = {}
    inv_flat = char.get("invocations", [])
    if inv_flat:
        inv_levels = INVOCATION_LEVELS.get(char.get("class_name", ""), [])
        if inv_levels:
            offset = 0
            for lvl in inv_levels:
                picks = INVOCATION_PICKS.get(lvl, 0)
                if picks > 0 and offset < len(inv_flat):
                    invocations_by_level[lvl] = inv_flat[offset:offset + picks]
                    offset += picks

    # Pre-compute expertise context for the edit modal
    expertise_options = []
    expertise_count = 0
    for cls, lvl in class_levels_data.items():
        ec = get_expertise_count(cls, lvl, char.get("subclass", "") if cls == char.get("class_name", "") else "")
        if ec > 0:
            expertise_count += ec
            eo = get_expertise_options(cls, char.get("subclass", "") if cls == char.get("class_name", "") else "",
                                       char.get("skills", []))
            for opt in eo:
                if opt not in expertise_options:
                    expertise_options.append(opt)

    # ── Merge manual race data into races dict for description popup ──
    merged_races = dict(RACES)
    # Also add common race name aliases (plurals, alternate names)
    _aliases = {
        "elves of mirkwood": "Mirkwood Elf",
        "hobbits of the shire": "Hobbit of the Shire",
        "hobbit of the shire": "Hobbit of the Shire",
        "high elves of rivendell": "High Elf of Rivendell",
    }
    for _mr in _MANUAL_RACES_RAW:
        merged_races[_mr["name"]] = {
            "desc": _mr.get("description", ""),
            "source": _mr.get("source", ""),
            "asi": _mr.get("asi", {}),
        }
    # Alias entries so "Elves of Mirkwood" → "Mirkwood Elf" data
    # Also store title-cased fallback key for case-insensitive JS lookup
    for _alias, _target in _aliases.items():
        if _alias in merged_races:
            continue
        if _target in merged_races:
            merged_races[_alias] = merged_races[_target]
        else:
            # Target may be a migrated subrace (e.g. Mirkwood Elf subrace of Elf)
            # Find which parent race owns it and pull the subrace description
            for _parent_name, _parent_data in merged_races.items():
                if _target in _parent_data.get("subraces", []):
                    _sr_desc = _parent_data.get("subrace_descs", {}).get(_target, "")
                    _sr_src = _parent_data.get("_subrace_sources", {}).get(_target, "")
                    _entry = {
                        "desc": _sr_desc,
                        "source": _sr_src,
                        "subrace": _target,
                    }
                    merged_races[_alias] = _entry
                    # Also store title-cased version for matching character.race
                    merged_races[_alias.title()] = _entry
                    break

    return _render("sheet.html", request=request, character=char, spells=spells,
                   mi_spells_data=mi_spells_data,
                   dm_preview=dm_preview,
                   skill_abilities=SKILL_ABILITIES, classes=CLASSES, races=merged_races,
                   bg_info=BACKGROUND_INFO, saves_class=saves_class, attacks=all_attacks,
                   charged_items=charged_items,
                   named_item_types=_get_named_item_types(),
                   armor_names=[], caster_type=caster_type, prepared_max=prepared_max,
                   spells_known_max=spells_known_max, cantrips_max=cantrips_max,
                   sc_mod=sc_mod, class_levels=class_levels_data,
                   feature_defenses=feature_defenses, item_effects=item_effects,
                   merged_resist=merged_resist, merged_immune=merged_immune,
                   merged_condition_immune=merged_condition_immune,
                   merged_armor_profs=merged_armor_profs,
                   merged_weapon_profs=merged_weapon_profs,
                   merged_tool_profs=merged_tool_profs,
                   racial_effects=racial_effects,
                   item_attunement_json=item_attunement_json,
                   item_attunement_dict=item_attunement_dict,
                   campaign_info=campaign_info,
                   racial_traits=_build_racial_traits(char),
                   draconic_ancestries=DRACONIC_ANCESTRIES,
                   maneuver_options=MANEUVER_OPTIONS,
                   metamagic_options=METAMAGIC_OPTIONS,
                   known_feats=KNOWN_FEATS,
                   feat_details=FEAT_DETAILS,
                   expertise_levels=EXPERTISE_LEVELS,
                   expertise_options=expertise_options,
                   expertise_count=expertise_count,
                   source_map_json=json.dumps(_get_source_slug_map()),
                   invocation_levels=INVOCATION_LEVELS,
                   invocation_picks=INVOCATION_PICKS,
                   invocations_by_level=invocations_by_level,
                   invocation_options=INVOCATION_OPTIONS,
                   pact_boon_options=PACT_BOON_OPTIONS,
                   summon_templates=SUMMON_TEMPLATES)

# ── Routes: Live Session API ───────────────────────────────────────────────

@router.post("/api/character/{char_id}/update", response_class=JSONResponse)
async def update_character(char_id: int, request: Request, body: UpdateCharacter):
    user = require_user(request)
    # Build data dict from model (extra fields pass through)
    data = body.model_dump(exclude_none=True) | (body.model_extra or {})

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
        "personality","backstory",
        # JSON arrays (serialized as JSON strings)
        "skills","save_proficiencies","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
        "languages","features","inventory","spell_slots_used","equipped","feature_data","attacks_data",
        "damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities",
        "attuned_items", "expertise_skills", "fighting_style",
        "cp",
        "gp",
        "dragonborn_ancestry",
        "summons",
    }
    updates = {}
    for k, v in data.items():
        if k in allowed:
            updates[k] = v

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values())
        if _is_admin(user):
            db.execute(f"UPDATE characters SET {sets} WHERE id=?", vals + [char_id])
        else:
            db.execute(f"UPDATE characters SET {sets} WHERE id=? AND user_id=?", vals + [char_id, user["id"]])

    # Spell slot updates
    if "spells" in data:
        for sp in data["spells"]:
            db.execute("UPDATE character_spells SET slots_used=? WHERE id=? AND character_id=?",
                       (sp.get("slots_used", 0), sp.get("id"), char_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

@router.post("/api/character/{char_id}/edit-asi", response_class=JSONResponse)
async def edit_asi_choice(char_id: int, request: Request, body: EditASI):
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

@router.get("/api/character/{char_id}/attacks", response_class=JSONResponse)
async def get_attacks(char_id: int, request: Request):
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
    attacks = _build_inventory_attacks(char)
    return JSONResponse({"attacks": attacks})

@router.get("/api/character/{char_id}/summons", response_class=JSONResponse)
async def get_summons(char_id: int, request: Request):
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
    return JSONResponse({"ok": True, "hp_current": summons[idx]["hp_current"]})

# ── Conditions (CRUD) ─────────────────────────────────────────────
@router.get("/api/character/{char_id}/conditions", response_class=JSONResponse)
async def get_conditions(char_id: int, request: Request):
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
    db.execute("UPDATE characters SET conditions=? WHERE id=? AND user_id=?",
               (json.dumps(conditions), char_id, user["id"]))
    db.commit()
    db.close()
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
    db.execute("UPDATE characters SET conditions=? WHERE id=? AND user_id=?",
               (json.dumps(conditions), char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"conditions": conditions, "removed": name})


@router.post("/api/sync-combat-hp", response_class=JSONResponse)
async def sync_combat_hp(request: Request):
    """Batch fetch HP + summons for characters in combat. Takes {char_ids: [...]}."""
    user = require_user(request)
    data = await request.json()
    char_ids = data.get("char_ids", [])
    if not char_ids:
        return JSONResponse({"characters": {}})
    db = get_db()
    result = {}
    for cid in char_ids:
        row = db.execute(
            "SELECT name, hp_current, hp_max, summons, conditions FROM characters WHERE id=? AND user_id=?",
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
        }
    db.close()
    return JSONResponse({"characters": result})

@router.post("/api/character/{char_id}/spend-charge", response_class=JSONResponse)
async def spend_charge(char_id: int, request: Request):
    """Spend one charge from an equipped charged item."""
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
            used = item.get("charges_used", 0)
            item["charges_used"] = used + 1
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


@router.get("/api/character/{char_id}/pdf")
async def character_pdf(char_id: int, request: Request):
    """Generate a printable D&D character sheet PDF."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    char = dict(row)
    # Build structured data for PDF generator
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pdf_generator import build_char_data, generate_character_sheet
    char_data = build_char_data(row, db, racial_traits=_build_racial_traits(char))
    db.close()
    
    pdf_bytes = generate_character_sheet(char_data)
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{char_data.get("name", "character").replace(" ", "_")}_sheet.pdf"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )

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
MAGIC_INITIATE_CLASSES = ["Bard", "Cleric", "Druid", "Sorcerer", "Warlock", "Wizard"]

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

    # Get existing spells
    known_rows = db.execute("SELECT spell_name, spell_level FROM character_spells WHERE character_id = ?",
                            (char_id,)).fetchall()
    known_names = {r[0].lower() for r in known_rows}

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
        current_prepared = sum(1 for r in known_rows if r[1] is not None)
        can_add = max(0, prep_max - current_prepared)
    else:
        # Spells known
        spells_known = _spells_known_for_class(caster_class, level)
        current_known = sum(1 for r in known_rows if (r[1] or 0) > 0)  # exclude cantrips
        can_add = max(0, spells_known - current_known)

    if can_add <= 0:
        db.close()
        return JSONResponse({"added": 0, "message": "Already at spell limit"})

    # Get available spells (reuse available_spells logic — fetch from SRD)
    slots = get_spell_slots(caster_class, level)
    max_spell_level = 0
    if caster_class == "Warlock":
        max_spell_level = slots.get("slot_level", 0) if slots else 0
    elif slots and slots.get("by_level"):
        max_spell_level = max((int(lvl) for lvl, cnt in slots["by_level"].items() if cnt > 0), default=0)

    # Build candidate pool from SRD
    pool = []
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
        pool.append({"name": name, "level": slvl, "spell": spell})

    # Score each spell by class-specific priority
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

    # Score and sort
    for item in pool:
        sp = item["spell"]
        item["score"] = _score(item["name"], item["level"], sp)
        item["name"] = sp.get("name", "")
        item["school"] = (sp.get("school") or {}).get("name", "") if isinstance(sp.get("school"), dict) else ""

    # Sort: prioritize spells with scores, then by level (higher first), then alphabetically
    pool.sort(key=lambda x: (-x["score"], -x["level"], x["name"].lower()))

    # Pick top spells up to limit
    selected = pool[:can_add]

    # Batch insert
    added = 0
    for item in selected:
        db.execute(
            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
            (char_id, item["name"], item["level"], 1 if is_prepared else 0, 0, 0))
        added += 1

    db.commit()
    db.close()

    names = [s["name"] for s in selected]
    return JSONResponse({
        "added": added,
        "spells": names,
        "message": f"Added {added} spells for {class_name} L{level}" + (f" ({subclass})" if subclass else ""),
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


# ── History & Relationships API ────────────────────────────────────────────

@router.get("/api/character/{char_id}/relationships", response_class=JSONResponse)
async def get_relationships(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM character_relationships WHERE character_id = ? AND user_id = ? ORDER BY created_at DESC",
        (char_id, user["id"])
    ).fetchall()
    db.close()
    return JSONResponse([dict(r) for r in rows])

@router.post("/api/character/{char_id}/relationships", response_class=JSONResponse)
async def create_relationship(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    name = data.get("name", "").strip()
    if not name:
        db.close()
        return JSONResponse({"error": "Name required"}, status_code=400)
    rel_type = data.get("relationship_type", "ally")
    description = data.get("description", "")
    npc_data = data.get("npc_data", {})
    ai_gen = 1 if data.get("ai_generated") else 0
    cursor = db.execute(
        "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, npc_data, ai_generated) VALUES (?,?,?,?,?,?,?)",
        (char_id, user["id"], name, rel_type, description, json.dumps(npc_data), ai_gen)
    )
    rel_id = cursor.lastrowid
    db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

@router.post("/api/character/{char_id}/relationships/generate", response_class=JSONResponse)
async def generate_relationship(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "Prompt required"}, status_code=400)
    rel_type = data.get("relationship_type", "ally")
    npc_level = data.get("level", char.get("level", 1))
    char_name = char.get("name", "the character")
    char_race = char.get("race", "Human")
    char_class = char.get("class_name", "Fighter")
    char_level = char.get("level", 1)
    ai_prompt = (
        f'Create a D&D 5e NPC for a character\'s backstory.\n'
        f'Character: {char_name}, {char_race} {char_class} L{char_level}.\n'
        f'Relationship type: {rel_type}.\n'
        f'NPC level: {npc_level}.\n'
        f'Player\'s description: "{prompt}"\n\n'
        'Generate a vivid NPC with full combat stats. Return ONLY valid JSON (no markdown):\n'
        '{"name": "NPC Name", "race": "D&D race", "class": "class or occupation", '
        '"level": 1-20, '
        '"description": "2-3 sentence appearance and personality", '
        f'"relationship_detail": "1-2 sentences about their history with {char_name}", '
        '"stats": {"ac": 10-20, "hp": "XdY+Z", "speed": "30 ft.", '
        '"str": 8-20, "dex": 8-20, "con": 8-20, "int": 8-20, "wis": 8-20, "cha": 8-20, '
        '"skills": ["skill+mod"], "saves": ["save+mod"], '
        '"attacks": [{"name": "weapon", "bonus": "+X", "damage": "XdY+Z", "type": "piercing"}], '
        '"cr": "1/8 to 10"}}'
    )
    ai_text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    npc_data = {}
    name = prompt[:50]
    description = ""
    if ai_text:
        try:
            cleaned = ai_text.strip().removeprefix("```json").removesuffix("```").strip()
            ai_json = json.loads(cleaned)
            name = ai_json.get("name", name)
            description = (ai_json.get("description", "") + "\n\n" + ai_json.get("relationship_detail", "")).strip()
            npc_data = {"race": ai_json.get("race", ""), "class": ai_json.get("class", ""), "level": npc_level, "stats": ai_json.get("stats", {})}
        except (json.JSONDecodeError, AttributeError):
            description = ai_text[:500]
    # Return generated data only — frontend calls /relationships to save
    return JSONResponse({
        "name": name,
        "description": description,
        "npc_data": npc_data,
        "prompt": prompt,
        "relationship_type": rel_type,
        "level": npc_level,
        "ai_generated": True,
    })

@router.put("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
async def update_relationship(char_id: int, rel_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM character_relationships WHERE id = ? AND character_id = ? AND user_id = ?",
                     (rel_id, char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Relationship not found")
    updates = {}
    for field in ["name", "relationship_type", "description"]:
        if field in data:
            updates[field] = data[field]
    if "npc_data" in data:
        updates["npc_data"] = json.dumps(data["npc_data"])
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(f"UPDATE character_relationships SET {set_clause} WHERE id = ?", list(updates.values()) + [rel_id])
        db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

@router.delete("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
async def delete_relationship(char_id: int, rel_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM character_relationships WHERE id = ? AND character_id = ? AND user_id = ?",
               (rel_id, char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── Level-Up API ────────────────────────────────────────────────────────────

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
    if new_fe_levels:
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
    if new_ft_levels:
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
    
    # Retroactive CON HP (PHB p.177): when CON mod increases, all prior levels gain the delta
    old_con = char.get("constitution", 10)
    new_con = cumulative.get("Constitution", 10)
    old_con_mod = (old_con - 10) // 2
    new_con_mod = (new_con - 10) // 2
    con_delta = new_con_mod - old_con_mod
    if con_delta > 0:
        retro_hp = old_total * con_delta
        updates["hp_max"] += retro_hp
        updates["hp_current"] += retro_hp
        changes.append(f"CON +{new_con - old_con}: +{retro_hp} HP (retroactive)")
    
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
    """Return what the character would look like at a lower level."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")
    char = dict(row)
    db.close()
    
    cls = char.get("class_name", "Fighter")
    current_level = char.get("level", 1)
    target_level = int(request.query_params.get("target_level", request.query_params.get("target", current_level - 1)))
    target_level = max(1, min(target_level, current_level - 1))
    
    # Compute class-specific levels for multiclass correctness
    cl = parse_class_levels(char)
    class_level = cl.get(cls, current_level)  # level in this class before
    levels_lost = current_level - target_level
    new_class_level = max(0, class_level - levels_lost)
    
    if current_level <= 1:
        return JSONResponse({"error": "Already at level 1"}, status_code=400)
    
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
    levels_lost = current_level - target_level
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
    cls = char.get("class_name", "Fighter")
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
    cls_to_reduce = data.get("class_to_level", cls)
    new_cl[cls_to_reduce] = max(0, new_cl.get(cls_to_reduce, 0) - levels_lost)
    if new_cl[cls_to_reduce] <= 0:
        del new_cl[cls_to_reduce]
    
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
        lost_asi = [lvl for lvl in range(target_level + 1, old_level + 1) if lvl in ASI_LEVELS.get(cls, [])]
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
            updates["subclass"] = ""
            changes.append(f"Subclass cleared ({char.get('subclass')})")
    
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

@router.post("/api/character/{char_id}/delete", response_class=JSONResponse)
async def delete_character(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    filter_clause, filter_params = _user_filter(user)
    db.execute(f"DELETE FROM characters WHERE id = ? {filter_clause}", (char_id, *filter_params))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── Name Generators ─────────────────────────────────────────────────────────

# Extend RACE_NAMES with expanded data covering all ingested races
# Loads from data/race_names.json; graceful fallback if file is missing.
_expanded_path = DATA_DIR / "race_names.json"
if _expanded_path.exists():
    try:
        with open(_expanded_path) as f:
            _expanded = json.load(f)
        for race_key, names in _expanded.items():
            if race_key not in RACE_NAMES:
                RACE_NAMES[race_key] = names
    except (json.JSONDecodeError, OSError):
        pass  # Keep existing RACE_NAMES as-is

# ── From data.py: STARTING_EQUIPMENT

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

# ── PHB-Grounded AI Generation ──────────────────────────────────────────────
# All mechanical data (races, classes, spells, equipment) is from the PHB 2014
# hardcoded above. AI only handles creative flavor: name, personality, backstory.
# Backgrounds and alignments are validated against PHB-approved lists.
# Model chain: Gemini → OpenRouter (free) → Ollama (local) → deterministic

PHB_BACKGROUNDS = BACKGROUNDS  # PHB p.125-141
PHB_ALIGNMENTS = ALIGNMENTS    # PHB p.122

# ── Character Build Optimization (PHB 2014) ─────────────────────────────────

# Ability Score Priority (PHB Quick Build sections + optimal play)
ABILITY_PRIORITY = {
    "Barbarian":  ["strength","constitution","dexterity","wisdom","charisma","intelligence"],
    "Bard":       ["charisma","dexterity","constitution","intelligence","wisdom","strength"],
    "Cleric":     ["wisdom","strength","constitution","dexterity","charisma","intelligence"],
    "Druid":      ["wisdom","constitution","dexterity","intelligence","charisma","strength"],
    "Fighter":    ["strength","constitution","dexterity","wisdom","intelligence","charisma"],
    "Monk":       ["dexterity","wisdom","constitution","strength","intelligence","charisma"],
    "Paladin":    ["strength","charisma","constitution","wisdom","dexterity","intelligence"],
    "Ranger":     ["dexterity","wisdom","constitution","strength","intelligence","charisma"],
    "Rogue":      ["dexterity","constitution","intelligence","wisdom","charisma","strength"],
    "Sorcerer":   ["charisma","constitution","dexterity","wisdom","intelligence","strength"],
    "Warlock":    ["charisma","constitution","dexterity","wisdom","intelligence","strength"],
    "Wizard":     ["intelligence","constitution","dexterity","wisdom","charisma","strength"],
}

# Standard array: 15,14,13,12,10,8 (PHB p.13)
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

# Proficiency bonus by level (PHB p.15)
PROFICIENCY_BONUS = {1:2,2:2,3:2,4:2,5:3,6:3,7:3,8:3,9:4,10:4,11:4,12:4,13:5,14:5,15:5,16:5,17:6,18:6,19:6,20:6}

# ── SRD-Backed Functions (replace hand-coded PHB tables) ─────────────────────

def get_spell_slots(class_name: str, level: int) -> dict:
    """Return spell slots from SRD API cache. Falls back to empty if class not found."""
    key = class_name.lower()
    levels = SRD_LEVELS.get(key, [])
    entry = None
    for l in levels:
        if l.get("level") == level:
            entry = l
            break
    if not entry:
        return {"slots": 0, "slot_level": None, "by_level": {}}

    sc = entry.get("spellcasting", {})
    # Warlock uses Pact Magic — different slot structure
    if key == "warlock":
        slot_count = 0
        slot_level = 0
        # Find max non-zero slot
        for i in range(1, 10):
            slots = sc.get(f"spell_slots_level_{i}", 0)
            if slots > 0:
                slot_count = slots
                slot_level = i
        note = f"{slot_count} Pact Magic slots, all {_ordinal(slot_level)} level" if slot_count else "No Pact Magic yet"
        return {"slots": slot_count, "slot_level": slot_level, "note": note, "by_level": {}}

    # Standard spell slots
    by_level = {}
    for i in range(1, 10):
        by_level[i] = sc.get(f"spell_slots_level_{i}", 0)
    return {"slots": sum(by_level.values()), "slot_level": None, "by_level": by_level}

def get_character_spell_slots(char_dict: dict) -> dict:
    """Return spell slots for a character, handling multiclass via PHB p.165.
    Single class: delegates to get_spell_slots. Multiclass: uses multiclass table.
    Warlock Pact Magic always tracked separately — returned as 'pact_slots' key."""
    cl = parse_class_levels(char_dict)
    total_lvl = total_level(cl)
    
    if len(cl) == 1:
        # Single class — use existing logic
        cls, lvl = next(iter(cl.items()))
        return get_spell_slots(cls, lvl)
    
    # Multiclass — separate Warlock from other casters
    result = {"slots": 0, "slot_level": None, "by_level": {}, "multiclass": True}
    pact_slots = {"slots": 0, "slot_level": 0, "note": "No Pact Magic"}
    
    # Warlock Pact Magic
    if "Warlock" in cl:
        warlock_level = cl["Warlock"]
        pact_result = get_spell_slots("Warlock", warlock_level)
        pact_slots = pact_result
        # Remove Warlock from caster level computation
        non_pact = {k: v for k, v in cl.items() if k != "Warlock"}
        if non_pact and has_casters(non_pact):
            result = get_multiclass_spell_slots(non_pact)
        result["pact_slots"] = pact_slots
        result["slots"] += pact_slots.get("slots", 0)
    else:
        if has_casters(cl):
            result = get_multiclass_spell_slots(cl)
    
    result["total_level"] = total_lvl
    return result

def has_casters(class_levels: dict[str, int]) -> bool:
    """Return True if any class in the dict is a spellcaster (full, half, or pact)."""
    return any(get_caster_type(cls) in ("full", "half", "pact") for cls in class_levels)


# ── From data.py: SUBCLASS_FEATURES (includes DMG subclasses: Death Domain, Oathbreaker)



# PHB-granted proficiencies that come from subclass choice (not base class)
SUBCLASS_PROFICIENCIES = {
    "Life Domain": {"armor_profs": ["Heavy armor"]},
    "Nature Domain": {"armor_profs": ["Heavy armor"]},
    "Tempest Domain": {"armor_profs": ["Heavy armor"]},
    "War Domain": {"armor_profs": ["Heavy armor"]},
    "College of Valor": {"armor_profs": ["Medium armor", "Shields"], "weapon_profs": ["Martial weapons"]},
    "College of Lore": {"skill_profs": []},
    "Knowledge Domain": {"skill_profs": [], "languages": 2},
    "Assassin": {"tool_profs": ["Disguise kit", "Poisoner's kit"]},
}


def _deduplicate_multiclass_features(features: list[dict], class_levels: dict[str, int]) -> list[dict]:
    """PHB 2014 p.164: Deduplicate features across classes.
    - Channel Divinity: keep all options, cap uses at max single-class value
    - Extra Attack: keep one (max 2), unless Fighter 11+ (3) or Fighter 20 (4)
    - Unarmored Defense: keep only first class's version
    Tags each feature with source_class for display."""
    import re
    seen = {}
    result = []
    has_extra_attack = False
    has_uad = False
    fighter_level = class_levels.get("Fighter", 0)
    
    for f in features:
        name = f.get("name", "")
        name_lower = name.lower()
        source = f.get("source_class", "")
        
        if not source and "/" not in name:  # skip if already merged
            # Infer source_class from feature name context (set by caller)
            pass
        
        # Channel Divinity — keep all entries, first one sets max uses
        if "channel divinity" in name_lower:
            key = "channel_divinity"
            if key not in seen:
                seen[key] = f
                result.append(f)
            else:
                existing = seen[key]
                # Merge options into name
                if name not in existing["name"]:
                    existing["name"] = existing["name"] + " | " + name
            continue
        
        # Extra Attack — no stacking (PHB p.164)
        if "extra attack" in name_lower:
            if has_extra_attack:
                continue
            has_extra_attack = True
            if fighter_level >= 20:
                f["name"] = "Extra Attack (4)"
            elif fighter_level >= 11:
                f["name"] = "Extra Attack (3)"
            result.append(f)
            continue
        
        # Unarmored Defense — first class only (PHB p.164)
        if "unarmored defense" in name_lower:
            if has_uad:
                continue
            has_uad = True
            result.append(f)
            continue
        
        # General dedup — strip use-count suffix differences
        base = re.sub(r'\s*\(\d+\s+(use|ki|point)s?\)', '', name).strip()
        key = (base.lower(), source)
        if key in seen:
            existing = seen[key]
            # Keep higher uses
            if f.get("uses", 0) > existing.get("uses", 0):
                result[result.index(existing)] = f
                seen[key] = f
            continue
        
        seen[key] = f
        result.append(f)
    
    return result


def get_class_features(class_name: str, level: int, subclass: str = "") -> list[str]:
    """Return class features gained by this level from SRD API cache.
    Deduplicates features that differ only by use count, and replaces
    generic subclass names with real feature names from SUBCLASS_FEATURES."""
    import re
    key = class_name.lower()
    levels = SRD_LEVELS.get(key, [])
    gained_raw = []
    for l in levels:
        lvl = l.get("level", 0)
        if lvl <= level:
            for feat in l.get("features", []):
                name = feat.get("name", "")
                if name:
                    gained_raw.append((lvl, name))
    
    # Deduplicate: strip use-count suffixes, keep highest-level entry
    # Only deduplicate when the original name had a use-count suffix
    import re
    _use_suffix_re = re.compile(r'\s*\(\d+\s+uses?(\s+per\s+rest)?\s*\)\s*$')
    
    def _strip_uses(name: str) -> str:
        return _use_suffix_re.sub('', name).strip()
    
    seen = {}  # base_name → (level, original_name, had_use_suffix)
    for lvl, name in gained_raw:
        base = _strip_uses(name)
        had_suffix = bool(_use_suffix_re.search(name))
        # Only deduplicate if the original had a use suffix
        if had_suffix:
            if base not in seen or lvl > seen[base][0]:
                seen[base] = (lvl, name, True)
        else:
            # Keep all entries that don't have use suffixes
            # Use a compound key to avoid collisions
            ckey = f"{base}__{lvl}"
            seen[ckey] = (lvl, name, False)
    
    # Build list sorted by level
    gained = []
    for key, (lvl, name, _had_suffix) in sorted(seen.items(), key=lambda x: x[1][0]):
        # Replace generic subclass names with real ones
        name = _replace_subclass_name(name, class_name, subclass, lvl)
        gained.append(f"L{lvl}: {name}")
    
    # Deduplicate "Domain Spells" — keep only the entry at the earliest level
    _ds_found = False
    _deduped = []
    for entry in gained:
        if "Domain Spells" in entry.split(": ", 1)[1]:
            if not _ds_found:
                _deduped.append(entry)
                _ds_found = True
        else:
            _deduped.append(entry)
    gained = _deduped
    
    # Add subclass features that SRD doesn't include
    if subclass and subclass in SUBCLASS_FEATURES:
        sc_feats = SUBCLASS_FEATURES[subclass]
        for sc_lvl, feat_names in sc_feats.items():
            if sc_lvl <= level:
                for fn in feat_names:
                    entry = f"L{sc_lvl}: {fn}"
                    if entry not in gained:
                        # Insert at correct position
                        insert_at = 0
                        for i, g in enumerate(gained):
                            g_lvl = int(g.split(":")[0][1:])
                            if g_lvl > sc_lvl:
                                insert_at = i
                                break
                            insert_at = i + 1
                        gained.insert(insert_at, entry)

    # Add Eldritch Invocation level markers for Warlock (SRD only has L2)
    if class_name == "Warlock":
        inv_levels = INVOCATION_LEVELS.get("Warlock", [])
        for inv_lvl in inv_levels:
            if inv_lvl <= level:
                entry = f"L{inv_lvl}: Eldritch Invocations"
                if entry not in gained:
                    insert_at = 0
                    for i, g in enumerate(gained):
                        g_lvl = int(g.split(":")[0][1:])
                        if g_lvl > inv_lvl:
                            insert_at = i
                            break
                        insert_at = i + 1
                    gained.insert(insert_at, entry)
    
    return gained


_GENERIC_SUBCLASS_NAMES: dict[str, list[str]] = {
    "Barbarian": ["Primal Path", "Path feature"],
    "Bard": ["Bard College", "Bard College feature", "Countercharm"],
    "Cleric": ["Divine Domain"],
    "Druid": ["Druid Circle"],
    "Fighter": ["Martial Archetype", "Martial Archetype feature"],
    "Monk": ["Monastic Tradition"],
    "Paladin": ["Sacred Oath"],
    "Ranger": ["Ranger Archetype"],
    "Rogue": ["Roguish Archetype"],
    "Sorcerer": ["Sorcerous Origin"],
    "Warlock": ["Otherworldly Patron"],
    "Wizard": ["Arcane Tradition"],
}


def _replace_subclass_name(name: str, class_name: str, subclass: str, level: int) -> str:
    """Replace generic SRD subclass feature names with real ones from SUBCLASS_FEATURES."""
    if not subclass or subclass not in SUBCLASS_FEATURES:
        return name
    generics = _GENERIC_SUBCLASS_NAMES.get(class_name, [])
    # Check if this name matches a generic pattern
    name_clean = name.strip()
    for gen in generics:
        if name_clean == gen or name_clean.startswith(gen):
            sc_feats = SUBCLASS_FEATURES[subclass]
            if level in sc_feats:
                return sc_feats[level][0]  # Replace with first real feature
    return name


def get_srd_spells_for_class(class_name: str, max_level: int) -> dict:
    """Get SRD spells (cantrips + leveled) for a class, filtered by max spell level available."""
    if not SRD_SPELLS:
        return {"cantrips": [], "spells": {}}
    cls_lower = class_name.lower()
    # Determine max spell level this class can cast at the given level
    spell_slots = get_spell_slots(class_name, max_level)
    if class_name == "Warlock":
        max_spell_level = spell_slots.get("slot_level", 0)
    else:
        by_level = spell_slots.get("by_level", {})
        max_spell_level = max((lvl for lvl, slots in by_level.items() if slots > 0), default=0)

    cantrips = []
    leveled = {i: [] for i in range(1, 10)}
    for spell in SRD_SPELLS:
        spell_classes = [c.get("name", "").lower() for c in spell.get("classes", [])]
        if cls_lower not in spell_classes:
            continue
        spell_level = spell.get("level", 0)
        spell_name = spell.get("name", "")
        if spell_level == 0:
            cantrips.append(spell_name)
        elif spell_level <= max_spell_level:
            leveled[spell_level].append(spell_name)

    # Limit to top spells (first 6 per level to keep output manageable)
    for lvl in leveled:
        leveled[lvl] = leveled[lvl][:6]

    return {"cantrips": cantrips[:8], "spells": leveled}


# ── Caster type detection & prepared spell computation ────────────────────

# ── Racial Limited-Use Feature Builder ────────────────────────────────────

def _build_racial_limited_features(race_name: str, subrace: str = "", level: int = 1) -> list[str]:
    """Return list of 'L{level}: TraitName' strings for all limited-use racial traits.
    
    Scans _manual_races_raw (full trait dicts) for traits with uses>0 and non-empty recharge.
    RACES dict only stores string trait names so we must consult the raw data.
    """
    features = []
    race_name_lower = race_name.lower()
    try:
        _mrr = _manual_races_raw
    except NameError:
        return features
    
    for race in _mrr:
        if race.get("name", "").lower() != race_name_lower:
            continue
        # Main race traits
        for t in race.get("traits", []):
            if t.get("uses", 0) > 0 and t.get("recharge", ""):
                features.append(f"L{level}: {t['name']}")
        # Subrace traits
        if subrace:
            subrace_lower = subrace.lower()
            for sr in race.get("subraces", []):
                if sr.get("name", "").lower() == subrace_lower:
                    for t in sr.get("traits", []):
                        if t.get("uses", 0) > 0 and t.get("recharge", ""):
                            features.append(f"L{level}: {t['name']}")
                    break
        break
    return features


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


# ── PHB 2014 Limited-Use Feature Definitions ─────────────────────────────
# (feature_key_lower, (min_level_uses, max_cap, recharge_type))
# recharge_type: 'short' (short or long rest), 'long' (long rest only), 'dawn' (at dawn)
# max_cap of 99 means scales with character level (capped by level-based formula)

# PHB Limited-Use Abilities (p.186+ per class)
# ── Multiclass Support (PHB 2014 p.163-165) ──────────────────────────────

# Proficiencies gained when multiclassing INTO a class (PHB p.164)
# None of these grant saving throw proficiencies
def parse_class_levels(char_dict: dict) -> dict[str, int]:
    """Parse class_levels JSON from DB. Falls back to {class_name: level} for old chars."""
    import json as _json
    raw = char_dict.get("class_levels")
    if raw and raw not in ("{}", "", None):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except (_json.JSONDecodeError, TypeError):
            pass
    # Fallback: old single-class character
    cls = char_dict.get("class_name", "Fighter")
    lvl = char_dict.get("level", 1)
    return {cls: int(lvl)} if cls else {"Fighter": 1}

def total_level(class_levels: dict[str, int]) -> int:
    """Total character level = sum of all class levels."""
    return sum(class_levels.values())

def primary_class(class_levels: dict[str, int]) -> tuple[str, int]:
    """Return (class_name, level) for the highest-level class. Ties go to first class taken."""
    if not class_levels:
        return ("Fighter", 0)
    # Return first key (insertion order = first class taken)
    for cls, lvl in class_levels.items():
        return (cls, lvl)
    return ("Fighter", 0)

def meets_multiclass_prereq(char_abilities: dict, new_class: str) -> bool:
    """Check if character meets ability prerequisites to multiclass INTO new_class."""
    prereqs = MULTICLASS_PREREQS.get(new_class, {})
    if not prereqs:
        return True
    # Fighter: either STR 13 OR DEX 13
    if new_class == "Fighter":
        return (char_abilities.get("strength", 10) >= 13 or
                char_abilities.get("dexterity", 10) >= 13)
    for ability, minimum in prereqs.items():
        if char_abilities.get(ability.lower(), 10) < minimum:
            return False
    return True

def get_multiclass_proficiencies(new_class: str) -> dict:
    """Return proficiencies gained when multiclassing INTO new_class."""
    return MULTICLASS_PROFICIENCIES.get(new_class, {})

# PHB 2014 p.165 — Multiclass Spellcaster: Spell Slots per Spell Level
# Key = combined caster level. Value = [1st, 2nd, 3rd, 4th, 5th, 6th, 7th, 8th, 9th]
MULTICLASS_SPELL_SLOTS = {
    1:  [2, 0, 0, 0, 0, 0, 0, 0, 0],
    2:  [3, 0, 0, 0, 0, 0, 0, 0, 0],
    3:  [4, 2, 0, 0, 0, 0, 0, 0, 0],
    4:  [4, 3, 0, 0, 0, 0, 0, 0, 0],
    5:  [4, 3, 2, 0, 0, 0, 0, 0, 0],
    6:  [4, 3, 3, 0, 0, 0, 0, 0, 0],
    7:  [4, 3, 3, 1, 0, 0, 0, 0, 0],
    8:  [4, 3, 3, 2, 0, 0, 0, 0, 0],
    9:  [4, 3, 3, 3, 1, 0, 0, 0, 0],
    10: [4, 3, 3, 3, 2, 0, 0, 0, 0],
    11: [4, 3, 3, 3, 2, 1, 0, 0, 0],
    12: [4, 3, 3, 3, 2, 1, 0, 0, 0],
    13: [4, 3, 3, 3, 2, 1, 1, 0, 0],
    14: [4, 3, 3, 3, 2, 1, 1, 0, 0],
    15: [4, 3, 3, 3, 2, 1, 1, 1, 0],
    16: [4, 3, 3, 3, 2, 1, 1, 1, 0],
    17: [4, 3, 3, 3, 2, 1, 1, 1, 1],
    18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1],
    20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}

def compute_multiclass_caster_level(class_levels: dict[str, int]) -> int:
    """PHB p.165: Full = level, Half = floor(level/2), Third = floor(level/3). Warlock excluded."""
    import math
    total = 0
    for cls, level in class_levels.items():
        if cls in FULL_CASTERS:
            total += level
        elif cls in HALF_CASTERS:
            total += int(math.floor(level / 2))
    return total

def get_multiclass_spell_slots(class_levels: dict[str, int]) -> dict:
    """Return spell slots using PHB p.165 multiclass table. Same shape as get_spell_slots."""
    caster_level = compute_multiclass_caster_level(class_levels)
    slots = MULTICLASS_SPELL_SLOTS.get(min(caster_level, 20), MULTICLASS_SPELL_SLOTS[1])
    by_level = {i+1: slots[i] for i in range(9)}
    return {"slots": sum(slots), "slot_level": None, "by_level": by_level,
            "caster_level": caster_level, "multiclass": True}

# ── Level-Up Data ──────────────────────────────────────────────────────
# ── From data.py: ABILITY_NAMES, ASI_LEVELS

# Subclass selection levels + options per class (PHB 2014)
# Expertise progression — class/subclass → {levels: [...], options: "skills" | "skills_and_thieves_tools" | [...]}
EXPERTISE_LEVELS: dict = {
    "Rogue":         {"levels": [1, 6], "options": "skills_and_thieves_tools"},
    "Bard":          {"levels": [3, 10], "options": "skills"},
    "Knowledge Domain": {"levels": [1], "options": ["Arcana", "History", "Nature", "Religion"]},
}

def get_expertise_count(class_name: str, level: int, subclass: str = "") -> int:
    """How many expertise picks the character has at given level."""
    # Check class first
    exp = EXPERTISE_LEVELS.get(class_name)
    if not exp:
        exp = EXPERTISE_LEVELS.get(subclass)
    if not exp:
        return 0
    levels = exp.get("levels", [])
    picks = sum(1 for l in levels if l <= level)
    return picks * 2  # 2 picks per expertise level

def get_expertise_options(class_name: str, subclass: str = "", skills: list = None) -> list:
    """Return the list of valid expertise options for this class/subclass."""
    exp = EXPERTISE_LEVELS.get(subclass) or EXPERTISE_LEVELS.get(class_name)
    if not exp:
        return []
    opts = exp.get("options")
    if isinstance(opts, list):
        return list(opts)
    if opts == "skills_and_thieves_tools":
        return list(skills or []) + ["Thieves' Tools"]
    if opts == "skills":
        return list(skills or [])
    return list(skills or [])

# ── Fighting Styles ────────────────────────────────────────────────────
FIGHTING_STYLES: dict[str, dict] = {
    "archery":                {"name": "Archery", "desc": "+2 bonus to attack rolls with ranged weapons", "attack_bonus_ranged": 2},
    "defense":                {"name": "Defense", "desc": "+1 AC while wearing armor", "ac_bonus": 1},
    "dueling":                {"name": "Dueling", "desc": "+2 damage with one-handed melee weapon (no other weapon in hand)", "damage_bonus_one_handed": 2},
    "great_weapon_fighting":  {"name": "Great Weapon Fighting", "desc": "Reroll 1s and 2s on damage dice with two-handed/versatile melee weapons"},
    "protection":             {"name": "Protection", "desc": "Reaction: impose disadvantage on attack against adjacent ally (requires shield)"},
    "two_weapon_fighting":    {"name": "Two-Weapon Fighting", "desc": "Add ability modifier to off-hand attack damage"},
}

FIGHTING_STYLE_LEVELS: dict[str, int] = {
    "Fighter": 1,
    "Paladin": 2,
    "Ranger": 2,
}

FIGHTING_STYLE_OPTIONS: dict[str, list[str]] = {
    "Fighter": ["archery", "defense", "dueling", "great_weapon_fighting", "protection", "two_weapon_fighting"],
    "Paladin": ["defense", "dueling", "great_weapon_fighting", "protection"],
    "Ranger": ["archery", "defense", "dueling", "two_weapon_fighting"],
}


# 8 roleplay/combat choice systems: level maps, options, and descriptions
# ── Metamagic (Sorcerer L3/10/17, pick from list each time) ──────────
METAMAGIC_LEVELS: dict[str, list[int]] = {"Sorcerer": [3, 10, 17]}
METAMAGIC_PICKS: dict[int, int] = {3: 2, 10: 1, 17: 1}  # level → number of choices

# ── Eldritch Invocations (Warlock L2+, ~33 SRD options) ──────────────
INVOCATION_LEVELS: dict[str, list[int]] = {"Warlock": [2, 5, 7, 9, 12, 15, 18]}
INVOCATION_PICKS: dict[int,int] = {2:2,5:1,7:1,9:1,12:1,15:1,18:1}

# ── Pact Boon (Warlock L3, pick 1 of 4) ──────────────────────────────
PACT_BOON_LEVELS: dict[str, int] = {"Warlock": 3}
# ── Summon Templates (imported from summon_templates.py) ─────────────────
from summon_templates import SUMMON_TEMPLATES

# ── Battle Master Maneuvers (Fighter L3/7/10/15, requires Battle Master) ──
MANEUVER_LEVELS: dict[str, list[int]] = {"Battle Master": [3, 7, 10, 15]}
MANEUVER_PICKS: dict[int,int] = {3:3,7:2,10:2,15:2}  # level → total known

# ── Magical Secrets (Bard L10/14/18, Lore Bard gets L6 bonus) ────────
MAGICAL_SECRETS_LEVELS: dict[str, list[int]] = {"Bard": [10, 14, 18], "College of Lore": [6]}
MAGICAL_SECRETS_PICKS: dict[int,int] = {6:2,10:2,14:2,18:2}

# ── Totem Spirit (Barbarian Totem Warrior L3/6/14, pick per tier) ────
TOTEM_SPIRIT_LEVELS: dict[str, list[int]] = {"Path of the Totem Warrior": [3, 6, 14]}
TOTEM_SPIRIT_TIER_LABELS: dict[int, str] = {3:"Totem Spirit", 6:"Aspect of the Beast", 14:"Totemic Attunement"}

# ── Hunter's Prey (Ranger Hunter L3, pick 1 of 3) ─────────────────────
HUNTERS_PREY_LEVELS: dict[str, int] = {"Hunter": 3}
# ── Ranger Favored Enemy (PHB p.91, L1/6/14, pick 1 per tier) ────────
FAVORED_ENEMY_LEVELS: dict[str, list[int]] = {"Ranger": [1, 6, 14]}
# ── Ranger Favored Terrain / Natural Explorer (PHB p.91, L1/6/10) ────
FAVORED_TERRAIN_LEVELS: dict[str, list[int]] = {"Ranger": [1, 6, 10]}
# ── Artificer Infusions (L2, pick from list) ─────────────────────────
INFUSION_LEVELS: dict[str, int] = {"Artificer": 2}
INFUSION_PICKS: dict[int,int] = {2:4}  # level → known infusions

# Cantrip progression
CANTRIPS_PROGRESSION: dict[str, dict[int, int]] = {
    "full": {1: 2, 4: 3, 10: 4},
    "warlock": {1: 2, 4: 3, 10: 4},
    "cleric": {1: 3, 4: 4, 10: 5},
}

# ── PHB 2014 Feats ─────────────────────────────────────────────────────
# Tag all PHB 2014 feats with source
for _feat in FEATS.values():
    if not _feat.get("source"):
        _feat["source"] = "Player's Handbook p.165-170"

# ── Feature → Combat Action mapping ──────────────────────────────────
# Maps feature name (lowercase) to (action_type, short_action_label)
# action_type: "Action", "Bonus Action", or "Reaction"
# ── Channel Divinity sub-option descriptions (PHB 2014, not in SRD) ──────
CHANNEL_DIVINITY_DESCRIPTIONS: dict[str, str] = {
    # Cleric domains — PHB p.59-62
    "channel divinity: turn undead":
        "As an action, you present your holy symbol and speak a prayer censuring the undead. "
        "Each undead that can see or hear you within 30 feet of you must make a Wisdom saving throw. "
        "If the creature fails its saving throw, it is turned for 1 minute or until it takes any damage. "
        "A turned creature must spend its turns trying to move as far away from you as it can, and it "
        "can't willingly move to a space within 30 feet of you. It also can't take reactions. For its "
        "action, it can use only the Dash action or try to escape from an effect that prevents it from "
        "moving. If there's nowhere to move, the creature can use the Dodge action. "
        "When a creature fails its save, if its CR is at or below the Destroy Undead threshold for "
        "your cleric level, it is instantly destroyed instead.",
    "channel divinity: knowledge of the ages":
        "As an action, you choose one skill or tool. For 10 minutes, you have proficiency with "
        "the chosen skill or tool.",
    "channel divinity: read thoughts":
        "As an action, choose one creature that you can see within 60 feet of you. That creature "
        "must make a Wisdom saving throw. If it succeeds, you can't use this feature on it again "
        "until you finish a long rest. If it fails, you can read its surface thoughts (those foremost "
        "in its mind, reflecting its current emotions and what it is actively thinking about) when "
        "it is within 60 feet of you. This effect lasts for 1 minute. During that time, you can use "
        "your action to end this effect and cast the Suggestion spell on the creature without "
        "expending a spell slot. The target automatically fails its saving throw against the spell.",
    "channel divinity: preserve life":
        "As an action, you present your holy symbol and evoke healing energy that can restore a "
        "number of hit points equal to five times your cleric level. Choose any creatures within "
        "30 feet of you, and divide those hit points among them. This feature can restore a "
        "creature to no more than half of its hit point maximum. You can't use this feature on "
        "an undead or a construct.",
    "channel divinity: radiance of the dawn":
        "As an action, you present your holy symbol, and any magical darkness within 30 feet of "
        "you is dispelled. Additionally, each hostile creature within 30 feet of you must make a "
        "Constitution saving throw. A creature takes radiant damage equal to 2d10 + your cleric "
        "level on a failed saving throw, and half as much on a successful one. A creature that "
        "has total cover from you is not affected.",
    "channel divinity: charm animals and plants":
        "As an action, you present your holy symbol and invoke the name of your deity. Each "
        "beast or plant creature that can see you within 30 feet of you must make a Wisdom "
        "saving throw. If the creature fails, it is charmed by you for 1 minute or until it takes "
        "damage. While charmed, it is friendly to you and other creatures you designate.",
    "channel divinity: destructive wrath":
        "When you roll lightning or thunder damage, you can use your Channel Divinity to deal "
        "maximum damage instead of rolling.",
    "channel divinity: invoke duplicity":
        "As an action, you create a perfect illusion of yourself that lasts for 1 minute, or until "
        "you lose your concentration (as if concentrating on a spell). The illusion appears in an "
        "unoccupied space that you can see within 30 feet of you. As a bonus action on your "
        "turn, you can move the illusion up to 30 feet, but it must remain within 120 feet of you. "
        "For the duration, you can cast spells as though you were in the illusion's space, but "
        "you must use your own senses. Additionally, when both you and your illusion are within "
        "5 feet of a creature that can see the illusion, you have advantage on attack rolls "
        "against that creature, given how distracting the illusion is to the target.",
    "channel divinity: cloak of shadows":
        "As an action, you become invisible until the end of your next turn. You become visible "
        "if you attack or cast a spell.",
    "channel divinity: guided strike":
        "When you make an attack roll, you can use your Channel Divinity to gain a +10 bonus "
        "to the roll. You make this choice after you see the roll, but before the DM says whether "
        "the attack hits or misses.",
    "channel divinity: war god's blessing":
        "When a creature within 30 feet of you makes an attack roll, you can use your reaction "
        "to grant that creature a +10 bonus to the roll, using your Channel Divinity. You make "
        "this choice after you see the roll, but before the DM says whether the attack hits "
        "or misses.",
    # Paladin oaths — PHB p.86-88
    "channel divinity: sacred weapon":
        "As an action, you can imbue one weapon that you are holding with positive energy, using "
        "your Channel Divinity. For 1 minute, you add your Charisma modifier to attack rolls made "
        "with that weapon (minimum bonus of +1). The weapon also emits bright light in a 20-foot "
        "radius and dim light 20 feet beyond that. If the weapon is not already magical, it becomes "
        "magical for the duration. You can end this effect on your turn as part of any other action. "
        "If you are no longer holding or carrying this weapon, or if you fall unconscious, this "
        "effect ends.",
    "channel divinity: turn the unholy":
        "As an action, you present your holy symbol and speak a prayer censuring fiends and undead, "
        "using your Channel Divinity. Each fiend or undead that can see or hear you within 30 feet "
        "of you must make a Wisdom saving throw. If the creature fails its saving throw, it is "
        "turned for 1 minute or until it takes damage. A turned creature must spend its turns "
        "trying to move as far away from you as it can, and it can't willingly move to a space "
        "within 30 feet of you. It also can't take reactions. For its action, it can use only the "
        "Dash action or try to escape from an effect that prevents it from moving.",
    "channel divinity: nature's wrath":
        "As an action, you can cause spectral vines to spring up and reach for a creature within "
        "10 feet of you that you can see. The creature must succeed on a Strength or Dexterity "
        "saving throw (its choice) or be restrained. While restrained by the vines, the creature "
        "repeats the saving throw at the end of each of its turns. On a success, it frees itself "
        "and the vines vanish.",
    "channel divinity: turn the faithless":
        "As an action, you present your holy symbol, and each fey or fiend within 30 feet of "
        "you that can hear you must make a Wisdom saving throw. On a failed save, the creature "
        "is turned for 1 minute or until it takes damage. A turned creature must spend its turns "
        "trying to move as far away from you as it can, and it can't willingly move to a space "
        "within 30 feet of you. It also can't take reactions. For its action, it can use only the "
        "Dash action or try to escape from an effect that prevents it from moving.",
    "channel divinity: abjure enemy":
        "As an action, you present your holy symbol and speak a prayer of denunciation, using "
        "your Channel Divinity. Choose one creature within 60 feet of you that you can see. "
        "That creature must make a Wisdom saving throw, unless it is immune to being frightened. "
        "Fiends and undead have disadvantage on this saving throw. On a failed save, the "
        "creature is frightened of you for 1 minute or until it takes any damage. While frightened, "
        "the creature's speed is 0, and it can't benefit from any bonus to its speed. On a "
        "successful save, the creature's speed is halved for 1 minute or until it takes damage.",
    "channel divinity: vow of enmity":
        "As a bonus action, you can utter a vow of enmity against a creature you can see "
        "within 10 feet of you, using your Channel Divinity. You gain advantage on attack rolls "
        "against the creature for 1 minute or until it drops to 0 hit points or falls unconscious.",
    # DMG subclasses
    "channel divinity: touch of death":
        "When you hit a creature with a melee weapon attack, you can use Channel Divinity to "
        "deal extra necrotic damage equal to 5 + twice your cleric level. If this damage reduces "
        "the target to 0 hit points, it dies instantly.",
    "channel divinity: control undead":
        "As an action, you target one undead creature you can see within 30 feet of you. The "
        "target must make a Wisdom saving throw. On a failed save, the target must obey your "
        "commands for the next 24 hours, or until you use this Channel Divinity option again. "
        "An undead whose CR is equal to or greater than your paladin level is immune to this effect.",
    "channel divinity: dreadful aspect":
        "As an action, you channel the darkest emotions and focus them into a burst of magical "
        "menace. Each creature of your choice within 30 feet of you must make a Wisdom saving "
        "throw if it can see you. On a failed save, the target is frightened of you for 1 minute. "
        "If a creature frightened by this effect ends its turn more than 30 feet away from you, it "
        "can attempt another Wisdom saving throw to end the effect on itself.",
}

# ── Domain / Oath spells — always prepared, don't count against limit (PHB p.58, p.85) ──
DOMAIN_SPELLS: dict[str, list[str]] = {
    # Cleric domains — PHB p.59-62
    "Knowledge Domain": ["Command","Identify","Augury","Suggestion","Nondetection","Speak with Dead",
                         "Arcane Eye","Confusion","Legend Lore","Scrying"],
    "Life Domain": ["Bless","Cure Wounds","Lesser Restoration","Spiritual Weapon",
                    "Beacon of Hope","Revivify","Death Ward","Guardian of Faith",
                    "Mass Cure Wounds","Raise Dead"],
    "Light Domain": ["Burning Hands","Faerie Fire","Flaming Sphere","Scorching Ray",
                     "Daylight","Fireball","Guardian of Faith","Wall of Fire",
                     "Flame Strike","Scrying"],
    "Nature Domain": ["Animal Friendship","Speak with Animals","Barkskin","Spike Growth",
                      "Plant Growth","Wind Wall","Dominate Beast","Grasping Vine",
                      "Insect Plague","Tree Stride"],
    "Tempest Domain": ["Fog Cloud","Thunderwave","Gust of Wind","Shatter","Call Lightning",
                       "Sleet Storm","Control Water","Ice Storm","Destructive Wave","Insect Plague"],
    "Trickery Domain": ["Charm Person","Disguise Self","Mirror Image","Pass without Trace",
                        "Blink","Dispel Magic","Dimension Door","Polymorph",
                        "Dominate Person","Modify Memory"],
    "War Domain": ["Divine Favor","Shield of Faith","Magic Weapon","Spiritual Weapon",
                   "Crusader's Mantle","Spirit Guardians","Freedom of Movement","Stoneskin",
                   "Flame Strike","Hold Monster"],
    # Paladin oaths — PHB p.86-88
    "Oath of Devotion": ["Protection from Evil and Good","Sanctuary","Lesser Restoration",
                         "Zone of Truth","Beacon of Hope","Dispel Magic","Freedom of Movement",
                         "Guardian of Faith","Commune","Flame Strike"],
    "Oath of the Ancients": ["Ensnaring Strike","Speak with Animals","Moonbeam","Misty Step",
                             "Plant Growth","Protection from Energy","Ice Storm","Stoneskin",
                             "Commune with Nature","Tree Stride"],
    "Oath of Vengeance": ["Bane","Hunter's Mark","Hold Person","Misty Step","Haste",
                          "Protection from Energy","Banishment","Dimension Door",
                          "Hold Monster","Scrying"],
    # DMG subclasses
    "Death Domain": ["False Life","Ray of Sickness","Blindness/Deafness","Ray of Enfeeblement",
                     "Animate Dead","Vampiric Touch","Blight","Death Ward",
                     "Antilife Shell","Cloudkill"],
    "Oathbreaker": ["Hellish Rebuke","Inflict Wounds","Crown of Madness","Darkness",
                    "Animate Dead","Bestow Curse","Blight","Confusion",
                    "Contagion","Dominate Person"],
}

# ── Subclass feature descriptions (PHB 2014, not in SRD) ──────
# ── Subclass feature descriptions (PHB 2014, not in SRD) ──────
SUBCLASS_FEATURE_DESCRIPTIONS: dict[str, str] = {
    # ── Barbarian: Path of the Berserker (PHB p.49) ──
    "frenzy":
        "Starting at 3rd level, you can go into a frenzy when you rage. If you do so, for the "
        "duration of your rage you can make a single melee weapon attack as a bonus action on "
        "each of your turns after this one. When your rage ends, you suffer one level of exhaustion.",
    "mindless rage":
        "Beginning at 6th level, you can't be charmed or frightened while raging. If you are "
        "charmed or frightened when you enter your rage, the effect is suspended for the "
        "duration of the rage.",
    "intimidating presence":
        "Beginning at 10th level, you can use your action to frighten someone with your menacing "
        "presence. Choose one creature that you can see within 30 feet of you. If the creature "
        "can see or hear you, it must succeed on a Wisdom saving throw (DC 8 + proficiency bonus "
        "+ Charisma modifier) or be frightened of you until the end of your next turn. On "
        "subsequent turns, you can use your action to extend the duration on the frightened "
        "creature until the end of your next turn. This effect ends if the creature ends its "
        "turn out of line of sight or more than 60 feet away from you. If the creature succeeds "
        "on its saving throw, you can't use this feature on that creature again for 24 hours.",
    "retaliation":
        "Starting at 14th level, when you take damage from a creature that is within 5 feet of "
        "you, you can use your reaction to make a melee weapon attack against that creature.",

    # ── Barbarian: Path of the Totem Warrior (PHB p.50) ──
    "spirit seeker":
        "At 3rd level, you gain the ability to cast the Beast Sense and Speak with Animals spells, "
        "but only as rituals.",
    "totem spirit":
        "At 3rd level, you choose a totem spirit and gain its feature. Bear: while raging, you have "
        "resistance to all damage except psychic damage. Eagle: while raging, other creatures have "
        "disadvantage on opportunity attack rolls against you, and you can Dash as a bonus action. "
        "Wolf: while raging, your allies have advantage on melee attack rolls against hostile creatures "
        "within 5 feet of you.",
    "aspect of the beast":
        "At 6th level, you gain a magical benefit based on the totem animal of your choice. Bear: "
        "your carrying capacity is doubled and you gain advantage on Strength checks to push, pull, "
        "lift, or break objects. Eagle: you can see up to 1 mile away with no difficulty, and dim "
        "light doesn't impose disadvantage on your Perception checks. Wolf: you can track creatures "
        "while moving at a fast pace and move stealthily at a normal pace.",
    "spirit walker":
        "At 10th level, you can cast the Commune with Nature spell as a ritual.",
    "totemic attunement":
        "At 14th level, you gain a magical benefit based on your totem animal. Bear: while raging, "
        "creatures within 5 feet have disadvantage on attacks against targets other than you. Eagle: "
        "while raging, you gain a flying speed equal to your walking speed. Wolf: while raging, you "
        "can use a bonus action to knock a Large or smaller creature prone when you hit with a melee attack.",

    # ── Bard: College of Lore (PHB p.54-55) ──
    "cutting words":
        "At 3rd level, you learn how to use your wit to distract, confuse, and otherwise sap the "
        "confidence and competence of others. When a creature that you can see within 60 feet of "
        "you makes an attack roll, an ability check, or a damage roll, you can use your reaction "
        "to expend one of your uses of Bardic Inspiration, rolling a Bardic Inspiration die and "
        "subtracting the number rolled from the creature's roll. You can choose to use this "
        "feature after the creature makes its roll, but before the DM determines whether the "
        "attack roll or ability check succeeds or fails, or before the creature deals its damage.",
    "peerless skill":
        "Starting at 14th level, when you make an ability check, you can expend one use of "
        "Bardic Inspiration. Roll a Bardic Inspiration die and add the number to your ability "
        "check. You can choose to do so after you roll the die for the ability check, but before "
        "the DM tells you whether you succeed or fail.",

    # ── Bard: College of Valor (PHB p.55) ──
    "combat inspiration":
        "At 3rd level, a creature that has a Bardic Inspiration die from you can roll that die and "
        "add the number to a weapon damage roll, or use its reaction to add the number to its AC "
        "against an attack.",

    # ── Druid: Circle of the Land (PHB p.68) ──
    "natural recovery":
        "Starting at 2nd level, you can regain some of your magical energy by sitting in meditation "
        "and communing with nature. During a short rest, you choose expended spell slots to recover. "
        "The spell slots can have a combined level that is equal to or less than half your druid "
        "level (rounded up), and none of the slots can be 6th level or higher. You can't use this "
        "feature again until you finish a long rest.",
    "battle magic":
        "At 14th level, when you use your action to cast a bard spell, you can make one weapon "
        "attack as a bonus action.",

    # ── Cleric: Death Domain (DMG p.96-97) ──
    "death domain spells":
        "You gain domain spells at the cleric levels listed: 1st — False Life, Ray of Sickness; "
        "3rd — Blindness/Deafness, Ray of Enfeeblement; 5th — Animate Dead, Vampiric Touch; "
        "7th — Blight, Death Ward; 9th — Antilife Shell, Cloudkill.",
    "reaper":
        "At 1st level, you learn one necromancy cantrip of your choice. When you cast a necromancy "
        "cantrip that normally targets only one creature, it can instead target two creatures within "
        "range and within 5 feet of each other.",
    "inescapable destruction":
        "At 6th level, your ability to channel negative energy becomes more potent. Necrotic damage "
        "dealt by your cleric spells and Channel Divinity options ignores resistance to necrotic damage.",
    "improved reaper":
        "At 17th level, when you cast a necromancy spell of 1st through 5th level that targets only "
        "one creature, the spell can instead target two creatures within range and within 5 feet of "
        "each other. If the spell consumes material components, you must provide them for each target.",

    # ── Cleric: Knowledge Domain (PHB p.59-60) ──
    "blessings of knowledge":
        "At 1st level, you learn two languages of your choice. You also become proficient in two "
        "skills of your choice: Arcana, History, Nature, or Religion. Your proficiency bonus is "
        "doubled for any ability check you make that uses either of those skills.",
    "potent spellcasting":
        "At 8th level, you add your Wisdom modifier to the damage you deal with any cleric cantrip.",
    "visions of the past":
        "At 17th level, you can call up visions of the past relating to an object you hold or your "
        "immediate surroundings. You spend at least 1 minute meditating and praying, then receive "
        "dreamlike, shadowy glimpses of recent events. Object Reading: you learn the object's "
        "previous owner, how they acquired/lost it, and the most significant past event involving it. "
        "Area Reading: you see events from within 50 feet, going back a number of days equal to "
        "your Wisdom score (minimum 1).",

    # ── Cleric: Light Domain (PHB p.60-61) ──
    "warding flare":
        "At 1st level, you can interpose divine light between yourself and an attacking enemy. "
        "When you are attacked by a creature within 30 feet that you can see, you can use your "
        "reaction to impose disadvantage on the attack roll. You can use this feature a number of "
        "times equal to your Wisdom modifier (minimum 1). You regain expended uses on a long rest.",
    "improved flare":
        "At 6th level, you can also use your Warding Flare when a creature within 30 feet attacks "
        "an ally other than you. If the creature is attacking both you and an ally simultaneously, "
        "you can impose disadvantage on all targets.",
    "corona of light":
        "At 17th level, you can use your action to activate an aura of sunlight that lasts for 1 "
        "minute or until you dismiss it. You emit bright light in a 60-foot radius and dim light "
        "30 feet beyond that. Enemies in the bright light have disadvantage on saving throws "
        "against any spell that deals fire or radiant damage.",

    # ── Cleric: Nature Domain (PHB p.61-62) ──
    "acolyte of nature":
        "At 1st level, you learn one druid cantrip of your choice. You also gain proficiency in "
        "one of the following skills: Animal Handling, Nature, or Survival.",
    "bonus proficiency":
        "At 1st level, you gain proficiency with heavy armor.",
    "dampen elements":
        "At 6th level, when you or a creature within 30 feet takes acid, cold, fire, lightning, "
        "or thunder damage, you can use your reaction to grant resistance to that instance of damage.",
    "divine strike":
        "At 8th level, once on each of your turns when you hit a creature with a weapon attack, "
        "you can cause the attack to deal an extra 1d8 damage of the same type dealt by the weapon "
        "to the target. At 14th level, the extra damage increases to 2d8.",
    "master of nature":
        "At 17th level, you gain the ability to command animals and plant creatures. As an action, "
        "you can issue a non-hostile command to beasts and plants within 30 feet. Creatures that "
        "can't be charmed are immune. You can use this feature a number of times equal to your "
        "Wisdom modifier (minimum 1). Regain uses on a long rest.",

    # ── Cleric: Tempest Domain (PHB p.62) ──
    "wrath of the storm":
        "At 1st level, you can thunderously rebuke attackers. When a creature within 5 feet hits "
        "you, you can use your reaction to deal 2d8 lightning or thunder damage (your choice). "
        "You can use this feature a number of times equal to your Wisdom modifier (minimum 1). "
        "Regain uses on a long rest.",
    "thunderbolt strike":
        "At 6th level, when you deal lightning damage to a Large or smaller creature, you can "
        "push it up to 10 feet away from you.",
    "stormborn":
        "At 17th level, you gain a flying speed equal to your walking speed when not underground "
        "or indoors.",

    # ── Cleric: Trickery Domain (PHB p.62-63) ──
    "blessing of the trickster":
        "At 1st level, you can use your action to touch a willing creature to give it advantage "
        "on Dexterity (Stealth) checks. This blessing lasts for 1 hour or until you use this "
        "feature again.",
    "improved duplicity":
        "At 17th level, you can create up to four duplicates with Invoke Duplicity, instead of "
        "one. As a bonus action, you can move any number of them up to 30 feet (max range 120 feet).",

    # ── Cleric: War Domain (PHB p.63) ──
    "war priest":
        "At 1st level, when you use the Attack action, you can make one weapon attack as a bonus "
        "action. You can use this feature a number of times equal to your Wisdom modifier (minimum "
        "1). Regain uses on a long rest.",
    "avatar of battle":
        "At 17th level, you gain resistance to bludgeoning, piercing, and slashing damage from "
        "nonmagical weapons.",

    # ── Druid: Circle of the Moon (PHB p.69) ──
    "combat wild shape":
        "At 2nd level, you gain the ability to use Wild Shape on your turn as a bonus action "
        "rather than an action. Additionally, while transformed by Wild Shape, you can use a "
        "bonus action to expend one spell slot and regain 1d8 hit points per level of the slot.",
    "circle forms":
        "At 2nd level, you can transform into beasts with a CR as high as 1 (instead of the "
        "normal 1/4). Starting at 6th level, the max CR equals your druid level divided by 3 "
        "(rounded down).",
    "primal strike":
        "At 6th level, your attacks in beast form count as magical for the purpose of overcoming "
        "resistance and immunity to nonmagical attacks and damage.",
    "elemental wild shape":
        "At 10th level, you can expend two uses of Wild Shape simultaneously to transform into "
        "an air, earth, fire, or water elemental.",
    "thousand forms":
        "At 14th level, you can cast the Alter Self spell at will.",

    # ── Fighter: Battle Master (PHB p.73-74) ──
    "combat superiority":
        "At 3rd level, you learn three maneuvers of your choice, detailed at the end of the "
        "Fighter class description. You gain four d8 superiority dice. You learn two additional "
        "maneuvers at 7th, 10th, and 15th level. You gain another superiority die at 7th and 15th "
        "level.",
    "know your enemy":
        "At 7th level, if you spend at least 1 minute observing or interacting with another "
        "creature outside combat, you can learn whether it is your equal, superior, or inferior "
        "in two of the following: Strength, Dexterity, Constitution, AC, current HP, total class "
        "levels (if any), or Fighter class levels (if any).",
    "improved combat superiority":
        "At 10th level, your superiority dice become d10s. At 18th level, they become d12s.",
    "relentless":
        "At 15th level, when you roll initiative and have no superiority dice remaining, you "
        "regain one superiority die.",

    # ── Fighter: Eldritch Knight (PHB p.74-75) ──
    "weapon bond":
        "At 3rd level, you learn a ritual that creates a magical bond between yourself and one "
        "weapon. You can't be disarmed of that weapon unless incapacitated. If it's on the same "
        "plane, you can summon it as a bonus action, causing it to teleport to your hand. You "
        "can bond with up to two weapons.",
    "war magic":
        "At 7th level, when you use your action to cast a cantrip, you can make one weapon "
        "attack as a bonus action.",
    "eldritch strike":
        "At 10th level, when you hit a creature with a weapon attack, that creature has "
        "disadvantage on the next saving throw it makes against a spell you cast before the end "
        "of your next turn.",
    "arcane charge":
        "At 15th level, you gain the ability to teleport up to 30 feet to an unoccupied space "
        "you can see when you use Action Surge. You can teleport before or after the additional action.",
    "improved war magic":
        "At 18th level, when you use your action to cast a spell, you can make one weapon attack "
        "as a bonus action.",

    # ── Monk: Way of the Open Hand (PHB p.79) ──
    "open hand technique":
        "At 3rd level, whenever you hit a creature with one of the attacks granted by your Flurry "
        "of Blows, you can impose one of the following effects on that target: it must succeed on "
        "a Dexterity saving throw or be knocked prone; it must make a Strength saving throw, and "
        "if it fails, you can push it up to 15 feet away from you; or it can't take reactions "
        "until the end of your next turn.",
    "wholeness of body":
        "At 6th level, you gain the ability to heal yourself. As an action, you can regain hit "
        "points equal to three times your monk level. You must finish a long rest before you can "
        "use this feature again.",
    "tranquility":
        "At 11th level, you enter a meditative state that persists until you are incapacitated or "
        "die. At the end of a long rest, you gain the effect of a Sanctuary spell that lasts until "
        "the start of your next long rest (it can end early as normal). The spell save DC for the "
        "effect equals 8 + your Wisdom modifier + your proficiency bonus.",
    "quivering palm":
        "At 17th level, you gain the ability to set up lethal vibrations in someone's body. When "
        "you hit a creature with an unarmed strike, you can spend 3 ki points to start these "
        "imperceptible vibrations, which last for a number of days equal to your monk level. The "
        "vibrations are harmless unless you use your action to end them — the creature must then "
        "make a Constitution saving throw. On a failure, it drops to 0 hit points. On a success, "
        "it takes 10d10 necrotic damage. You can have only one creature under the effect of this "
        "feature at a time.",

    # ── Monk: Way of Shadow (PHB p.80) ──
    "shadow arts":
        "At 3rd level, you can use your ki to duplicate certain spells. As an action, you can "
        "spend 2 ki points to cast Darkness, Darkvision, Pass Without Trace, or Silence, without "
        "providing material components. You also learn the Minor Illusion cantrip.",
    "shadow step":
        "At 6th level, you gain the ability to step from one shadow into another. When in dim "
        "light or darkness, as a bonus action you can teleport up to 60 feet to an unoccupied "
        "space you can see that is also in dim light or darkness. You then have advantage on the "
        "first melee attack before the end of your turn.",
    "cloak of shadows":
        "At 11th level, when you are in dim light or darkness, you can use your action to become "
        "invisible. You remain invisible until you make an attack, cast a spell, or enter bright light.",
    "opportunist":
        "At 17th level, when a creature within 5 feet is hit by an attack from a creature other "
        "than you, you can use your reaction to make a melee attack against that creature.",

    # ── Monk: Way of the Four Elements (PHB p.80-81) ──
    "disciple of the elements":
        "At 3rd level, you learn magical disciplines that harness the four elements. You learn "
        "the Elemental Attunement discipline and one other elemental discipline of your choice. "
        "You learn additional disciplines at 6th, 11th, and 17th level. When you gain a level, "
        "you may replace one discipline with another. Casting elemental spells costs ki points "
        "equal to the spell's level + 1 (max 6 ki for 5th-level spells).",

    # ── Paladin: Oath of Devotion (PHB p.85-86) ──
    "aura of devotion":
        "Starting at 7th level, you and friendly creatures within 10 feet of you can't be "
        "charmed while you are conscious. At 18th level, the range of this aura increases to "
        "30 feet.",
    "purity of spirit":
        "Beginning at 15th level, you are always under the effects of a Protection from Evil "
        "and Good spell.",
    "holy nimbus":
        "At 20th level, as an action, you can emanate an aura of sunlight. For 1 minute, bright "
        "light shines from you in a 30-foot radius, and dim light shines 30 feet beyond that. "
        "Whenever an enemy starts its turn in the bright light, it takes 10 radiant damage. In "
        "addition, for the duration, you have advantage on saving throws against spells cast by "
        "fiends or undead. Once you use this feature, you can't use it again until you finish "
        "a long rest.",

    # ── Paladin: Oath of Vengeance (PHB p.87-88) ──
    "relentless avenger":
        "At 7th level, when you hit a creature with an opportunity attack, you can move up to "
        "half your speed immediately after the attack as part of the same reaction. This movement "
        "doesn't provoke opportunity attacks.",
    "soul of vengeance":
        "At 15th level, when a creature under the effect of your Vow of Enmity makes an attack, "
        "you can use your reaction to make a melee weapon attack against that creature if it is "
        "within range.",
    "avenging angel":
        "At 20th level, you can assume the form of an angelic avenger. Using your action, you "
        "undergo a transformation for 1 hour: you sprout wings granting 60 ft flying speed, and "
        "you emanate an aura of menace in a 30-foot radius. Enemies that start their turn in the "
        "aura must succeed on a Wisdom save or be frightened for 1 minute. Once used, can't be "
        "used again until a long rest.",

    # ── Paladin: Oath of the Ancients (PHB p.86-87) ──
    "aura of warding":
        "At 7th level, you and friendly creatures within 10 feet have resistance to damage from "
        "spells. At 18th level, the range increases to 30 feet.",
    "undying sentinel":
        "At 15th level, when you are reduced to 0 hit points and not killed outright, you can "
        "drop to 1 hit point instead. Once used, can't be used again until a long rest. "
        "Additionally, you suffer none of the drawbacks of old age and can't be aged magically.",
    "elder champion":
        "At 20th level, you can use your action to become an ancient force of nature for 1 minute. "
        "You regain 10 HP at the start of each turn, you cast paladin spells with a casting time of "
        "1 action as a bonus action, and enemies within 10 feet have disadvantage on saves against "
        "your spells and Channel Divinity. Once used, can't be used again until a long rest.",

    # ── Paladin: Oathbreaker (DMG p.97) ──
    "oathbreaker spells":
        "You gain oath spells at the paladin levels listed: 3rd — Hellish Rebuke, Inflict Wounds; "
        "5th — Crown of Madness, Darkness; 9th — Animate Dead, Bestow Curse; 13th — Blight, "
        "Confusion; 17th — Contagion, Dominate Person.",
    "aura of hate":
        "At 7th level, you and any fiends/undead within 10 feet gain a bonus to melee weapon "
        "damage equal to your Charisma modifier (minimum +1). At 18th level, range increases to 30 feet.",
    "supernatural resistance":
        "At 15th level, you gain resistance to bludgeoning, piercing, and slashing damage from "
        "nonmagical weapons.",
    "dread lord":
        "At 20th level, you can use your action to become an avatar of darkness for 1 minute. "
        "You emit an aura of gloom in a 30-foot radius, and enemies that start their turn there "
        "must succeed on a Wisdom save or be frightened. As a bonus action, you can make a melee "
        "spell attack (CHA) against a creature in the aura, dealing 3d10 + CHA necrotic damage. "
        "Once used, can't be used again until a long rest.",

    # ── Ranger: Hunter (PHB p.93) ──
    "hunter's prey":
        "At 3rd level, you gain one of the following features of your choice. Colossus Slayer: "
        "when you hit a creature with a weapon attack, it takes an extra 1d8 damage if it's "
        "below its hit point maximum (once per turn). Giant Killer: when a Large or larger "
        "creature within 5 feet hits or misses you, you can use your reaction to attack it. "
        "Horde Breaker: when you make a weapon attack, you can make another attack against a "
        "different creature within 5 feet of the original target and within your weapon's range "
        "(once per turn).",
    "defensive tactics":
        "At 7th level, you gain one of the following features of your choice. Escape the Horde: "
        "opportunity attacks against you have disadvantage. Multiattack Defense: when a creature "
        "hits you, you gain +4 AC against all subsequent attacks it makes for the rest of the "
        "turn. Steel Will: you have advantage on saving throws against being frightened.",
    "multiattack":
        "At 11th level, you gain one of the following features of your choice. Volley: you can "
        "use your action to make a ranged attack against any number of creatures within 10 feet "
        "of a point you can see (ammunition required per target). Whirlwind Attack: you can use "
        "your action to make a melee attack against any number of creatures within 5 feet of "
        "you, with a separate attack roll for each target.",
    "superior hunter's defense":
        "At 15th level, you gain one of the following features of your choice. Evasion: when "
        "subjected to a DEX save for half damage, you take none on a success and half on a "
        "failure. Stand Against the Tide: when a hostile creature misses you with a melee "
        "attack, you can use your reaction to force it to repeat the same attack against another "
        "creature of your choice. Uncanny Dodge: when an attacker you can see hits you, you can "
        "use your reaction to halve the damage.",

    # ── Ranger: Beast Master (PHB p.93) ──
    "ranger's companion":
        "At 3rd level, you gain a beast companion. Choose a beast of CR 1/4 or lower (Medium or "
        "smaller). Add your proficiency bonus to its AC, attack rolls, damage rolls, and any "
        "saving throws/skills it's proficient in. It obeys your commands and acts on your "
        "initiative. You can command it verbally (no action) to take the Attack, Dash, Disengage, "
        "Dodge, or Help action. If you don't command it, it takes the Dodge action.",
    "exceptional training":
        "At 7th level, on any turn where your companion doesn't attack, you can use a bonus "
        "action to command it to Dash, Disengage, Dodge, or Help. Additionally, its attacks "
        "count as magical.",
    "bestial fury":
        "At 11th level, when you command your companion to take the Attack action, it can make "
        "two attacks, or it can take the Multiattack action if it has one.",
    "share spells":
        "At 15th level, when you cast a spell targeting yourself, you can also affect your beast "
        "companion if it's within 30 feet of you.",

    # ── Rogue: Thief (PHB p.97) ──
    "fast hands":
        "Starting at 3rd level, you can use the bonus action granted by your Cunning Action to "
        "make a Dexterity (Sleight of Hand) check, use your thieves' tools to disarm a trap or "
        "open a lock, or take the Use an Object action.",
    "second-story work":
        "At 3rd level, you gain the ability to climb faster than normal; climbing no longer costs "
        "you extra movement. In addition, when you make a running jump, the distance you cover "
        "increases by a number of feet equal to your Dexterity modifier.",
    "supreme sneak":
        "Starting at 9th level, you have advantage on a Dexterity (Stealth) check if you move "
        "no more than half your speed on the same turn.",
    "use magic device":
        "By 13th level, you have learned enough about the workings of magic that you can "
        "improvise the use of items even when they are not intended for you. You ignore all "
        "class, race, and level requirements on the use of magic items.",
    "thief's reflexes":
        "When you reach 17th level, you have become adept at laying ambushes and quickly "
        "escaping danger. You can take two turns during the first round of any combat. You take "
        "your first turn at your normal initiative and your second turn at your initiative minus "
        "10. You can't use this feature when you are surprised.",

    # ── Rogue: Arcane Trickster (PHB p.97-98) ──
    "mage hand legerdemain":
        "At 3rd level, when you cast Mage Hand, you can make the spectral hand invisible and "
        "perform additional tasks: stow/retrieve an object from a container worn or carried by "
        "another creature, use thieves' tools to pick locks/disarm traps at range, or perform "
        "Sleight of Hand checks. You can do these tasks without being noticed with a successful "
        "Sleight of Hand check contested by the target's Perception.",
    "magical ambush":
        "At 9th level, if you are hidden from a creature when you cast a spell on it, the "
        "creature has disadvantage on any saving throw against the spell this turn.",
    "versatile trickster":
        "At 13th level, you gain the ability to distract targets with your Mage Hand. As a bonus "
        "action, you can designate a creature within 5 feet of the hand. You gain advantage on "
        "attack rolls against that creature until the end of your next turn.",
    "spell thief":
        "At 17th level, you can steal the knowledge of how to cast a spell from another "
        "spellcaster. Immediately after a creature casts a spell that targets you or includes "
        "you in its area of effect, you can use your reaction to force it to make a save with "
        "its spellcasting modifier (DC = your spell save DC). On a failure, you negate the "
        "effect against you and steal the spell. For the next 8 hours, you know the spell and "
        "can cast it with your slots. The creature can't cast it during that time. Once used, "
        "can't be used again until a long rest.",

    # ── Rogue: Assassin (PHB p.97) ──
    "assassinate":
        "At 3rd level, you have advantage on attack rolls against any creature that hasn't taken "
        "a turn in combat yet. In addition, any hit you score against a surprised creature is a "
        "critical hit.",
    "infiltration expertise":
        "At 9th level, you can create a false identity for yourself. You must spend 7 days and "
        "25 gp to establish the identity's history, profession, and affiliations. You can't "
        "establish an identity belonging to someone else. Thereafter, you can adopt the persona "
        "with a disguise. Others believe you are that person until given an obvious reason not to.",
    "impostor":
        "At 13th level, you can mimic another person's speech, writing, and behavior. You must "
        "spend at least 3 hours studying these components: speech (listening), writing (reading "
        "samples), and mannerisms (observing). Your ruse is indiscernible to the casual observer. "
        "If a wary creature suspects, you have advantage on Charisma (Deception) checks.",
    "death strike":
        "At 17th level, when you attack and hit a surprised creature, it must make a Constitution "
        "save (DC 8 + DEX mod + proficiency bonus). On a failure, double the damage of your "
        "attack against it.",

    # ── Sorcerer: Draconic Bloodline (PHB p.102-103) ──
    "dragon ancestor":
        "At 1st level, you choose one type of dragon as your ancestor. The damage type associated "
        "with each dragon is used by features you gain later. You can speak, read, and write "
        "Draconic, and when you make a Charisma check interacting with dragons, your proficiency "
        "bonus is doubled if it applies.",
    "draconic resilience":
        "At 1st level, your hit point maximum increases by 1 and increases by 1 again whenever "
        "you gain a level in this class. Additionally, when you aren't wearing armor, your AC "
        "equals 13 + your Dexterity modifier.",
    "elemental affinity":
        "Starting at 6th level, when you cast a spell that deals damage of the type associated "
        "with your draconic ancestry, you can add your Charisma modifier to one damage roll of "
        "that spell. At the same time, you can spend 1 sorcery point to gain resistance to that "
        "damage type for 1 hour.",
    "dragon wings":
        "At 14th level, you gain the ability to sprout a pair of dragon wings from your back as "
        "a bonus action, gaining a flying speed equal to your current walking speed. They last "
        "until you dismiss them as a bonus action. You can't manifest your wings while wearing "
        "armor unless it is made to accommodate them, and clothing not made to accommodate them "
        "might be destroyed.",
    "draconic presence":
        "Beginning at 18th level, you can channel the dread presence of your dragon ancestor, "
        "causing those around you to become awestruck or frightened. As an action, you can spend "
        "5 sorcery points to draw on this power and exude an aura of awe or fear (your choice) "
        "to a distance of 60 feet. For 1 minute or until you lose your concentration (as if "
        "concentrating on a spell), each hostile creature that starts its turn in this aura must "
        "succeed on a Wisdom saving throw or be charmed (if you chose awe) or frightened (if you "
        "chose fear) until the aura ends. A creature that succeeds on this save is immune to your "
        "aura for 24 hours.",

    # ── Sorcerer: Wild Magic (PHB p.103-104) ──
    "wild magic surge":
        "At 1st level, your spellcasting can unleash surges of untamed magic. Immediately after "
        "you cast a sorcerer spell of 1st level or higher, the DM can have you roll a d20. On a "
        "1, roll on the Wild Magic Surge table to create a random magical effect.",
    "tides of chaos":
        "At 1st level, you can manipulate the forces of chance to gain advantage on one attack "
        "roll, ability check, or saving throw. Once used, you must finish a long rest before "
        "using it again. Any time before you regain the use of this feature, the DM can have you "
        "roll on the Wild Magic Surge table immediately after you cast a spell of 1st level or "
        "higher, and you regain the use of this feature.",
    "bend luck":
        "At 6th level, you can twist fate. When another creature you can see makes an attack "
        "roll, ability check, or saving throw, you can use your reaction and spend 2 sorcery "
        "points to roll 1d4 and apply the result as a bonus or penalty (your choice). You can do "
        "so after the creature rolls but before the outcome is determined.",
    "controlled chaos":
        "At 14th level, you gain a modicum of control over your Wild Magic Surges. Whenever you "
        "roll on the Wild Magic Surge table, you can roll twice and choose which effect occurs.",
    "spell bombardment":
        "At 18th level, when you roll damage for a spell and roll the highest number on any of "
        "the dice, choose one of those dice, roll it again, and add that roll to the damage. You "
        "can use this feature only once per turn.",

    # ── Warlock: The Archfey (PHB p.108-109) ──
    "fey presence":
        "At 1st level, you can project the beguiling and fearsome presence of the fey. As an "
        "action, each creature in a 10-foot cube originating from you must make a Wisdom save "
        "against your warlock spell DC. Creatures that fail are charmed or frightened by you "
        "(your choice) until the end of your next turn. Once used, can't be used again until "
        "a short or long rest.",
    "misty escape":
        "At 6th level, when you take damage, you can use your reaction to turn invisible and "
        "teleport up to 60 feet to an unoccupied space you can see. You remain invisible until "
        "the start of your next turn or until you attack or cast a spell. Once used, can't be "
        "used again until a short or long rest.",
    "beguiling defenses":
        "At 10th level, you are immune to being charmed. When another creature attempts to charm "
        "you, you can use your reaction to attempt to turn the charm back. The creature must "
        "succeed on a Wisdom save or be charmed by you for 1 minute or until it takes damage.",
    "dark delirium":
        "At 14th level, you can plunge a creature into an illusory realm. As an action, choose "
        "a creature you can see within 60 feet. It must make a Wisdom save. On a failure, it is "
        "charmed or frightened (your choice) for 1 minute. The creature believes it's lost in a "
        "misty realm whose appearance you choose. It can't see or hear anything more than 5 feet "
        "away. The creature repeats the save at the end of each turn, ending the effect on "
        "success. Once used, can't be used again until a short or long rest.",

    # ── Warlock: The Fiend (PHB p.109) ──
    "dark one's blessing":
        "Starting at 1st level, when you reduce a hostile creature to 0 hit points, you gain "
        "temporary hit points equal to your Charisma modifier + your warlock level (minimum of 1).",
    "dark one's own luck":
        "Starting at 6th level, you can call on your patron to alter fate in your favor. When "
        "you make an ability check or a saving throw, you can add a d10 to your roll. You can "
        "do so after seeing the initial roll but before any of the roll's effects occur. Once "
        "you use this feature, you can't use it again until you finish a short or long rest.",
    "fiendish resilience":
        "Starting at 10th level, you can choose one damage type when you finish a short or long "
        "rest. You gain resistance to that damage type until you choose a different one with "
        "this feature. Damage from magical weapons or silver weapons ignores this resistance.",
    "hurl through hell":
        "Starting at 14th level, when you hit a creature with an attack, you can use this "
        "feature to instantly transport the target through the lower planes. The creature "
        "disappears and hurtles through a nightmare landscape. At the end of your next turn, "
        "the target returns to the space it previously occupied, or the nearest unoccupied "
        "space. If the target is not a fiend, it takes 10d10 psychic damage as it reels from "
        "its horrific experience. Once you use this feature, you can't use it again until you "
        "finish a long rest.",

    # ── Warlock: The Great Old One (PHB p.109-110) ──
    "awakened mind":
        "At 1st level, you can communicate telepathically with any creature you can see within "
        "30 feet. You don't need to share a language, but the creature must be able to understand "
        "at least one language.",
    "entropic ward":
        "At 6th level, you can use your reaction to impose disadvantage on an attack roll against "
        "you. If the attack misses, your next attack roll against that creature has advantage "
        "until the end of your next turn. Once used, can't be used again until a short or long rest.",
    "thought shield":
        "At 10th level, your thoughts can't be read by telepathy or other means unless you allow "
        "it. You gain resistance to psychic damage, and whenever a creature deals psychic damage "
        "to you, it takes the same amount of damage.",
    "create thrall":
        "At 14th level, you can use your action to touch an incapacitated humanoid who becomes "
        "charmed by you until a Remove Curse is cast, the charmed condition is removed, or you "
        "use this feature again. You can communicate telepathically with your thrall as long as "
        "you are on the same plane.",

    # ── Wizard: School of Abjuration (PHB p.115) ──
    "abjuration savant":
        "At 2nd level, the gold and time you must spend to copy an abjuration spell into your "
        "spellbook is halved.",
    "arcane ward":
        "At 2nd level, you can weave magic around yourself for protection. When you cast an "
        "abjuration spell of 1st level or higher, you create a magical ward on yourself lasting "
        "until you finish a long rest. The ward has HP equal to twice your wizard level + your "
        "Intelligence modifier. Whenever you take damage, the ward takes it instead. If reduced "
        "to 0 HP, you take the remaining damage. Whenever you cast an abjuration spell of 1st "
        "level or higher, the ward regains HP equal to twice the spell's level.",
    "projected ward":
        "At 6th level, when a creature you can see within 30 feet takes damage, you can use "
        "your reaction to cause your Arcane Ward to absorb that damage. If the damage reduces "
        "the ward to 0 HP, the warded creature takes the remaining damage.",
    "improved abjuration":
        "At 10th level, when you cast an abjuration spell that requires you to make an ability "
        "check as part of casting (such as Counterspell or Dispel Magic), you add your "
        "proficiency bonus to that check.",
    "spell resistance":
        "At 14th level, you have advantage on saving throws against spells, and you have "
        "resistance against damage from spells.",

    # ── Wizard: School of Conjuration (PHB p.116) ──
    "conjuration savant":
        "At 2nd level, the gold and time you must spend to copy a conjuration spell into your "
        "spellbook is halved.",
    "minor conjuration":
        "At 2nd level, you can use your action to conjure an inanimate object in your hand or "
        "on the ground in an unoccupied space within 10 feet. The object can be no larger than "
        "3 feet on a side and weigh no more than 10 pounds, and its form must be one you've seen. "
        "It is visibly magical, radiating dim light out to 5 feet. It disappears after 1 hour, "
        "when you use this feature again, or if it takes any damage.",
    "benign transposition":
        "At 6th level, you can use your action to teleport up to 30 feet to an unoccupied space "
        "you can see. Alternatively, you can choose a space within range that is occupied by a "
        "Small or Medium creature and swap places with it. Once used, can't be used again until "
        "you cast a conjuration spell of 1st level or higher or finish a long rest.",
    "focused conjuration":
        "At 10th level, while concentrating on a conjuration spell, your concentration can't be "
        "broken as a result of taking damage.",
    "durable summons":
        "At 14th level, any creature that you summon or create with a conjuration spell has 30 "
        "temporary hit points.",

    # ── Wizard: School of Divination (PHB p.116) ──
    "divination savant":
        "At 2nd level, the gold and time you must spend to copy a divination spell into your "
        "spellbook is halved.",
    "portent":
        "At 2nd level, glimpses of the future begin to press in on your awareness. When you "
        "finish a long rest, roll two d20s and record the numbers. You can replace any attack "
        "roll, saving throw, or ability check made by you or a creature you can see with one "
        "of these foretelling rolls. You must choose to do so before the roll. Each roll can "
        "be used only once. When you finish a long rest, you lose any unused rolls.",
    "expert divination":
        "At 6th level, when you cast a divination spell of 2nd level or higher using a spell "
        "slot, you regain one expended spell slot. The slot you regain must be of a level "
        "lower than the spell you cast and can't be higher than 5th level.",
    "the third eye":
        "At 10th level, you can use your action to increase your powers of perception. Choose "
        "one of the following benefits until you are incapacitated or take a short/long rest: "
        "Darkvision 60 ft, See Invisibility (10 ft), See into the Ethereal Plane (60 ft), or "
        "Comprehend Languages (read any written language).",
    "greater portent":
        "At 14th level, you roll three d20s for your Portent feature instead of two.",

    # ── Wizard: School of Enchantment (PHB p.117) ──
    "enchantment savant":
        "At 2nd level, the gold and time you must spend to copy an enchantment spell into your "
        "spellbook is halved.",
    "hypnotic gaze":
        "At 2nd level, you can use your action to choose one creature you can see within 5 feet. "
        "If it can see or hear you, it must succeed on a Wisdom save or be charmed by you until "
        "the end of your next turn. Its speed drops to 0 and it is incapacitated and visibly "
        "dazed. On subsequent turns, you can use your action to maintain this effect, extending "
        "it until the end of your next turn. The effect ends if you move more than 5 feet away, "
        "the creature can neither see nor hear you, or it takes damage. Once the effect ends, "
        "you can't use it on that creature again until a long rest.",
    "instinctive charm":
        "At 6th level, when a creature you can see within 30 feet makes an attack roll against "
        "you, you can use your reaction to divert it, provided another creature is within the "
        "attack's range. The attacker must make a Wisdom save. On a failure, it must target the "
        "nearest creature other than you or itself. Once a creature saves, it's immune until "
        "a long rest.",
    "split enchantment":
        "At 10th level, when you cast an enchantment spell of 1st level or higher that targets "
        "only one creature, you can have it target a second creature instead.",
    "alter memories":
        "At 14th level, when you cast an enchantment spell to charm one or more creatures, you "
        "can make one of them unaware of being charmed. Additionally, once before the spell "
        "expires, you can use your action to make the creature forget some of its time spent "
        "charmed. It must succeed on an Intelligence save or lose a number of hours of memories "
        "equal to 1 + your Charisma modifier (minimum 1).",

    # ── Wizard: School of Evocation (PHB p.117-118) ──
    "evocation savant":
        "At 2nd level, the gold and time you must spend to copy an evocation spell into your "
        "spellbook is halved.",
    "sculpt spells":
        "At 2nd level, you can create pockets of relative safety within your evocation spells. "
        "When you cast an evocation spell that affects other creatures you can see, you can "
        "choose a number of them equal to 1 + the spell's level. The chosen creatures "
        "automatically succeed on their saving throws and take no damage if they would normally "
        "take half on a success.",
    "potent cantrip":
        "At 6th level, your damaging cantrips affect even creatures that avoid the brunt of "
        "the effect. When a creature succeeds on a saving throw against your cantrip, it takes "
        "half the cantrip's damage (if any) but suffers no additional effect.",
    "empowered evocation":
        "At 10th level, you can add your Intelligence modifier (minimum +1) to one damage roll "
        "of any wizard evocation spell you cast.",
    "overchannel":
        "At 14th level, you can increase the power of your simpler spells. When you cast a "
        "wizard spell of 1st through 5th level that deals damage, you can deal maximum damage "
        "with that spell. The first time you do so, you suffer no adverse effect. If you use "
        "this feature again before finishing a long rest, you take 2d12 necrotic damage for each "
        "level of the spell, immediately after casting. Each time you use it again before "
        "finishing a long rest, the necrotic damage per spell level increases by 1d12.",

    # ── Wizard: School of Illusion (PHB p.118) ──
    "illusion savant":
        "At 2nd level, the gold and time you must spend to copy an illusion spell into your "
        "spellbook is halved.",
    "improved minor illusion":
        "At 2nd level, you learn the Minor Illusion cantrip. If you already know it, you learn "
        "a different wizard cantrip. When you cast Minor Illusion, you can create both a sound "
        "and an image with a single casting.",
    "malleable illusions":
        "At 6th level, when you cast an illusion spell that has a duration of 1 minute or "
        "longer, you can use your action to change the nature of that illusion (using the "
        "spell's normal parameters), provided you can see it.",
    "illusory self":
        "At 10th level, you can create an illusory duplicate of yourself as an instant, "
        "almost instinctual reaction to danger. When a creature makes an attack roll against "
        "you, you can use your reaction to interpose the duplicate between you and the attacker. "
        "The attack automatically misses you, then the illusion dissipates. Once used, can't be "
        "used again until a short or long rest.",
    "illusory reality":
        "At 14th level, when you cast an illusion spell of 1st level or higher, you can "
        "choose one inanimate, nonmagical object that is part of the illusion and make that "
        "object real. You can do this on your turn as a bonus action while the spell is ongoing. "
        "The object remains real for 1 minute and can't deal damage or directly harm anyone.",

    # ── Wizard: School of Necromancy (PHB p.118-119) ──
    "necromancy savant":
        "At 2nd level, the gold and time you must spend to copy a necromancy spell into your "
        "spellbook is halved.",
    "grim harvest":
        "At 2nd level, you gain the ability to reap life energy from creatures you kill. Once "
        "per turn, when you kill one or more creatures with a spell of 1st level or higher, you "
        "regain hit points equal to twice the spell's level, or three times if it's a necromancy "
        "spell. You don't gain this benefit for killing constructs or undead.",
    "undead thralls":
        "At 6th level, you add the Animate Dead spell to your spellbook if it's not there. When "
        "you cast Animate Dead, you can target one additional corpse or pile of bones, creating "
        "another zombie or skeleton. Additionally, creatures you create with necromancy spells "
        "add your wizard level to their HP and your proficiency bonus to their weapon damage rolls.",
    "inured to undeath":
        "At 10th level, you have resistance to necrotic damage, and your hit point maximum "
        "can't be reduced.",
    "command undead":
        "At 14th level, you can use magic to bring undead under your control, even those created "
        "by other wizards. As an action, you can choose one undead you can see within 60 feet. "
        "It must make a Charisma save against your wizard spell save DC. If it fails, it becomes "
        "friendly and obeys your commands. Intelligent undead (INT 8+) have advantage. If it has "
        "INT 12+, it can repeat the save at the end of every hour. If you use this feature again, "
        "the prior effect ends.",

    # ── Subclass Spellcasting (Eldritch Knight / Arcane Trickster) ──
    "spellcasting":
        "You gain the ability to cast spells. See the subclass description for your spell list, "
        "cantrips, spells known, and spell slots. Eldritch Knights use the Wizard spell list "
        "(abjuration and evocation primarily); Arcane Tricksters use the Wizard spell list "
        "(enchantment and illusion primarily). Both are one-third casters, gaining spell slots "
        "at half the rate of full casters.",

    # ── Wizard: School of Transmutation (PHB p.119) ──
    "transmutation savant":
        "At 2nd level, the gold and time you must spend to copy a transmutation spell into your "
        "spellbook is halved.",
    "minor alchemy":
        "At 2nd level, you can temporarily alter the physical properties of one nonmagical "
        "object. Perform a special alchemical procedure on an object composed entirely of wood, "
        "stone (but not a gem), iron, copper, or silver, transforming it into a different one "
        "of those materials. For every 10 minutes you spend performing the procedure, you can "
        "transform up to 1 cubic foot of material. After 1 hour, or until you lose concentration "
        "(as if concentrating on a spell), the material reverts.",
    "transmuter's stone":
        "At 6th level, you can spend 8 hours creating a transmuter's stone that stores "
        "transmutation magic. You gain the benefit while holding the stone: darkvision 60 ft, "
        "+10 speed, proficiency in Constitution saves, or resistance to acid/cold/fire/"
        "lightning/thunder (choose one). You can change the benefit when you cast a "
        "transmutation spell of 1st level or higher. If you create a new stone, the old one "
        "ceases to function.",
    "shapechanger":
        "At 10th level, you add the Polymorph spell to your spellbook if it's not there. You "
        "can cast Polymorph without expending a spell slot, but only targeting yourself and "
        "transforming into a beast of CR 1 or lower. Once you do so, can't do it again until "
        "a short or long rest.",
    "master transmuter":
        "At 14th level, you can use your action to consume the reserve of transmutation magic "
        "stored within your transmuter's stone. Choose one: Panacea (remove all curses, diseases, "
        "and poisons; restore all HP), Restore Life (Raise Dead), or Restore Youth (reduce target's "
        "apparent age by 3d10 years, minimum 13). The stone is destroyed. Once used, can't be "
        "used again until a long rest.",

    "a creature of stone and steel":
        "A Bounder subclass feature. Grants a thematic ability tied to the Bounder's specialty — check your sourcebook for full mechanical details.",
    "a light when all other lights go out":
        "A gift of hope and courage — the Warden kindles light in dark places, rallying companions against despair and the Shadow's influence.",
    "accursed specter":
        "Starting at 6th level, you can curse the soul of a person you slay, temporarily binding it to your service. When you slay a humanoid, you can cause its spirit to rise as a specter with temp HP equal to half your warlock level. It obeys your verbal commands and gains a bonus to attack rolls equal to your Charisma modifier. The specter vanishes after your next long rest. 1/long rest.",
    "alchemist spells":
        "An alchemical feature — brewing potent elixirs, identifying compounds, or using alchemical reagents to produce magical effects.",
    "ambush master":
        "A Scout subclass feature. Grants a thematic ability tied to the Scout's specialty — check your sourcebook for full mechanical details.",
    "among the dead":
        "A The Undying subclass feature. Grants a thematic ability tied to the The Undying's specialty — check your sourcebook for full mechanical details.",
    "an end worthy of song":
        "A musical or poetic ability drawn from the rich oral traditions of Middle-earth, inspiring allies and dismaying foes through the power of song.",
    "ancestral protectors":
        "A Path of the Ancestral Guardian subclass feature. Grants a thematic ability tied to the Path of the Ancestral Guardian's specialty — check your sourcebook for full mechanical details.",
    "ancient lore":
        "A Master Scholar subclass feature. Grants a thematic ability tied to the Master Scholar's specialty — check your sourcebook for full mechanical details.",
    "ancient oak":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "animating performance":
        "A College of Creation subclass feature. Grants a thematic ability tied to the College of Creation's specialty — check your sourcebook for full mechanical details.",
    "anticipate":
        "A Counsellor subclass feature. Grants a thematic ability tied to the Counsellor's specialty — check your sourcebook for full mechanical details.",
    "arcane abjuration":
        "A Arcana Domain subclass feature. Grants a thematic ability tied to the Arcana Domain's specialty — check your sourcebook for full mechanical details.",
    "arcane archer lore":
        "A Arcane Archer subclass feature. Grants a thematic ability tied to the Arcane Archer's specialty — check your sourcebook for full mechanical details.",
    "arcane armor":
        "A Armorer subclass feature. Grants a thematic ability tied to the Armorer's specialty — check your sourcebook for full mechanical details.",
    "arcane deflection":
        "A War Magic subclass feature. Grants a thematic ability tied to the War Magic's specialty — check your sourcebook for full mechanical details.",
    "arcane firearm":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "arcane mastery":
        "A Arcana Domain subclass feature. Grants a thematic ability tied to the Arcana Domain's specialty — check your sourcebook for full mechanical details.",
    "arcane shot":
        "A Arcane Archer subclass feature. Grants a thematic ability tied to the Arcane Archer's specialty — check your sourcebook for full mechanical details.",
    "arcane shot options":
        "A Arcane Archer subclass feature. Grants a thematic ability tied to the Arcane Archer's specialty — check your sourcebook for full mechanical details.",
    "armor model":
        "A Armorer subclass feature. Grants a thematic ability tied to the Armorer's specialty — check your sourcebook for full mechanical details.",
    "armor modifications":
        "A Armorer subclass feature. Grants a thematic ability tied to the Armorer's specialty — check your sourcebook for full mechanical details.",
    "armor of hexes":
        "At 10th level, your hex grows more powerful. If the target cursed by your Hexblade's Curse hits you with an attack roll, you can use your reaction to roll a d6. On a 4 or higher, the attack instead misses you, regardless of its roll.",
    "armorer spells":
        "A Armorer subclass feature. Grants a thematic ability tied to the Armorer's specialty — check your sourcebook for full mechanical details.",
    "armoured fury":
        "A combat stance or battle-fury unique to the Foe-Hammer — channeling righteous anger into devastating strikes against the Enemy.",
    "arms of the astral self":
        "A Way of the Astral Self subclass feature. Grants a thematic ability tied to the Way of the Astral Self's specialty — check your sourcebook for full mechanical details.",
    "army of shadow":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "artillerist spells":
        "A Artillerist subclass feature. Grants a thematic ability tied to the Artillerist's specialty — check your sourcebook for full mechanical details.",
    "aura magnification":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "aura of alacrity":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "aura of conquest":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "aura of the guardian":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "aura of the guardian (30 ft.)":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "aura of the sentinel":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "avatar of the wood":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "awakened astral self":
        "A Way of the Astral Self subclass feature. Grants a thematic ability tied to the Way of the Astral Self's specialty — check your sourcebook for full mechanical details.",
    "awakened spellbook":
        "A Order of Scribes subclass feature. Grants a thematic ability tied to the Order of Scribes's specialty — check your sourcebook for full mechanical details.",
    "balm of the summer court":
        "A Circle of Dreams subclass feature. Grants a thematic ability tied to the Circle of Dreams's specialty — check your sourcebook for full mechanical details.",
    "bane":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "barkskin":
        "A Circle of Oaks subclass feature. Grants a thematic ability tied to the Circle of Oaks's specialty — check your sourcebook for full mechanical details.",
    "bastion of law":
        "A Clockwork Soul subclass feature. Grants a thematic ability tied to the Clockwork Soul's specialty — check your sourcebook for full mechanical details.",
    "battle ready":
        "A Battle Smith subclass feature. Grants a thematic ability tied to the Battle Smith's specialty — check your sourcebook for full mechanical details.",
    "battle smith spells":
        "A Battle Smith subclass feature. Grants a thematic ability tied to the Battle Smith's specialty — check your sourcebook for full mechanical details.",
    "battle-fury":
        "A combat stance or battle-fury unique to the Slayer — channeling righteous anger into devastating strikes against the Enemy.",
    "battlerager armor":
        "A Path of the Battlerager subclass feature. Grants a thematic ability tied to the Path of the Battlerager's specialty — check your sourcebook for full mechanical details.",
    "battlerager charge":
        "A Path of the Battlerager subclass feature. Grants a thematic ability tied to the Path of the Battlerager's specialty — check your sourcebook for full mechanical details.",
    "beguiling twist":
        "A Fey Wanderer subclass feature. Grants a thematic ability tied to the Fey Wanderer's specialty — check your sourcebook for full mechanical details.",
    "bestial soul":
        "A Path of the Beast subclass feature. Grants a thematic ability tied to the Path of the Beast's specialty — check your sourcebook for full mechanical details.",
    "birds & beasts":
        "A primal feature connecting to the wild — granting bestial abilities, enhanced senses, or the ability to take on animalistic traits.",
    "black magic":
        "A Umbral Binder subclass feature. Grants a thematic ability tied to the Umbral Binder's specialty — check your sourcebook for full mechanical details.",
    "black mist":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "blade flourish":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "blade song":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "bladesong":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "blazing revival":
        "A Circle of Wildfire subclass feature. Grants a thematic ability tied to the Circle of Wildfire's specialty — check your sourcebook for full mechanical details.",
    "blessed chosen":
        "A Keeper Domain subclass feature. Grants a thematic ability tied to the Keeper Domain's specialty — check your sourcebook for full mechanical details.",
    "blessing of the forge":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "bloodline arcana":
        "A Ooze Bloodline subclass feature. Grants a thematic ability tied to the Ooze Bloodline's specialty — check your sourcebook for full mechanical details.",
    "bob and weave":
        "A Blade Dancer subclass feature. Grants a thematic ability tied to the Blade Dancer's specialty — check your sourcebook for full mechanical details.",
    "body of the astral self":
        "A Way of the Astral Self subclass feature. Grants a thematic ability tied to the Way of the Astral Self's specialty — check your sourcebook for full mechanical details.",
    "bolstering magic":
        "A Path of Wild Magic subclass feature. Grants a thematic ability tied to the Path of Wild Magic's specialty — check your sourcebook for full mechanical details.",
    "bonus cantrips":
        "A The Celestial subclass feature. Grants a thematic ability tied to the The Celestial's specialty — check your sourcebook for full mechanical details.",
    "bonus feat":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "bonus feats":
        "A Ooze Bloodline subclass feature. Grants a thematic ability tied to the Ooze Bloodline's specialty — check your sourcebook for full mechanical details.",
    "bonus spells":
        "A Ooze Bloodline subclass feature. Grants a thematic ability tied to the Ooze Bloodline's specialty — check your sourcebook for full mechanical details.",
    "born to the saddle":
        "A Cavalier subclass feature. Grants a thematic ability tied to the Cavalier's specialty — check your sourcebook for full mechanical details.",
    "bound magic":
        "A Elven High Magic subclass feature. Grants a thematic ability tied to the Elven High Magic's specialty — check your sourcebook for full mechanical details.",
    "break resolve":
        "A Herald subclass feature. Grants a thematic ability tied to the Herald's specialty — check your sourcebook for full mechanical details.",
    "briny murk":
        "A Circle of Fermentation subclass feature. Grants a thematic ability tied to the Circle of Fermentation's specialty — check your sourcebook for full mechanical details.",
    "bulwark":
        "A Purple Dragon Knight subclass feature. Grants a thematic ability tied to the Purple Dragon Knight's specialty — check your sourcebook for full mechanical details.",
    "bulwark of force":
        "A Psi Warrior subclass feature. Grants a thematic ability tied to the Psi Warrior's specialty — check your sourcebook for full mechanical details.",
    "call the hunt":
        "A Path of the Beast subclass feature. Grants a thematic ability tied to the Path of the Beast's specialty — check your sourcebook for full mechanical details.",
    "camouflage":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "campfire tales (d10)":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "campfire tales (d12)":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "campfire tales (d6)":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "campfire tales (d8)":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "canopy":
        "A Spear of the Weald subclass feature. Grants a thematic ability tied to the Spear of the Weald's specialty — check your sourcebook for full mechanical details.",
    "cauterizing flames":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "ceaseless guard":
        "A Bounder subclass feature. Grants a thematic ability tied to the Bounder's specialty — check your sourcebook for full mechanical details.",
    "celestial resilience":
        "A celestial feature channeling starlight and lunar magic — granting radiant damage, divination, or healing under the night sky.",
    "channel divinity":
        "A Oath of Glory subclass feature. Grants a thematic ability tied to the Oath of Glory's specialty — check your sourcebook for full mechanical details.",
    "channel ley line":
        "A Geomancy subclass feature. Grants a thematic ability tied to the Geomancy's specialty — check your sourcebook for full mechanical details.",
    "character improvement":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "charming aura":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "charming presence":
        "A Circle of Roses subclass feature. Grants a thematic ability tied to the Circle of Roses's specialty — check your sourcebook for full mechanical details.",
    "circle of mortality":
        "A Grave Domain subclass feature. Grants a thematic ability tied to the Grave Domain's specialty — check your sourcebook for full mechanical details.",
    "circle of oaks":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "circle of owls":
        "A Circle of Owls subclass feature. Grants a thematic ability tied to the Circle of Owls's specialty — check your sourcebook for full mechanical details.",
    "circle of roses spells":
        "A Circle of Roses subclass feature. Grants a thematic ability tied to the Circle of Roses's specialty — check your sourcebook for full mechanical details.",
    "circle spells":
        "A Circle of Spores subclass feature. Grants a thematic ability tied to the Circle of Spores's specialty — check your sourcebook for full mechanical details.",
    "class skill":
        "A Ooze Bloodline subclass feature. Grants a thematic ability tied to the Ooze Bloodline's specialty — check your sourcebook for full mechanical details.",
    "cloaked dagger":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "clockwork cavalcade":
        "A Clockwork Soul subclass feature. Grants a thematic ability tied to the Clockwork Soul's specialty — check your sourcebook for full mechanical details.",
    "clockwork magic":
        "A Clockwork Soul subclass feature. Grants a thematic ability tied to the Clockwork Soul's specialty — check your sourcebook for full mechanical details.",
    "close combat shot":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "commanding voice":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "compelling words":
        "A Agent subclass feature. Grants a thematic ability tied to the Agent's specialty — check your sourcebook for full mechanical details.",
    "consult the spirits":
        "A Path of the Ancestral Guardian subclass feature. Grants a thematic ability tied to the Path of the Ancestral Guardian's specialty — check your sourcebook for full mechanical details.",
    "consume darkness":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "controlled surge":
        "A Path of Wild Magic subclass feature. Grants a thematic ability tied to the Path of Wild Magic's specialty — check your sourcebook for full mechanical details.",
    "coordinated strikes":
        "A Griffon Scout subclass feature. Grants a thematic ability tied to the Griffon Scout's specialty — check your sourcebook for full mechanical details.",
    "corrosive haze":
        "A Shadow Gnawer subclass feature. Grants a thematic ability tied to the Shadow Gnawer's specialty — check your sourcebook for full mechanical details.",
    "cosmic omen":
        "A Circle of Stars subclass feature. Grants a thematic ability tied to the Circle of Stars's specialty — check your sourcebook for full mechanical details.",
    "cover of night":
        "A Shadow Domain subclass feature. Grants a thematic ability tied to the Shadow Domain's specialty — check your sourcebook for full mechanical details.",
    "creative crescendo":
        "A College of Creation subclass feature. Grants a thematic ability tied to the College of Creation's specialty — check your sourcebook for full mechanical details.",
    "creeping fog":
        "A Shadow Gnawer subclass feature. Grants a thematic ability tied to the Shadow Gnawer's specialty — check your sourcebook for full mechanical details.",
    "cunning action":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "curving shot":
        "A Arcane Archer subclass feature. Grants a thematic ability tied to the Arcane Archer's specialty — check your sourcebook for full mechanical details.",
    "dance of death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "dancing shadows":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "dark inoculation":
        "A Mother of Sorrows subclass feature. Grants a thematic ability tied to the Mother of Sorrows's specialty — check your sourcebook for full mechanical details.",
    "dark knowledge":
        "A Master Scholar subclass feature. Grants a thematic ability tied to the Master Scholar's specialty — check your sourcebook for full mechanical details.",
    "dark servant":
        "A Circle of Shadows subclass feature. Grants a thematic ability tied to the Circle of Shadows's specialty — check your sourcebook for full mechanical details.",
    "dark transfusion":
        "A Shadow Arcane Tradition subclass feature. Grants a thematic ability tied to the Shadow Arcane Tradition's specialty — check your sourcebook for full mechanical details.",
    "darkness falls":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "darkness's embrace":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "dauntless":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "death's friend":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "defence against the shadow":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "deflecting shroud":
        "A War Magic subclass feature. Grants a thematic ability tied to the War Magic's specialty — check your sourcebook for full mechanical details.",
    "defy death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "detect portal":
        "A Horizon Walker subclass feature. Grants a thematic ability tied to the Horizon Walker's specialty — check your sourcebook for full mechanical details.",
    "discourse":
        "A Counsellor subclass feature. Grants a thematic ability tied to the Counsellor's specialty — check your sourcebook for full mechanical details.",
    "distant strike":
        "A Horizon Walker subclass feature. Grants a thematic ability tied to the Horizon Walker's specialty — check your sourcebook for full mechanical details.",
    "distraction":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "divine allegiance":
        "A Oath of the Crown subclass feature. Grants a thematic ability tied to the Oath of the Crown's specialty — check your sourcebook for full mechanical details.",
    "divine fury":
        "A Path of the Zealot subclass feature. Grants a thematic ability tied to the Path of the Zealot's specialty — check your sourcebook for full mechanical details.",
    "divine magic":
        "A Divine Soul subclass feature. Grants a thematic ability tied to the Divine Soul's specialty — check your sourcebook for full mechanical details.",
    "divine strike (2d8)":
        "A Forge Domain subclass feature. Grants a thematic ability tied to the Forge Domain's specialty — check your sourcebook for full mechanical details.",
    "domain spells":
        "A Order Domain subclass feature. Grants a thematic ability tied to the Order Domain's specialty — check your sourcebook for full mechanical details.",
    "dread ambusher":
        "A Gloom Stalker subclass feature. Grants a thematic ability tied to the Gloom Stalker's specialty — check your sourcebook for full mechanical details.",
    "dreadful strikes":
        "A Fey Wanderer subclass feature. Grants a thematic ability tied to the Fey Wanderer's specialty — check your sourcebook for full mechanical details.",
    "dreamland traversal":
        "A Circle of the Weald subclass feature. Grants a thematic ability tied to the Circle of the Weald's specialty — check your sourcebook for full mechanical details.",
    "drunkard's luck":
        "A Way of the Drunken Master subclass feature. Grants a thematic ability tied to the Way of the Drunken Master's specialty — check your sourcebook for full mechanical details.",
    "drunken technique":
        "A Way of the Drunken Master subclass feature. Grants a thematic ability tied to the Way of the Drunken Master's specialty — check your sourcebook for full mechanical details.",
    "durable magic":
        "A War Magic subclass feature. Grants a thematic ability tied to the War Magic's specialty — check your sourcebook for full mechanical details.",
    "duty over death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "ear for deceit":
        "A Inquisitive subclass feature. Grants a thematic ability tied to the Inquisitive's specialty — check your sourcebook for full mechanical details.",
    "effervescence":
        "A Circle of Fermentation subclass feature. Grants a thematic ability tied to the Circle of Fermentation's specialty — check your sourcebook for full mechanical details.",
    "eldritch cannon":
        "A Artillerist subclass feature. Grants a thematic ability tied to the Artillerist's specialty — check your sourcebook for full mechanical details.",
    "elegant courtier":
        "A Samurai subclass feature. Grants a thematic ability tied to the Samurai's specialty — check your sourcebook for full mechanical details.",
    "elegant maneuver":
        "A Swashbuckler subclass feature. Grants a thematic ability tied to the Swashbuckler's specialty — check your sourcebook for full mechanical details.",
    "elemental gift":
        "A Genie subclass feature. Grants a thematic ability tied to the Genie's specialty — check your sourcebook for full mechanical details.",
    "embassy":
        "A Counsellor subclass feature. Grants a thematic ability tied to the Counsellor's specialty — check your sourcebook for full mechanical details.",
    "embodiment of the law":
        "A Order Domain subclass feature. Grants a thematic ability tied to the Order Domain's specialty — check your sourcebook for full mechanical details.",
    "emboldening bond":
        "A Peace Domain subclass feature. Grants a thematic ability tied to the Peace Domain's specialty — check your sourcebook for full mechanical details.",
    "emissary of redemption":
        "A Oath of Redemption subclass feature. Grants a thematic ability tied to the Oath of Redemption's specialty — check your sourcebook for full mechanical details.",
    "empowered healing":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "enchant arrows +1":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "enchant arrows +2":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "enchant arrows +3":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "enchant arrows +4":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "enhanced bond":
        "A Circle of Wildfire subclass feature. Grants a thematic ability tied to the Circle of Wildfire's specialty — check your sourcebook for full mechanical details.",
    "enthralling performance":
        "A College of Glamour subclass feature. Grants a thematic ability tied to the College of Glamour's specialty — check your sourcebook for full mechanical details.",
    "equipment":
        "A Wanderer subclass feature. Grants a thematic ability tied to the Wanderer's specialty — check your sourcebook for full mechanical details.",
    "ethereal step":
        "A Horizon Walker subclass feature. Grants a thematic ability tied to the Horizon Walker's specialty — check your sourcebook for full mechanical details.",
    "evasion":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "ever watchful":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "ever-ready shot":
        "A Arcane Archer subclass feature. Grants a thematic ability tied to the Arcane Archer's specialty — check your sourcebook for full mechanical details.",
    "exalted champion":
        "A Oath of the Crown subclass feature. Grants a thematic ability tied to the Oath of the Crown's specialty — check your sourcebook for full mechanical details.",
    "exit strategy":
        "A Agent subclass feature. Grants a thematic ability tied to the Agent's specialty — check your sourcebook for full mechanical details.",
    "expanded spell list":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "expansive bond":
        "A Peace Domain subclass feature. Grants a thematic ability tied to the Peace Domain's specialty — check your sourcebook for full mechanical details.",
    "experienced explorer":
        "A Peerless Scout subclass feature. Grants a thematic ability tied to the Peerless Scout's specialty — check your sourcebook for full mechanical details.",
    "experimental elixir":
        "An alchemical feature — brewing potent elixirs, identifying compounds, or using alchemical reagents to produce magical effects.",
    "explosive cannon":
        "A Artillerist subclass feature. Grants a thematic ability tied to the Artillerist's specialty — check your sourcebook for full mechanical details.",
    "expression feature":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "eye for detail":
        "A Inquisitive subclass feature. Grants a thematic ability tied to the Inquisitive's specialty — check your sourcebook for full mechanical details.",
    "eye for weakness":
        "A Inquisitive subclass feature. Grants a thematic ability tied to the Inquisitive's specialty — check your sourcebook for full mechanical details.",
    "eyes in the dark":
        "A Umbral Binder subclass feature. Grants a thematic ability tied to the Umbral Binder's specialty — check your sourcebook for full mechanical details.",
    "eyes of night":
        "A Twilight Domain subclass feature. Grants a thematic ability tied to the Twilight Domain's specialty — check your sourcebook for full mechanical details.",
    "eyes of the dark":
        "A Shadow Magic subclass feature. Grants a thematic ability tied to the Shadow Magic's specialty — check your sourcebook for full mechanical details.",
    "eyes of the dark (darkness)":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "eyes of the grave":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "fade to black":
        "A Shadow Domain subclass feature. Grants a thematic ability tied to the Shadow Domain's specialty — check your sourcebook for full mechanical details.",
    "faithful summons":
        "A Circle of the Shepherd subclass feature. Grants a thematic ability tied to the Circle of the Shepherd's specialty — check your sourcebook for full mechanical details.",
    "famed protector":
        "A Knight subclass feature. Grants a thematic ability tied to the Knight's specialty — check your sourcebook for full mechanical details.",
    "fanatical focus":
        "A Path of the Zealot subclass feature. Grants a thematic ability tied to the Path of the Zealot's specialty — check your sourcebook for full mechanical details.",
    "fancy footwork":
        "A Swashbuckler subclass feature. Grants a thematic ability tied to the Swashbuckler's specialty — check your sourcebook for full mechanical details.",
    "fast movement":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "fast-talk":
        "A College of the Arts subclass feature. Grants a thematic ability tied to the College of the Arts's specialty — check your sourcebook for full mechanical details.",
    "fathomless plunge":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "favored by the gods":
        "A Divine Soul subclass feature. Grants a thematic ability tied to the Divine Soul's specialty — check your sourcebook for full mechanical details.",
    "fear of the dark":
        "A College of Shadow subclass feature. Grants a thematic ability tied to the College of Shadow's specialty — check your sourcebook for full mechanical details.",
    "fermentative engine":
        "A Circle of Fermentation subclass feature. Grants a thematic ability tied to the Circle of Fermentation's specialty — check your sourcebook for full mechanical details.",
    "ferocious charger":
        "A Cavalier subclass feature. Grants a thematic ability tied to the Cavalier's specialty — check your sourcebook for full mechanical details.",
    "fey reinforcements":
        "A fey-touched feature — charming, beguiling, or mischievously manipulating enemies with the magic of the Feywild.",
    "fey wanderer magic":
        "A fey-touched feature — charming, beguiling, or mischievously manipulating enemies with the magic of the Feywild.",
    "fighting fit":
        "A Keeper Domain subclass feature. Grants a thematic ability tied to the Keeper Domain's specialty — check your sourcebook for full mechanical details.",
    "fighting spirit":
        "A Samurai subclass feature. Grants a thematic ability tied to the Samurai's specialty — check your sourcebook for full mechanical details.",
    "fighting style":
        "A College of Swords subclass feature. Grants a thematic ability tied to the College of Swords's specialty — check your sourcebook for full mechanical details.",
    "filch":
        "A Burglar subclass feature. Grants a thematic ability tied to the Burglar's specialty — check your sourcebook for full mechanical details.",
    "flickering aura":
        "A persistent aura emanates from you, granting beneficial effects to allies or hindering enemies within a specific radius.",
    "flurry of healing and harm":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "foe of the enemy":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "force of personality":
        "A Way of the Prophet subclass feature. Grants a thematic ability tied to the Way of the Prophet's specialty — check your sourcebook for full mechanical details.",
    "forest's defender":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "form of the beast":
        "A primal feature connecting to the wild — granting bestial abilities, enhanced senses, or the ability to take on animalistic traits.",
    "fortified position":
        "A Artillerist subclass feature. Grants a thematic ability tied to the Artillerist's specialty — check your sourcebook for full mechanical details.",
    "friend to all":
        "A Master Healer subclass feature. Grants a thematic ability tied to the Master Healer's specialty — check your sourcebook for full mechanical details.",
    "from the shadows":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "frost rune":
        "A rune-magic feature — inscribing arcane sigils that grant protective wards, elemental damage, or battlefield control.",
    "full of stars":
        "A celestial feature channeling starlight and lunar magic — granting radiant damage, divination, or healing under the night sky.",
    "fungal body":
        "A Circle of Spores subclass feature. Grants a thematic ability tied to the Circle of Spores's specialty — check your sourcebook for full mechanical details.",
    "fungal infestation":
        "A Circle of Spores subclass feature. Grants a thematic ability tied to the Circle of Spores's specialty — check your sourcebook for full mechanical details.",
    "garden of thorns":
        "A Circle of Roses subclass feature. Grants a thematic ability tied to the Circle of Roses's specialty — check your sourcebook for full mechanical details.",
    "gathered swarm":
        "A Swarmkeeper subclass feature. Grants a thematic ability tied to the Swarmkeeper's specialty — check your sourcebook for full mechanical details.",
    "genie's vessel":
        "A Genie subclass feature. Grants a thematic ability tied to the Genie's specialty — check your sourcebook for full mechanical details.",
    "ghost walk":
        "A Phantom subclass feature. Grants a thematic ability tied to the Phantom's specialty — check your sourcebook for full mechanical details.",
    "giant's might":
        "A Rune Knight subclass feature. Grants a thematic ability tied to the Rune Knight's specialty — check your sourcebook for full mechanical details.",
    "gift of the sea":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "gloom stalker magic":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "glorious defense":
        "A Oath of Glory subclass feature. Grants a thematic ability tied to the Oath of Glory's specialty — check your sourcebook for full mechanical details.",
    "grasping roots":
        "A Circle of Oaks subclass feature. Grants a thematic ability tied to the Circle of Oaks's specialty — check your sourcebook for full mechanical details.",
    "grasping tentacles":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "gray ooze nature":
        "A feature channeling the mutable nature of oozes — granting amorphous movement, acid resistance, or the ability to engulf and dissolve foes.",
    "griffon scout magic":
        "A Griffon Scout subclass feature. Grants a thematic ability tied to the Griffon Scout's specialty — check your sourcebook for full mechanical details.",
    "griffon wings":
        "A Griffon Scout subclass feature. Grants a thematic ability tied to the Griffon Scout's specialty — check your sourcebook for full mechanical details.",
    "grove warden magic":
        "A Grove Warden subclass feature. Grants a thematic ability tied to the Grove Warden's specialty — check your sourcebook for full mechanical details.",
    "grove's avatar":
        "A Grove Warden subclass feature. Grants a thematic ability tied to the Grove Warden's specialty — check your sourcebook for full mechanical details.",
    "grove's blessing":
        "A Grove Warden subclass feature. Grants a thematic ability tied to the Grove Warden's specialty — check your sourcebook for full mechanical details.",
    "grove's sanctuary":
        "A Grove Warden subclass feature. Grants a thematic ability tied to the Grove Warden's specialty — check your sourcebook for full mechanical details.",
    "grove's wrath":
        "A Grove Warden subclass feature. Grants a thematic ability tied to the Grove Warden's specialty — check your sourcebook for full mechanical details.",
    "guarded mind":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "guardian":
        "A Bounder subclass feature. Grants a thematic ability tied to the Bounder's specialty — check your sourcebook for full mechanical details.",
    "guardian coil":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "guardian oak":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "guardian spirit":
        "A Circle of the Shepherd subclass feature. Grants a thematic ability tied to the Circle of the Shepherd's specialty — check your sourcebook for full mechanical details.",
    "halo of spores":
        "A Circle of Spores subclass feature. Grants a thematic ability tied to the Circle of Spores's specialty — check your sourcebook for full mechanical details.",
    "hammerhand":
        "A Foe-Hammer subclass feature. Grants a thematic ability tied to the Foe-Hammer's specialty — check your sourcebook for full mechanical details.",
    "hand of harm":
        "A Way of Mercy subclass feature. Grants a thematic ability tied to the Way of Mercy's specialty — check your sourcebook for full mechanical details.",
    "hand of healing":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "hand of ultimate mercy":
        "A Way of Mercy subclass feature. Grants a thematic ability tied to the Way of Mercy's specialty — check your sourcebook for full mechanical details.",
    "healer’s staunching song":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "healing light":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "heart of the storm":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "hearth of moonlight and shadow":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "heckle":
        "A College of the Arts subclass feature. Grants a thematic ability tied to the College of the Arts's specialty — check your sourcebook for full mechanical details.",
    "hex warrior":
        "At 1st level, you gain proficiency with medium armor, shields, and martial weapons. When you finish a long rest, touch one proficient weapon lacking the two-handed property — use Charisma for attack/damage rolls with it instead of Str/Dex. If you later gain Pact of the Blade, this extends to every pact weapon you conjure.",
    "hexblade's curse":
        "Starting at 1st level, as a bonus action, curse a creature you can see within 30 ft for 1 minute. You gain +proficiency bonus to damage rolls against it. Any attack roll against it is a critical hit on 19-20. If the cursed target dies, you regain HP equal to your warlock level + Cha modifier. 1/short or long rest.",
    "hidden paths":
        "A Circle of Dreams subclass feature. Grants a thematic ability tied to the Circle of Dreams's specialty — check your sourcebook for full mechanical details.",
    "hide in shadows":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "high magic":
        "A Elven High Magic subclass feature. Grants a thematic ability tied to the Elven High Magic's specialty — check your sourcebook for full mechanical details.",
    "hill rune (7th level or higher)":
        "A rune-magic feature — inscribing arcane sigils that grant protective wards, elemental damage, or battlefield control.",
    "hit points":
        "A Wanderer subclass feature. Grants a thematic ability tied to the Wanderer's specialty — check your sourcebook for full mechanical details.",
    "hive mind":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "hive tender magic":
        "A Path of the Hive Tender subclass feature. Grants a thematic ability tied to the Path of the Hive Tender's specialty — check your sourcebook for full mechanical details.",
    "hobbling strike":
        "A Keeper Domain subclass feature. Grants a thematic ability tied to the Keeper Domain's specialty — check your sourcebook for full mechanical details.",
    "hold the line":
        "A Cavalier subclass feature. Grants a thematic ability tied to the Cavalier's specialty — check your sourcebook for full mechanical details.",
    "hooped and hasped":
        "A Foe-Hammer subclass feature. Grants a thematic ability tied to the Foe-Hammer's specialty — check your sourcebook for full mechanical details.",
    "horizon walker magic":
        "A Horizon Walker subclass feature. Grants a thematic ability tied to the Horizon Walker's specialty — check your sourcebook for full mechanical details.",
    "horns wildly blowing":
        "A primal feature connecting to the wild — granting bestial abilities, enhanced senses, or the ability to take on animalistic traits.",
    "hound of ill omen":
        "A Shadow Magic subclass feature. Grants a thematic ability tied to the Shadow Magic's specialty — check your sourcebook for full mechanical details.",
    "hour of reaping":
        "A Way of the Long Death subclass feature. Grants a thematic ability tied to the Way of the Long Death's specialty — check your sourcebook for full mechanical details.",
    "house of the healer":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "hunt domain spells":
        "A Hunt Domain subclass feature. Grants a thematic ability tied to the Hunt Domain's specialty — check your sourcebook for full mechanical details.",
    "hunter's aspect":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "hunter's mark":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "hunter's sense":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "hunter’s blessing":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "implement of peace":
        "A Peace Domain subclass feature. Grants a thematic ability tied to the Peace Domain's specialty — check your sourcebook for full mechanical details.",
    "implements of mercy":
        "A Way of Mercy subclass feature. Grants a thematic ability tied to the Way of Mercy's specialty — check your sourcebook for full mechanical details.",
    "indestructible life":
        "A The Undying subclass feature. Grants a thematic ability tied to the The Undying's specialty — check your sourcebook for full mechanical details.",
    "indomitable might":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "infectious fury":
        "A Path of the Beast subclass feature. Grants a thematic ability tied to the Path of the Beast's specialty — check your sourcebook for full mechanical details.",
    "infectious inspiration":
        "A College of Eloquence subclass feature. Grants a thematic ability tied to the College of Eloquence's specialty — check your sourcebook for full mechanical details.",
    "insightful fighting":
        "A Inquisitive subclass feature. Grants a thematic ability tied to the Inquisitive's specialty — check your sourcebook for full mechanical details.",
    "insightful manipulator":
        "A Mastermind subclass feature. Grants a thematic ability tied to the Mastermind's specialty — check your sourcebook for full mechanical details.",
    "inspiring surge":
        "A Purple Dragon Knight subclass feature. Grants a thematic ability tied to the Purple Dragon Knight's specialty — check your sourcebook for full mechanical details.",
    "intoxicated frenzy":
        "A Way of the Drunken Master subclass feature. Grants a thematic ability tied to the Way of the Drunken Master's specialty — check your sourcebook for full mechanical details.",
    "invincible conqueror":
        "A Oath of Conquest subclass feature. Grants a thematic ability tied to the Oath of Conquest's specialty — check your sourcebook for full mechanical details.",
    "iron mind":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "ironbark":
        "A Circle of Oaks subclass feature. Grants a thematic ability tied to the Circle of Oaks's specialty — check your sourcebook for full mechanical details.",
    "jack of all trades":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "keeper domain spells":
        "A Keeper Domain subclass feature. Grants a thematic ability tied to the Keeper Domain's specialty — check your sourcebook for full mechanical details.",
    "keeper of souls":
        "A Grave Domain subclass feature. Grants a thematic ability tied to the Grave Domain's specialty — check your sourcebook for full mechanical details.",
    "lengthen shadow":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "ley line adept":
        "A Geomancy subclass feature. Grants a thematic ability tied to the Geomancy's specialty — check your sourcebook for full mechanical details.",
    "ley line manipulation":
        "A Geomancy subclass feature. Grants a thematic ability tied to the Geomancy's specialty — check your sourcebook for full mechanical details.",
    "ley line mastery":
        "A Geomancy subclass feature. Grants a thematic ability tied to the Geomancy's specialty — check your sourcebook for full mechanical details.",
    "ley line savant":
        "A Geomancy subclass feature. Grants a thematic ability tied to the Geomancy's specialty — check your sourcebook for full mechanical details.",
    "lightfoot":
        "A gift of hope and courage — the Elven Archer kindles light in dark places, rallying companions against despair and the Shadow's influence.",
    "limited wish":
        "A Genie subclass feature. Grants a thematic ability tied to the Genie's specialty — check your sourcebook for full mechanical details.",
    "living legend":
        "A Oath of Glory subclass feature. Grants a thematic ability tied to the Oath of Glory's specialty — check your sourcebook for full mechanical details.",
    "magic arrow":
        "A Arcane Archer subclass feature. Grants a thematic ability tied to the Arcane Archer's specialty — check your sourcebook for full mechanical details.",
    "magic awareness":
        "A Path of Wild Magic subclass feature. Grants a thematic ability tied to the Path of Wild Magic's specialty — check your sourcebook for full mechanical details.",
    "magic-user's nemesis":
        "A Monster Slayer subclass feature. Grants a thematic ability tied to the Monster Slayer's specialty — check your sourcebook for full mechanical details.",
    "manifest mind":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "mantle of inspiration":
        "A College of Glamour subclass feature. Grants a thematic ability tied to the College of Glamour's specialty — check your sourcebook for full mechanical details.",
    "mantle of majesty":
        "A College of Glamour subclass feature. Grants a thematic ability tied to the College of Glamour's specialty — check your sourcebook for full mechanical details.",
    "mantle of whispers":
        "A College of Whispers subclass feature. Grants a thematic ability tied to the College of Whispers's specialty — check your sourcebook for full mechanical details.",
    "marks of honour":
        "A Knight subclass feature. Grants a thematic ability tied to the Knight's specialty — check your sourcebook for full mechanical details.",
    "marrowbark form":
        "A Circle of the Weald subclass feature. Grants a thematic ability tied to the Circle of the Weald's specialty — check your sourcebook for full mechanical details.",
    "master duelist":
        "A Swashbuckler subclass feature. Grants a thematic ability tied to the Swashbuckler's specialty — check your sourcebook for full mechanical details.",
    "master healer herbs":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "master hunter":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "master hunter (second choice)":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "master of hexes":
        "Starting at 14th level, you can spread your Hexblade's Curse from a slain creature to another. When the cursed target dies, as a bonus action apply the curse to a different creature you can see within 30 ft. You don't regain HP from the previous target's death.",
    "master of intrigue":
        "A Mastermind subclass feature. Grants a thematic ability tied to the Mastermind's specialty — check your sourcebook for full mechanical details.",
    "master of lies":
        "A College of the Arts subclass feature. Grants a thematic ability tied to the College of the Arts's specialty — check your sourcebook for full mechanical details.",
    "master of tactics":
        "A Mastermind subclass feature. Grants a thematic ability tied to the Mastermind's specialty — check your sourcebook for full mechanical details.",
    "master of the hunt":
        "A Hunt Domain subclass feature. Grants a thematic ability tied to the Hunt Domain's specialty — check your sourcebook for full mechanical details.",
    "master of the night":
        "A Circle of Owls subclass feature. Grants a thematic ability tied to the Circle of Owls's specialty — check your sourcebook for full mechanical details.",
    "master scrivener":
        "A Order of Scribes subclass feature. Grants a thematic ability tied to the Order of Scribes's specialty — check your sourcebook for full mechanical details.",
    "master's flourish":
        "A College of Swords subclass feature. Grants a thematic ability tied to the College of Swords's specialty — check your sourcebook for full mechanical details.",
    "masteries":
        "A Weaponmaster subclass feature. Grants a thematic ability tied to the Weaponmaster's specialty — check your sourcebook for full mechanical details.",
    "mastery of death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "mighty spear-throw":
        "A The Rider subclass feature. Grants a thematic ability tied to the The Rider's specialty — check your sourcebook for full mechanical details.",
    "mighty summoner":
        "A Circle of the Shepherd subclass feature. Grants a thematic ability tied to the Circle of the Shepherd's specialty — check your sourcebook for full mechanical details.",
    "mighty swarm":
        "A Swarmkeeper subclass feature. Grants a thematic ability tied to the Swarmkeeper's specialty — check your sourcebook for full mechanical details.",
    "misdirection":
        "A Mastermind subclass feature. Grants a thematic ability tied to the Mastermind's specialty — check your sourcebook for full mechanical details.",
    "misty wanderer":
        "A Fey Wanderer subclass feature. Grants a thematic ability tied to the Fey Wanderer's specialty — check your sourcebook for full mechanical details.",
    "monster slayer magic":
        "A Monster Slayer subclass feature. Grants a thematic ability tied to the Monster Slayer's specialty — check your sourcebook for full mechanical details.",
    "mortal bulwark":
        "A Oath of the Watchers subclass feature. Grants a thematic ability tied to the Oath of the Watchers's specialty — check your sourcebook for full mechanical details.",
    "mortal wound (1 die)":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "mortal wound (2 dice)":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "mortal wound (3 dice)":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "mote of potential":
        "A College of Creation subclass feature. Grants a thematic ability tied to the College of Creation's specialty — check your sourcebook for full mechanical details.",
    "mother's gift":
        "A Circle of Fermentation subclass feature. Grants a thematic ability tied to the Circle of Fermentation's specialty — check your sourcebook for full mechanical details.",
    "mounted combat":
        "A The Rider subclass feature. Grants a thematic ability tied to the The Rider's specialty — check your sourcebook for full mechanical details.",
    "mounted scout":
        "A Griffon Scout subclass feature. Grants a thematic ability tied to the Griffon Scout's specialty — check your sourcebook for full mechanical details.",
    "mucus spray":
        "A Ooze School subclass feature. Grants a thematic ability tied to the Ooze School's specialty — check your sourcebook for full mechanical details.",
    "multitudinous arrows":
        "A Courser Mage subclass feature. Grants a thematic ability tied to the Courser Mage's specialty — check your sourcebook for full mechanical details.",
    "natural world":
        "A Master Scholar subclass feature. Grants a thematic ability tied to the Master Scholar's specialty — check your sourcebook for full mechanical details.",
    "nature's endurance":
        "A The Old Wood subclass feature. Grants a thematic ability tied to the The Old Wood's specialty — check your sourcebook for full mechanical details.",
    "night music":
        "A College of Shadow subclass feature. Grants a thematic ability tied to the College of Shadow's specialty — check your sourcebook for full mechanical details.",
    "night vision":
        "A Circle of Owls subclass feature. Grants a thematic ability tied to the Circle of Owls's specialty — check your sourcebook for full mechanical details.",
    "nor weariness, nor endless barren miles":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "oaken vitality":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "oath of glory spells":
        "A Oath of Glory subclass feature. Grants a thematic ability tied to the Oath of Glory's specialty — check your sourcebook for full mechanical details.",
    "oath spells":
        "A Oath of the Watchers subclass feature. Grants a thematic ability tied to the Oath of the Watchers's specialty — check your sourcebook for full mechanical details.",
    "obfuscation":
        "A Burglar subclass feature. Grants a thematic ability tied to the Burglar's specialty — check your sourcebook for full mechanical details.",
    "oceanic soul":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "one with the blade":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "one with the word":
        "A Order of Scribes subclass feature. Grants a thematic ability tied to the Order of Scribes's specialty — check your sourcebook for full mechanical details.",
    "ooze form":
        "A feature channeling the mutable nature of oozes — granting amorphous movement, acid resistance, or the ability to engulf and dissolve foes.",
    "ooze mind":
        "A feature channeling the mutable nature of oozes — granting amorphous movement, acid resistance, or the ability to engulf and dissolve foes.",
    "orb of night":
        "A Shadow Arcane Tradition subclass feature. Grants a thematic ability tied to the Shadow Arcane Tradition's specialty — check your sourcebook for full mechanical details.",
    "order's wrath":
        "A Order Domain subclass feature. Grants a thematic ability tied to the Order Domain's specialty — check your sourcebook for full mechanical details.",
    "otherworldly glamour":
        "A Fey Wanderer subclass feature. Grants a thematic ability tied to the Fey Wanderer's specialty — check your sourcebook for full mechanical details.",
    "otherworldly wings":
        "A Divine Soul subclass feature. Grants a thematic ability tied to the Divine Soul's specialty — check your sourcebook for full mechanical details.",
    "overwhelm":
        "A Spear of the Weald subclass feature. Grants a thematic ability tied to the Spear of the Weald's specialty — check your sourcebook for full mechanical details.",
    "owl's wisdom":
        "A Circle of Owls subclass feature. Grants a thematic ability tied to the Circle of Owls's specialty — check your sourcebook for full mechanical details.",
    "panache":
        "A Swashbuckler subclass feature. Grants a thematic ability tied to the Swashbuckler's specialty — check your sourcebook for full mechanical details.",
    "parry":
        "A Blade Dancer subclass feature. Grants a thematic ability tied to the Blade Dancer's specialty — check your sourcebook for full mechanical details.",
    "path of the kensei":
        "A Way of the Kensei subclass feature. Grants a thematic ability tied to the Way of the Kensei's specialty — check your sourcebook for full mechanical details.",
    "perfected armor":
        "A Armorer subclass feature. Grants a thematic ability tied to the Armorer's specialty — check your sourcebook for full mechanical details.",
    "performance of creation":
        "A College of Creation subclass feature. Grants a thematic ability tied to the College of Creation's specialty — check your sourcebook for full mechanical details.",
    "physician's touch":
        "A Way of Mercy subclass feature. Grants a thematic ability tied to the Way of Mercy's specialty — check your sourcebook for full mechanical details.",
    "pierced by many arrows":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "pinpoint weakness":
        "A Courser Mage subclass feature. Grants a thematic ability tied to the Courser Mage's specialty — check your sourcebook for full mechanical details.",
    "planar warrior":
        "A Horizon Walker subclass feature. Grants a thematic ability tied to the Horizon Walker's specialty — check your sourcebook for full mechanical details.",
    "poison soul":
        "A Mother of Sorrows subclass feature. Grants a thematic ability tied to the Mother of Sorrows's specialty — check your sourcebook for full mechanical details.",
    "power surge":
        "A War Magic subclass feature. Grants a thematic ability tied to the War Magic's specialty — check your sourcebook for full mechanical details.",
    "precision +1d6":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "precision +2d6":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "precision +3d6":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "precision 4d6":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "predator's mark":
        "A The Hunter in Darkness subclass feature. Grants a thematic ability tied to the The Hunter in Darkness's specialty — check your sourcebook for full mechanical details.",
    "predator's senses":
        "A Hunt Domain subclass feature. Grants a thematic ability tied to the Hunt Domain's specialty — check your sourcebook for full mechanical details.",
    "predatory grace":
        "A The Old Wood subclass feature. Grants a thematic ability tied to the The Old Wood's specialty — check your sourcebook for full mechanical details.",
    "preferred target":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "preternatural augment":
        "A Peerless Scout subclass feature. Grants a thematic ability tied to the Peerless Scout's specialty — check your sourcebook for full mechanical details.",
    "proficiencies":
        "A Wanderer subclass feature. Grants a thematic ability tied to the Wanderer's specialty — check your sourcebook for full mechanical details.",
    "protective bond":
        "A Peace Domain subclass feature. Grants a thematic ability tied to the Peace Domain's specialty — check your sourcebook for full mechanical details.",
    "protective spirit":
        "A Oath of Redemption subclass feature. Grants a thematic ability tied to the Oath of Redemption's specialty — check your sourcebook for full mechanical details.",
    "psionic power":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "psionic sorcery":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "psionic spells":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "psychic blades":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "psychic defenses":
        "A Aberrant Mind subclass feature. Grants a thematic ability tied to the Aberrant Mind's specialty — check your sourcebook for full mechanical details.",
    "psychic veil":
        "A Soulknife subclass feature. Grants a thematic ability tied to the Soulknife's specialty — check your sourcebook for full mechanical details.",
    "radiant soul":
        "A The Celestial subclass feature. Grants a thematic ability tied to the The Celestial's specialty — check your sourcebook for full mechanical details.",
    "radiant sun bolt":
        "A Way of the Sun Soul subclass feature. Grants a thematic ability tied to the Way of the Sun Soul's specialty — check your sourcebook for full mechanical details.",
    "rage beyond death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "raging storm":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "rakish audacity":
        "A Swashbuckler subclass feature. Grants a thematic ability tied to the Swashbuckler's specialty — check your sourcebook for full mechanical details.",
    "rallying cry":
        "A Purple Dragon Knight subclass feature. Grants a thematic ability tied to the Purple Dragon Knight's specialty — check your sourcebook for full mechanical details.",
    "rapid strike":
        "A Samurai subclass feature. Grants a thematic ability tied to the Samurai's specialty — check your sourcebook for full mechanical details.",
    "reckless abandon":
        "A Path of the Battlerager subclass feature. Grants a thematic ability tied to the Path of the Battlerager's specialty — check your sourcebook for full mechanical details.",
    "reckless attack":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "refraction shield":
        "A Light Weaver subclass feature. Grants a thematic ability tied to the Light Weaver's specialty — check your sourcebook for full mechanical details.",
    "reliable talent":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "relief from long burdens":
        "A Master Healer subclass feature. Grants a thematic ability tied to the Master Healer's specialty — check your sourcebook for full mechanical details.",
    "rend mind":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "restore balance":
        "A healing feature — restoring hit points, removing conditions, or granting protective wards to allies in need.",
    "restriction: alseid":
        "A Spear of the Weald subclass feature. Grants a thematic ability tied to the Spear of the Weald's specialty — check your sourcebook for full mechanical details.",
    "restriction: dwarves only":
        "A Path of the Battlerager subclass feature. Grants a thematic ability tied to the Path of the Battlerager's specialty — check your sourcebook for full mechanical details.",
    "revelation in flesh":
        "A Aberrant Mind subclass feature. Grants a thematic ability tied to the Aberrant Mind's specialty — check your sourcebook for full mechanical details.",
    "revenge":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "riddling words":
        "A Agent subclass feature. Grants a thematic ability tied to the Agent's specialty — check your sourcebook for full mechanical details.",
    "righteous strike":
        "A Way of the Prophet subclass feature. Grants a thematic ability tied to the Way of the Prophet's specialty — check your sourcebook for full mechanical details.",
    "ritual focus":
        "A Elven High Magic subclass feature. Grants a thematic ability tied to the Elven High Magic's specialty — check your sourcebook for full mechanical details.",
    "ritual master":
        "A Elven High Magic subclass feature. Grants a thematic ability tied to the Elven High Magic's specialty — check your sourcebook for full mechanical details.",
    "ritual savant":
        "A Elven High Magic subclass feature. Grants a thematic ability tied to the Elven High Magic's specialty — check your sourcebook for full mechanical details.",
    "rose's embrace":
        "A Circle of Roses subclass feature. Grants a thematic ability tied to the Circle of Roses's specialty — check your sourcebook for full mechanical details.",
    "royal envoy":
        "A Purple Dragon Knight subclass feature. Grants a thematic ability tied to the Purple Dragon Knight's specialty — check your sourcebook for full mechanical details.",
    "run to ground":
        "A Hunter of Beasts subclass feature. Grants a thematic ability tied to the Hunter of Beasts's specialty — check your sourcebook for full mechanical details.",
    "runes":
        "A rune-magic feature — inscribing arcane sigils that grant protective wards, elemental damage, or battlefield control.",
    "runic shield":
        "A Rune Knight subclass feature. Grants a thematic ability tied to the Rune Knight's specialty — check your sourcebook for full mechanical details.",
    "sacrifice":
        "A Knight subclass feature. Grants a thematic ability tied to the Knight's specialty — check your sourcebook for full mechanical details.",
    "saint of forge and fire":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "sanctuary vessel":
        "A Genie subclass feature. Grants a thematic ability tied to the Genie's specialty — check your sourcebook for full mechanical details.",
    "scornful rebuke":
        "A Oath of Conquest subclass feature. Grants a thematic ability tied to the Oath of Conquest's specialty — check your sourcebook for full mechanical details.",
    "searing arc strike":
        "A Way of the Sun Soul subclass feature. Grants a thematic ability tied to the Way of the Sun Soul's specialty — check your sourcebook for full mechanical details.",
    "searing sunburst":
        "A Way of the Sun Soul subclass feature. Grants a thematic ability tied to the Way of the Sun Soul's specialty — check your sourcebook for full mechanical details.",
    "searing vengeance":
        "A The Celestial subclass feature. Grants a thematic ability tied to the The Celestial's specialty — check your sourcebook for full mechanical details.",
    "second skin":
        "A Shadow Arcane Tradition subclass feature. Grants a thematic ability tied to the Shadow Arcane Tradition's specialty — check your sourcebook for full mechanical details.",
    "secret lores":
        "A Master Scholar subclass feature. Grants a thematic ability tied to the Master Scholar's specialty — check your sourcebook for full mechanical details.",
    "secrets gleaned":
        "A Agent subclass feature. Grants a thematic ability tied to the Agent's specialty — check your sourcebook for full mechanical details.",
    "seen and unseen":
        "A Master Healer subclass feature. Grants a thematic ability tied to the Master Healer's specialty — check your sourcebook for full mechanical details.",
    "sentinel at death's door":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "shade step":
        "A College of Shadow subclass feature. Grants a thematic ability tied to the College of Shadow's specialty — check your sourcebook for full mechanical details.",
    "shadow bind":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow chewer":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow devourer":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow domain spells":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow grasp":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow killer":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow lore":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow mass":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow smoke":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow symbiote":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow walk":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadow weakness":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadowy dodge":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shadowy resilience":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "shielding storm":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "sickening revenge":
        "A Mother of Sorrows subclass feature. Grants a thematic ability tied to the Mother of Sorrows's specialty — check your sourcebook for full mechanical details.",
    "silent flight":
        "A Circle of Owls subclass feature. Grants a thematic ability tied to the Circle of Owls's specialty — check your sourcebook for full mechanical details.",
    "silver tongue":
        "A College of Eloquence subclass feature. Grants a thematic ability tied to the College of Eloquence's specialty — check your sourcebook for full mechanical details.",
    "situational awareness: impromptu ambush":
        "A Peerless Scout subclass feature. Grants a thematic ability tied to the Peerless Scout's specialty — check your sourcebook for full mechanical details.",
    "situational awareness: master ambusher":
        "A Peerless Scout subclass feature. Grants a thematic ability tied to the Peerless Scout's specialty — check your sourcebook for full mechanical details.",
    "skirmisher":
        "A Scout subclass feature. Grants a thematic ability tied to the Scout's specialty — check your sourcebook for full mechanical details.",
    "skirmisher’s step":
        "A Griffon Scout subclass feature. Grants a thematic ability tied to the Griffon Scout's specialty — check your sourcebook for full mechanical details.",
    "slayer path":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "slayer's counter":
        "A Monster Slayer subclass feature. Grants a thematic ability tied to the Monster Slayer's specialty — check your sourcebook for full mechanical details.",
    "slayer's prey":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "slippery mind":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "sneak attack":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "softer underneath":
        "A Master Healer subclass feature. Grants a thematic ability tied to the Master Healer's specialty — check your sourcebook for full mechanical details.",
    "song of defense":
        "A Bladesinging subclass feature. Grants a thematic ability tied to the Bladesinging's specialty — check your sourcebook for full mechanical details.",
    "song of victory":
        "A Bladesinging subclass feature. Grants a thematic ability tied to the Bladesinging's specialty — check your sourcebook for full mechanical details.",
    "songs of slaying":
        "A musical or poetic ability drawn from the rich oral traditions of Middle-earth, inspiring allies and dismaying foes through the power of song.",
    "soul blades":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "soul of deceit":
        "A Mastermind subclass feature. Grants a thematic ability tied to the Mastermind's specialty — check your sourcebook for full mechanical details.",
    "soul of the forge":
        "A forge-and-flame feature — enhancing armor and weapons with elemental fire, granting fire resistance, or dealing bonus fire damage.",
    "spectral defense":
        "A Horizon Walker subclass feature. Grants a thematic ability tied to the Horizon Walker's specialty — check your sourcebook for full mechanical details.",
    "spell arrow":
        "A Courser Mage subclass feature. Grants a thematic ability tied to the Courser Mage's specialty — check your sourcebook for full mechanical details.",
    "spell blind":
        "A Light Weaver subclass feature. Grants a thematic ability tied to the Light Weaver's specialty — check your sourcebook for full mechanical details.",
    "spell breaker":
        "A Arcana Domain subclass feature. Grants a thematic ability tied to the Arcana Domain's specialty — check your sourcebook for full mechanical details.",
    "spiked retribution":
        "A Path of the Battlerager subclass feature. Grants a thematic ability tied to the Path of the Battlerager's specialty — check your sourcebook for full mechanical details.",
    "spirit shield (2d8)":
        "A Path of the Ancestral Guardian subclass feature. Grants a thematic ability tied to the Path of the Ancestral Guardian's specialty — check your sourcebook for full mechanical details.",
    "spirit shield (3d8)":
        "A Path of the Ancestral Guardian subclass feature. Grants a thematic ability tied to the Path of the Ancestral Guardian's specialty — check your sourcebook for full mechanical details.",
    "spirit shield (4d8)":
        "A Path of the Ancestral Guardian subclass feature. Grants a thematic ability tied to the Path of the Ancestral Guardian's specialty — check your sourcebook for full mechanical details.",
    "spirit totem":
        "A Circle of the Shepherd subclass feature. Grants a thematic ability tied to the Circle of the Shepherd's specialty — check your sourcebook for full mechanical details.",
    "splintered spears & shattered shields":
        "A Foe-Hammer subclass feature. Grants a thematic ability tied to the Foe-Hammer's specialty — check your sourcebook for full mechanical details.",
    "split":
        "A Ooze School subclass feature. Grants a thematic ability tied to the Ooze School's specialty — check your sourcebook for full mechanical details.",
    "spreading spores":
        "A Circle of Spores subclass feature. Grants a thematic ability tied to the Circle of Spores's specialty — check your sourcebook for full mechanical details.",
    "stalker's flurry":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "stalker's pounce":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "stalking savant":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "stand against the tide":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "star map":
        "A celestial feature channeling starlight and lunar magic — granting radiant damage, divination, or healing under the night sky.",
    "starry form":
        "A celestial feature channeling starlight and lunar magic — granting radiant damage, divination, or healing under the night sky.",
    "steady eye":
        "A Inquisitive subclass feature. Grants a thematic ability tied to the Inquisitive's specialty — check your sourcebook for full mechanical details.",
    "steel defender":
        "A Battle Smith subclass feature. Grants a thematic ability tied to the Battle Smith's specialty — check your sourcebook for full mechanical details.",
    "steps of night":
        "A Twilight Domain subclass feature. Grants a thematic ability tied to the Twilight Domain's specialty — check your sourcebook for full mechanical details.",
    "steps of the forest god":
        "A nature-focused feature — drawing power from ancient trees and the deep forest for protection, healing, or primal magic.",
    "stone rune":
        "A rune-magic feature — inscribing arcane sigils that grant protective wards, elemental damage, or battlefield control.",
    "storm aura":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "storm guide":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "storm rune (7th level or higher)":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "storm soul":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "storm's fury":
        "A storm-themed feature — calling lightning from the sky, surrounding yourself with thunderous energy, or unleashing gale-force winds.",
    "strength before death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "strength greater than any hand":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "strength of the grave":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "strike and fade":
        "A Griffon Scout subclass feature. Grants a thematic ability tied to the Griffon Scout's specialty — check your sourcebook for full mechanical details.",
    "style focus":
        "A Weaponmaster subclass feature. Grants a thematic ability tied to the Weaponmaster's specialty — check your sourcebook for full mechanical details.",
    "sudden strike":
        "A Scout subclass feature. Grants a thematic ability tied to the Scout's specialty — check your sourcebook for full mechanical details.",
    "summon wildfire spirit":
        "A primal feature connecting to the wild — granting bestial abilities, enhanced senses, or the ability to take on animalistic traits.",
    "sun shield":
        "A Way of the Sun Soul subclass feature. Grants a thematic ability tied to the Way of the Sun Soul's specialty — check your sourcebook for full mechanical details.",
    "superior mobility":
        "A Scout subclass feature. Grants a thematic ability tied to the Scout's specialty — check your sourcebook for full mechanical details.",
    "superior two-weapon fighting":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "supernatural defense":
        "A Monster Slayer subclass feature. Grants a thematic ability tied to the Monster Slayer's specialty — check your sourcebook for full mechanical details.",
    "survivalist":
        "A Scout subclass feature. Grants a thematic ability tied to the Scout's specialty — check your sourcebook for full mechanical details.",
    "swarm of bees":
        "A Path of the Hive Tender subclass feature. Grants a thematic ability tied to the Path of the Hive Tender's specialty — check your sourcebook for full mechanical details.",
    "swarm of hornets":
        "A Path of the Hive Tender subclass feature. Grants a thematic ability tied to the Path of the Hive Tender's specialty — check your sourcebook for full mechanical details.",
    "swarm of wasps":
        "A Path of the Hive Tender subclass feature. Grants a thematic ability tied to the Path of the Hive Tender's specialty — check your sourcebook for full mechanical details.",
    "swarming dispersal":
        "A Swarmkeeper subclass feature. Grants a thematic ability tied to the Swarmkeeper's specialty — check your sourcebook for full mechanical details.",
    "swarmkeeper magic":
        "A Swarmkeeper subclass feature. Grants a thematic ability tied to the Swarmkeeper's specialty — check your sourcebook for full mechanical details.",
    "swift shot":
        "A Hunter of Beasts subclass feature. Grants a thematic ability tied to the Hunter of Beasts's specialty — check your sourcebook for full mechanical details.",
    "swift tracker":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "sworn defender":
        "A Knight subclass feature. Grants a thematic ability tied to the Knight's specialty — check your sourcebook for full mechanical details.",
    "symbiotic entity":
        "A Circle of Spores subclass feature. Grants a thematic ability tied to the Circle of Spores's specialty — check your sourcebook for full mechanical details.",
    "tactical wit":
        "A War Magic subclass feature. Grants a thematic ability tied to the War Magic's specialty — check your sourcebook for full mechanical details.",
    "take aim":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "talented":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "telekinetic adept":
        "A Psi Warrior subclass feature. Grants a thematic ability tied to the Psi Warrior's specialty — check your sourcebook for full mechanical details.",
    "telekinetic master":
        "A Psi Warrior subclass feature. Grants a thematic ability tied to the Psi Warrior's specialty — check your sourcebook for full mechanical details.",
    "telepathic speech":
        "A psionic feature — reading thoughts, establishing telepathic links, or assaulting foes with psychic damage.",
    "tempestuous magic":
        "A Storm Sorcery subclass feature. Grants a thematic ability tied to the Storm Sorcery's specialty — check your sourcebook for full mechanical details.",
    "tentacle of the deeps":
        "A The Fathomless subclass feature. Grants a thematic ability tied to the The Fathomless's specialty — check your sourcebook for full mechanical details.",
    "the shadow of my pockets":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "the weapons of the enemy":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
    "there many foes he fought":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "thieves' cant":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "thorny whip":
        "A Circle of Roses subclass feature. Grants a thematic ability tied to the Circle of Roses's specialty — check your sourcebook for full mechanical details.",
    "threatening shot":
        "A Elven Archer subclass feature. Grants a thematic ability tied to the Elven Archer's specialty — check your sourcebook for full mechanical details.",
    "tipsy sway":
        "A Way of the Drunken Master subclass feature. Grants a thematic ability tied to the Way of the Drunken Master's specialty — check your sourcebook for full mechanical details.",
    "tireless spirit":
        "A Samurai subclass feature. Grants a thematic ability tied to the Samurai's specialty — check your sourcebook for full mechanical details.",
    "tokens of the departed":
        "A Phantom subclass feature. Grants a thematic ability tied to the Phantom's specialty — check your sourcebook for full mechanical details.",
    "tool proficiency":
        "A Alchemist subclass feature. Grants a thematic ability tied to the Alchemist's specialty — check your sourcebook for full mechanical details.",
    "tools of the trade":
        "A Armorer subclass feature. Grants a thematic ability tied to the Armorer's specialty — check your sourcebook for full mechanical details.",
    "touch of death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "touch of sorrow":
        "A Mother of Sorrows subclass feature. Grants a thematic ability tied to the Mother of Sorrows's specialty — check your sourcebook for full mechanical details.",
    "touch of the bright land":
        "A Spear of the Weald subclass feature. Grants a thematic ability tied to the Spear of the Weald's specialty — check your sourcebook for full mechanical details.",
    "touch of the long death":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "touch of zymurgy":
        "A Circle of Fermentation subclass feature. Grants a thematic ability tied to the Circle of Fermentation's specialty — check your sourcebook for full mechanical details.",
    "track":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "tracker":
        "A hunter's technique — marking quarry, tracking with supernatural precision, or gaining combat bonuses against chosen prey.",
    "training in war and song":
        "A Bladesinging subclass feature. Grants a thematic ability tied to the Bladesinging's specialty — check your sourcebook for full mechanical details.",
    "trance of order":
        "A Clockwork Soul subclass feature. Grants a thematic ability tied to the Clockwork Soul's specialty — check your sourcebook for full mechanical details.",
    "treasure lore":
        "A Burglar subclass feature. Grants a thematic ability tied to the Burglar's specialty — check your sourcebook for full mechanical details.",
    "trick of the light":
        "A Light Weaver subclass feature. Grants a thematic ability tied to the Light Weaver's specialty — check your sourcebook for full mechanical details.",
    "twilight shroud":
        "A Twilight Domain subclass feature. Grants a thematic ability tied to the Twilight Domain's specialty — check your sourcebook for full mechanical details.",
    "twinkling constellations":
        "A Circle of Stars subclass feature. Grants a thematic ability tied to the Circle of Stars's specialty — check your sourcebook for full mechanical details.",
    "umbral form":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "umbral sight":
        "A shadow-magic feature — manipulating darkness to obscure, teleport between shadows, or strike from hidden places.",
    "unarmoured defence":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "unbreakable majesty":
        "A College of Glamour subclass feature. Grants a thematic ability tied to the College of Glamour's specialty — check your sourcebook for full mechanical details.",
    "unbreakable will":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "uncanny dodge":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "underfoot":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "underfoot escape":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "underfoot mastery":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "underfoot tactics":
        "A The Underfoot subclass feature. Grants a thematic ability tied to the The Underfoot's specialty — check your sourcebook for full mechanical details.",
    "unearthly recovery":
        "A Divine Soul subclass feature. Grants a thematic ability tied to the Divine Soul's specialty — check your sourcebook for full mechanical details.",
    "unerring eye":
        "A Inquisitive subclass feature. Grants a thematic ability tied to the Inquisitive's specialty — check your sourcebook for full mechanical details.",
    "unfailing inspiration":
        "A College of Eloquence subclass feature. Grants a thematic ability tied to the College of Eloquence's specialty — check your sourcebook for full mechanical details.",
    "universal speech":
        "A College of Eloquence subclass feature. Grants a thematic ability tied to the College of Eloquence's specialty — check your sourcebook for full mechanical details.",
    "unseen assailant":
        "A Courser Mage subclass feature. Grants a thematic ability tied to the Courser Mage's specialty — check your sourcebook for full mechanical details.",
    "unsettling words":
        "A College of Eloquence subclass feature. Grants a thematic ability tied to the College of Eloquence's specialty — check your sourcebook for full mechanical details.",
    "unstable backlash":
        "A Path of Wild Magic subclass feature. Grants a thematic ability tied to the Path of Wild Magic's specialty — check your sourcebook for full mechanical details.",
    "unwavering mark":
        "A Cavalier subclass feature. Grants a thematic ability tied to the Cavalier's specialty — check your sourcebook for full mechanical details.",
    "unyielding guard":
        "A Bounder subclass feature. Grants a thematic ability tied to the Bounder's specialty — check your sourcebook for full mechanical details.",
    "unyielding spirit":
        "A Oath of the Crown subclass feature. Grants a thematic ability tied to the Oath of the Crown's specialty — check your sourcebook for full mechanical details.",
    "vengeful ancestors":
        "A Path of the Ancestral Guardian subclass feature. Grants a thematic ability tied to the Path of the Ancestral Guardian's specialty — check your sourcebook for full mechanical details.",
    "venomous mark":
        "A Mother of Sorrows subclass feature. Grants a thematic ability tied to the Mother of Sorrows's specialty — check your sourcebook for full mechanical details.",
    "vigilant blessing":
        "A Twilight Domain subclass feature. Grants a thematic ability tied to the Twilight Domain's specialty — check your sourcebook for full mechanical details.",
    "vigilant defender":
        "A Cavalier subclass feature. Grants a thematic ability tied to the Cavalier's specialty — check your sourcebook for full mechanical details.",
    "vigilant rebuke":
        "A Oath of the Watchers subclass feature. Grants a thematic ability tied to the Oath of the Watchers's specialty — check your sourcebook for full mechanical details.",
    "vigilant senses":
        "A Slayer subclass feature. Grants a thematic ability tied to the Slayer's specialty — check your sourcebook for full mechanical details.",
    "visage of the astral self":
        "A Way of the Astral Self subclass feature. Grants a thematic ability tied to the Way of the Astral Self's specialty — check your sourcebook for full mechanical details.",
    "voice of authority":
        "A Order Domain subclass feature. Grants a thematic ability tied to the Order Domain's specialty — check your sourcebook for full mechanical details.",
    "volley":
        "A Hunter of Beasts subclass feature. Grants a thematic ability tied to the Hunter of Beasts's specialty — check your sourcebook for full mechanical details.",
    "wails from the grave":
        "A necromantic feature — manipulating life force, raising undead servants, or warding against death itself.",
    "walker in dreams":
        "A Circle of Dreams subclass feature. Grants a thematic ability tied to the Circle of Dreams's specialty — check your sourcebook for full mechanical details.",
    "warden expression":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "warden's gift":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "warden's gift (d10)":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "warden's gift (d12)":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "warden's gift (d8)":
        "A Warden subclass feature. Grants a thematic ability tied to the Warden's specialty — check your sourcebook for full mechanical details.",
    "warding maneuver":
        "A Cavalier subclass feature. Grants a thematic ability tied to the Cavalier's specialty — check your sourcebook for full mechanical details.",
    "warping implosion":
        "A Aberrant Mind subclass feature. Grants a thematic ability tied to the Aberrant Mind's specialty — check your sourcebook for full mechanical details.",
    "warrior of the gods":
        "A Path of the Zealot subclass feature. Grants a thematic ability tied to the Path of the Zealot's specialty — check your sourcebook for full mechanical details.",
    "wary":
        "A Burglar subclass feature. Grants a thematic ability tied to the Burglar's specialty — check your sourcebook for full mechanical details.",
    "weald spear":
        "A Spear of the Weald subclass feature. Grants a thematic ability tied to the Spear of the Weald's specialty — check your sourcebook for full mechanical details.",
    "whirlwind attack":
        "A Hunter of Shadows subclass feature. Grants a thematic ability tied to the Hunter of Shadows's specialty — check your sourcebook for full mechanical details.",
    "whispers of the dead":
        "A Phantom subclass feature. Grants a thematic ability tied to the Phantom's specialty — check your sourcebook for full mechanical details.",
    "wild empathy":
        "A primal feature connecting to the wild — granting bestial abilities, enhanced senses, or the ability to take on animalistic traits.",
    "wild surge":
        "A primal feature connecting to the wild — granting bestial abilities, enhanced senses, or the ability to take on animalistic traits.",
    "wind soul":
        "A Storm Sorcery subclass feature. Grants a thematic ability tied to the Storm Sorcery's specialty — check your sourcebook for full mechanical details.",
    "wind speaker":
        "A Storm Sorcery subclass feature. Grants a thematic ability tied to the Storm Sorcery's specialty — check your sourcebook for full mechanical details.",
    "winged guardian":
        "A Circle of Owls subclass feature. Grants a thematic ability tied to the Circle of Owls's specialty — check your sourcebook for full mechanical details.",
    "wise words":
        "A Way of the Prophet subclass feature. Grants a thematic ability tied to the Way of the Prophet's specialty — check your sourcebook for full mechanical details.",
    "wizardly quill":
        "A Order of Scribes subclass feature. Grants a thematic ability tied to the Order of Scribes's specialty — check your sourcebook for full mechanical details.",
    "words of terror":
        "A College of Whispers subclass feature. Grants a thematic ability tied to the College of Whispers's specialty — check your sourcebook for full mechanical details.",
    "worthy counsel":
        "A Counsellor subclass feature. Grants a thematic ability tied to the Counsellor's specialty — check your sourcebook for full mechanical details.",
    "writhing tide":
        "A Swarmkeeper subclass feature. Grants a thematic ability tied to the Swarmkeeper's specialty — check your sourcebook for full mechanical details.",
    "zealous presence":
        "A Path of the Zealot subclass feature. Grants a thematic ability tied to the Path of the Zealot's specialty — check your sourcebook for full mechanical details.",
    # ── Bard College Features ──
    "bonus proficiencies":
        "You gain proficiency with three skills of your choice. At 3rd level, the College of Lore "
        "grants any three skills; the College of Valor grants proficiency with medium armor, shields, "
        "and martial weapons.",
    "additional magical secrets":
        "At 6th level, you learn two spells of your choice from any class. A spell you choose must be "
        "of a level you can cast or a cantrip. These spells count as bard spells for you but don't "
        "count against your number of bard spells known.",

    # ── Life Domain ──
    "disciple of life":
        "Also starting at 1st level, your healing spells are more effective. Whenever you cast a "
        "spell of 1st level or higher that restores hit points to a creature, the creature regains "
        "additional hit points equal to 2 + the spell's level.",
    "blessed healer":
        "Beginning at 6th level, the healing spells you cast on others heal you as well. When you "
        "cast a spell of 1st level or higher that restores hit points to another creature, you regain "
        "hit points equal to 2 + the spell's level.",
    "supreme healing":
        "Starting at 17th level, when you would normally roll one or more dice to restore hit points "
        "with a spell, you instead use the highest number possible for each die. For example, instead "
        "of restoring 2d6 hit points to a creature, you restore 12.",

    # ── Circle of the Land ──
    "bonus cantrip":
        "When you choose this circle at 2nd level, you learn one additional druid cantrip of your "
        "choice. This cantrip doesn't count against your number of cantrips known.",
    "land's stride":
        "Starting at 6th level, moving through nonmagical difficult terrain costs you no extra movement. "
        "You can also pass through nonmagical plants without being slowed by them and without taking "
        "damage from them if they have thorns, spines, or a similar hazard. In addition, you have "
        "advantage on saving throws against magically created or manipulated plants that would impede "
        "movement, such as those created by the entangle spell.",
    "nature's ward":
        "When you reach 10th level, you can't be charmed or frightened by elementals or fey, and you "
        "are immune to poison and disease.",
    "nature's sanctuary":
        "When you reach 14th level, creatures from the natural world sense your connection to nature "
        "and become hesitant to attack you. When a beast or plant creature attacks you, that creature "
        "must make a Wisdom saving throw against your druid spell save DC. On a failed save, the "
        "creature must choose a different target, or the attack automatically misses. On a successful "
        "save, the creature is immune to this effect for 24 hours. The creature is aware of this "
        "effect before it makes its attack.",

    # ── Champion ──
    "improved critical":
        "Beginning when you choose this archetype at 3rd level, your weapon attacks score a critical "
        "hit on a roll of 19 or 20.",
    "remarkable athlete":
        "Starting at 7th level, you can add half your proficiency bonus (rounded up) to any Strength, "
        "Dexterity, or Constitution check you make that doesn't already use your proficiency bonus. "
        "In addition, when you make a running long jump, the distance you can cover increases by a "
        "number of feet equal to your Strength modifier.",
    "additional fighting style":
        "At 10th level, you can choose a second option from the Fighting Style class feature.",
    "superior critical":
        "Starting at 15th level, your weapon attacks score a critical hit on a roll of 18–20.",
    "survivor":
        "At 18th level, you attain the pinnacle of resilience in battle. At the start of each of your "
        "turns, you regain hit points equal to 5 + your Constitution modifier if you have no more "
        "than half your hit points left. You don't gain this benefit if you have 0 hit points.",

    # ── Extra Attack (shared by Valor Bard, others) ──
    "extra attack":
        "Beginning at 6th level, you can attack twice, instead of once, whenever you take the Attack "
        "action on your turn.",

    # ── Limited-Use wiring additions ──
    "thunderbolt strike":
        "When you deal lightning damage to a Large or smaller creature, you can push it up to 10 feet "
        "away from you. Usable at will (no limit), but requires dealing lightning damage first.",
    "dragon wings":
        "At 14th level, you gain the ability to sprout a pair of dragon wings from your back, gaining "
        "a flying speed equal to your current speed. You can create these wings as a bonus action on "
        "your turn. They last until you dismiss them as a bonus action on your turn.",
    "bend luck":
        "Starting at 6th level, you have the ability to twist fate using your wild magic. When another "
        "creature you can see makes an attack roll, an ability check, or a saving throw, you can use "
        "your reaction and spend 2 sorcery points to roll 1d4 and apply the number rolled as a bonus "
        "or penalty (your choice) to the creature's roll. You can do so after the creature rolls "
        "but before any effects of the roll occur.",
    "minor conjuration":
        "Starting at 2nd level, you can use your action to conjure up an inanimate object in your hand "
        "or on the ground in an unoccupied space that you can see within 10 feet of you. The object "
        "must be no larger than 3 feet on a side and weigh no more than 10 pounds, and its form must "
        "be that of a nonmagical object you have seen. The object is visibly magical, radiating dim "
        "light out to 5 feet. It disappears after 1 hour, when you use this feature again, or if it "
        "takes or deals any damage.",
    "greater portent":
        "Starting at 14th level, the visions in your dreams intensify. When you finish a long rest, "
        "roll three d20s instead of two and record the numbers rolled. You can replace any attack "
        "roll, saving throw, or ability check with one of these foretelling rolls, and you gain a "
        "third foretelling roll.",
    "improved minor illusion":
        "When you choose this school at 2nd level, you learn the minor illusion cantrip. If you "
        "already know this cantrip, you learn a different wizard cantrip of your choice. When you "
        "cast minor illusion, you can create both a sound and an image with a single casting.",
    "alter memories":
        "At 14th level, you gain the ability to make a creature unaware of your magical influence on "
        "it. When you cast an enchantment spell to charm one or more creatures, you can alter one "
        "creature's understanding so that it remains unaware of being charmed. Additionally, once "
        "before the spell expires, you can use your action to make the creature forget some of the "
        "time it spent charmed. The creature must succeed on an Intelligence saving throw against "
        "your wizard spell save DC or lose a number of hours of memory equal to 1 + your Charisma "
        "modifier (minimum of 1).",
    "command undead":
        "Starting at 14th level, you can use magic to bring undead under your control, even those "
        "created by other wizards. As an action, you can choose one undead that you can see within "
        "60 feet and force it to make a Charisma saving throw against your wizard spell save DC. "
        "If it succeeds, you can't use this feature on it again. If it fails, it becomes friendly "
        "to you and obeys your commands until you use this feature again. Intelligent undead are "
        "harder to control — if it has an Intelligence of 8 or higher, it has advantage on the save. "
        "If it fails and has an Intelligence of 12 or higher, it can repeat the save at the end of "
        "every hour until it succeeds and breaks free.",
    "minor alchemy":
        "Starting at 2nd level, you can temporarily alter the physical properties of one nonmagical "
        "object, changing it from one substance into another. You perform a special alchemical procedure "
        "on one object composed entirely of wood, stone (but not a gemstone), iron, copper, or silver, "
        "transforming it into a different one of those materials. For every 10 minutes you spend "
        "performing the procedure, you can transform up to 1 cubic foot of material. After 1 hour, "
        "or until you lose concentration (as if concentrating on a spell), the material reverts to "
        "its original substance.",
    "transmuter's stone":
        "Starting at 6th level, you can spend 8 hours creating a transmuter's stone that stores "
        "transmutation magic. You can create the stone at the end of a long rest. A creature gains "
        "a benefit of your choice while holding the stone: darkvision 60 ft, +10 ft speed, proficiency "
        "in Constitution saves, or resistance to acid/cold/fire/lightning/thunder damage. Each time "
        "you cast a transmutation spell of 1st level or higher, you can change the effect. If you "
        "create a new stone, the old one ceases to function.",
}

# Merge subclass descriptions into FEATURE_DESCRIPTIONS
for sub_key, sub_desc in SUBCLASS_FEATURE_DESCRIPTIONS.items():
    if sub_key not in FEATURE_DESCRIPTIONS:
        FEATURE_DESCRIPTIONS[sub_key] = sub_desc

# Merge CD descriptions into FEATURE_DESCRIPTIONS so enrich_features finds them
for cd_key, cd_desc in CHANNEL_DIVINITY_DESCRIPTIONS.items():
    if cd_key not in FEATURE_DESCRIPTIONS:
        FEATURE_DESCRIPTIONS[cd_key] = cd_desc

# Call manual data loader after all data structures are defined
# (Guard against double-load when imported from routes/dm.py during startup)
if not MANUAL_TRAPS:
    load_manual_data()

# ── Known feats list for ASI picker dropdown ──────────────────────────
# Auto-generated from FEATS dict after manual data merge — filters out
# Eldritch Invocations and ALL-CAPS duplicates. No manual maintenance.
_INVOCATION_NAMES: set[str] = {
    "Agonizing Blast", "Armor of Shadows", "Ascendant Step", "Beast Speech",
    "Beguiling Influence", "Bewitching Whispers", "Book of Ancient Secrets",
    "Chains of Carceri", "Devil's Sight", "Dreadful Word", "Eldritch Sight",
    "Eldritch Spear", "Eyes of the Rune Keeper", "Fiendish Vigor",
    "Gaze of Two Minds", "Lifedrinker", "Mask of Many Faces",
    "Master of Myriad Forms", "Minions of Chaos", "Mire the Mind",
    "Misty Visions", "One with Shadows", "Otherworldly Leap", "Repelling Blast",
    "Sculptor of Flesh", "Sign of Ill Omen", "Thief of Five Fates",
    "Thirsting Blade", "Visions of Distant Realms", "Voice of the Chain Master",
    "Whispers of the Grave", "Witch Sight",
}
def _build_known_feats() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    title_names = {f["name"].lower(): f["name"] for f in FEATS.values()}
    for f in FEATS.values():
        name = f["name"]
        if not name or name in _INVOCATION_NAMES:
            continue
        # Skip ALL-CAPS names that aren't proper title case
        # (OCR-garbled imports like "ATTACKER" for "Savage Attacker",
        #  or "MOBILE" when "Mobile" already exists as a key)
        if name.isupper():
            title_version = name.title()
            if title_version in title_names or title_version.lower() in title_names:
                continue
        if name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    result.sort()
    return result
KNOWN_FEATS: list[str] = _build_known_feats()

# Feat name → {desc, prereq, source} for the ASI picker preview
FEAT_DETAILS: dict[str, dict] = {}
# Case-insensitive name → FEATS value for enrichment lookups
FEAT_BY_NAME: dict[str, dict] = {}
for _f in FEATS.values():
    _name = _f.get("name", "")
    if _name in KNOWN_FEATS:
        FEAT_DETAILS[_name] = {
            "desc": _f.get("description") or _f.get("desc", ""),
            "prereq": _f.get("prerequisite") or _f.get("prereq", ""),
            "source": _f.get("source", ""),
        }
    FEAT_BY_NAME[_name.lower()] = _f

# ── Merge manual equipment into ITEM_INDEX ──
# SRD equipment is a subset of PHB equipment. Manual data fills in missing
# items (Holy Symbol, Arcane Focus, Druidic Focus, armor variants, siege
# weapons, etc.). Without this merge, those items are not searchable and
# disappear after deletion.
_manual_equipment = _load_manual_json("equipment.json")
_equipment_added = 0
for _item in _manual_equipment:
    _name = _item.get("name", "")
    if not _name:
        continue
    _key = _name.lower()
    if _key in ITEM_INDEX:
        continue  # Don't overwrite existing entries
    _source = _resolve_source(_key, _item.get("source", "") or "PHB 2014")
    _desc = _item.get("description", "").strip()
    # Derive a fallback cost/weight from the SRD-equipment-style description
    # if manual data doesn't provide separate fields
    _cost = (_item.get("cost") or "").strip()
    _weight = _item.get("weight", None)
    _type_str = (_item.get("type") or "Adventuring Gear").strip()
    ITEM_INDEX[_key] = {
        "name": _name,
        "type": _type_str,
        "description": _desc,
        "cost": _cost or "—",
        "weight": _weight,
        "rarity": "",
        "source": _source,
    }
    if _item.get("dice"):
        ITEM_INDEX[_key]["dice"] = _item["dice"]
    _equipment_added += 1
if _equipment_added:
    print(f"  Manual equipment added to index: {_equipment_added}")

# ── PHB scale functions per feature ──
def get_uses_for_level(feat_key: str, class_name: str, level: int) -> int:
    """Return the number of uses for a limited-use feature at this level."""
    lu = LIMITED_USE.get(feat_key, {})
    if not lu:
        return 0
    lu_class = lu.get("class", "")
    if lu_class and lu_class != class_name:
        return 0
    if lu["per"] == "fixed":
        # Fixed-scaling features: use level thresholds
        if feat_key == "action surge":
            return 1 if level < 17 else 2  # PHB p.72: L2=1, L17=2
        if feat_key == "indomitable":
            return 1 if level < 13 else 2 if level < 17 else 3  # PHB p.72: L9=1, L13=2, L17=3
        if feat_key == "combat superiority":
            # PHB p.73: 4 dice L3-6, 5 dice L7-14, 6 dice L15+
            return 4 if level < 7 else 5 if level < 15 else 6
        if feat_key == "second wind":
            return 1  # Always 1 use
        if feat_key == "mystic arcanum":
            return 1  # 1 per mystic arcanum level (but each is a separate feature)
    if lu["per"] == "level":
        # Level-scaling features
        if feat_key == "rage":
            # PHB Barbarian table: L1-2=2, L3-5=3, L6-11=4, L12-16=5, L17-20=6
            if level >= 17: return 6
            if level >= 12: return 5
            if level >= 6:  return 4
            if level >= 3:  return 3
            return 2
        if feat_key == "bardic inspiration":
            # PHB Bard table: L1-4=3, L5-9=4, L10-14=5, L15-20=6
            if level >= 15: return 6
            if level >= 10: return 5
            if level >= 5:  return 4
            return 3
        if feat_key == "channel divinity":
            # Cleric: L1-5=1, L6-17=2, L18+=3
            if class_name == "Cleric":
                if level >= 18: return 3
                if level >= 6:  return 2
                return 1
            # Paladin: always 1 use per short rest (PHB p.85)
            return 1
        if feat_key == "wild shape":
            # PHB p.66: always 2 uses per short rest. (Archdruid at L20 = unlimited)
            return 2
        if feat_key == "ki":
            # Ki = monk level (PHB p.78)
            return level
        if feat_key == "divine sense":
            # Paladin: 1 + Cha mod (min 1). We return 1 as base, Cha mod handled separately
            return 1  # + Cha mod added at enrichment time
        if feat_key == "lay on hands":
            # Paladin: 5 * level (HP pool, not per-use)
            return level * 5
        if feat_key == "hands of the healer":
            # Scholar: 1 Healing Die per level, short rest
            return level
        if feat_key == "sorcery points":
            # Sorcery points = sorcerer level (PHB p.101)
            return level
    if lu["per"] == "wis":
        # WIS-mod features (min 1): actual WIS mod added in enrich_features
        return 1
    return lu.get("min", 1)


def get_caster_type(class_name: str) -> str:
    """Return 'full', 'half', 'pact', 'third', or 'none' for a single class."""
    if class_name in FULL_CASTERS:
        return "full"
    if class_name in HALF_CASTERS:
        return "half"
    if class_name in PACT_CASTERS:
        return "pact"
    return "none"

def get_multiclass_caster_types(class_levels: dict[str, int]) -> dict:
    """Return dict of {caster_type: total_level} for multiclass character.
    e.g. {'full': 5, 'pact': 3, 'none': 2} for Wizard 5 / Warlock 3 / Barb 2."""
    types = {}
    for cls, level in class_levels.items():
        ct = get_caster_type(cls)
        types[ct] = types.get(ct, 0) + level
    return types

def is_multiclass_caster(class_levels: dict[str, int]) -> bool:
    """Return True if character has 2+ spellcasting classes (pact + normal counts)."""
    types = get_multiclass_caster_types(class_levels)
    caster_count = sum(1 for ct in types if ct != "none")
    return caster_count >= 2

def get_prepared_max(class_name: str, level: int, spellcasting_mod: int) -> int:
    """PHB p.xxx: prepared casters prepare = level + spellcasting_mod spells.
    Paladin uses Cha mod, Cleric/Druid use Wis mod, Wizard uses Int mod."""
    if class_name not in PREPARED_CASTERS:
        return 0
    return max(1, level + spellcasting_mod)

def get_spellcasting_mod(class_name: str, mods: dict) -> int:
    """Return the spellcasting ability modifier for this class (PHB p.xxx)."""
    if class_name in ("Bard", "Paladin", "Sorcerer", "Warlock"):
        return mods.get("charisma", 0)
    if class_name in ("Cleric", "Druid", "Ranger"):
        return mods.get("wisdom", 0)
    if class_name in ("Wizard",):
        return mods.get("intelligence", 0)
    return 0

def get_spells_known_max(class_name: str, level: int) -> int:
    """Return max spells known at this level from SRD data, or 0 if prepared caster / non-caster."""
    if class_name not in SPELLS_KNOWN_CASTERS:
        return 0
    key = class_name.lower()
    entries = SRD_LEVELS.get(key, [])
    for e in entries:
        if e.get("level") == level:
            return e.get("spellcasting", {}).get("spells_known", 0)
    return 0

def get_cantrips_known_max(class_name: str, level: int) -> int:
    """Return max cantrips known at this level from SRD data."""
    key = class_name.lower()
    entries = SRD_LEVELS.get(key, [])
    for e in entries:
        if e.get("level") == level:
            return e.get("spellcasting", {}).get("cantrips_known", 0)
    return 0

# ── Spell enrichment (SRD descriptions) ───────────────────────────────────

def _scaled_dice_display(dice_info: dict, character_level: int | None) -> str:
    """Return the dice display string, scaling cantrips to character level.

    If ``display`` contains ``{scaled}``, the scaled dice string is substituted
    there (supports complex patterns like '+{scaled} / {scaled}+MOD fire').
    Otherwise the entire display is replaced by the scaled dice string.

    ``display_at_1`` (optional) is used when character_level < 5 —
    some cantrips (Green-Flame Blade, Booming Blade) have no bonus dice
    before tier 1."""
    display = dice_info.get("display", "")
    if character_level and character_level < 5 and dice_info.get("display_at_1"):
        return dice_info["display_at_1"]
    if not (character_level and dice_info.get("cantrip_scaling")):
        return display
    # Cantrip scaling: 1x at 1-4, 2x at 5-10, 3x at 11-16, 4x at 17+
    base = dice_info["base_dice"]
    m = re.match(r"(\d+)d(\d+)", base)
    if not m:
        return display
    count = int(m.group(1))
    die = m.group(2)
    if character_level >= 17:
        count = count * 4
    elif character_level >= 11:
        count = count * 3
    elif character_level >= 5:
        count = count * 2
    scaled = f"{count}d{die}"
    if "{scaled}" in display:
        return display.replace("{scaled}", scaled)
    return scaled

def enrich_spells(spells: list[dict], character_level: int | None = None) -> None:
    """Add full SRD spell data to each spell dict in-place.

    If character_level is provided, cantrip dice are scaled to the
    appropriate tier (5th→2x, 11th→3x, 17th→4x)."""
    if not SRD_SPELLS:
        return
    # Build lookup by lowercase name
    srd_lookup = {s.get("name", "").lower(): s for s in SRD_SPELLS}
    for sp in spells:
        name = sp.get("spell_name", "")
        srd = srd_lookup.get(name.lower())
        if srd:
            # Normalize manual spell formats (description→desc, higher_levels→higher_level)
            _desc = srd.get("desc") or srd.get("description", "")
            _higher = srd.get("higher_level") or srd.get("higher_levels", "")
            _components = srd.get("components", [])
            if isinstance(_components, str):
                _components = [c.strip() for c in _components.split(",") if c.strip()]
            sp["srd"] = {
                "desc": _desc if isinstance(_desc, list) else ([_desc] if _desc else []),
                "higher_level": _higher if isinstance(_higher, list) else ([_higher] if _higher else []),
                "range": srd.get("range", ""),
                "components": _components,
                "material": srd.get("material", ""),
                "ritual": srd.get("ritual", False),
                "duration": srd.get("duration", ""),
                "concentration": srd.get("concentration", False),
                "casting_time": srd.get("casting_time", ""),
                "school": (srd.get("school") or {}).get("name", ""),
                "attack_type": srd.get("attack_type", ""),
                "damage": srd.get("damage"),
                "source": srd.get("source", ""),
            }
            # Attach dice roll indicator from precomputed lookup
            dice_info = SPELL_DICE.get(name.lower())
            if dice_info:
                sp["dice"] = _scaled_dice_display(dice_info, character_level)
                if dice_info.get("healing"):
                    sp["dice_healing"] = True
                if dice_info.get("ac_bonus"):
                    sp["ac_bonus"] = True
                if dice_info.get("buff"):
                    sp["buff"] = True

# ── Spells also available as tiered recommendations (from SRD cache) ──────────

def get_spells_for_level(class_name: str, level: int) -> dict:
    """Get recommended spells for this class from SRD data."""
    return get_srd_spells_for_class(class_name, level)

# Feats by class tier (PHB-optimal picks at ASI levels 4,8,12,16,19)
# Scaled equipment by class and level tier (PHB starting equipment + reasonable progression)
# Levels: 1, 5, 10, 15, 20
# HP calculation: max at level 1, average (rounded up) thereafter
def calc_hp(class_name: str, level: int, con_mod: int) -> int:
    hd = CLASSES.get(class_name, {}).get("hd", 8)
    avg_roll = (hd // 2) + 1  # PHB: half+1, effectively ceiling(average)
    return hd + con_mod + (level - 1) * (avg_roll + con_mod)

def allocate_ability_scores(class_name: str, race_name: str, subrace: str = "") -> dict:
    """Allocate standard array optimally for class, then apply racial ASIs."""
    priority = ABILITY_PRIORITY.get(class_name, ABILITY_PRIORITY["Fighter"])
    scores = {ability: val for ability, val in zip(priority, sorted(STANDARD_ARRAY, reverse=True))}
    # Apply racial ASIs (PHB p.12-40)
    race_data = RACES.get(race_name, RACES["Human"])
    for ability, bonus in race_data["asi"].items():
        scores[ability] = scores.get(ability, 10) + bonus
    if subrace and subrace in SUBASIS:
        for ability, bonus in SUBASIS[subrace].items():
            scores[ability] = scores.get(ability, 10) + bonus
    # Fill any missing abilities
    for ability in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        scores.setdefault(ability, 10)
    return scores

def modifier(score: int) -> int:
    return (score - 10) // 2

def get_asi_levels(level: int, class_name: str = "") -> list[int]:
    """List of ASI levels the character has passed."""
    asis = {4,8,12,16,19}
    if class_name == "Fighter": asis.update({6,14})  # Fighter gets extra ASIs (PHB p.71)
    return sorted([a for a in asis if a <= level])

def _ordinal(n: int) -> str:
    """Return ordinal string: 1st, 2nd, 3rd, etc."""
    if 11 <= n % 100 <= 13: return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"

def get_feats_for_level(class_name: str, level: int) -> list[str]:
    """Recommended feats based on how many ASIs the character has taken."""
    asi_count = len(get_asi_levels(level, class_name))
    all_feats = RECOMMENDED_FEATS.get(class_name, RECOMMENDED_FEATS["Fighter"])
    # Pick top N feats (or fewer if not enough levels)
    count = min(asi_count, len(all_feats))
    return all_feats[:count]

def get_equipment_for_level(class_name: str, level: int) -> list[str]:
    """Get scaled equipment for this class at the closest tier."""
    if class_name not in SCALED_EQUIPMENT:
        return ["Explorer's Pack", "Dagger"]
    tiers = sorted(SCALED_EQUIPMENT[class_name].keys())
    tier = 1
    for t in tiers:
        if t <= level:
            tier = t
    return SCALED_EQUIPMENT[class_name][tier]


def pick_magic_items(class_name: str, level: int) -> list[dict]:
    """Pick level-appropriate SRD magic items for this class."""
    if not SRD_MAGIC_ITEMS:
        return []
    rarities = _item_rarity_for_level(level)
    if not rarities:
        return []
    
    items = []
    # Determine what item types this class wants
    is_martial = class_name in ("Barbarian", "Fighter", "Paladin", "Ranger")
    is_caster = class_name in ("Wizard", "Sorcerer", "Warlock", "Bard", "Cleric", "Druid")
    
    for rarity in rarities:
        pool = ITEMS_BY_RARITY.get(rarity, [])
        if not pool:
            continue
        
        # Pick 1 weapon for martials, 1 focus for casters, 1 armor, 1 wondrous
        if is_martial:
            weapons = [i for i in pool if i in ITEM_WEAPONS]
            if weapons:
                items.append(random.choice(weapons))
        if is_caster:
            foci = [i for i in pool if i in ITEM_RODS_STAVES_WANDS]
            if foci:
                items.append(random.choice(foci))
        
        armor = [i for i in pool if i in ITEM_ARMOR]
        if armor and random.random() < 0.4:
            items.append(random.choice(armor))
        
        wondrous = [i for i in pool if i in ITEM_WONDROUS]
        if wondrous:
            items.append(random.choice(wondrous))
        
        if len(items) >= 3:
            break
    
    # Format as name + rarity + short description
    result = []
    for item in items[:5]:
        name = item["name"]
        rarity = item.get("rarity", {}).get("name", "")
        desc = " ".join(item.get("desc", []))
        result.append({"name": name, "rarity": rarity, "description": desc})
    return result

# ── Treasure Hoard Engine (DMG 2014 p.137-139) ──

# Coin formulas per CR bracket — all standardized to GP.
# Original DMG 2014 values (cp/sp/ep/pp) converted at standard rates:
#   100 cp = 1 gp, 10 sp = 1 gp, 2 ep = 1 gp, 1 pp = 10 gp
# ── Treasure hoard data & helpers (moved to campaign.py to avoid circular import) ──
from routes.characters.campaign import roll_treasure_hoard, TREASURE_HOARD_COINS, TREASURE_HOARD_TABLE, MAGIC_TABLE_POOLS, _roll_dice, _pick_magic_item

# ── Feature → Defense Mappings (PHB 2014) ──
# Maps feature names to resistances/immunities they grant.
# Format: {"resist": [...], "immune": [...], "note": "while raging"}
FEATURE_DEFENSES = {
    "Rage": {"resist": ["Bludgeoning", "Piercing", "Slashing"], "note": "while raging"},
    "Totem Spirit: Bear": {"resist": ["All except Psychic"], "note": "while raging"},
    "Empty Body": {"resist": ["All except Force"], "note": "while invisible (4 ki, 1 min)"},
}

# ── Item Attunement & Properties (PHB 2014 p.136-138) ──
# Auto-built at startup from SRD magic items desc text.
# Items that say "requires attunement" in their description.
ITEM_ATTUNEMENT: dict[str, bool] = {}

# Item → mechanical effects when equipped AND attuned (if required).
# Keys are lowercase item names. Properties merged into character sheet.
# Supported keys: resist, immune, ac_bonus, save_bonus,
#   str_override, dex_override, con_override, int_override, wis_override, cha_override,
#   str_bonus, con_bonus, adv_skill, note
ITEM_PROPERTIES: dict[str, dict] = {}


def _build_item_properties():
    """Parse SRD magic items for attunement + known property patterns."""
    global ITEM_ATTUNEMENT, ITEM_PROPERTIES
    import re

    # ── Attunement detection from desc ──
    for item in SRD_MAGIC_ITEMS:
        name = item.get("name", "")
        if not name:
            continue
        desc = " ".join(item.get("desc", []))
        ITEM_ATTUNEMENT[name.lower()] = "requires attunement" in desc.lower()

    # ── Hand-curated item properties ──
    _props = {
        # === Resistance items ===
        "armor of resistance": {
            "resist": ["*"], "note": "One type (GM chooses). Requires attunement.",
        },
        "ring of resistance": {
            "resist": ["*"], "note": "One type (gem indicates). Requires attunement.",
        },
        "ring of warmth": {"resist": ["Cold"]},
        "boots of the winterlands": {"resist": ["Cold"]},
        "armor of invulnerability": {
            "resist": ["Bludgeoning", "Piercing", "Slashing"],
            "note": "Nonmagical only. Requires attunement.",
        },
        "brooch of shielding": {
            "immune": ["Force"],
            "note": "Also immune to Magic Missile. Requires attunement.",
        },
        "belt of dwarvenkind": {
            "con_bonus": 2, "resist": ["Poison"],
            "adv_skill": "Persuasion (dwarves)",
            "note": "Also 50% chance to grow beard. Requires attunement.",
        },
        "dragon scale mail": {
            "resist": ["*"], "note": "Matches dragon color. Requires attunement.",
        },
        "ring of evasion": {"note": "3 charges, Dex save → half/no damage. Requires attunement."},
        "ring of feather falling": {"note": "Falls at 60 ft/round, no fall damage. Requires attunement."},
        "ring of free action": {"note": "Immune to difficult terrain, paralysis, restraint. Requires attunement."},
        "periapt of proof against poison": {"immune": ["Poison", "Poisoned"]},
        "periapt of wound closure": {"note": "Stabilize at start of turn, double HP from HD. Requires attunement."},

        # === Ability score items ===
        "belt of hill giant strength": {"str_override": 21},
        "belt of stone giant strength": {"str_override": 23},
        "belt of frost giant strength": {"str_override": 23},
        "belt of fire giant strength": {"str_override": 25},
        "belt of cloud giant strength": {"str_override": 27},
        "belt of storm giant strength": {"str_override": 29},
        "gauntlets of ogre power": {"str_override": 19},
        "headband of intellect": {"int_override": 19},
        "amulet of health": {"con_override": 19},
        "ioun stone of strength": {"str_bonus": 2},
        "ioun stone of dexterity": {"dex_bonus": 2},
        "ioun stone of constitution": {"con_bonus": 2},
        "ioun stone of intelligence": {"int_bonus": 2},
        "ioun stone of wisdom": {"wis_bonus": 2},
        "ioun stone of charisma": {"cha_bonus": 2},
        "manual of bodily health": {"con_bonus": 2, "note": "Permanent. +2 max CON."},
        "manual of gainful exercise": {"str_bonus": 2, "note": "Permanent. +2 max STR."},
        "manual of quickness of action": {"dex_bonus": 2, "note": "Permanent. +2 max DEX."},
        "tome of clear thought": {"int_bonus": 2, "note": "Permanent. +2 max INT."},
        "tome of leadership and influence": {"cha_bonus": 2, "note": "Permanent. +2 max CHA."},
        "tome of understanding": {"wis_bonus": 2, "note": "Permanent. +2 max WIS."},

        # === AC / Save bonuses ===
        "ring of protection": {"ac_bonus": 1, "save_bonus": 1},
        "cloak of protection": {"ac_bonus": 1, "save_bonus": 1},
        "ioun stone of protection": {"ac_bonus": 1, "save_bonus": 1},
        "cloak of displacement": {"note": "Disadvantage on attacks vs you. Requires attunement."},
        "bracers of defense": {"ac_bonus": 2, "note": "Only when wearing no armor/shield. Requires attunement."},

        # === Skill / utility items ===
        "boots of elvenkind": {"adv_skill": "Stealth (moving silently)"},
        "cloak of elvenkind": {"adv_skill": "Stealth (hiding)"},
        "gloves of thievery": {"adv_skill": "Sleight of Hand, Thieves' Tools"},
        "eyes of the eagle": {"adv_skill": "Perception (sight)"},
        "stone of good luck": {"save_bonus": 1, "adv_skill": "Ability checks +1"},
        "luck blade": {"save_bonus": "1 (reroll 1/day)", "note": "Also has wishes. Requires attunement."},

        # === Armor of Vulnerability (negative property) ===
        "armor of vulnerability": {
            "resist": ["*"],
            "note": "Resist one type BUT vulnerable to two others. Requires attunement.",
        },

        # === Cursed items ===
        "shield of missile attraction": {
            "resist": ["Ranged weapon damage"],
            "note": "Also attracts ALL ranged attacks within 10 ft. Cursed. Requires attunement.",
        },
        "armor of vulnerability (slashing)": {
            "resist": ["Slashing"],
            "note": "Vulnerable to Bludgeoning and Piercing.",
        },
    }

    for k, v in _props.items():
        if "requires_attunement" not in v:
            # Infer from ITEM_ATTUNEMENT if not explicitly set
            v["requires_attunement"] = ITEM_ATTUNEMENT.get(k, False)
        ITEM_PROPERTIES[k] = v

    # ── Auto-detect remaining attunement-only items (no curated properties yet) ──
    for name, needs_attune in ITEM_ATTUNEMENT.items():
        if needs_attune and name not in ITEM_PROPERTIES:
            ITEM_PROPERTIES[name] = {"requires_attunement": True, "note": ""}


def compute_item_effects(equipped: list[str], attuned: list[str],
                         inventory: list[dict] = None) -> dict:
    """Compute combined mechanical effects from equipped+attuned items.

    Args:
        equipped: list of equipped item names
        attuned: list of attuned item names
        inventory: full inventory list (for item lookup with quantities)

    Returns dict with keys: resist, immune, ac_bonus, save_bonus,
        str_override, dex_override, con_override, int_override, wis_override, cha_override,
        str_bonus, dex_bonus, con_bonus, int_bonus, wis_bonus, cha_bonus,
        adv_skills (list), notes (list), attunement_slots_used (int)
    """
    result = {
        "resist": [], "immune": [],
        "ac_bonus": 0, "save_bonus": 0,
        "str_override": None, "dex_override": None, "con_override": None,
        "int_override": None, "wis_override": None, "cha_override": None,
        "str_bonus": 0, "dex_bonus": 0, "con_bonus": 0,
        "int_bonus": 0, "wis_bonus": 0, "cha_bonus": 0,
        "adv_skills": [], "notes": [],
        "attunement_slots_used": 0,
    }
    attuned_set = set(a.lower() for a in attuned)

    # ── Armor/shield keywords for enhancement detection ──
    _ARMOR_KEYWORDS = [
        "padded", "leather", "studded", "hide", "chain shirt", "scale mail",
        "breastplate", "half plate", "ring mail", "chain mail", "splint",
        "plate", "shield", "armor",
    ]

    for item in equipped:
        # Normalize: item may be string or dict
        if isinstance(item, dict):
            item_name = item.get("name", "")
            enhancement = item.get("enhancement", 0)
        else:
            item_name = str(item)
            enhancement = _parse_enhancement(item_name)

        key = item_name.lower()
        props = ITEM_PROPERTIES.get(key, {})
        if not props:
            # Check for armor/shield with enhancement (e.g. "Studded Leather +1")
            if enhancement:
                is_armor_item = any(kw in key for kw in _ARMOR_KEYWORDS)
                if is_armor_item:
                    result["ac_bonus"] += enhancement
                    result["notes"].append(f"{item_name}: +{enhancement} enhancement AC")
            continue

        # Attunement gating
        if props.get("requires_attunement"):
            if key not in attuned_set:
                continue  # skip — equipped but not attuned
            result["attunement_slots_used"] += 1

        # Resistances / immunities
        for r in props.get("resist", []):
            if r not in result["resist"]:
                result["resist"].append(r)
        for i in props.get("immune", []):
            if i not in result["immune"]:
                result["immune"].append(i)

        # AC / save bonuses
        result["ac_bonus"] += props.get("ac_bonus", 0)
        if isinstance(props.get("save_bonus"), (int, float)):
            result["save_bonus"] += props["save_bonus"]

        # Ability overrides (highest wins)
        for abv in ["str", "dex", "con", "int", "wis", "cha"]:
            ov_key = f"{abv}_override"
            if props.get(ov_key):
                current = result[ov_key]
                if current is None or props[ov_key] > current:
                    result[ov_key] = props[ov_key]

        # Ability bonuses (stackable)
        for abv in ["str", "dex", "con", "int", "wis", "cha"]:
            bon_key = f"{abv}_bonus"
            result[bon_key] += props.get(bon_key, 0)

        # Skill advantage
        if props.get("adv_skill"):
            result["adv_skills"].append(f"{item_name}: {props['adv_skill']}")

        # Notes
        if props.get("note"):
            result["notes"].append(f"{item_name}: {props['note']}")

    return result

# Initialize item properties at module load
_build_item_properties()


def enrich_features(feature_list: list[str], class_name: str = "", level: int = 0, mods: dict = None, class_levels: dict = None, subclass: str = "") -> list[dict]:
    """Add SRD descriptions to feature names, and track limited-use abilities.
    When class_levels dict provided, uses per-class levels for multiclass limited uses."""
    enriched = []
    for feat_str in feature_list:
        if ": " in feat_str:
            level_part, name = feat_str.split(": ", 1)
        else:
            level_part, name = feat_str, feat_str
        key = name.lower()
        # Try subclass-specific description first (disambiguates shared names like "Bonus Proficiencies")
        desc = ""
        if subclass:
            sc_key = f"{subclass}::{key}"
            desc = FEATURE_DESCRIPTIONS.get(sc_key, "")
        if not desc:
            desc = FEATURE_DESCRIPTIONS.get(key, "")
        # Fallback: check RACIAL_TRAIT_DESCS for racial features
        if not desc:
            desc = RACIAL_TRAIT_DESCS.get(name, "")
        # If composite name from multiclass dedup, try first segment
        if not desc and " | " in key:
            first_seg = key.split(" | ")[0].strip()
            desc = FEATURE_DESCRIPTIONS.get(first_seg, "")
        entry = {"name": name, "level": level_part, "description": desc}
        # Look up source from SRD feature data — but prefer class source over "SRD 5.1"
        _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == key), "")
        if _src and _src != "SRD 5.1" and _src != "PHB 2014":
            entry["source"] = _src
        elif class_name:
            _cls_src = CLASSES.get(class_name, {}).get("source", "")
            if _cls_src:
                entry["source"] = _cls_src
        # Parse composite Channel Divinity names into sub_options with individual descriptions
        if " | " in name and "channel divinity" in key:
            segments = name.split(" | ")
            sub_options = []
            for seg in segments:
                seg = seg.strip()
                # Strip level prefix like "L2: " to get the actual feature name
                sub_name = seg
                if ": " in seg:
                    maybe_lvl, rest = seg.split(": ", 1)
                    if maybe_lvl.startswith("L") and maybe_lvl[1:].replace("-","").replace("+","").isdigit():
                        sub_name = rest
                sub_key = sub_name.lower()
                sub_desc = FEATURE_DESCRIPTIONS.get(sub_key, "")
                sub_options.append({"name": sub_name, "description": sub_desc})
            # First segment is always the generic CD header — keep it as description
            # but store all sub-options (including the header for context) for frontend
            entry["sub_options"] = sub_options
            # Build a summary description listing all available options
            option_names = [so["name"] for so in sub_options if "channel divinity:" in so["name"].lower()]
            if option_names:
                entry["description"] = f"{desc}\n\nAvailable options: {', '.join(option_names)}."
        # Determine source class + level for limited-use computation
        source_class = None
        source_level = 0
        if class_levels and len(class_levels) > 1:
            # Multiclass: infer source from feature context or use primary
            for cls_name in class_levels:
                if cls_name.lower() in key or key in cls_name.lower():
                    source_class = cls_name
                    source_level = class_levels[cls_name]
                    break
            if not source_class:
                source_class = class_name
                source_level = class_levels.get(class_name, level)
        else:
            source_class = class_name
            source_level = level
        # Check limited-use features
        if source_class and source_level > 0:
            # Features that should never have uses/recharge (DM-triggered, point-driven, or passive)
            _NON_LIMITED_FEATURES = {
                # Wild Magic Sorcerer
                "wild magic surge",  # DM-triggered, not player-activated
                "bend luck",         # costs 2 sorcery points, unlimited uses
                "controlled chaos",  # passive modifier
                "spell bombardment", # passive modifier
                # Totem Warrior Barbarian — permanent ritual choices
                "totem spirit", "aspect of the beast", "totemic attunement",
                # Tempest Cleric — at-will/passive
                "thunderbolt strike", "stormborn",
                # Draconic Sorcerer
                "dragon wings",  # unlimited BA, permanent
                # Great Old One Warlock
                "awakened mind",  # unlimited telepathy
                # Divination Wizard
                "the third eye", "greater portent",
                # Conjuration Wizard
                "minor conjuration",
                # Enchantment Wizard
                "hypnotic gaze", "alter memories",
                # Illusion Wizard
                "improved minor illusion", "illusory reality",
                # Transmutation Wizard
                "minor alchemy", "transmuter's stone",
                # Arcane Trickster Rogue
                "mage hand legerdemain", "magical ambush", "versatile trickster",
            }
            if key not in _NON_LIMITED_FEATURES:
                # Feature name aliases (raw name → LIMITED_USE key)
                _FEAT_ALIASES = {"font of magic": "sorcery points"}
                for lkey, lu in LIMITED_USE.items():
                    _match_key = _FEAT_ALIASES.get(key, key)
                    if lkey in _match_key or _match_key.startswith(lkey) or lkey.startswith(_match_key):
                        uses_max = get_uses_for_level(lkey, source_class, source_level)
                        if uses_max > 0:
                            if lkey == "divine sense":
                                cha_mod = (mods or {}).get("charisma", 0)
                                uses_max = max(1, uses_max + cha_mod)
                            if lkey == "cleansing touch":
                                cha_mod = (mods or {}).get("charisma", 0)
                                uses_max = max(1, uses_max + cha_mod - 1)  # base 1 + CHA
                            # WIS-mod features (warding flare, improved flare, dampen elements, wrath of the storm, blessing of the trickster)
                            if lu.get("per") == "wis":
                                wis_mod = (mods or {}).get("wisdom", 0)
                                uses_max = max(1, wis_mod)
                            entry["uses_max"] = uses_max
                            entry["uses"] = uses_max
                            entry["recharge"] = lu["recharge"]
                            if lu.get("pool_kind"):
                                entry["pool_kind"] = lu["pool_kind"]
                        break
        # Check if this feature is a combat action
        # Strip parenthetical suffix for matching (e.g. "Bardic Inspiration (d6)" -> "bardic inspiration")
        import re
        _clean_key = re.sub(r'\s*\([^)]*\)\s*$', '', key).strip()
        action_info = FEATURE_ACTION_TYPES.get(_clean_key) or FEATURE_ACTION_TYPES.get(key)
        # Fallback: composite Channel Divinity names
        if not action_info and "channel divinity" in _clean_key:
            action_info = FEATURE_ACTION_TYPES.get("channel divinity")
        if action_info:
            entry["action_type"] = action_info[0]
            entry["action_desc"] = action_info[1]
        enriched.append(entry)
    return enriched


def _add_cd_sub_options(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add sub_options to composite Channel Divinity entries
    that lack them. Safe to call on already-enriched data — no-ops if sub_options exist."""
    for feat in feature_data:
        name = feat.get("name", "")
        if "channel divinity" not in name.lower():
            continue
        if " | " not in name:
            continue
        if feat.get("sub_options"):
            continue  # Already enriched
        # Parse composite name into sub-options
        segments = name.split(" | ")
        sub_options = []
        for seg in segments:
            seg = seg.strip()
            sub_name = seg
            if ": " in seg:
                maybe_lvl, rest = seg.split(": ", 1)
                if maybe_lvl.startswith("L") and maybe_lvl[1:].replace("-", "").replace("+", "").isdigit():
                    sub_name = rest
            sub_key = sub_name.lower()
            sub_desc = FEATURE_DESCRIPTIONS.get(sub_key, "")
            sub_options.append({"name": sub_name, "description": sub_desc})
        feat["sub_options"] = sub_options
        # Update description to list available options
        option_names = [so["name"] for so in sub_options if "channel divinity:" in so["name"].lower()]
        if option_names:
            existing_desc = feat.get("description", "")
            if "Available options:" not in existing_desc:
                feat["description"] = f"{existing_desc}\n\nAvailable options: {', '.join(option_names)}."


def _add_source_to_features(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add 'source' field from SRD_FEATURES lookup.
    Tries exact name match first, then strips class suffix for composite names.
    Safe to call on already-enriched data — no-ops if source already present.
    Skips 'SRD 5.1' and bare 'PHB 2014' (no page) — fallback handles those."""
    for feat in feature_data:
        src = feat.get("source", "")
        if src and src != "SRD 5.1" and src != "PHB 2014":
            continue  # Already has a real source
        name = feat.get("name", "")
        key = name.lower()
        # Try exact match
        _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == key), "")
        # Try stripping class suffix: "Spellcasting: Cleric" → try base "Spellcasting"
        if not _src and ": " in name:
            base_name = name.split(": ", 1)[0].strip().lower()
            _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == base_name), "")
        # For composite names with " | ", try each segment
        if not _src and " | " in name:
            for seg in name.split(" | "):
                seg = seg.strip()
                # Strip level prefix like "L2: "
                if ": " in seg:
                    maybe_lvl, rest = seg.split(": ", 1)
                    if maybe_lvl.startswith("L") and maybe_lvl[1:].replace("-","").replace("+","").isdigit():
                        seg = rest
                seg_key = seg.lower()
                _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == seg_key), "")
                if _src:
                    break
        if _src:
            feat["source"] = _src


def _add_dice_to_features(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add 'dice' field from FEATS lookup.
    Only adds if feat doesn't already have a dice field (preserves hardcoded values)."""
    for feat in feature_data:
        if feat.get("dice"):
            continue  # Already has dice (hardcoded or from previous enrichment)
        name = feat.get("name", "")
        key = name.lower()
        # Look up in FEATS dict
        _feat = FEATS.get(key)
        if _feat and _feat.get("dice"):
            feat["dice"] = _feat["dice"]


def _add_invocation_levels(feature_data: list[dict], class_name: str, char_level: int) -> None:
    """Inject Eldritch Invocation level cards for Warlocks.

    The SRD only lists 'Eldritch Invocations' at level 2, but warlocks gain
    additional invocations at 5, 7, 9, 12, 15, and 18. This adds a feature
    card at each level where invocations are gained, so per-level choices
    can be displayed separately."""
    if class_name != "Warlock":
        return
    inv_levels = INVOCATION_LEVELS.get("Warlock", [])
    existing_levels = {
        int(f["level"].replace("L", ""))
        for f in feature_data
        if f.get("name") == "Eldritch Invocations"
    }
    for inv_lvl in inv_levels:
        if inv_lvl <= char_level and inv_lvl not in existing_levels:
            # Insert at correct position
            insert_at = 0
            for i, f in enumerate(feature_data):
                fl = int(f.get("level", "L0").replace("L", ""))
                if fl > inv_lvl:
                    insert_at = i
                    break
                insert_at = i + 1
            feature_data.insert(insert_at, {
                "name": "Eldritch Invocations",
                "level": f"L{inv_lvl}",
                "description": f"Gain additional Eldritch Invocations at level {inv_lvl}.",
            })



# ── AI Generation Routes (extracted to ai_routes.py) ─────
from routes.characters.ai_routes import router as _ai_router
from routes.characters.ai_routes import (
    _call_gemini, _call_openrouter, _call_ollama,
    _fetch_stable_horde_image, _extract_json, _validate_and_fix,
    _fallback_generate, _random_items, _fallback_background,
    _fallback_history, _try_ai_enrich_prompt, _try_generate_image,
    _fallback_portrait_prompt, _calculate_ac, _calculate_attacks,
    _pick_skills,
)
router.include_router(_ai_router)

