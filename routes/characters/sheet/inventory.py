"""Equipment and attunement: the manual-equipment merge into ITEM_INDEX
at import, the attunement table, item properties and compute_item_effects.

Split out of the single-module sheet.py; behaviour and URLs are unchanged.
"""

from core.data import ITEM_INDEX, SRD_MAGIC_ITEMS, _load_manual_json
from core.features import _parse_enhancement, _resolve_source

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
        "goggles of night": {"darkvision": 60, "note": "Darkvision 60 ft (if you already have darkvision, its range increases by 60 ft)."},
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
        "darkvision": 0,
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

        # Darkvision (item grants/extends darkvision)
        dv = props.get("darkvision", 0)
        if dv:
            result["darkvision"] = max(result["darkvision"], int(dv))

        # Notes
        if props.get("note"):
            result["notes"].append(f"{item_name}: {props['note']}")

    return result

# Initialize item properties at module load
_build_item_properties()
