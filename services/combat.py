"""Combat / racial-trait helpers — traits, attacks, armor proficiency.

Extracted from main.py (2026-08-12). Pure data registries come from
data.py; main-built registries (ITEM_INDEX, WEAPONS, SUBRACE_TRAITS,
...) are imported lazily at call time to avoid import-time circulars.
"""

from __future__ import annotations

import json
import re

from data import (
    CLASSES, DRACONIC_ANCESTRIES, RACES, RACIAL_TRAIT_DESCS,
    RACIAL_TRAIT_EFFECTS,
)
from services.items import _resolve_item_key

NAMED_ITEM_TYPES: dict | None = None  # computed lazily on first _get_named_item_types() call

def get_racial_trait_effects(race_name: str, subrace: str = "", ancestry: str = "") -> dict:
    """Return merged mechanical effects for a race/subrace combination.

    Returns {armor_profs, weapon_profs, tool_profs, skill_profs,
             damage_resist, condition_immune, speed, darkvision, hp_per_level}.

    For Dragonborn, pass the draconic ancestry color (e.g. 'Gold') to get
    the correct damage resistance from the Draconic Ancestry table (PHB p.34).

    Callers should merge these into DB-stored values for display (render-time)
    and into the character record at creation time.
    """
    from main import SUBRACE_TRAITS
    result = {
        "armor_profs": [],
        "weapon_profs": [],
        "tool_profs": [],
        "skill_profs": [],
        "damage_resist": [],
        "condition_immune": [],
        "speed": None,
        "darkvision": None,
        "hp_per_level": 0,
        "natural_armor": None,
    }

    rl = race_name.lower()

    # ── Base racial effects not tied to named traits ──

    # Dwarf: Dwarven Combat Training (PHB p.20)
    if "dwarf" in rl:
        result["weapon_profs"].extend(["Battleaxe", "Handaxe", "Light Hammer", "Warhammer"])

    # Dark Elf (Drow): Drow Weapon Training (PHB p.24)
    sub_lower = subrace.lower() if subrace else ""
    if "drow" in rl or "dark elf" in rl or "drow" in sub_lower or "dark elf" in sub_lower:
        result["weapon_profs"].extend(["Rapier", "Shortsword", "Hand Crossbow"])

    # ── Collect all named traits for this race + subrace ──
    race_data = RACES.get(race_name, {})
    all_traits = list(race_data.get("traits", []))
    if subrace:
        all_traits.extend(SUBRACE_TRAITS.get(subrace, []))

    for trait_name in all_traits:
        effects = RACIAL_TRAIT_EFFECTS.get(trait_name, {})
        for key in ("armor_profs", "weapon_profs", "tool_profs", "skill_profs",
                     "damage_resist", "condition_immune"):
            for val in effects.get(key, []):
                if val not in result[key]:
                    result[key].append(val)
        for key in ("speed", "darkvision"):
            if effects.get(key) is not None:
                result[key] = effects[key]
        result["hp_per_level"] += effects.get("hp_per_level", 0)

    # Natural armor: read from per-race data (avoids global-trait conflicts
    # where Loxodon/Tortle/Lizardfolk all share "Natural Armor" trait name)
    if race_data.get("natural_armor"):
        result["natural_armor"] = race_data["natural_armor"]

    # ── Dragonborn ancestry resistance (PHB p.34) ──
    if ancestry and ancestry in DRACONIC_ANCESTRIES:
        resist_type = DRACONIC_ANCESTRIES[ancestry]["resist"]
        if resist_type not in result["damage_resist"]:
            result["damage_resist"].append(resist_type)

    return result


