"""Leveling / spell-slot helper core.

Extracted from routes/characters/all.py (2026-07-31). Pure functions
over SRD + manual data — no route logic. all.py re-exports these so
existing callers keep working; route modules may import them directly.

Only import from main / data / stdlib here — importing from
routes.characters.* creates circulars.
"""

import re
import math
import functools
import json as _json

from main import SRD_LEVELS, SRD_SPELLS
from main import (
    CLASSES, EXPERTISE_LEVELS, FEATURE_ACTION_TYPES, FEATURE_DESCRIPTIONS,
    FULL_CASTERS, HALF_CASTERS, INVOCATION_LEVELS, LIMITED_USE,
    MULTICLASS_PREREQS, MULTICLASS_PROFICIENCIES, PACT_CASTERS,
    PREPARED_CASTERS, RACIAL_TRAIT_DESCS, RECOMMENDED_FEATS,
    SPELLS_KNOWN_CASTERS, SPELL_DICE, SRD_FEATURES, SUBCLASS_FEATURES,
    _manual_races_raw,
)


MAGIC_INITIATE_CLASSES = ["Bard", "Cleric", "Druid", "Sorcerer", "Warlock", "Wizard"]


def _ordinal(n: int) -> str:
    """Return ordinal string: 1st, 2nd, 3rd, etc."""
    if 11 <= n % 100 <= 13: return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


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


def _scaled_dice_display(dice_info: dict, character_level: int | None) -> str:
    """Return the dice display string, scaling cantrips to character level.

    If ``display`` contains ``{scaled}``, the scaled dice string is substituted
    there (supports complex patterns like '+{scaled} / {scaled}+MOD fire').
    Otherwise the entire display is replaced by the scaled dice string.

    ``display_at_1`` (optional) is used when character_level < 5 —
    some cantrips (Green-Flame Blade, Booming Blade) have no bonus dice
    before tier 1.
    """
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
        result = display.replace("{scaled}", scaled)
        # Wounded variant (e.g. Toll the Dead: d8->d12)
        if "{scaled_w}" in result:
            base_w = dice_info.get("base_dice_wounded", "")
            mw = re.match(r"(\d+)d(\d+)", base_w) if base_w else None
            if mw:
                count_w = int(mw.group(1))
                die_w = mw.group(2)
                if character_level >= 17: count_w *= 4
                elif character_level >= 11: count_w *= 3
                elif character_level >= 5: count_w *= 2
                result = result.replace("{scaled_w}", f"{count_w}d{die_w}")
            else:
                result = result.replace("{scaled_w}", scaled)
        return result
    return scaled


# ── Progression core (batch 2, extracted from all.py 2026-07-31) ──

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


PROFICIENCY_BONUS = {1:2,2:2,3:2,4:2,5:3,6:3,7:3,8:3,9:4,10:4,11:4,12:4,13:5,14:5,15:5,16:5,17:6,18:6,19:6,20:6}


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


@functools.cache
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


def get_asi_levels(level: int, class_name: str = "") -> list[int]:
    """List of ASI levels the character has passed."""
    asis = {4,8,12,16,19}
    if class_name == "Fighter": asis.update({6,14})  # Fighter gets extra ASIs (PHB p.71)
    return sorted([a for a in asis if a <= level])


def get_feats_for_level(class_name: str, level: int) -> list[str]:
    """Recommended feats based on how many ASIs the character has taken."""
    asi_count = len(get_asi_levels(level, class_name))
    all_feats = RECOMMENDED_FEATS.get(class_name, RECOMMENDED_FEATS["Fighter"])
    # Pick top N feats (or fewer if not enough levels)
    count = min(asi_count, len(all_feats))
    return all_feats[:count]


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
        # Strip parenthetical suffix like "(1 use)" or "(d6)" for matching
        import re
        _strip_key = re.sub(r'\s*\([^)]*\)\s*$', '', key).strip()
        # Try subclass-specific description first (disambiguates shared names like "Bonus Proficiencies")
        desc = ""
        is_subclass_feature = False
        if subclass:
            sc_key = f"{subclass}::{key}"
            desc = FEATURE_DESCRIPTIONS.get(sc_key, "")
            if desc:
                is_subclass_feature = True
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
        elif is_subclass_feature:
            # Subclass-specific features should use the subclass's source book
            # (e.g. Swashbuckler→Xanathar's, not Rogue→PHB)
            _ss_map = CLASSES.get(class_name, {}).get("_subclass_sources", {})
            _sc_src = _ss_map.get(subclass, "")
            if _sc_src:
                entry["source"] = _sc_src
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
                if cls_name.lower() in _strip_key or _strip_key in cls_name.lower():
                    source_class = cls_name
                    source_level = class_levels[cls_name]
                    break
            if not source_class:
                # Fallback: check LIMITED_USE for the feature's registered class
                for lkey, lu in LIMITED_USE.items():
                    if lkey == _strip_key or lkey in _strip_key or _strip_key.startswith(lkey) or lkey.startswith(_strip_key):
                        lu_class = lu.get("class", "")
                        if lu_class and lu_class in class_levels:
                            source_class = lu_class
                            source_level = class_levels[lu_class]
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
            if _strip_key not in _NON_LIMITED_FEATURES:
                # Feature name aliases (raw name → LIMITED_USE key)
                _FEAT_ALIASES = {"font of magic": "sorcery points"}
                for lkey, lu in LIMITED_USE.items():
                    _match_key = _FEAT_ALIASES.get(_strip_key, _strip_key)
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


