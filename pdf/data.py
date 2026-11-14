"""Normalise a character row into the dict the renderers consume."""

import json

from .text import (
    mod_int,
    _build_condensed_features,
    _build_equipment_appendix_text,
    _build_full_feature_text,
    _build_page1_features_text,
    _build_short_features_text,
    _build_spell_appendix_text,
    _build_summons_appendix_text,
)

# ═══════════════════════════════════════════════════════════════
#  DATA BUILDER
# ═══════════════════════════════════════════════════════════════
def build_char_data(row, db_cursor=None, racial_traits=None):
    if hasattr(row, "keys"):
        d = dict(row)
    else:
        raise TypeError("build_char_data requires sqlite3.Row")

    json_fields = [
        "skills", "tool_proficiencies", "weapon_proficiencies",
        "armor_proficiencies", "languages", "features", "inventory",
        "equipped", "feature_data", "attacks_data", "spell_slot_data",
        "save_proficiencies", "damage_resistances", "damage_immunities",
        "damage_vulnerabilities", "condition_immunities", "class_levels",
        "attuned_items", "background_data", "spell_slots_used",
        "personality_data", "summons",
    ]
    for field in json_fields:
        val = d.get(field)
        if isinstance(val, str) and val:
            try:
                d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass

    for field in json_fields:
        if d.get(field) is None:
            d[field] = {} if field in ("spell_slots_used", "spell_slot_data", "background_data", "personality_data") else []

    for f in ["personality", "backstory", "alignment", "subrace", "subclass"]:
        d.setdefault(f, "")

    pd = d.get("personality_data", {}) or {}
    bg_data = d.get("background_data", {}) or {}
    # Personality Traits: personality_data.traits → old personality field
    d["personality_traits"] = pd.get("traits", "") or d.get("personality", "")
    d["ideals"] = pd.get("ideals", "") or bg_data.get("ideals", "") or d.get("ideals", "") or ""
    d["bonds"] = pd.get("bonds", "") or bg_data.get("bonds", "") or d.get("bonds", "") or ""
    d["flaws"] = pd.get("flaws", "") or bg_data.get("flaws", "") or d.get("flaws", "") or ""
    # Physical appearance
    for fld in ("age", "height", "weight", "eyes", "skin", "hair"):
        d[fld] = pd.get(fld, "")
    # Allies & Organizations
    d["allies"] = pd.get("allies", "")

    d["initiative"] = mod_int(d.get("dexterity", 10))
    d["passive_perception"] = d.get("passive_perception", 10) or 10
    d["inspiration"] = d.get("inspiration", 0) or 0
    d["proficiency_bonus"] = d.get("proficiency_bonus", 2) or 2
    d["str_mod"] = mod_int(d.get("strength", 10))
    d["dex_mod"] = mod_int(d.get("dexterity", 10))
    d["con_mod"] = mod_int(d.get("constitution", 10))
    d["int_mod"] = mod_int(d.get("intelligence", 10))
    d["wis_mod"] = mod_int(d.get("wisdom", 10))
    d["cha_mod"] = mod_int(d.get("charisma", 10))

    hd = d.get("hit_dice", "1d8") or "1d8"
    d["hit_dice_type"] = hd
    d["hit_dice_total"] = d.get("level", 1)
    d["hit_dice_used"] = d.get("hit_dice_used", 0) or 0

    ss = d.get("spell_slot_data", {}) or {}
    d["spell_slots"] = ss.get("by_level", {})
    d["spell_slots_used"] = d.get("spell_slots_used", {}) or {}
    d["cp"] = d.get("cp", 0) or 0
    d["gp"] = d.get("gp", 0) or 0

    d["spells"] = []
    d["is_caster"] = False
    if db_cursor:
        spells = db_cursor.execute(
            "SELECT spell_name, spell_level, prepared FROM character_spells WHERE character_id=? ORDER BY spell_level, spell_name",
            (d["id"],),
        ).fetchall()
        if spells:
            d["spells"] = [(s[0], s[1], s[2]) for s in spells]
            d["is_caster"] = True

    class_to_ability = {
        "Bard": "CHA", "Cleric": "WIS", "Druid": "WIS",
        "Paladin": "CHA", "Ranger": "WIS", "Sorcerer": "CHA",
        "Warlock": "CHA", "Wizard": "INT",
    }
    d["spell_ability"] = d.get("spell_ability") or class_to_ability.get(d.get("class_name", ""), "")

    ab_scores = {
        "STR": d.get("strength", 10), "DEX": d.get("dexterity", 10),
        "CON": d.get("constitution", 10), "INT": d.get("intelligence", 10),
        "WIS": d.get("wisdom", 10), "CHA": d.get("charisma", 10),
    }
    sa = d["spell_ability"]
    spell_ab_mod = mod_int(ab_scores.get(sa, 10)) if sa else 0
    prof = d["proficiency_bonus"]
    d["spell_save_dc"] = 8 + prof + spell_ab_mod
    d["spell_attack_bonus"] = prof + spell_ab_mod

    # Merge racial traits into features and feature_data for the PDF
    if racial_traits:
        features_list = d.get("features", []) or []
        feature_data_list = d.get("feature_data", []) or []
        seen_rt = {fd.get("name", "").lower() for fd in feature_data_list}
        seen_rt.update((f.get("name", "") if isinstance(f, dict) else "").lower() for f in features_list if isinstance(f, dict))
        for rt in racial_traits:
            rt_name = rt.get("name", "")
            if rt_name.lower() not in seen_rt:
                features_list.append({"name": rt_name, "source": rt.get("source", "Race")})
                feature_data_list.append({"name": rt_name, "description": rt.get("desc", "")})
                seen_rt.add(rt_name.lower())
        d["features"] = features_list
        d["feature_data"] = feature_data_list

    d["condensed_features"] = _build_condensed_features(d)
    d["full_feature_text"] = _build_full_feature_text(d)
    d["page1_features"] = _build_page1_features_text(d)
    d["short_features"] = _build_short_features_text(d)
    d["spell_appendix"] = _build_spell_appendix_text(d)
    d["has_long_features"] = len(d.get("full_feature_text", "")) > 140
    d["equipment_appendix"] = _build_equipment_appendix_text(d)
    d["has_equipment_appendix"] = len(d.get("equipment_appendix", "")) > 0
    d["summons_appendix"] = _build_summons_appendix_text(d)
    d["has_summons_appendix"] = len(d.get("summons_appendix", "")) > 0

    return d