def _build_racial_traits(char: dict) -> list:
    """Build a list of {name, desc, source} for the character's race and subrace traits."""
    from main import SUBRACE_TRAITS, _TRAIT_DICE, _trait_page_map
    result = []
    race_name = char.get("race", "")
    subrace = char.get("subrace", "")

    race_data = RACES.get(race_name)
    if race_data:
        for t in race_data.get("traits", []):
            # Always try race-prefixed key first to avoid cross-race trait name conflicts
            desc = RACIAL_TRAIT_DESCS.get(f"{race_name}::{t}", "")
            if not desc:
                desc = RACIAL_TRAIT_DESCS.get(t, "")
            if desc:
                # Look up page-accurate source
                src = _trait_page_map.get(t, "")
                if not src:
                    src = race_name
                dice = _TRAIT_DICE.get(t, "")
                result.append({"name": t, "desc": desc, "source": src, "dice": dice})

    if subrace:
        sub_traits = SUBRACE_TRAITS.get(subrace, [])
        for t in sub_traits:
            # Always try subrace-prefixed key first to avoid cross-race conflicts
            desc = RACIAL_TRAIT_DESCS.get(f"{subrace}::{t}", "")
            if not desc:
                desc = RACIAL_TRAIT_DESCS.get(t, "")
            if desc:
                # Look up page-accurate source
                src = _trait_page_map.get(t, "")
                if not src:
                    src = subrace
                dice = _TRAIT_DICE.get(t, "")
                result.append({"name": t, "desc": desc, "source": src, "dice": dice})

    return result


def _subrace_traits(manual_entry: dict) -> list[str]:
    """Extract subrace-specific trait names, filtering out generic ones."""
    from main import _GENERIC_TRAITS
    traits = []
    for t in manual_entry.get("traits", []):
        name = t.get("name", "")
        if name and name.lower() not in _GENERIC_TRAITS:
            traits.append(name)
    return traits


def _find_weapon(item_name: str) -> dict | None:
    """Match an inventory item name to a known SRD weapon. Fuzzy match."""
    from main import ITEM_INDEX, WEAPONS
    name = item_name.lower().strip()
    # Strip leading quantity (e.g., "2 Handaxes" → "Handaxes")
    import re
    name = re.sub(r'^\d+\s+', '', name)
    # Strip trailing 's' for plural matching (Handaxes → Handaxe)
    name_singular = name.rstrip('s') if name.endswith('s') and len(name) > 3 else name
    # Direct match
    if name in WEAPONS:
        return WEAPONS[name]
    if name_singular in WEAPONS:
        return WEAPONS[name_singular]
    # Substring match — check if any known weapon name appears in the item
    for wpn_name, wpn_data in WEAPONS.items():
        if wpn_name in name or name in wpn_name or wpn_name in name_singular or name_singular in wpn_name:
            return wpn_data
    # Keyword fallback
    # Check ITEM_INDEX for magic weapons with base_weapon
    idx_entry = _resolve_item_key(name) or _resolve_item_key(name_singular)
    if not idx_entry:
        for k, v in ITEM_INDEX.items():
            if k in name or name in k or k in name_singular or name_singular in k:
                idx_entry = v
                break
    if idx_entry and idx_entry.get("base_weapon"):
        return WEAPONS.get(idx_entry["base_weapon"])

    keywords = ["sword","axe","hammer","bow","dagger","mace","spear","flail",
                "rapier","scimitar","glaive","halberd","pike","lance","whip",
                "javelin","crossbow","club","staff","sling","dart","trident"]
    for kw in keywords:
        if kw in name or kw in name_singular:
            generic = {
                "sword": WEAPONS["longsword"], "axe": WEAPONS["battleaxe"],
                "hammer": WEAPONS["warhammer"], "bow": WEAPONS["shortbow"],
                "dagger": WEAPONS["dagger"], "mace": WEAPONS["mace"],
                "spear": WEAPONS["spear"], "flail": WEAPONS["flail"],
                "rapier": WEAPONS["rapier"], "scimitar": WEAPONS["scimitar"],
                "glaive": WEAPONS["glaive"], "halberd": WEAPONS["halberd"],
                "pike": WEAPONS["pike"], "lance": WEAPONS["lance"],
                "whip": WEAPONS["whip"], "javelin": WEAPONS["javelin"],
                "crossbow": WEAPONS["crossbow, light"], "club": WEAPONS["club"],
                "staff": WEAPONS["quarterstaff"], "sling": WEAPONS["sling"],
                "dart": WEAPONS["dart"], "trident": WEAPONS["trident"],
            }
            if kw in generic:
                return generic[kw]
    return None


