"""Leveling / spell-slot helper core.

Extracted from routes/characters/all.py (2026-07-31). Pure functions
over SRD + manual data — no route logic. all.py re-exports these so
existing callers keep working; route modules may import them directly.

Only import from main / data / stdlib here — importing from
routes.characters.* creates circulars.
"""

import re

from main import SRD_LEVELS, SRD_SPELLS


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
