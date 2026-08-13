"""Cached JSON data access boundary + manual data merge.

Extracted from main.py (2026-08-12). The loaders are deliberately
small; merge policy (load_manual_data) operates on main registry
objects in place, imported lazily at call time to avoid circulars.
"""

from __future__ import annotations

import json
import re
import time as _time
from functools import lru_cache
from pathlib import Path
from typing import Any

from data import FEATURE_DESCRIPTIONS, RACIAL_TRAIT_DESCS, RACIAL_TRAIT_EFFECTS
from services.items import _resolve_source


@lru_cache(maxsize=128)
def load_json(data_dir: str, filename: str) -> Any:
    path = Path(data_dir) / filename
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def clear_cache() -> None:
    load_json.cache_clear()


def cache_info():
    return load_json.cache_info()


def _load_srd_class_data() -> tuple[dict, dict]:
    """Load cached SRD class levels and metadata."""
    from main import SRD_CACHE
    try:
        with open(SRD_CACHE / "class_levels.json") as f:
            levels = json.load(f)
        with open(SRD_CACHE / "class_meta.json") as f:
            meta = json.load(f)
        return levels, meta
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}


def _load_spell_dice() -> dict[str, dict]:
    from main import DATA_DIR
    try:
        with open(DATA_DIR / "spell_dice.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_json_cache(filename: str) -> list[dict]:
    from main import SRD_CACHE
    try:
        with open(SRD_CACHE / filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


_DND_KEYWORDS = [
    "saving throw", "attack roll", "bonus action", "ability check",
    "damage", "resistance", "immunity", "vulnerability", "concentration",
    "spell slot", "proficiency", "advantage", "disadvantage",
    "hit points", "armor class", "initiative", "reaction",
    "ritual", "cantrip", "melee", "ranged",
]


def _count_keywords(text: str) -> int:
    """Count D&D mechanical keyword occurrences in text."""
    t = text.lower()
    return sum(t.count(kw) for kw in _DND_KEYWORDS)


def _should_replace_description(existing: str, new: str) -> bool:
    """Return True if the new description is substantively better than existing.
    Requires: new is >50% longer AND has more mechanical keywords."""
    if not new or not new.strip():
        return False
    if not existing or not existing.strip():
        # Existing is empty — new wins regardless
        return True

    len_ratio = len(new) / max(len(existing), 1)
    new_kw = _count_keywords(new)
    existing_kw = _count_keywords(existing)

    # New must be >50% longer AND have more (or equal) keywords
    if len_ratio > 1.5 and new_kw >= existing_kw:
        return True
    # Or new is dramatically longer (>3x)
    if len_ratio > 3.0:
        return True
    # Or existing has zero keywords and new has some
    if existing_kw == 0 and new_kw > 0:
        return True

    return False


def _load_manual_json(filename: str) -> list[dict]:
    from main import MANUAL_DATA
    result = load_json(str(MANUAL_DATA), filename)
    return result if isinstance(result, list) else result


def _normalize_manual_source(source: str, source_manual: str, meta: dict) -> str:
    """Rebuild source string with proper book prefix when it's missing or malformed.

    Handles bare page references like 'Page 9', 'p.141', 'page 20-21' by
    reconstructing them as '{display_name} p.{page}' using _source_manual metadata.
    """
    from main import _get_source_slug_map
    source = (source or "").strip()
    if not source_manual:
        return source
    is_bare_page = bool(re.match(r'^(page|p\.?|pg)\b', source, re.IGNORECASE))
    if source and "Unknown" not in source and not is_bare_page:
        return source  # Already has a valid format
    slug = source_manual
    page_match = re.search(r'(\d+)', source) if source else None
    page_str = f" p.{page_match.group(1)}" if page_match else ""
    book_info = (meta.get("pdf_map", {}) if isinstance(meta, dict) else {}).get(slug, {})
    if book_info:
        title = book_info.get("title", slug)
        title = re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
        # Prefer curated display name from slug map over raw PDF title
        slug_map = _get_source_slug_map()
        slug_info = slug_map.get(slug, {})
        display = slug_info.get("display", "")
        title = display if display else title
        return f"{title}{page_str}"
    return f"{slug}{page_str}"


_SOURCE_VALID_CLEAN = re.compile(r'^\([A-Za-z][^)]+\)$')


_SOURCE_VALID_PAGE = re.compile(r'^\(([^,]+),\s*p\.(\d+)\)$')


_SOURCE_VALID_NO_PAGE = re.compile(r'^\(([^)]+)\)$')


def _validate_manual_sources() -> None:
    """Check all manual data files for clean source format at startup.
    Logs warnings for bad formats, out-of-range pages, or slug mismatches.
    This does not fix — it only warns so the operator can investigate."""
    from main import DATA_DIR, _get_source_slug_map
    from pathlib import Path
    import json
    
    data_dir = DATA_DIR / "manual_data"
    files = ["races.json", "spells.json", "magic_items.json", "equipment.json",
             "monsters.json", "npcs.json", "feats.json", "backgrounds.json", "subclasses.json",
             "traps.json"]
    
    # PDF page ranges (compact)
    max_pages = {
        "MM": 354, "DMG": 320, "PHB": 322, "XGE": 195, "MTF": 258, "VGM": 226,
        "GGR": 258, "EGW": 307, "COTN": 226, "TCSR": 283, "TOA": 260, "LMoP": 64,
        "WDH": 228, "HOTDQ": 97, "ROT": 98, "TTP": 28, "SCAG": 160, "TCE": 256,
        "CC": 426, "EBT": 257, "CSF": 151, "TMFRV": 206, "MPG": 64, "TFS": 196,
        "SME": 22, "SOM": 100, "WRKF": 70, "WLL": 180, "W2": 30, "W5": 32,
        "W7": 38, "EIA": 37, "TLT": 32, "SDQ": 26, "DD": 25, "RRG": 144,
        "ERIA": 144, "AIPG": 256, "BLRG": 160, "LMRG": 160, "EREA": 200,
        "RVR": 160, "WLA": 200, "MWC": 200, "KW": 100, "DPM1": 100, "AW": 50,
        "W": 9, "W1": 9, "W3": 30, "W4": 30, "W6": 30, "MOM": 15, "WSC": 50,
        "WS": 9, "W8": 7, "W9": 7, "LMG": 256, "RAT": 50, "RGEO": 100,
        "ETR": 50, "DDP": 50, "SSK": 50, "DPM": 100, "BGDIA": 256, "GOS": 256,
    }
    
    # Slug → display (use existing slug map from the app)
    slug_map = _get_source_slug_map()
    
    warnings = []
    for fn in files:
        path = data_dir / fn
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        items = list(data.values()) if isinstance(data, dict) else data
        for entry in items:
            name = entry.get("name", "?")
            src = entry.get("source", "")
            slug = (entry.get("_source_manual") or "").strip()
            if not src:
                continue
            if not _SOURCE_VALID_CLEAN.match(src):
                warnings.append(f"  ⚠ {fn}: {name} — bad format: '{src[:80]}'")
                continue
            m = _SOURCE_VALID_PAGE.match(src)
            if m:
                page = int(m.group(2))
                display = m.group(1)
                max_p = max_pages.get(slug.upper())
                if max_p and page > max_p:
                    warnings.append(f"  ⚠ {fn}: {name} — page {page} > {slug} max {max_p}")
                if page <= 0:
                    warnings.append(f"  ⚠ {fn}: {name} — page {page} (≤0)")
                # Check slug→display match
                slug_info = slug_map.get(slug.upper()) or slug_map.get(slug)
                if slug_info:
                    expected = slug_info.get("display", "")
                    if expected and display != expected:
                        warnings.append(f"  ⚠ {fn}: {name} — display '{display}' ≠ expected '{expected}'")
    
    if warnings:
        print(f"[source_validation] {len(warnings)} warning(s):")
        for w in warnings:
            print(w)
    else:
        print("[source_validation] All sources clean ✅")


def _normalize_recharge(recharge: str) -> str:
    """Normalize racial trait recharge strings to canonical forms.
    Canonical values: 'short', 'long', 'combat', 'special', 'dawn'.
    """
    r = recharge.lower().strip()
    if "short" in r and "long" in r:
        return "short"
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
    return r


def load_manual_data():
    """Merge extracted manual data into runtime structures. Called at startup."""
    from main import SRD_SPELLS, SRD_MAGIC_ITEMS, RACES, FEATS, FEAT_BY_NAME, BACKGROUNDS, CLASSES, SUBCLASS_FEATURES, LIMITED_USE, ITEM_INDEX, SUBRACE_TRAITS, SUBASIS, BACKGROUND_SOURCES, SUBRACE_SOURCES, MANUAL_TRAPS, RICH_RACE_DESCS, RICH_SUBRACE_DESCS, _GENERIC_TRAITS, _background_page_map, _spell_page_map, PAGE_MAP_DIR, DATA_DIR

    _loader_start = _time.time()

    meta = _load_manual_json("_meta.json")
    if not meta or isinstance(meta, list):
        return  # No manual data yet

    print(f"[manual_data] Loading from {meta.get('source_manuals', [])}")

    # ── Startup source validation ────────────────────────────────────────
    _validate_manual_sources()
    # ──────────────────────────────────────────────────────────────────────

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

    # ── Spells ── append to SRD_SPELLS (normalize classes/school to dict format)
    manual_spells = _load_manual_json("spells.json")
    # Backfill missing classes/schools from reference map
    _scm_path = DATA_DIR / "spell_classes_map.json"
    if _scm_path.exists():
        with open(_scm_path) as _f:
            _spell_classes = json.load(_f)
        _scm_fixed = 0
        _scm_enhanced = 0
        for _s in manual_spells:
            _name = _s.get("name", "")
            _lookup = _name.replace('\u2019', "'").replace('\u2018', "'").lower()
            if _lookup in _spell_classes:
                _mapped = _spell_classes[_lookup]
                _existing = _s.get("classes") or []
                if not _existing:
                    _s["classes"] = _mapped
                    _scm_fixed += 1
                elif len(_mapped) > len(_existing):
                    _s["classes"] = _mapped
                    _scm_enhanced += 1
        if _scm_fixed:
            print(f"  Spell classes backfilled: {_scm_fixed}")
        if _scm_enhanced:
            print(f"  Spell classes enhanced: {_scm_enhanced}")
    existing_spell_names = {s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower() for s in SRD_SPELLS}
    # Track which index in SRD_SPELLS corresponds to each normalized name (for replacement)
    _spell_index: dict[str, int] = {}
    for _i, _s in enumerate(SRD_SPELLS):
        _n = _s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower()
        if _n not in _spell_index:
            _spell_index[_n] = _i
    for spell in manual_spells:
        norm_name = spell.get("name", "").replace('\u2019', "'").replace('\u2018', "'")
        key = norm_name.lower()
        if key not in existing_spell_names:
            # Normalize classes: strings → {name: ...} dicts matching SRD format
            raw_classes = spell.get("classes", [])
            if raw_classes and isinstance(raw_classes[0], str):
                spell["classes"] = [{"name": c, "index": c.lower().replace(" ", "-")} for c in raw_classes]
            # Normalize school: string → {name: ...} dict
            school = spell.get("school")
            if isinstance(school, str):
                spell["school"] = {"name": school}
            SRD_SPELLS.append(spell)
            existing_spell_names.add(key)
            _spell_index[key] = len(SRD_SPELLS) - 1
        elif spell.get("components") and key in _spell_index:
            # Replace existing entry if it lacks components but this one has them
            _existing = SRD_SPELLS[_spell_index[key]]
            if not _existing.get("components"):
                SRD_SPELLS[_spell_index[key]] = spell
                # Re-normalize classes/school for the replacement
                raw_classes = spell.get("classes", [])
                if raw_classes and isinstance(raw_classes[0], str):
                    spell["classes"] = [{"name": c, "index": c.lower().replace(" ", "-")} for c in raw_classes]
                school = spell.get("school")
                if isinstance(school, str):
                    spell["school"] = {"name": school}
    if manual_spells:
        print(f"  + Spells: {len(manual_spells)}")

    # Also apply spell_classes_map to SRD spells (not just manual ones)
    _scm_path2 = DATA_DIR / "spell_classes_map.json"
    if _scm_path2.exists():
        with open(_scm_path2) as _f:
            _scm_data = json.load(_f)
        _scm_patched = 0
        for _i, _s in enumerate(SRD_SPELLS):
            _n = _s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower()
            if _n in _scm_data:
                _mapped = _scm_data[_n]
                _existing_classes = [c.get("name", "") for c in (_s.get("classes") or [])]
                _missing = [c for c in _mapped if c not in _existing_classes]
                if _missing:
                    _s["classes"] = (_s.get("classes") or []) + [{"name": c, "index": c.lower().replace(" ", "-")} for c in _missing]
                    _scm_patched += 1
        if _scm_patched:
            print(f"  Spell classes enriched (SRD): {_scm_patched}")

    # Dedup SRD_SPELLS: smart-quote duplicates (e.g. Aganazzar\u2019s vs Aganazzar's)
    _seen_names: dict[str, int] = {}  # normalized_name -> index of best entry
    _to_remove: list[int] = []
    for _i, _s in enumerate(SRD_SPELLS):
        _n = _s.get("name", "").replace('\u2019', "'").replace('\u2018', "'").lower()
        if _n in _seen_names:
            _existing = SRD_SPELLS[_seen_names[_n]]
            # Keep the one with components, or the one with more classes
            if not _s.get("components") and _existing.get("components"):
                _to_remove.append(_i)
            elif not _existing.get("components") and _s.get("components"):
                _to_remove.append(_seen_names[_n])
                _seen_names[_n] = _i
            else:
                _to_remove.append(_i)  # duplicate, remove later one
        else:
            _seen_names[_n] = _i
    for _i in reversed(_to_remove):
        SRD_SPELLS.pop(_i)
    if _to_remove:
        print(f"  Spell duplicates removed: {len(_to_remove)}")

    # Re-apply spell page map to catch newly-added manual spells
    _spm_enriched2 = 0
    for _spell in SRD_SPELLS:
        if "p." not in (_spell.get("source") or ""):
            _name = _spell.get("name", "").lower()
            _mapped = _spell_page_map.get(_name)
            if _mapped:
                _spell["source"] = _mapped
                _spm_enriched2 += 1
    if _spm_enriched2:
        print(f"  Spell sources enriched (manual): {_spm_enriched2}")

    # ── Magic Items ── append to SRD_MAGIC_ITEMS (ITEM_INDEX auto-picks them up)
    manual_items = _load_manual_json("magic_items.json")
    existing_item_names = {i.get("name", "").lower() for i in SRD_MAGIC_ITEMS}
    for item in manual_items:
        if item.get("name", "").lower() not in existing_item_names:
            # Enrich source from _source_manual + pdf_map
            source = (item.get("source") or "").strip()
            if item.get("_source_manual"):
                source = _normalize_manual_source(source, item["_source_manual"], meta)
            # Normalize rarity (handle non-standard strings from ingested data)
            raw_rarity = (item.get("rarity") or "unknown").strip()
            rarity_lower = raw_rarity.lower()
            RARITY_NORM = {
                "fabled": "legendary", "unique": "artifact",
                "none (non-magical)": "common", "faint conjuration": "common",
                "faint abjuration": "common", "moderate": "uncommon",
                "minor magical property": "common",
            }
            if rarity_lower in RARITY_NORM:
                norm_rarity = RARITY_NORM[rarity_lower]
            elif "varies" in rarity_lower or "see" in rarity_lower:
                norm_rarity = "varies"
            elif "fabled" in rarity_lower:
                norm_rarity = "legendary"
            elif rarity_lower.startswith("uncommon") and ("+" in rarity_lower or "," in rarity_lower):
                norm_rarity = "varies"  # +1/+2/+3 scaling items like "uncommon (+1), rare (+2), or very rare (+3)"
            else:
                std_rarities = {"common", "uncommon", "rare", "very rare", "legendary", "artifact", "unknown", "varies"}
                norm_rarity = rarity_lower if rarity_lower in std_rarities else "unknown"

            # Heuristic: classify remaining "unknown" items by type
            if norm_rarity == "unknown":
                raw_type_lower = (item.get("type") or "").lower()
                name_lower = (item.get("name") or "").lower()
                # Consumables → common
                if any(w in raw_type_lower for w in ("potion", "scroll", "drug", "oil", "elixir", "dust", "powder")):
                    norm_rarity = "common"
                elif any(w in name_lower for w in ("potion", "scroll", "oil of", "dust of")):
                    norm_rarity = "common"
                # Wondrous items → uncommon (default for misc magic)
                elif "wondrous" in raw_type_lower or "artefact" in raw_type_lower:
                    norm_rarity = "uncommon"
                # Weapons/armor → uncommon
                elif any(w in raw_type_lower for w in ("weapon", "sword", "axe", "bow", "spear", "dagger", "mace", "hammer", "staff")):
                    norm_rarity = "uncommon"
                elif any(w in raw_type_lower for w in ("armor", "armour", "shield", "helm", "plate", "mail", "chain", "leather")):
                    norm_rarity = "uncommon"
                # Rings/rods/wands → uncommon
                elif any(w in raw_type_lower for w in ("ring", "rod", "wand")):
                    norm_rarity = "uncommon"
                else:
                    norm_rarity = "uncommon"  # Safe default for unknown items

            # Normalize category (extract base from compound types)
            import re as _re2
            raw_type = (item.get("type") or "Wondrous item").strip()
            type_lower = raw_type.lower()
            base_m = _re2.match(r'([\w][\w\s]*?)(?:\s*[\(,]|$)', type_lower)
            base_cat = base_m.group(1).strip() if base_m else type_lower
            CAT_NORM = {
                "armour": "armor", "wondrous artefact": "wondrous item",
                "ammunition": "weapon", "arrow": "weapon", "great spear": "weapon",
                "great bow": "weapon", "great shield": "armor", "great axe": "weapon",
                "short sword": "weapon", "long sword": "weapon", "spear": "weapon",
                "axe": "weapon", "helm": "wondrous item", "mirror": "wondrous item",
                "ring-mail": "armor", "coat of mail": "armor", "scale hauberk": "armor",
                "shield": "armor", "drug": "potion", "primal boon": "wondrous item",
                "enchanted quality": "wondrous item", "close combat weapon": "weapon",
                "armor (shield)": "armor", "artifact": "wondrous item",
                "armor (light": "armor", "armor (medium": "armor", "armor (heavy": "armor",
            }
            norm_cat = CAT_NORM.get(base_cat, base_cat)

            # Map to SRD format
            mapped = {
                "name": item.get("name", ""),
                "desc": [item.get("description", "")],
                "rarity": {"name": norm_rarity},
                "equipment_category": {"name": norm_cat},
                "source": source,
            }
            SRD_MAGIC_ITEMS.append(mapped)
            existing_item_names.add(item["name"].lower())
    if manual_items:
        print(f"  + Magic Items: {len(manual_items)}")
        # Rebuild ITEM_INDEX entries for manual magic items
        for item in manual_items:
            name = item.get("name", "")
            if not name:
                continue
            key = name.lower()
            if key not in ITEM_INDEX:
                source = (item.get("source") or "").strip()
                if item.get("_source_manual"):
                    source = _normalize_manual_source(source, item["_source_manual"], meta)
                rarity = item.get("rarity", "varies")
                entry_dict = {
                    "name": name,
                    "type": "Magic Item",
                    "description": item.get("description", ""),
                    "cost": "—",
                    "weight": None,
                    "rarity": rarity,
                    "source": _resolve_source(key, source or ""),
                }
                if item.get("dice"):
                    entry_dict["dice"] = item["dice"]
                ITEM_INDEX[key] = entry_dict

    # ── Feats ── merge into FEATS dict
    manual_feats = _load_manual_json("feats.json")
    _feat_name_to_key = {f["name"].lower(): k for k, f in FEATS.items()}
    for feat in manual_feats:
        name = feat.get("name", "")
        if not name:
            continue
        key = _feat_name_to_key.get(name.lower(), name)
        manual_desc = feat.get("description", "")
        if key not in FEATS:
            FEATS[key] = {
                "name": name,
                "prerequisite": feat.get("prerequisite", ""),
                "description": manual_desc,
                "source": feat.get("source", ""),
            }
            # Also register in FEAT_BY_NAME so feat picker finds it
            _lkey = name.lower()
            if _lkey not in FEAT_BY_NAME:
                FEAT_BY_NAME[_lkey] = FEATS[key]
        elif manual_desc and len(manual_desc) > len(FEATS[key].get("description", "")):
            # Skip OCR-garbled descriptions — never replace clean text with garbage
            _ocr_markers = [
                "Vou ", "vou ", "Vour ", "lhe ", "lhal ", "lhis ", "lhan ",
                "lhrough ", "lhrow ", "lurn ", "lhe ", "crealure", "dalllage",
                "aclion", "effecl", "reaclion", "benelils", "benelit",
                "proleclion", "rnaximum", "rnake", "RolI", "Whcn",
                "beeornes", "discordam", "fillthe", "notjust", "bdore",
                "levei ", "leveI ", "proliciency", "proticiency",
                "olheI'", "ralheI'", "wilhin", "PART I CUSTOMIZ",
                "maslered", "disadvanlage", "prolicienl",
            ]
            if any(m in manual_desc for m in _ocr_markers):
                continue
            # Update existing feat with full PHB description (manual data is richer)
            FEATS[key]["description"] = manual_desc
            FEATS[key]["desc"] = manual_desc
            if not FEATS[key].get("prerequisite") and feat.get("prerequisite"):
                FEATS[key]["prerequisite"] = feat["prerequisite"]
    if manual_feats:
        print(f"  + Feats: {len(manual_feats)}")

    # ── Enrich feat sources with page numbers ──
    _feat_page_map: dict[str, str] = {}
    try:
        _fpm_path = PAGE_MAP_DIR / "feat_page_map.json"
        if _fpm_path.exists():
            with open(_fpm_path) as _f:
                _feat_page_map = json.load(_f)
        _feat_enriched = 0
        for _key, _feat in FEATS.items():
            _mapped = _feat_page_map.get(_key.lower())
            if not _mapped:
                # Also try by name
                _fname = _feat.get("name", "").lower().replace(" ", "_")
                _mapped = _feat_page_map.get(_fname)
            if _mapped and "p." in _mapped:
                _feat["source"] = _mapped
                _feat_enriched += 1
        if _feat_enriched:
            print(f"  Feat sources enriched: {_feat_enriched}/{len(FEATS)}")
    except Exception as _e:
        print(f"  (feat page map unavailable: {_e})")

    # ── Backgrounds ── append to BACKGROUNDS list
    manual_backgrounds = _load_manual_json("backgrounds.json")
    for bg in manual_backgrounds:
        name = bg.get("name", "")
        if name and name not in BACKGROUNDS:
            BACKGROUNDS.append(name)
    if manual_backgrounds:
        print(f"  + Backgrounds: {len(manual_backgrounds)}")
        # Enrich manual background sources
        _bg_added = 0
        for bg in manual_backgrounds:
            name = bg.get("name", "")
            if name and name not in BACKGROUND_SOURCES:
                _mapped = _background_page_map.get(name)
                if _mapped:
                    BACKGROUND_SOURCES[name] = _mapped
                    _bg_added += 1
        if _bg_added:
            print(f"  Manual background sources enriched: {_bg_added}")

    # ── Subclasses ── append to CLASSES[class_name]["subclasses"] + descriptions
    manual_subclasses = _load_manual_json("subclasses.json")
    for sc in manual_subclasses:
        sc_name = sc.get("name", "")
        parent_class = sc.get("class", "")
        if not sc_name or not parent_class:
            continue
        if parent_class not in CLASSES:
            print(f"  ⚠ Subclass '{sc_name}' references unknown class '{parent_class}' — skipping")
            continue
        subs = CLASSES[parent_class].setdefault("subclasses", [])
        descs = CLASSES[parent_class].setdefault("subclass_descs", {})
        srcs = CLASSES[parent_class].setdefault("_subclass_sources", {})
        # Skip base progression entries (name == class) — they carry base features,
        # not a real subclass choice. Still extract features/descriptions below.
        _is_base_progression = (sc_name == parent_class)
        # Skip subclasses whose name is itself a known base class (e.g. "Wanderer"
        # extracted as a Ranger subclass, but Wanderer is an AiME standalone class).
        _is_known_class = (sc_name in CLASSES and sc_name != parent_class)
        if sc_name not in subs and not _is_base_progression and not _is_known_class:
            subs.append(sc_name)
        descs[sc_name] = sc.get("description", "")
        sc_source = sc.get("source", "")
        if sc_source:
            # Clean up bad sources: bare page numbers, Unknown markers
            ref_manual = sc.get("_source_manual", "")
            if ref_manual and meta.get("pdf_map", {}).get(ref_manual):
                book_title = meta["pdf_map"][ref_manual]["title"]
                for prefix in ("D&D 5E - ", "D&D 5E-", "DnD 5E - "):
                    if book_title.startswith(prefix):
                        book_title = book_title[len(prefix):]
                        break
                if sc_source.startswith("p.") or sc_source.startswith("p "):
                    sc_source = f"{book_title} {sc_source}"
                elif "p." in sc_source or "p " in sc_source:
                    pass  # already has a page reference, keep as-is (e.g., "PHB p.97")
                elif "Unknown" in sc_source or "Part 1" in sc_source or len(sc_source) < 12:
                    sc_source = book_title
            srcs[sc_name] = sc_source

        # Populate SUBCLASS_FEATURES from extracted feature data
        features = sc.get("features", [])
        if features:
            by_level: dict[int, list[str]] = {}
            for feat in features:
                lvl = feat.get("level", 0)
                fname = feat.get("name", "")
                fdesc = feat.get("description", "")
                if fname and lvl > 0:  # skip L0 "atonement" meta-features
                    by_level.setdefault(lvl, []).append(fname)
                    # Store description for lookup (replace hardcoded if ingested is better)
                    if fdesc:
                        key = fname.lower()
                        existing = FEATURE_DESCRIPTIONS.get(key, "")
                        if not existing or _should_replace_description(existing, fdesc):
                            if existing and _should_replace_description(existing, fdesc):
                                pass  # Ingested version is better, will overwrite
                            FEATURE_DESCRIPTIONS[key] = fdesc
                        # Also store subclass-specific key for disambiguation
                        sc_key = f"{sc_name}::{key}"
                        FEATURE_DESCRIPTIONS[sc_key] = fdesc
                    # Register limited-use subclass features
                    fuses = feat.get("uses")
                    frecharge = feat.get("recharge", "")
                    # Coerce uses to int if it's a string (LLM sometimes outputs "3")
                    try:
                        fuses = int(fuses) if fuses else 0
                    except (ValueError, TypeError):
                        fuses = 0
                    if fuses > 0 and frecharge:
                        key = fname.lower()
                        if key not in LIMITED_USE:
                            LIMITED_USE[key] = {"min": fuses, "max": fuses,
                                "recharge": "short" if "short" in frecharge.lower() else "long",
                                "class": parent_class, "per": "fixed"}
            if by_level and sc_name not in SUBCLASS_FEATURES:
                SUBCLASS_FEATURES[sc_name] = by_level
    # Merge base progression features (name == class) into all subclasses
    # so AiME classes like Slayer/Warden get their base features regardless of subclass.
    for parent_cls in list(SUBCLASS_FEATURES.keys()):
        if parent_cls in CLASSES:
            base_feats = SUBCLASS_FEATURES.get(parent_cls, {})
            if base_feats:
                for sc_name in CLASSES[parent_cls].get("subclasses", []):
                    if sc_name != parent_cls and sc_name in SUBCLASS_FEATURES:
                        for lvl, names in base_feats.items():
                            existing = SUBCLASS_FEATURES[sc_name].setdefault(lvl, [])
                            for n in names:
                                if n not in existing:
                                    existing.append(n)
    if manual_subclasses:
        print(f"  + Subclasses: {len(manual_subclasses)}")

    # ── Traps ── load from manual data
    global MANUAL_TRAPS
    manual_traps = _load_manual_json("traps.json")
    if manual_traps:
        MANUAL_TRAPS.clear(); MANUAL_TRAPS.extend(manual_traps)
        print(f"  + Traps: {len(manual_traps)}")

    print(f"  Manual data loaded: {meta.get('totals', {})}")
    print(f"  [timing] load_manual_data: {(_time.time() - _loader_start) * 1000:.0f}ms")

    # ── Register homebrew class limited-use features not in SRD ──
    _HOMEBREW_LIMITED_USE = {
        # Scholar: 1 Healing Die per level, short rest
        "hands of the healer": {"min": 1, "max": 99, "recharge": "short",
                                "class": "Scholar", "per": "level"},
    }
    for _hk, _hv in _HOMEBREW_LIMITED_USE.items():
        if _hk not in LIMITED_USE:
            LIMITED_USE[_hk] = _hv

    # ── Catch-up: register limited-use race traits that were missed by the main
    #    loop (SRD races already in RACES, subrace-migrated races, etc.) ──
    def _normalize_recharge_light(_r: str) -> str:
        _r = _r.lower().strip()
        if "short" in _r and "long" in _r:
            return "short"
        if "short" in _r:
            return "short"
        if "long" in _r:
            return "long"
        if "special" in _r:
            return "special"
        return _r
    for _race in _load_manual_json("races.json"):
        for _t in _race.get("traits", []):
            _tname = _t.get("name", "") if isinstance(_t, dict) else ""
            _tuses = _t.get("uses", 0) if isinstance(_t, dict) else 0
            _trech = _t.get("recharge", "") if isinstance(_t, dict) else ""
            if _tname and (_tuses or _trech):
                _key = _tname.lower()
                if _key not in LIMITED_USE:
                    LIMITED_USE[_key] = {
                        "min": _tuses, "max": _tuses,
                        "recharge": _normalize_recharge_light(_trech) if _trech else "long",
                        "class": "", "per": "fixed",
                    }
        for _sr in _race.get("subraces", []):
            for _st in _sr.get("traits", []):
                _stname = _st.get("name", "") if isinstance(_st, dict) else ""
                _stuses = _st.get("uses", 0) if isinstance(_st, dict) else 0
                _strech = _st.get("recharge", "") if isinstance(_st, dict) else ""
                if _stname and (_stuses or _strech):
                    _key = _stname.lower()
                    if _key not in LIMITED_USE:
                        LIMITED_USE[_key] = {
                            "min": _stuses, "max": _stuses,
                            "recharge": _normalize_recharge_light(_strech) if _strech else "long",
                            "class": "", "per": "fixed",
                        }

    # ── Catch-up: register NPC features with uses/recharge ──
    # First pass: structured uses/recharge fields from extraction
    # Second pass: scan feature descriptions for usage patterns
    _USAGE_PATTERNS = [
        (re.compile(r'(?:can be used|usable|use)\s*(?:\w+\s+)*?(\d+)\s*(?:times\s*)?per\s+(short|long)\s+rest', re.I), None),
        (re.compile(r'(\d+)\s*(?:times?\s*)?per\s+(short|long)\s+rest', re.I), None),
        (re.compile(r'(\d+)/(?:day|long rest)', re.I), None),
        (re.compile(r'(\d+)/(?:short rest|encounter)', re.I), 'short'),
        (re.compile(r'recharges?\s*(?:on|after|upon)?\s*(?:a\s+)?(\d)[-–]\s*(\d)', re.I), None),
        (re.compile(r'recharges?\s*(?:on|after|upon)?\s*a\s+(short|long)\s+rest', re.I), None),
        (re.compile(r'once per day', re.I), 'long'),
        (re.compile(r'once per short rest', re.I), 'short'),
        (re.compile(r'(\d+)\s*use', re.I), None),
    ]
    for _npc in _load_manual_json("npcs.json"):
        for _f in _npc.get("features", []):
            if not isinstance(_f, dict):
                continue
            _fn = _f.get("name", "")
            _fu = _f.get("uses", 0) or 0
            _fr = _f.get("recharge", "") or ""
            _desc = _f.get("description", "") or ""
            # First pass: explicit structured fields
            if _fn and (_fu or _fr):
                _key = _fn.lower()
                if _key not in LIMITED_USE:
                    _cls = _npc.get("class_name", "")
                    LIMITED_USE[_key] = {
                        "min": _fu, "max": _fu,
                        "recharge": _normalize_recharge_light(_fr) if _fr else "long",
                        "class": _cls, "per": "fixed",
                    }
            # Second pass: scan description for usage patterns
            if _fn and _desc and not _fu and not _fr:
                _key = _fn.lower()
                if _key in LIMITED_USE:
                    continue  # Already registered by structured fields
                _found_uses = 0
                _found_recharge = ""
                for _pat, _default_recharge in _USAGE_PATTERNS:
                    _m = _pat.search(_desc)
                    if _m:
                        if _default_recharge:
                            _found_recharge = _default_recharge
                            _found_uses = int(_m.group(1)) if _m.lastindex is not None and _m.lastindex >= 1 and _m.group(1).isdigit() else 1
                        elif len(_m.groups()) == 1:
                            _g1 = _m.group(1)
                            if _g1.lower() in ('short', 'long'):
                                _found_recharge = _g1.lower()
                                _found_uses = 1
                            elif _g1.isdigit():
                                _found_uses = int(_g1)
                                _found_recharge = "long"
                        elif len(_m.groups()) >= 2:
                            # Recharge range (e.g., "recharges on a 5-6")
                            _found_recharge = "short"
                            _found_uses = 1  # treat dice-recharge as 1 use per short
                        break
                if _found_uses and _found_recharge:
                    _cls = _npc.get("class_name", "")
                    LIMITED_USE[_key] = {
                        "min": _found_uses, "max": _found_uses,
                        "recharge": _found_recharge,
                        "class": _cls, "per": "fixed",
                    }


__all__ = ["load_json", "clear_cache", "cache_info"] + [
    "_load_srd_class_data", "_load_spell_dice", "_load_json_cache",
    "_count_keywords", "_should_replace_description", "_load_manual_json",
    "_normalize_manual_source", "_validate_manual_sources", "_normalize_recharge",
    "load_manual_data",
]