def _parse_enhancement(item_name: str) -> int:
    """Extract +1, +2, +3 enhancement bonus from item name like 'Longsword +1'."""
    import re
    m = re.search(r'\+\s*([123])\s*$', str(item_name))
    return int(m.group(1)) if m else 0


def _build_attack_for_weapon(item_name: str, weapon_data: dict, abilities: dict, prof_bonus: int, qty: int = 1, enhancement: int = 0) -> dict:
    """Build an attack entry from weapon data and character stats."""
    damage = weapon_data["damage"]
    dmg_type = weapon_data["type"]
    props = weapon_data.get("props", [])

    # Determine attack ability
    is_ranged = "ranged" in weapon_data.get("category", "")
    is_thrown = any("thrown" in p for p in props)
    is_finesse = "finesse" in props

    if is_ranged and not is_thrown:
        ability = "dexterity"
    elif is_finesse:
        # Finesse: use better of STR or DEX
        ability = "dexterity" if abilities.get("dexterity",10) > abilities.get("strength",10) else "strength"
    else:
        ability = "strength"

    ab_mod = (abilities.get(ability, 10) - 10) // 2
    attack_bonus = ab_mod + prof_bonus + enhancement

    # Damage string
    if damage == "—":
        dmg_str = "Special"
    else:
        dmg_mod = ab_mod + enhancement
        dmg_str = f"{damage} + {dmg_mod} {dmg_type}"

    # Range
    range_str = None
    for p in props:
        if "thrown" in p:
            range_str = p.replace("thrown ", "Thrown ").replace("(", "").replace(")", "")
        elif "ammunition" in p:
            range_str = p.replace("ammunition ", "Range ").replace("(", "").replace(")", "")

    return {
        "name": item_name,
        "attack_bonus": attack_bonus,
        "damage": dmg_str,
        "range": range_str,
        "properties": [p for p in props if not ("thrown" in p or "ammunition" in p)],
        "qty": qty,
        "enhancement": enhancement,
        "description": (_resolve_item_key(item_name) or {}).get("description", ""),
    }