# ── Level-up data constants (moved from all.py 2026-07-31) ──

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


MANEUVER_PICKS: dict[int,int] = {3:3,7:2,10:2,15:2}  # level → total known


MAGICAL_SECRETS_LEVELS: dict[str, list[int]] = {"Bard": [10, 14, 18], "College of Lore": [6]}


MAGICAL_SECRETS_PICKS: dict[int,int] = {6:2,10:2,14:2,18:2}


TOTEM_SPIRIT_LEVELS: dict[str, list[int]] = {"Path of the Totem Warrior": [3, 6, 14]}


TOTEM_SPIRIT_TIER_LABELS: dict[int, str] = {3:"Totem Spirit", 6:"Aspect of the Beast", 14:"Totemic Attunement"}


HUNTERS_PREY_LEVELS: dict[str, int] = {"Hunter": 3}


FAVORED_ENEMY_LEVELS: dict[str, list[int]] = {"Ranger": [1, 6, 14]}


FAVORED_TERRAIN_LEVELS: dict[str, list[int]] = {"Ranger": [1, 6, 10]}


INFUSION_LEVELS: dict[str, int] = {"Artificer": 2}


INFUSION_PICKS: dict[int,int] = {2:4}  # level → known infusions


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


WARLOCK_EXPANDED_SPELLS_BY_LEVEL: dict[str, dict[int, list[str]]] = {
    # PHB p.109
    "The Fiend": {
        1: ["Burning Hands", "Command"],
        3: ["Blindness/Deafness", "Scorching Ray"],
        5: ["Fireball", "Stinking Cloud"],
        7: ["Fire Shield", "Wall of Fire"],
        9: ["Flame Strike", "Hallow"],
    },
    # PHB p.108
    "The Archfey": {
        1: ["Faerie Fire", "Sleep"],
        3: ["Calm Emotions", "Phantasmal Force"],
        5: ["Blink", "Plant Growth"],
        7: ["Dominate Beast", "Greater Invisibility"],
        9: ["Dominate Person", "Seeming"],
    },
    # PHB p.109-110
    "The Great Old One": {
        1: ["Dissonant Whispers", "Tasha's Hideous Laughter"],
        3: ["Detect Thoughts", "Phantasmal Force"],
        5: ["Clairvoyance", "Sending"],
        7: ["Dominate Beast", "Evard's Black Tentacles"],
        9: ["Dominate Person", "Telekinesis"],
    },
    # XGtE p.56-57
    "The Celestial": {
        1: ["Cure Wounds", "Guiding Bolt"],
        3: ["Flaming Sphere", "Lesser Restoration"],
        5: ["Daylight", "Revivify"],
        7: ["Guardian of Faith", "Wall of Fire"],
        9: ["Flame Strike", "Greater Restoration"],
    },
    # XGtE p.55-56
    "The Hexblade": {
        1: ["Shield", "Wrathful Smite"],
        3: ["Blur", "Branding Smite"],
        5: ["Blink", "Elemental Weapon"],
        7: ["Phantasmal Killer", "Staggering Smite"],
        9: ["Banishing Smite", "Cone of Cold"],
    },
}


# ── Choice-system constants (moved from all.py 2026-07-31; shadow data.py versions) ──

METAMAGIC_LEVELS: dict[str, list[int]] = {"Sorcerer": [3, 10, 17]}


METAMAGIC_PICKS: dict[int, int] = {3: 2, 10: 1, 17: 1}  # level → number of choices


INVOCATION_LEVELS: dict[str, list[int]] = {"Warlock": [2, 5, 7, 9, 12, 15, 18]}


INVOCATION_PICKS: dict[int,int] = {2:2,5:1,7:1,9:1,12:1,15:1,18:1}


PACT_BOON_LEVELS: dict[str, int] = {"Warlock": 3}


MANEUVER_LEVELS: dict[str, list[int]] = {"Battle Master": [3, 7, 10, 15]}


CANTRIPS_PROGRESSION: dict[str, dict[int, int]] = {
    "full": {1: 2, 4: 3, 10: 4},
    "warlock": {1: 2, 4: 3, 10: 4},
    "cleric": {1: 3, 4: 4, 10: 5},
}
