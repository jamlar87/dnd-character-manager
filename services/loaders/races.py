"""Manual races and subraces -> RACES / SUBASIS / SUBRACE_TRAITS.

Creates, merges and repairs race records, including the post-pass that migrates
races the extractor filed as top-level entries into subraces of their parent.
"""

from __future__ import annotations

import re

from data import RACIAL_TRAIT_DESCS, RACIAL_TRAIT_EFFECTS

from services.loaders.files import _load_manual_json
from services.loaders.sources import _normalize_recharge

__all__ = ["merge_manual_races"]


def merge_manual_races(meta: dict) -> None:
    """Merge races.json into the race registry."""
    from core.data import (
        LIMITED_USE, RACES, RICH_RACE_DESCS, RICH_SUBRACE_DESCS, SUBASIS,
        SUBRACE_SOURCES, SUBRACE_TRAITS, _GENERIC_TRAITS,
    )

    # ── Races ── merge into RACES dict
    # Build set of known subrace names so we don't add them as top-level races
    _known_subraces = set()
    for _rdata in RACES.values():
        for _sr in _rdata.get("subraces", []):
            _known_subraces.add(_sr)
            _known_subraces.add(_sr.lower())  # case-insensitive check
    # Map known aliases / plural forms to prevent duplicates
    _subrace_aliases = {
        "ghostwise halflings": "Ghostwise Halfling",
        "strongheart halfling": "Stout Halfling",  # FR name
        "gray dwarf (duergar)": "Duergar",
        # Subrace migration aliases — plurals / alternate names that map to migrated subraces
        "elves of mirkwood": "Mirkwood Elf",
        "high elves of rivendell": "High Elf of Rivendell",
        "hobbits of the shire": "Hobbit of the Shire",
    }
    # Races whose subrace entry was extracted as a separate top-level race — skip them.
    # These are duplicates of subraces already inlined in the parent race entry.
    _duplicate_subrace_races = {
        "Erina (Spiritfarer)",  # Duplicate of Spiritfarer subrace already in Erina entry
    }
    # Races whose ingested name differs from the canonical name
    _race_renames = {
        "Spiritfarer Erina": "Erina",  # Base race is just "Erina"; Spiritfarer is a subrace
    }
    
    manual_races = _load_manual_json("races.json")
    
    # ── Helper: inject subrace data from a manual race entry ───────────────
    def _inject_subrace_data(parent_name, race, manual_subraces):
        """Populate SUBASIS, SUBRACE_TRAITS, RACIAL_TRAIT_DESCS, etc.
        for each subrace dict inside a manual race entry."""
        _stat_names = {"str": "strength", "dex": "dexterity", "con": "constitution",
                       "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
        for sr in manual_subraces:
            sr_name = sr.get("name", "")
            if not sr_name:
                continue
            # ASI
            sr_asi = {}
            for k, v in sr.get("asi", {}).items():
                if v:
                    key = _stat_names.get(k, k)
                    sr_asi[key] = v
            if sr_asi and sr_name not in SUBASIS:
                SUBASIS[sr_name] = sr_asi
            # Trait names
            sr_trait_names = [st.get("name", "") for st in sr.get("traits", [])]
            if sr_trait_names and sr_name not in SUBRACE_TRAITS:
                SUBRACE_TRAITS[sr_name] = sr_trait_names
            # Trait descriptions
            for st in sr.get("traits", []):
                stname = st.get("name", "")
                stdesc = st.get("description", "")
                if stname and stdesc:
                    key = f"{sr_name}::{stname}" if stname.lower() in _GENERIC_TRAITS else stname
                    if key not in RACIAL_TRAIT_DESCS:
                        RACIAL_TRAIT_DESCS[key] = stdesc
            # Effects
            sr_effects = sr.get("_effects", {})
            for stname, eff in sr_effects.items():
                if stname not in RACIAL_TRAIT_EFFECTS:
                    RACIAL_TRAIT_EFFECTS[stname] = {
                        "armor_profs": eff.get("armor_profs", []),
                        "weapon_profs": eff.get("weapon_profs", []),
                        "tool_profs": eff.get("tool_profs", []),
                        "skill_profs": eff.get("skill_profs", []),
                        "damage_resist": eff.get("damage_resist", []),
                        "condition_immune": eff.get("condition_immune", []),
                        "speed": eff.get("speed"),
                        "darkvision": eff.get("darkvision"),
                        "hp_per_level": eff.get("hp_per_level", 0),
                        "natural_armor": eff.get("natural_armor"),
                    }
            # Source
            parent_src = RACES[parent_name].get("source", "")
            _sr_srcs = RACES[parent_name].setdefault("_subrace_sources", {})
            if sr_name not in _sr_srcs:
                _sr_srcs[sr_name] = SUBRACE_SOURCES.get(sr_name, parent_src)
            # Description
            sr_desc = sr.get("description", "")
            if sr_desc:
                _sr_descs = RACES[parent_name].setdefault("subrace_descs", {})
                if sr_name not in _sr_descs:
                    _sr_descs[sr_name] = sr_desc
            # Limited-use traits
            for t in sr.get("traits", []):
                tname = t.get("name", "")
                tuses = t.get("uses", 0)
                trecharge = t.get("recharge", "")
                if tname and tuses > 0 and trecharge:
                    key = tname.lower()
                    if key not in LIMITED_USE:
                        LIMITED_USE[key] = {"min": tuses, "max": tuses,
                            "recharge": _normalize_recharge(trecharge),
                            "class": "", "per": "fixed"}

    for race in manual_races:
        name = race.get("name", "")
        if not name:
            continue
        # ── Inject subrace data from manual entries even when race already exists ──
        # Some core races (e.g. Shifter) exist in RACES with empty subraces, but the
        # manual entry has the full subrace data. Process those here before the skip.
        # Also inject race-level trait descriptions and effects for manual races (e.g.
        # Ratfolk) that exist in the export but need their trait data populated.
        if name in RACES:
            # Inject race trait descriptions (never overwrite generic traits)
            for t in race.get("traits", []):
                tname = t.get("name", "")
                tdesc = t.get("description", "")
                if tname and tdesc:
                    key = f"{name}::{tname}" if tname.lower() in _GENERIC_TRAITS else tname
                    if key not in RACIAL_TRAIT_DESCS:
                        RACIAL_TRAIT_DESCS[key] = tdesc
            # Inject race trait effects
            effects = race.get("_effects", {})
            for tname, eff in effects.items():
                if tname not in RACIAL_TRAIT_EFFECTS:
                    mapped = {
                        "armor_profs": eff.get("armor_profs", []),
                        "weapon_profs": eff.get("weapon_profs", []),
                        "tool_profs": eff.get("tool_profs", []),
                        "skill_profs": eff.get("skill_profs", []),
                        "damage_resist": eff.get("damage_resist", []),
                        "condition_immune": eff.get("condition_immune", []),
                        "speed": eff.get("speed"),
                        "darkvision": eff.get("darkvision"),
                        "hp_per_level": eff.get("hp_per_level", 0),
                        "natural_armor": eff.get("natural_armor"),
                    }
                    RACIAL_TRAIT_EFFECTS[tname] = mapped
                else:
                    na = eff.get("natural_armor")
                    if na:
                        RACIAL_TRAIT_EFFECTS[tname]["natural_armor"] = na
            # Register limited-use race traits
            for t in race.get("traits", []):
                tname = t.get("name", "")
                tuses = t.get("uses", 0)
                trecharge = t.get("recharge", "")
                if tname and tuses > 0 and trecharge:
                    key = tname.lower()
                    if key not in LIMITED_USE:
                        LIMITED_USE[key] = {"min": tuses, "max": tuses,
                            "recharge": _normalize_recharge(trecharge),
                            "class": "", "per": "fixed"}
            # Inject subrace data (e.g. Shifter subraces from manual data)
            manual_subraces = race.get("subraces", [])
            existing_subs = RACES[name].get("subraces") or []
            if manual_subraces and len(manual_subraces) > len(existing_subs):
                RACES[name]["subraces"] = [sr["name"] for sr in manual_subraces]
                _inject_subrace_data(name, race, manual_subraces)
            # Skip top-level race creation for existing core races
            continue
        # Apply canonical renames
        if name in _race_renames:
            name = _race_renames[name]
            race["name"] = name
        # Skip duplicate subrace entries that are standalone races
        if name in _duplicate_subrace_races:
            continue
        # Skip if this is a known subrace (e.g. "Wood Elf" is an Elf subrace)
        if name in _known_subraces or name.lower() in _known_subraces:
            continue
        # Skip known aliases (e.g. "Ghostwise Halflings" → Ghostwise Halfling)
        if name.lower() in _subrace_aliases:
            continue
        # ── Convert manual top-level races into subraces of core parents ──
        # These are standalone entries that should be subraces of existing races.
        _subrace_parents = {
            # Eberron Dragonmarks
            "Mark of Detection": ("Half-Elf", None),
            "Mark of Finding (Human)": ("Human", None),
            "Mark of Handling (Human)": ("Human", None),
            "Mark of Healing (Halfling)": ("Halfling", None),
            "Mark of Passage (Human)": ("Human", None),
            "Mark of Scribing (Gnome)": ("Gnome", None),
            "Mark of Sentinel": ("Human", None),
            "Mark of Shadow": ("Elf", None),
            "Mark of Storm (Half-Elf)": ("Half-Elf", None),
            "Mark of Warding (Dwarf)": ("Dwarf", None),
            # EGW / other source subraces
            "Genasi (Fire)": ("Genasi", "Fire Genasi"),
            "Lotusden Halfling": ("Halfling", None),
            "Pallid Elf": ("Elf", None),
            "Wildhunt Shifter": ("Shifter", None),
        }
        if name in _subrace_parents:
            parent_name, explicit_sr = _subrace_parents[name]
            if parent_name not in RACES:
                # Parent race not found — fall through to top-level creation
                pass
            else:
                # Build a clean subrace name (strip parenthetical) or use explicit
                sr_name = explicit_sr if explicit_sr else re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
                # Add to parent's subrace list
                if sr_name not in RACES[parent_name].get("subraces", []):
                    RACES[parent_name].setdefault("subraces", []).append(sr_name)
                # Inject ASI into SUBASIS
                _asi = {}
                _stat_names = {"str": "strength", "dex": "dexterity", "con": "constitution",
                               "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
                for k, v in race.get("asi", {}).items():
                    if v:
                        key = _stat_names.get(k, k)
                        _asi[key] = v
                if _asi and sr_name not in SUBASIS:
                    SUBASIS[sr_name] = _asi
                # Inject trait names into SUBRACE_TRAITS
                sr_trait_names = [st.get("name", "") for st in race.get("traits", [])]
                if sr_trait_names and sr_name not in SUBRACE_TRAITS:
                    SUBRACE_TRAITS[sr_name] = sr_trait_names
                # Inject trait descriptions
                for st in race.get("traits", []):
                    stname = st.get("name", "")
                    stdesc = st.get("description", "")
                    if stname and stdesc:
                        key = f"{sr_name}::{stname}" if stname.lower() in _GENERIC_TRAITS else stname
                        if key not in RACIAL_TRAIT_DESCS:
                            RACIAL_TRAIT_DESCS[key] = stdesc
                # Inject trait effects
                sr_effects = race.get("_effects", {})
                for stname, eff in sr_effects.items():
                    if stname not in RACIAL_TRAIT_EFFECTS:
                        RACIAL_TRAIT_EFFECTS[stname] = {
                            "armor_profs": eff.get("armor_profs", []),
                            "weapon_profs": eff.get("weapon_profs", []),
                            "tool_profs": eff.get("tool_profs", []),
                            "skill_profs": eff.get("skill_profs", []),
                            "damage_resist": eff.get("damage_resist", []),
                            "condition_immune": eff.get("condition_immune", []),
                            "speed": eff.get("speed"),
                            "darkvision": eff.get("darkvision"),
                            "hp_per_level": eff.get("hp_per_level", 0),
                            "natural_armor": eff.get("natural_armor"),
                        }
                # Add subrace source (inherit from parent manual data source)
                parent_src = RACES[parent_name].get("source", "")
                manual_src = race.get("source", "")
                src_to_use = manual_src if manual_src and len(manual_src) > 12 else parent_src
                _sr_srcs = RACES[parent_name].setdefault("_subrace_sources", {})
                if sr_name not in _sr_srcs:
                    _sr_srcs[sr_name] = src_to_use
                # Add subrace description
                sr_desc = race.get("description", "")
                if sr_desc:
                    _sr_descs = RACES[parent_name].setdefault("subrace_descs", {})
                    if sr_name not in _sr_descs:
                        _sr_descs[sr_name] = sr_desc
                # Add limited-use race traits
                for t in race.get("traits", []):
                    tname = t.get("name", "")
                    tuses = t.get("uses", 0)
                    trecharge = t.get("recharge", "")
                    if tname and tuses > 0 and trecharge:
                        key = tname.lower()
                        if key not in LIMITED_USE:
                            LIMITED_USE[key] = {"min": tuses, "max": tuses,
                                "recharge": _normalize_recharge(trecharge),
                                "class": "", "per": "fixed"}
                continue  # Skip top-level race creation
        # Map extracted format → RACES format
        asi_map = {"strength": "strength", "dexterity": "dexterity",
                   "constitution": "constitution", "intelligence": "intelligence",
                   "wisdom": "wisdom", "charisma": "charisma",
                   "str": "strength", "dex": "dexterity", "con": "constitution",
                   "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
        asi = {}
        for k, v in race.get("asi", {}).items():
            if v and k in asi_map:
                asi[asi_map[k]] = v
        traits = [t.get("name", "") for t in race.get("traits", [])]
        ref_manual = race.get("_source_manual", "")
        # Process subraces
        subrace_names = []
        subrace_descs = {}
        for sr in race.get("subraces", []):
            sr_name = sr.get("name", "")
            if sr_name:
                subrace_names.append(sr_name)
                subrace_descs[sr_name] = RICH_SUBRACE_DESCS.get(sr_name, sr.get("description", ""))
                # Merge subrace ASI into SUBASIS (normalize abbreviated stat names)
                _stat_names = {
                    "str": "strength", "dex": "dexterity", "con": "constitution",
                    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
                }
                sr_asi = {}
                for k, v in sr.get("asi", {}).items():
                    if v:
                        key = _stat_names.get(k, k)  # Normalize abbreviated keys
                        sr_asi[key] = v
                if sr_asi and sr_name not in SUBASIS:
                    SUBASIS[sr_name] = sr_asi
                # Add subrace trait names to SUBRACE_TRAITS
                sr_trait_names = [st.get("name", "") for st in sr.get("traits", [])]
                if sr_trait_names and sr_name not in SUBRACE_TRAITS:
                    SUBRACE_TRAITS[sr_name] = sr_trait_names
                # Add subrace trait descriptions (generic traits scoped to subrace)
                for st in sr.get("traits", []):
                    stname = st.get("name", "")
                    stdesc = st.get("description", "")
                    if stname and stdesc:
                        key = f"{sr_name}::{stname}" if stname.lower() in _GENERIC_TRAITS else stname
                        if key not in RACIAL_TRAIT_DESCS:
                            RACIAL_TRAIT_DESCS[key] = stdesc
                # Add subrace trait effects
                sr_effects = sr.get("_effects", {})
                for stname, eff in sr_effects.items():
                    if stname not in RACIAL_TRAIT_EFFECTS:
                        RACIAL_TRAIT_EFFECTS[stname] = {
                            "armor_profs": eff.get("armor_profs", []),
                            "weapon_profs": eff.get("weapon_profs", []),
                            "tool_profs": eff.get("tool_profs", []),
                            "skill_profs": eff.get("skill_profs", []),
                            "damage_resist": eff.get("damage_resist", []),
                            "condition_immune": eff.get("condition_immune", []),
                            "speed": eff.get("speed"),
                            "darkvision": eff.get("darkvision"),
                            "hp_per_level": eff.get("hp_per_level", 0),
                            "natural_armor": eff.get("natural_armor"),
                        }
        RACES[name] = {
            "subraces": subrace_names,
            "asi": asi,
            "speed": race.get("speed", 30),
            "darkvision": race.get("darkvision", 0),
            "languages": race.get("languages", ["Common"]),
            "traits": traits,
            "desc": RICH_RACE_DESCS.get(name, race.get("description", "")),
            "subrace_descs": subrace_descs,
            "source": race.get("source", ""),
            "_source_slug": ref_manual if ref_manual and meta.get("pdf_map", {}).get(ref_manual) else "",
            "natural_armor": None,  # Populated below from _na_fixes or _effects
        }
        # Clean up bad sources: bare page numbers, Unknown markers
        src = RACES[name].get("source", "")
        if ref_manual and meta.get("pdf_map", {}).get(ref_manual):
            book_title = meta["pdf_map"][ref_manual]["title"]
            # Strip noisy prefixes from raw filenames
            for prefix in ("D&D 5E - ", "D&D 5E-", "DnD 5E - "):
                if book_title.startswith(prefix):
                    book_title = book_title[len(prefix):]
                    break
            if src.startswith("p.") or src.startswith("p "):
                RACES[name]["source"] = f"{book_title} {src}"
            elif "p." in src or "p " in src:
                pass  # already has a page reference, keep as-is (e.g., "PHB p.24")
            elif "Unknown" in src or not src or len(src) < 12:
                # Short/garbled sources (SGtS, Part 1, etc.) — replace entirely
                RACES[name]["source"] = book_title
        # Attach per-subrace sources (inherit parent, overridden by SUBRACE_SOURCES)
        parent_src = RACES[name].get("source", "")
        sr_srcs = {}
        for s in subrace_names:
            sr_srcs[s] = SUBRACE_SOURCES.get(s, parent_src)
        RACES[name]["_subrace_sources"] = sr_srcs
        # Fix auto-extracted natural armor data — store per-race (not shared globally)
        # Loxodon uses CON, Lizardfolk gets full DEX (uncapped), Tortle is flat 17
        _na_fixes: dict[str, dict] = {
            "Lizardfolk": {"base_ac": 13, "uncapped": True},
            "Loxodon": {"base_ac": 12, "stat": "constitution", "uncapped": True},
            "Tortle": {"base_ac": 17},
            "Tlincalli": {"base_ac": 13, "uncapped": True},
        }
        if name in _na_fixes:
            RACES[name]["natural_armor"] = _na_fixes[name]
        else:
            # Fallback: extract from _effects for races not in the fix list
            for eff in race.get("_effects", {}).values():
                na = eff.get("natural_armor")
                if na:
                    RACES[name]["natural_armor"] = na
                    break
        # Add trait descriptions (quality-aware, but never overwrite generic traits)
        for t in race.get("traits", []):
            tname = t.get("name", "")
            tdesc = t.get("description", "")
            if tname and tdesc:
                key = f"{name}::{tname}" if tname.lower() in _GENERIC_TRAITS else tname
                if key not in RACIAL_TRAIT_DESCS:
                    RACIAL_TRAIT_DESCS[key] = tdesc
        # Add trait effects
        effects = race.get("_effects", {})
        for tname, eff in effects.items():
            if tname not in RACIAL_TRAIT_EFFECTS:
                mapped = {
                    "armor_profs": eff.get("armor_profs", []),
                    "weapon_profs": eff.get("weapon_profs", []),
                    "tool_profs": eff.get("tool_profs", []),
                    "skill_profs": eff.get("skill_profs", []),
                    "damage_resist": eff.get("damage_resist", []),
                    "condition_immune": eff.get("condition_immune", []),
                    "speed": eff.get("speed"),
                    "darkvision": eff.get("darkvision"),
                    "hp_per_level": eff.get("hp_per_level", 0),
                    "natural_armor": eff.get("natural_armor"),
                }
                RACIAL_TRAIT_EFFECTS[tname] = mapped
            else:
                # natural_armor is always race-specific (different body types)
                # Override even for duplicate trait names so Tortle gets AC 17
                # instead of Loxodon's AC 12, and Lizardfolk gets AC 13.
                na = eff.get("natural_armor")
                if na:
                    RACIAL_TRAIT_EFFECTS[tname]["natural_armor"] = na
        # Register limited-use race traits into LIMITED_USE
        for t in race.get("traits", []):
            tname = t.get("name", "")
            tuses = t.get("uses", 0)
            trecharge = t.get("recharge", "")
            if tname and tuses > 0 and trecharge:
                key = tname.lower()
                if key not in LIMITED_USE:
                    LIMITED_USE[key] = {"min": tuses, "max": tuses,
                        "recharge": _normalize_recharge(trecharge),
                        "class": "", "per": "fixed"}
        for sr in race.get("subraces", []):
            for st in sr.get("traits", []):
                stname = st.get("name", "")
                stuses = st.get("uses", 0)
                strecharge = st.get("recharge", "")
                if stname and stuses > 0 and strecharge:
                    key = stname.lower()
                    if key not in LIMITED_USE:
                        LIMITED_USE[key] = {"min": stuses, "max": stuses,
                            "recharge": _normalize_recharge(strecharge),
                            "class": "", "per": "fixed"}
        sub_text = f", {len(subrace_names)} subraces" if subrace_names else ""
        print(f"  + Race: {name} ({len(traits)} traits{sub_text})")

    # ── Second pass: load trait descriptions for races already in RACES ──
    # Some races (e.g. Thri-kreen) exist in the export JSON but their manual
    # trait descriptions were skipped because `name in RACES` prevented entry.
    for race in manual_races:
        name = race.get("name", "")
        if not name or name not in RACES:
            continue
        # Add trait descriptions (never overwrite existing)
        for t in race.get("traits", []):
            tname = t.get("name", "")
            tdesc = t.get("description", "")
            if tname and tdesc:
                # Always store under race-prefixed key to avoid cross-race name conflicts.
                # _build_racial_traits checks prefixed key first, falls back to bare name.
                key = f"{name}::{tname}"
                if key not in RACIAL_TRAIT_DESCS:
                    RACIAL_TRAIT_DESCS[key] = tdesc
                # Also store bare key as fallback (only if not already set by another race)
                if tname not in RACIAL_TRAIT_DESCS:
                    RACIAL_TRAIT_DESCS[tname] = tdesc
        # Add trait effects
        effects = race.get("_effects", {})
        for tname, eff in effects.items():
            if tname not in RACIAL_TRAIT_EFFECTS:
                mapped = {
                    "armor_profs": eff.get("armor_profs", []),
                    "weapon_profs": eff.get("weapon_profs", []),
                    "tool_profs": eff.get("tool_profs", []),
                    "skill_profs": eff.get("skill_profs", []),
                    "damage_resist": eff.get("damage_resist", []),
                    "condition_immune": eff.get("condition_immune", []),
                    "speed": eff.get("speed"),
                    "darkvision": eff.get("darkvision"),
                    "hp_per_level": eff.get("hp_per_level", 0),
                    "natural_armor": eff.get("natural_armor"),
                }
                RACIAL_TRAIT_EFFECTS[tname] = mapped
            else:
                na = eff.get("natural_armor")
                if na:
                    RACIAL_TRAIT_EFFECTS[tname]["natural_armor"] = na

    # ── Post-race migration: Promote races to subraces of manual-data parents ──
    # (Goblin, Bearfolk only exist in RACES after the loop above.
    #  Module-level migration can't reach them — they weren't in RACES yet.)
    _post_parents = {
        "Goblin": ["Shadow Goblin"],
        "Bearfolk": ["Shadowborn Bearfolk"],
        "Beorning": ["Woodmen of Wilderland", "Woodmen of Mountain Hall"],
    }
    for _post_parent, _post_names in _post_parents.items():
        if _post_parent not in RACES:
            continue
        for _post_name in _post_names:
            _post_child = RACES.get(_post_name)
            if not _post_child:
                continue
            # Move to parent's subrace list
            if _post_name not in RACES[_post_parent]["subraces"]:
                RACES[_post_parent]["subraces"].append(_post_name)
            # Copy ASI (already normalized by the manual race loader)
            _post_asi = _post_child.get("asi", {})
            if _post_asi and _post_name not in SUBASIS:
                SUBASIS[_post_name] = dict(_post_asi)
            # Copy traits to SUBRACE_TRAITS
            _post_child_traits = _post_child.get("traits", [])
            if _post_child_traits and _post_name not in SUBRACE_TRAITS:
                SUBRACE_TRAITS[_post_name] = list(_post_child_traits)
            # Copy source
            _post_src = _post_child.get("source", "")
            if _post_src and _post_name not in SUBRACE_SOURCES:
                SUBRACE_SOURCES[_post_name] = _post_src
            # Copy description
            _post_desc = _post_child.get("description", "") or _post_child.get("desc", "")
            if _post_desc and _post_name not in RICH_SUBRACE_DESCS:
                RICH_SUBRACE_DESCS[_post_name] = _post_desc
            # Also set in parent's subrace_descs for the detail modal
            if _post_desc:
                RACES[_post_parent].setdefault("subrace_descs", {})[_post_name] = _post_desc
            # Remove top-level entry — it's a subrace now
            del RACES[_post_name]
            print(f"  ↳ Moved '{_post_name}' → subrace of '{_post_parent}'")