def _build_inventory_attacks(character: dict) -> list:
    """Scan inventory and equipped items for weapons and build attack entries.
    Includes magazine/charge tracking for firearms and charged weapons."""
    abilities = {a: character.get(a, 10) for a in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
    prof_bonus = character.get("proficiency_bonus", 2)

    # Build lookup of equipped items for charges_used
    equipped_charges = {}
    for item in (character.get("equipped") or []):
        if isinstance(item, dict):
            equipped_charges[item.get("name", "").lower()] = item.get("charges_used", 0)

    attacks = []
    seen = set()

    def _scan(items):
        for item in (items or []):
            if isinstance(item, dict):
                name = item.get("name", "")
                qty = item.get("qty", 1)
            else:
                name = str(item)
                qty = 1
            key = name.lower()
            if key in seen:
                continue
            wpn = _find_weapon(name)
            if wpn:
                # Parse enhancement from item dict or name
                enh = item.get("enhancement", 0) if isinstance(item, dict) else _parse_enhancement(name)
                atk = _build_attack_for_weapon(name, wpn, abilities, prof_bonus, qty, enhancement=enh)
                # Enrich with charge/magazine data from ITEM_INDEX
                item_info = _resolve_item_key(key) or {}
                max_charges = item_info.get("charges")
                if max_charges:
                    used = equipped_charges.get(key, 0)
                    atk["max_charges"] = max_charges
                    atk["current_charges"] = max(0, max_charges - used)
                    atk["charge_recharge"] = item_info.get("charge_recharge", "")
                # Pass through source for clickable badge
                if item_info.get("source"):
                    atk["source"] = item_info["source"]
                attacks.append(atk)
                seen.add(key)

    _scan(character.get("inventory", []))
    _scan(character.get("equipped", []))
    return attacks


def _build_charged_item_attacks(character: dict) -> list:
    """Scan equipped items for charged magic items (wands, staves, rods, etc.)
    and build charge-card entries. Tracks charges_used per item.
    Weapons (including firearms) are excluded — they appear in Weapon Attacks instead."""
    charged = []
    seen = set()
    for item in (character.get("equipped") or []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        key = name.lower()
        if key in seen:
            continue
        info = _resolve_item_key(key)
        if not info or not info.get("charges"):
            continue
        # Skip weapons — they render in the Weapon Attacks card
        if _find_weapon(name):
            continue
        max_charges = info["charges"]
        used = item.get("charges_used", 0)
        current = max(0, max_charges - used)
        charged.append({
            "name": name,
            "max_charges": max_charges,
            "current_charges": current,
            "type": info.get("type", "Magic Item"),
            "rarity": info.get("rarity", ""),
            "description": info.get("description", "")[:200],
            "recharge": info.get("charge_recharge"),
        })
        seen.add(key)
    return charged


def _normalize_equipped(equipped: list) -> list:
    """Convert equipped items to [{name, qty, enhancement}] format. Handles old string-list format."""
    if not equipped:
        return []
    result = []
    for item in equipped:
        if isinstance(item, dict):
            result.append({
                "name": item.get("name", ""),
                "qty": item.get("qty", 1),
                "enhancement": item.get("enhancement", 0),
            })
        else:
            result.append({"name": str(item), "qty": 1, "enhancement": 0})
    return result


def _build_named_item_types() -> dict:
    """Build {name_lower: 'wpn'|'arm'} map from ITEM_INDEX for frontend badge detection."""
    from main import ITEM_INDEX, SRD_MAGIC_ITEMS, WEAPONS
    result = {}
    wpn_names = [w for w in WEAPONS if w not in ("unarmed strike",)]
    for key, entry in ITEM_INDEX.items():
        lower = key.lower()
        bw = entry.get("base_weapon", "")
        itype = entry.get("type", "").lower()
        desc = entry.get("description", "").lower()
        name_lower = entry.get("name", "").lower()
        # Known weapons: has base_weapon
        if bw and bw in WEAPONS:
            result[lower] = "wpn"
            continue
        # Check type or description for weapon patterns
        is_wpn = "weapon" in itype
        if not is_wpn:
            for wpn_name in wpn_names:
                if wpn_name in desc or wpn_name in name_lower:
                    is_wpn = True
                    break
        if is_wpn:
            result[lower] = "wpn"
            continue
        # Known armor
        if itype in ("armor", "shield", "heavy armor", "medium armor", "light armor"):
            result[lower] = "arm"
            continue
        for arm_kw in ["armor", "plate", "mail", "shield", "leather", "breastplate", "studded"]:
            if arm_kw in desc or arm_kw in name_lower:
                if "natural armor" not in desc:
                    result[lower] = "arm"
                    break

    # Also classify magic items by their equipment_category (SRD + manual merged).
    # Runs AFTER ITEM_INDEX to override incorrect keyword-based guesses
    # (e.g. "sentinel shield" misclassified as wpn, "+1 yklwa" misclassified as arm).
    for item in SRD_MAGIC_ITEMS:
        name = item.get("name", "")
        if not name:
            continue
        key = name.lower()
        cat = (item.get("equipment_category", {}) or {}).get("name", "").lower()
        if cat == "weapon":
            result[key] = "wpn"
        elif cat in ("armor", "shield"):
            result[key] = "arm"

    return result


def _get_named_item_types() -> dict:
    """Return cached {name_lower: 'wpn'|'arm'} map. Computed on first call."""
    global NAMED_ITEM_TYPES
    if NAMED_ITEM_TYPES is None:
        NAMED_ITEM_TYPES = _build_named_item_types()
    return NAMED_ITEM_TYPES


def _equipped_names(equipped: list) -> list:
    """Extract just the names from a [{name, qty}] equipped list or string-list."""
    result = []
    for e in (equipped or []):
        if isinstance(e, dict) and e.get("name"):
            result.append(e["name"])
        elif isinstance(e, str):
            result.append(e)
    return result


def _normalize_armor_profs(raw: list | str | None) -> set[str]:
    """Normalize armor proficiencies to canonical set.

    Handles legacy string formats like "Light armor, Medium armor, Shields"
    and array formats like ["Light armor", "Medium armor"].
    """
    profs = set()
    if not raw:
        return profs
    if isinstance(raw, str):
        if raw.lower() == "none":
            return profs
        if raw.lower().startswith("all"):
            profs.update(["Light armor", "Medium armor", "Heavy armor"])
            if "shield" in raw.lower():
                profs.add("Shields")
            return profs
        parts = [p.strip() for p in raw.split(",")]
        raw = parts
    for p in (raw or []):
        p = p.strip()
        if not p or p.lower() == "none":
            continue
        pl = p.lower()
        if pl.startswith("all"):
            profs.update(["Light armor", "Medium armor", "Heavy armor"])
            if "shield" in pl:
                profs.add("Shields")
            continue
        if "light" in pl:
            profs.add("Light armor")
        if "medium" in pl:
            profs.add("Medium armor")
        if "heavy" in pl:
            profs.add("Heavy armor")
        if "shield" in pl:
            profs.add("Shields")
    return profs


def get_character_armor_profs(char: dict, class_levels: dict = None) -> set[str]:
    """Full set of armor proficiencies from all sources."""
    profs = set()
    if class_levels:
        for cls_name in class_levels:
            cls_data = CLASSES.get(cls_name, {})
            profs.update(_normalize_armor_profs(cls_data.get("armor", [])))
    else:
        cls_data = CLASSES.get(char.get("class_name", ""), {})
        profs.update(_normalize_armor_profs(cls_data.get("armor", [])))
    # Racial traits
    re = get_racial_trait_effects(char.get("race", ""), char.get("subrace", ""),
                                  char.get("dragonborn_ancestry", ""))
    profs.update(_normalize_armor_profs(re.get("armor_profs", [])))
    # DB-stored
    db_armor = char.get("armor_proficiencies", [])
    if isinstance(db_armor, str):
        try:
            db_armor = json.loads(db_armor)
        except (json.JSONDecodeError, TypeError):
            db_armor = []
    profs.update(_normalize_armor_profs(db_armor))
    return profs


def _resolve_armor_item(eq_lower: str) -> dict | None:
    """Look up armor item in SRD equipment data, with longest-substring fallback for magic variants."""
    from main import _ARMOR_LOOKUP, _ARMOR_STATS
    item = _ARMOR_STATS.get(eq_lower)
    if item:
        ec = (item.get("equipment_category") or {}).get("name", "")
        if ec == "Armor":
            return item
    # Fallback: match against known SRD armor names (longest first for magic armor)
    best_match = None
    best_len = 0
    for ak, ai in _ARMOR_LOOKUP.items():
        if ak in eq_lower and len(ak) > best_len:
            best_match = ai
            best_len = len(ak)
    return best_match


def check_armor_proficiency_from_set(profs: set[str], armor_category: str) -> dict:
    """Check if proficient with given armor category. Returns {proficient, penalty, source}."""
    from main import ARMOR_PROFICIENCY_MAP
    if not armor_category:
        return {"proficient": True, "penalty": None, "source": ""}
    if armor_category == "Shield":
        proficient = "Shields" in profs
    else:
        required = ARMOR_PROFICIENCY_MAP.get(armor_category, armor_category)
        proficient = required in profs
    return {
        "proficient": proficient,
        "penalty": None if proficient else (
            "Disadvantage on Strength/Dexterity ability checks, saving throws, "
            "and attack rolls. Cannot cast spells."
        ),
        "source": "PHB p.144",
    }



__all__ = ["get_racial_trait_effects", "_build_racial_traits", "_subrace_traits", "_find_weapon", "_parse_enhancement", "_build_attack_for_weapon", "_build_inventory_attacks", "_build_charged_item_attacks", "_normalize_equipped", "_build_named_item_types", "_get_named_item_types", "_equipped_names", "_normalize_armor_profs", "get_character_armor_profs", "_resolve_armor_item", "check_armor_proficiency_from_set"]