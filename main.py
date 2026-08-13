"""D&D Character Manager — FastAPI webapp with multi-user character tracking."""
from __future__ import annotations

import json
import os
import asyncio
import random
import re
import sqlite3
import sys
import functools
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Load .env (local config, not committed) ──────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
# ─────────────────────────────────────────────────────────────────

import bcrypt
import httpx
from fastapi import FastAPI, Request, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

from data import (
    ABILITY_NAMES, SKILL_ABILITIES, ALL_SKILLS, LANGUAGES,
    BACKGROUNDS, BACKGROUND_INFO, ALIGNMENTS, DRACONIC_ANCESTRIES,
    STARTING_EQUIPMENT, ASI_LEVELS,
    RACES, CLASSES, RACE_NAMES,
    FEATS, FEAT_BY_NAME,
    FEATURE_DESCRIPTIONS, FEATURE_ACTION_TYPES,
    FULL_CASTERS, HALF_CASTERS, PACT_CASTERS, PREPARED_CASTERS, SPELLS_KNOWN_CASTERS,
    SUBCLASS_LEVELS, SUBCLASS_FEATURES, SUBCLASS_FEATURE_REPLACEMENTS,
    LIMITED_USE, METAMAGIC_OPTIONS, METAMAGIC_LEVELS, METAMAGIC_PICKS,
    INVOCATION_LEVELS, INVOCATION_PICKS, INVOCATION_OPTIONS,
    PACT_BOON_OPTIONS, PACT_BOON_LEVELS,
    MANEUVER_LEVELS, MANEUVER_OPTIONS,
    TOTEM_SPIRIT_OPTIONS, HUNTERS_PREY_OPTIONS,
    FAVORED_ENEMY_OPTIONS, FAVORED_TERRAIN_OPTIONS,
    INFUSION_OPTIONS,
    MULTICLASS_PREREQS, MULTICLASS_PROFICIENCIES,
    EXPERTISE_LEVELS,
    RACIAL_TRAIT_EFFECTS, RACIAL_TRAIT_DESCS,
    RECOMMENDED_FEATS, SCALED_EQUIPMENT,
)

# ── Item helpers (extracted to services/items.py) ─────────────────────────
from services.items import (
    _resolve_item_key, _split_curse_text, _build_item_description,
    _build_item_type, _resolve_source, _extract_srd_dice,
    _item_rarity_for_level,
)

# ── Combat / racial-trait helpers (extracted to services/combat.py) ────────
from services.combat import (
    get_racial_trait_effects, _build_racial_traits, _subrace_traits,
    _find_weapon, _parse_enhancement, _build_attack_for_weapon,
    _build_inventory_attacks, _build_charged_item_attacks, _normalize_equipped,
    _build_named_item_types, _get_named_item_types, _equipped_names,
    _normalize_armor_profs, get_character_armor_profs, _resolve_armor_item,
    check_armor_proficiency_from_set,
)

# ── Config ──────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DND_DATA_DIR", str(HERE / "data")))
PAGE_MAP_DIR = DATA_DIR / "page_maps"
EXPORTS_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "characters.db"
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
SECRET_KEY = os.environ.get("SECRET_KEY", "dnd-dev-secret-change-me")
APP_ENV = os.environ.get("APP_ENV", "development").lower()
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
if APP_ENV in {"production", "prod"} and SECRET_KEY == "dnd-dev-secret-change-me":
    raise RuntimeError("SECRET_KEY must be set to a unique value in production")
SRD_CACHE = DATA_DIR / "srd_cache"

# ── SRD Class Data (from dnd5eapi.co 2014, cached locally) ──────────────────

def _load_srd_class_data() -> tuple[dict, dict]:
    """Load cached SRD class levels and metadata."""
    try:
        with open(SRD_CACHE / "class_levels.json") as f:
            levels = json.load(f)
        with open(SRD_CACHE / "class_meta.json") as f:
            meta = json.load(f)
        return levels, meta
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}

SRD_LEVELS, SRD_META = _load_srd_class_data()

# ── Campaign Expert imports (reuse existing engine data) ────────────────────

_CE_PATH = Path(os.environ.get("DND_CAMPAIGN_EXPERT_PATH", str(HERE.parent / "dnd-campaign-expert")))
if str(_CE_PATH) not in sys.path:
    sys.path.insert(0, str(_CE_PATH))

try:
    from engine.spells import _load_spell_cache as _ce_load_spells
    SRD_SPELLS: list[dict] = _ce_load_spells()
except Exception:
    SRD_SPELLS = []

# ── Spell dice roll lookup (for card indicators) ────────────────────────────

def _load_spell_dice() -> dict[str, dict]:
    try:
        with open(DATA_DIR / "spell_dice.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

SPELL_DICE: dict[str, dict] = _load_spell_dice()

# ── SRD Magic Items & Features (from dnd5eapi.co 2014, cached locally) ──────

def _load_json_cache(filename: str) -> list[dict]:
    try:
        with open(SRD_CACHE / filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

SRD_MAGIC_ITEMS: list[dict] = _load_json_cache("magic-items.json")
SRD_FEATURES: list[dict] = _load_json_cache("features.json")

# Tag SRD data with source defaults (PHB 2014 / DMG 2014 chapters)
for _item in SRD_SPELLS:
    if "source" not in _item:
        _item["source"] = "PHB 2014 p.207"
for _item in SRD_FEATURES:
    if "source" not in _item:
        _item["source"] = "PHB 2014"  # overridden per-class in enrich_features
for _item in SRD_MAGIC_ITEMS:
    if "source" not in _item:
        _item["source"] = "DMG 2014 p.150"

# Build feature lookup by class+name for enrichment
for f in SRD_FEATURES:
    key = f.get("name", "").lower()
    desc = " ".join(f.get("desc", []))
    if desc:
        FEATURE_DESCRIPTIONS[key] = desc

# ── Quality-aware description replacement ──────────────────────────────────
# Used when ingested data has a description that competes with a hardcoded one.
# Prefer the ingested version when it's substantially richer (50%+ longer + has
# more D&D mechanical keywords), but keep hardcoded when it's comparable.

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

# ═══════════════════════════════════════════════════════════════════════════════
# Manual Data Loader — ingest extracted data from manual PDFs
# ═══════════════════════════════════════════════════════════════════════════════

MANUAL_DATA = HERE / "data" / "manual_data"
MANUAL_TRAPS: list[dict] = []

def _load_manual_json(filename: str) -> list[dict]:
    from services.data_loader import load_json
    result = load_json(str(MANUAL_DATA), filename)
    return result if isinstance(result, list) else result


def _normalize_manual_source(source: str, source_manual: str, meta: dict) -> str:
    """Rebuild source string with proper book prefix when it's missing or malformed.

    Handles bare page references like 'Page 9', 'p.141', 'page 20-21' by
    reconstructing them as '{display_name} p.{page}' using _source_manual metadata.
    """
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


# ── Startup source validation ────────────────────────────────────────────────

_SOURCE_VALID_CLEAN = re.compile(r'^\([A-Za-z][^)]+\)$')
_SOURCE_VALID_PAGE = re.compile(r'^\(([^,]+),\s*p\.(\d+)\)$')
_SOURCE_VALID_NO_PAGE = re.compile(r'^\(([^)]+)\)$')


def _validate_manual_sources() -> None:
    """Check all manual data files for clean source format at startup.
    Logs warnings for bad formats, out-of-range pages, or slug mismatches.
    This does not fix — it only warns so the operator can investigate."""
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
    global SRD_SPELLS, SRD_MAGIC_ITEMS, RACES, FEATS, BACKGROUNDS, CLASSES, SUBCLASS_FEATURES, LIMITED_USE, FEAT_BY_NAME

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

# ── Enrich spell sources with page numbers ──
_spell_page_map: dict[str, str] = {}
try:
    _spm_path = PAGE_MAP_DIR / "spell_page_map.json"
    if _spm_path.exists():
        with open(_spm_path) as _f:
            _raw_spm = json.load(_f)
        for _k, _v in _raw_spm.items():
            _src = _v.get("source_str", "")
            if _src and "p." in _src:
                _spell_page_map[_k] = _src
        # Apply to SRD_SPELLS
        for _spell in SRD_SPELLS:
            _name = _spell.get("name", "").lower()
            _mapped = _spell_page_map.get(_name)
            if _mapped:
                _spell["source"] = _mapped
        _enriched = sum(1 for s in SRD_SPELLS if "p." in s.get("source", ""))
        print(f"  Spell sources enriched: {_enriched}/{len(SRD_SPELLS)}")
except Exception as _e:
    print(f"  (spell page map unavailable: {_e})")



# ═══════════════════════════════════════════════════════════════════════════════
# ITEM INDEX — unified equipment + magic items with SRD/PHB 2014 descriptions
# ═══════════════════════════════════════════════════════════════════════════════

SRD_EQUIPMENT: list[dict] = _load_json_cache("equipment.json")



# Build unified item index (equipment + magic items)

# ── Load item→page map for source badges ──
_item_page_map: dict[str, str] = {}
try:
    _ppm_path = PAGE_MAP_DIR / "item_page_map.json"
    if _ppm_path.exists():
        with open(_ppm_path) as _f:
            _raw_map = json.load(_f)
        for _k, _v in _raw_map.items():
            _src = _v.get("source_str", "")
            if _src and "p." in _src:  # Only use entries with actual page numbers
                _item_page_map[_k] = _src
        print(f"  Items with page numbers: {len(_item_page_map)}")
except Exception as _e:
    print(f"  (item page map unavailable: {_e})")





ITEM_INDEX: dict[str, dict] = {}
for item in SRD_EQUIPMENT:
    name = item.get("name", "")
    if name:
        key = name.lower()
        cost = item.get("cost", {})
        entry = {
            "name": name,
            "type": _build_item_type(item),
            "description": _build_item_description(item),
            "cost": f"{cost.get('quantity', '?')} {cost.get('unit', 'gp')}",
            "weight": item.get("weight", None),
            "rarity": "",
            "source": _resolve_source(key, "PHB 2014"),
        }
        d = _extract_srd_dice(item)
        if d:
            entry["dice"] = d
        ITEM_INDEX[key] = entry

# ── Firearms, Ammo & Explosives (DMG 2014 p.267-268) ──
_FIREARM_ITEMS = [
    # Renaissance
    {"name":"Pistol","type":"Martial Ranged Weapon (Renaissance)","description":"1d10 piercing — Ammunition (30/90), loading. Loading: you can fire only one piece of ammunition per action, bonus action, or reaction, regardless of your number of attacks.","cost":"250 gp","weight":3,"rarity":"","source":"DMG 2014 p.267","dice":"1d10"},
    {"name":"Musket","type":"Martial Ranged Weapon (Renaissance)","description":"1d12 piercing — Ammunition (40/120), loading, two-handed. Loading: you can fire only one piece of ammunition per action, bonus action, or reaction, regardless of your number of attacks.","cost":"500 gp","weight":10,"rarity":"","source":"DMG 2014 p.267","dice":"1d12"},
    {"name":"Bullets (10)","type":"Ammunition (Renaissance)","description":"Ten lead bullets for use with Renaissance firearms (pistol, musket).","cost":"3 gp","weight":2,"rarity":"","source":"DMG 2014 p.267"},
    # Modern
    {"name":"Pistol, automatic","type":"Martial Ranged Weapon (Modern)","description":"2d6 piercing — Ammunition (50/150), reload (15 shots). Reload: after 15 shots, use an action or bonus action to reload the magazine.","cost":"—","weight":3,"rarity":"","source":"DMG 2014 p.267","charges":15,"charge_recharge":"reload (action or bonus action)","dice":"2d6"},
    {"name":"Revolver","type":"Martial Ranged Weapon (Modern)","description":"2d8 piercing — Ammunition (40/120), reload (6 shots). Reload: after 6 shots, use an action or bonus action to reload all six chambers.","cost":"—","weight":3,"rarity":"","source":"DMG 2014 p.267","charges":6,"charge_recharge":"reload (action or bonus action)","dice":"2d8"},
    {"name":"Rifle, hunting","type":"Martial Ranged Weapon (Modern)","description":"2d10 piercing — Ammunition (80/240), reload (5 shots), two-handed. Reload: after 5 shots, use an action or bonus action to reload the internal magazine.","cost":"—","weight":8,"rarity":"","source":"DMG 2014 p.267","charges":5,"charge_recharge":"reload (action or bonus action)","dice":"2d10"},
    {"name":"Rifle, automatic","type":"Martial Ranged Weapon (Modern)","description":"2d8 piercing — Ammunition (80/240), burst fire, reload (30 shots), two-handed. Burst Fire: spend 10 shots to force every creature in a 10-ft cube within range to make a DC 15 DEX save, taking the weapon's damage on a failure (no damage on success). Reload: after 30 shots, use an action or bonus action to reload.","cost":"—","weight":8,"rarity":"","source":"DMG 2014 p.267","charges":30,"charge_recharge":"reload (action or bonus action)","dice":"2d8"},
    {"name":"Shotgun","type":"Martial Ranged Weapon (Modern)","description":"2d8 piercing — Ammunition (30/90), reload (2 shots), two-handed. Reload: after 2 shots, use an action or bonus action to reload both barrels.","cost":"—","weight":7,"rarity":"","source":"DMG 2014 p.267","charges":2,"charge_recharge":"reload (action or bonus action)","dice":"2d8"},
    # Futuristic
    {"name":"Laser pistol","type":"Martial Ranged Weapon (Futuristic)","description":"3d6 radiant — Ammunition (40/120), reload (50 shots). Reload: after 50 shots, use an action or bonus action to swap the energy cell. Radiant damage bypasses some resistances.","cost":"—","weight":2,"rarity":"","source":"DMG 2014 p.268","charges":50,"charge_recharge":"reload (action or bonus action)","dice":"3d6"},
    {"name":"Antimatter rifle","type":"Martial Ranged Weapon (Futuristic)","description":"6d8 necrotic — Ammunition (120/360), reload (2 shots), two-handed. Reload: after 2 shots, use an action or bonus action to swap the energy cell. Necrotic damage withers flesh and ignores some defenses. Highest single-shot damage of any weapon.","cost":"—","weight":10,"rarity":"","source":"DMG 2014 p.268","charges":2,"charge_recharge":"reload (action or bonus action)","dice":"6d8"},
    {"name":"Laser rifle","type":"Martial Ranged Weapon (Futuristic)","description":"3d8 radiant — Ammunition (100/300), reload (30 shots), two-handed. Reload: after 30 shots, use an action or bonus action to swap the energy cell. Radiant damage bypasses some resistances.","cost":"—","weight":7,"rarity":"","source":"DMG 2014 p.268","charges":30,"charge_recharge":"reload (action or bonus action)","dice":"3d8"},
    {"name":"Energy cell","type":"Ammunition (Futuristic)","description":"A power cell for futuristic firearms (laser pistol, antimatter rifle, laser rifle).","cost":"—","weight":0.3,"rarity":"","source":"DMG 2014 p.268"},
    # Explosives
    {"name":"Bomb","type":"Explosive","description":"As an action, light and throw up to 60 ft. Explodes at the start of your next turn. DC 12 DEX save; 3d6 fire damage on failure, half on success.","cost":"150 gp","weight":1,"rarity":"","source":"DMG 2014 p.267","dice":"3d6"},
    {"name":"Gunpowder, powder horn","type":"Explosive","description":"A water-resistant horn of gunpowder. Set fire to cause 3d6 fire damage in 10 ft (DC 12 DEX half). One ounce flares for 1 round.","cost":"35 gp","weight":2,"rarity":"","source":"DMG 2014 p.267","dice":"3d6"},
    {"name":"Gunpowder, keg","type":"Explosive","description":"A small wooden keg of gunpowder. Set fire to cause 7d6 fire damage in 10 ft (DC 12 DEX half).","cost":"250 gp","weight":20,"rarity":"","source":"DMG 2014 p.267","dice":"7d6"},
    {"name":"Dynamite (stick)","type":"Explosive","description":"As an action, light and throw up to 60 ft. Explodes at the start of your next turn. DC 12 DEX save; 3d6 bludgeoning damage in 5 ft, half on success.","cost":"—","weight":1,"rarity":"","source":"DMG 2014 p.267","dice":"3d6"},
    {"name":"Grenade, fragmentation","type":"Explosive","description":"As an action, throw up to 60 ft. DC 15 DEX save; 5d6 piercing damage in 20-ft radius, half on success.","cost":"—","weight":1,"rarity":"","source":"DMG 2014 p.268","dice":"5d6"},
    {"name":"Grenade, smoke","type":"Explosive","description":"As an action, throw up to 60 ft. Heavily obscures a 20-ft radius for 1 minute.","cost":"—","weight":2,"rarity":"","source":"DMG 2014 p.268"},
    {"name":"Grenade launcher","type":"Martial Ranged Weapon (Modern)","description":"Launches fragmentation grenades (range 120 ft). Uses the fragmentation grenade's DC 15 DEX save and 5d6 piercing damage in a 20-ft radius. Requires a fragmentation grenade as ammunition — one shot per grenade.","cost":"—","weight":7,"rarity":"","source":"DMG 2014 p.268"},
]
for item in _FIREARM_ITEMS:
    ITEM_INDEX[item["name"].lower()] = item

for item in SRD_MAGIC_ITEMS:
    name = item.get("name", "")
    if name:
        key = name.lower()
        rarity = item.get("rarity", {}).get("name", "")
        desc_list = item.get("desc", [])
        desc = " ".join(desc_list) if desc_list else ""
        # Use actual source for manual items, fall back to "DMG 2014" for SRD
        source = _resolve_source(key, item.get("source", "") or "DMG 2014")

        # ── Proper type tagging ──
        cat = (item.get("equipment_category") or {}).get("name", "")
        cat_lower = cat.lower()
        TYPE_MAP = {
            "weapon": "Magic Weapon",
            "armor": "Magic Armor",
            "potion": "Potion",
            "scroll": "Scroll",
            "ring": "Ring",
            "wand": "Wand",
            "staff": "Staff",
            "rod": "Rod",
            "ammunition": "Magic Ammunition",
            "wondrous item": "Wondrous Item",
        }
        item_type = TYPE_MAP.get(cat_lower, cat or "Magic Item")

        # ── Extract charges / limited uses ──
        desc_lower = desc.lower()
        charges = None
        charge_recharge = None
        import re as _re
        cm = _re.search(r'has\s+(\d+)\s*charges?', desc_lower)
        if cm:
            charges = int(cm.group(1))
        rm = _re.search(r'regains?\s*(?:1d\d+\s*\+\s*)?(\d+)\s*expended charges?', desc_lower)
        if rm:
            charge_recharge = int(rm.group(1))
        # Per-day uses
        dm = _re.search(r'(\d+|-)\s*(?:times?|uses?)?\s*(?:per|each|a)\s*day', desc_lower)
        uses_per_day = None
        if dm:
            raw = dm.group(1)
            uses_per_day = 0 if raw == '-' else int(raw)
        # Once per long rest
        if 'once per' in desc_lower or 'can\'t be used again until' in desc_lower:
            if charges is None and uses_per_day is None:
                charges = 1
                charge_recharge = 'long rest'

        entry = {
            "name": name,
            "type": item_type,
            "description": desc,
            "cost": "—",
            "weight": None,
            "rarity": rarity,
            "source": source,
        }
        if charges is not None:
            entry["charges"] = charges
        if charge_recharge:
            entry["charge_recharge"] = charge_recharge
        if uses_per_day is not None:
            entry["uses_per_day"] = uses_per_day
        if item.get("dice"):
            entry["dice"] = item["dice"]
        ITEM_INDEX[key] = entry

# Build magic item index by rarity
from collections import defaultdict
ITEMS_BY_RARITY: dict[str, list[dict]] = defaultdict(list)
ITEM_WEAPONS: list[dict] = []
ITEM_ARMOR: list[dict] = []
ITEM_WONDROUS: list[dict] = []
ITEM_RODS_STAVES_WANDS: list[dict] = []

for item in SRD_MAGIC_ITEMS:
    rarity = item.get("rarity", {}).get("name", "").lower()
    ITEMS_BY_RARITY[rarity].append(item)
    cat = item.get("equipment_category", {}).get("name", "").lower()
    if cat == "weapon":
        ITEM_WEAPONS.append(item)
    elif cat == "armor":
        ITEM_ARMOR.append(item)
    elif cat in ("rod", "staff", "wand"):
        ITEM_RODS_STAVES_WANDS.append(item)
    elif cat == "wondrous item":
        ITEM_WONDROUS.append(item)

# Rarity tier by level bracket (PHB DMG p.135 magic item distribution)

# Compatibility wrappers; implementation lives in services.auth.
def _hash(password: str) -> str:
    from services.auth import hash_password
    return hash_password(password)

def _verify(password: str, hash_: str) -> bool:
    from services.auth import verify_password
    return verify_password(password, hash_)

_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES)))

# ── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="D&D Character Manager")

# ── Static files ──────────────────────────────────────────────────────────────
from starlette.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# ── Logging ───────────────────────────────────────────────────────────────────
import logging as _logging, logging.handlers as _logging_handlers, time as _time, uuid as _uuid

_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        _logging.StreamHandler(),
        _logging_handlers.RotatingFileHandler(
            str(DATA_DIR / "app.log"), maxBytes=5*1024*1024, backupCount=3
        ),
    ],
)
_log = _logging.getLogger(__name__)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    req_id = _uuid.uuid4().hex[:12]
    start = _time.time()
    try:
        response = await call_next(request)
        elapsed = _time.time() - start
        _log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, elapsed * 1000)
        response.headers.setdefault("X-Request-ID", req_id)
        return response
    except Exception as e:
        elapsed = _time.time() - start
        _log.error("%s %s 500 %.0fms | %s: %s", request.method, request.url.path, elapsed * 1000, type(e).__name__, e, exc_info=True)
        return HTMLResponse("Internal Server Error", status_code=500, headers={"X-Request-ID": req_id})


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Apply security headers and protect cookie-authenticated writes."""
    csrf_cookie = request.cookies.get("csrf_token")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.cookies.get("dnd_token"):
        supplied = request.headers.get("x-csrf-token", "")
        if not csrf_cookie or not supplied or not secrets.compare_digest(csrf_cookie, supplied):
            return JSONResponse({"error": "CSRF token required"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin.rstrip("/") != expected.rstrip("/"):
                return JSONResponse({"error": "Cross-origin request rejected"}, status_code=403)
    response = await call_next(request)
    if not csrf_cookie:
        response.set_cookie("csrf_token", secrets.token_urlsafe(32), httponly=False, secure=APP_ENV in {"production", "prod"}, samesite="lax", path="/")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    _log.error("Unhandled %s on %s %s: %s", type(exc).__name__, request.method, request.url.path, exc, exc_info=True)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Internal server error"}, status_code=500)
    return HTMLResponse("Internal Server Error", status_code=500)

# ── DB ──────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    from services.db import connect
    return connect(DB_PATH)




def _get_user(email: str) -> dict | None:
    from services.users import get_user
    return get_user(DB_PATH, email)

def _create_session(user_id: int) -> str:
    from services.sessions import create_session
    return create_session(DB_PATH, user_id, SESSION_TTL_DAYS)

def _get_user_by_token(token: str) -> dict | None:
    from services.sessions import get_user_by_token
    return get_user_by_token(DB_PATH, token)

def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get("dnd_token")
    if not token:
        return None
    return _get_user_by_token(token)

def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

def _is_admin(user: dict) -> bool:
    from services.users import is_admin
    return is_admin(user)

def _user_filter(user: dict, column: str = "user_id") -> tuple[str, tuple]:
    """Return (sql_clause, params) that filters by user_id, or empty if admin."""
    if _is_admin(user):
        return ("", ())
    return (f"AND {column} = ?", (user["id"],))

def _user_where(user: dict, column: str = "user_id") -> tuple[str, tuple]:
    """Return (WHERE_clause, params) that filters by user_id, or empty if admin."""
    if _is_admin(user):
        return ("", ())
    return (f"WHERE {column} = ?", (user["id"],))

def _require_owned(db, user: dict, table: str, item_id: int, id_col: str = "id") -> dict | None:
    """Fetch a row by id, checking ownership unless user is admin. Returns dict or None.
    
    Raises HTTPException(404) implicitly if not found, but callers typically
    check for None to return their own 404. Admin bypasses user_id check.
    """
    if _is_admin(user):
        row = db.execute(f"SELECT * FROM {table} WHERE {id_col} = ?", (item_id,)).fetchone()
    else:
        row = db.execute(f"SELECT * FROM {table} WHERE {id_col} = ? AND user_id = ?", (item_id, user["id"])).fetchone()
    return dict(row) if row else None

# ── DB schema init/migrations (extracted to services/db_schema.py) ────────
from services.db_schema import init_db, _migrate_npc_source_columns

# ── Render helper ───────────────────────────────────────────────────────────

def _render(template: str, request: Request | None = None, **ctx) -> HTMLResponse:
    user = get_current_user(request) if request else None
    content = _jinja.get_template(template).render(
        user=user,
        request=request,
        **ctx
    )
    return HTMLResponse(content)

# ── D&D Data — race/class/skill/etc (embedded constants) ────────────────────
# All data from Player's Handbook (2014), verified against DnD-Manuals PDFs.
# PHB page references inline.

# PHB Ch.2 p.17-43 — Races
# Races with flexible ASI: +2 to one ability of choice (ASI not in sourcebook)
FLEXIBLE_ASI_RACES = {
    "Custom Lineage", "Dark Folk", "Ratfolk", "Vanara",
    "Darakhul", "Umbral Human", "Thri-kreen", "Grung",
    "Dwarves of the Lonely Mountain", "Hobbit of the Shire",
    "High Elves of Rivendell", "Tlincalli"
}

# ── Rich supplement race descriptions ──
# Overrides short auto-extracted descriptions from manual data with full PHB lore.
RICH_RACE_DESCS: dict[str, str] = {
    "Aarakocra": (
        "Aarakocra are avian humanoids who soar the skies of the Elemental Plane of Air. Standing about 5 feet tall "
        "with wingspans of 20 feet, they have feathers in brilliant hues—reds, blues, greens, and browns—with sharp, "
        "beak-like faces and taloned hands and feet. Their lightweight, hollow bones make them fragile by human standards "
        "but perfectly adapted for flight. Aarakocra live roughly 30 years, reaching maturity by age 3.\n\n"
        "Aarakocra society revolves around the sky. They build communal eyries atop the highest peaks and value freedom "
        "above all else. They are cheerful, welcoming, and fiercely protective of their flocks. Aarakocra who venture to "
        "the Material Plane are often explorers, emissaries, or exiles driven by an insatiable curiosity about the "
        "ground-bound world. They find enclosed spaces—dungeons, caves, low-ceilinged buildings—deeply unsettling.\n\n"
        "Mechanically, aarakocra gain +2 Dexterity and +1 Wisdom. Their Flight gives them a 50-foot flying speed, "
        "making them unparalleled scouts and skirmishers—though they cannot fly in medium or heavy armor. Their Talons "
        "deal 1d4 slashing damage as unarmed strikes. They speak Common, Aarakocra, and Auran."
    ),
    "Aasimar": (
        "Aasimar are mortals touched by the Upper Planes—celestial bloodlines that manifest as angelic guides, radiant "
        "souls, and a divine purpose that shapes their entire lives. They appear mostly human, standing 5 to 6 feet "
        "tall, but their celestial heritage shows through in subtle ways: metallic-flecked eyes, a faint nimbus of "
        "light in darkness, or a voice that resonates with otherworldly authority. Aasimar live up to 160 years.\n\n"
        "Aasimar are rare and often solitary. Each carries an inner conflict between mortal desires and celestial "
        "expectations. Most are guided from childhood by a deva—an angelic being who communicates through dreams and "
        "omens. This guidance can feel like a blessing or a burden, depending on the aasimar's disposition. Those "
        "who embrace their divine nature become champions of light; those who rebel against it often walk darker paths "
        "while their celestial guide watches in silent disappointment.\n\n"
        "Mechanically, aasimar gain +2 Charisma and +1 to another ability. Their Celestial Resistance grants "
        "resistance to necrotic and radiant damage. Healing Hands provides a pool of healing. Light Bearer grants "
        "the light cantrip. At 3rd level, they can transform for 1 minute via their subrace—Protector (radiant flight "
        "+ bonus damage), Scourge (consuming radiance), or Fallen (frightening necrotic shroud). They speak Common "
        "and Celestial."
    ),
    "Bugbear": (
        "Bugbears are the largest and laziest of the goblinoids—hulking, shaggy brutes standing 6 to 8 feet tall "
        "with muscular builds that belie their slouching posture. Their fur ranges from yellow-brown to deep russet, "
        "their ears are wedge-shaped, and their faces combine goblin features with a bear-like muzzle. Bugbear eyes "
        "are typically dark and predatory. They live about 80 years—if they survive the violence of goblinoid society.\n\n"
        "Bugbear culture is built on bullying and stealth—paradoxical as it sounds, they are ambush predators who use "
        "their surprising grace to stalk prey before unleashing devastating brute force. In goblinoid hierarchy, "
        "bugbears serve as enforcers and shock troops for hobgoblin warlords. Left to their own devices, they are "
        "lazy and exploitative, taking what they want through intimidation rather than effort. Adventuring bugbears "
        "are typically outcasts who found goblinoid society too stifling—or were driven out for challenging the wrong "
        "boss.\n\n"
        "Mechanically, bugbears gain +2 Strength and +1 Dexterity. Their Long-Limbed trait gives them an extra "
        "5 feet of reach on melee attacks on their turn. Sneaky grants proficiency in Stealth. Surprise Attack "
        "deals an extra 2d6 damage to any creature that hasn't taken a turn in combat yet. They have Darkvision "
        "60 ft and count as one size larger for carrying capacity. Powerful Build rounds out their identity "
        "as terrifying ambush strikers."
    ),
    "Centaur": (
        "Centaurs are fey creatures with the upper body of a humanoid and the lower body of a horse. Standing 6 to "
        "7 feet tall and weighing over 600 pounds, they have equine bodies in shades of chestnut, bay, dapple gray, "
        "or palomino, while their human torsos share those colorations in skin and hair. Their ears are slightly "
        "pointed, and their eyes can be solid colors—gold, green, or amber. Centaurs age at roughly the same rate "
        "as humans and rarely live past 100.\n\n"
        "Centaurs are children of the Feywild, and despite their imposing size, they are creatures of celebration "
        "and nature. They organize in migratory tribes called kashta, following ancient star-paths across the land. "
        "Centaur culture values harmony with nature, the wisdom of elders, and the joy of the run—galloping freely "
        "across open plains is a spiritual act. They are wary of walls and cities, which they see as prisons for "
        "the soul. Adventuring centaurs are often wanderers who felt the call of distant lands.\n\n"
        "Mechanically, centaurs gain +2 Strength and +1 Wisdom. Their Fey typing gives them advantage on saves "
        "against magic. Charge lets them make a bonus-action hoof attack after moving 30 feet straight toward "
        "a target. Hooves deal 1d6 bludgeoning. Equine Build counts them as one size larger for carrying capacity "
        "and makes climbing cost extra movement—a real consideration for dungeon-crawling. They speak Common and "
        "Sylvan."
    ),
    "Changeling": (
        "Changelings are shapeshifters capable of altering their physical appearance at will. In their natural "
        "form, they are pale and slender, with colorless skin, large white eyes, and silver-white hair. They "
        "stand 5 to 6 feet tall with thin, graceful builds, and their features are so fluid that even at rest, "
        "their faces seem subtly indistinct—a nose that could be longer, eyes that might be any color. Changelings "
        "reach adulthood in their late teens and can live over 100 years.\n\n"
        "Changelings are defined by the tension between identity and disguise. Every changeling is raised among "
        "other races and must decide who—and what—they want to be. Some embrace a single persona, building a life "
        "around one identity; others drift between faces, never belonging to any single self. Changeling society "
        "is loose and informal, built on networks of travelers, performers, and information brokers who recognize "
        "each other through subtle cues invisible to outsiders. They are natural spies, actors, and diplomats.\n\n"
        "Mechanically, changelings gain +2 Charisma and +1 Dexterity or Intelligence. Shapechanger lets them "
        "alter their appearance as an action—same size, same basic arrangement of limbs, but any face, voice, "
        "and coloration. This is not an illusion and holds up to physical inspection. Unsettling Visage frightens "
        "or grants advantage on Intimidation. Divergent Persona grants a tool proficiency that can be changed "
        "each long rest. They speak Common and two other languages."
    ),
    "Firbolg": (
        "Firbolgs are forest-dwelling giants who stand between 7 and 8 feet tall and weigh 240–300 pounds. Their "
        "skin is blue-gray to pale blue, their hair ranges from brown to deep red, and their features are broad "
        "and strong—thick brows, wide jaws, and kind eyes. Despite their imposing size, firbolgs move with "
        "uncanny quiet. They are long-lived, reaching up to 500 years, and their connection to nature deepens "
        "with every century.\n\n"
        "Firbolg culture is gentle and reclusive. They live in remote forest clans, speaking softly and avoiding "
        "confrontation. They are caretakers of the wild, not its masters—they see themselves as part of the forest, "
        "not its rulers. Firbolgs distrust outsiders and use their innate magic to hide their villages and themselves. "
        "A firbolg's name is a private treasure, rarely shared with non-firbolgs. Adventuring firbolgs are often "
        "on personal quests—driven by visions, defending a sacred grove, or seeking to understand a world that "
        "frightens and fascinates them in equal measure.\n\n"
        "Mechanically, firbolgs gain +2 Wisdom and +1 Strength. Firbolg Magic grants detect magic and disguise "
        "self once per short rest (disguise self can make them appear up to 3 feet shorter). Hidden Step lets them "
        "turn invisible as a bonus action until the start of their next turn, once per short rest. Speech of Beast "
        "and Leaf gives them advantage on Charisma checks with plants and animals. Powerful Build counts them as "
        "one size larger for carrying. They speak Common, Elvish, and Giant."
    ),
    "Gith": (
        "The gith are an ancient race divided by a civil war so bitter it split them into two peoples: the "
        "militaristic githyanki and the monastic githzerai. Both were once slaves of the mind flayers, psychically "
        "enslaved for untold millennia until the hero Gith led a bloody revolt that shattered the illithid empire. "
        "Today, githyanki dwell in the Astral Plane on the corpse of a dead god, while githzerai build serene "
        "monasteries in the chaos of Limbo. Both subraces are lean and angular, standing 5 to 6 feet tall, "
        "with yellow-green skin, pointed ears, and gaunt, almost skeletal features.\n\n"
        "Githyanki culture is martial and predatory. They are raiders who descend from the Astral Plane on red "
        "dragon mounts, led by a lich-queen named Vlaakith who devours the souls of any githyanki who grow too "
        "powerful. Githzerai culture, by contrast, is ascetic and introspective—they channel their psionic gifts "
        "into discipline and enlightenment, building fortress-monasteries held together by pure mental focus. "
        "Both subraces share a burning hatred of mind flayers and a profound, instinctive mastery of psionics.\n\n"
        "Mechanically, all gith gain +1 Intelligence. Githyanki gain +2 Strength and Decadent Mastery "
        "(a skill and tool proficiency that can be swapped daily). Githzerai gain +2 Wisdom and Mental Discipline "
        "(advantage on saves vs charmed and frightened). Both subraces gain Githyanki Psionics or Githzerai "
        "Psionics—mage hand, jump or shield, and misty step or detect thoughts at higher levels. They speak "
        "Common and Gith."
    ),
    "Goblin": (
        "Goblins are small, wiry humanoids standing 3 to 4 feet tall with flat faces, pointed ears, wide mouths "
        "full of sharp teeth, and skin in shades of green, yellow, or orange. They are thin and quick, with "
        "long fingers ideal for picking locks and pockets alike. Goblins age rapidly, reaching adulthood by 8 "
        "and rarely living past 60 in the wild.\n\n"
        "Goblin society is a brutal hierarchy where the strong eat first and the weak get eaten. They are "
        "wretched and cunning in equal measure—cowards by nature but vicious when cornered or when they have "
        "overwhelming numbers. Most goblins serve hobgoblin or bugbear masters in goblinoid legions. Goblin "
        "culture values cleverness over strength, and the ideal goblin hero is the one who survives by any "
        "means necessary. Adventuring goblins who break from the tribe are often driven out for being too soft, "
        "too ambitious, or simply in the wrong place when the boss was angry.\n\n"
        "Mechanically, goblins gain +2 Dexterity and +1 Constitution. Fury of the Small lets them deal extra "
        "damage equal to their level to a larger creature once per short rest—a satisfyingly spiteful ability. "
        "Nimble Escape lets them Disengage or Hide as a bonus action, making them infuriatingly hard to pin "
        "down in combat. They have Darkvision 60 ft. Goblins speak Common and Goblin, and make natural rogues, "
        "rangers, and dexterity-based fighters."
    ),
    "Goliath": (
        "Goliaths are mountain-dwelling nomads built like living boulders. Standing 7 to 8 feet tall and weighing "
        "280 to 340 pounds, they have stone-gray skin mottled with darker patches called lithoderms, and eyes "
        "of deep blue, green, or brown. Their skulls have a distinct bony ridge, and their hair is typically "
        "dark—black, brown, or deep gray. Goliaths live to be less than a century old, but every year is "
        "earned through trial and competition.\n\n"
        "Goliath culture is centered on fairness, self-sufficiency, and relentless self-improvement. Their tribes "
        "dwell above the tree line where nothing grows easy, and everything must be earned. Personal achievement "
        "is everything—goliaths track their deeds obsessively, introducing themselves with a litany of accomplishments. "
        "A goliath who fails to pull their weight is given a chance to improve; one who consistently fails is "
        "exiled. They hate cheaters, pity the weak, and respect only what is proven. Adventuring goliaths seek "
        "challenges their mountain home cannot provide.\n\n"
        "Mechanically, goliaths gain +2 Strength and +1 Constitution. Stone's Endurance lets them shrug off "
        "damage once per short rest (1d12 + CON, reaction). Powerful Build counts them as one size larger for "
        "carrying. Mountain Born grants natural adaptation to cold climates and high altitudes. They speak Common "
        "and Giant. Goliaths make exceptional barbarians, fighters, and paladins—built to take hits and keep "
        "swinging."
    ),
    "Grung": (
        "Grungs are small, brightly colored frog-folk from tropical jungles and swamps. Standing 2 to 3 feet "
        "tall, they have smooth, moist skin in brilliant warning colors—every grung's hue indicates its caste: "
        "green for hunters and warriors, blue for artisans, purple for administrators, red for magicians, orange "
        "for the elite, and gold for the supreme chieftain. Their large eyes, webbed hands and feet, and "
        "permanently slick skin mark them as amphibious predators. Grungs reach adulthood by their first year "
        "and can live up to 50 years.\n\n"
        "Grung society is a rigid, toxic caste system—literally. Their skin secretes a poison that can incapacitate "
        "any creature that touches them, and higher castes produce more potent toxins. This poison defines every "
        "interaction: lower castes must avoid contact with superiors, and grung who touch above their station are "
        "punished. Grungs are slavers who raid neighboring settlements and capture prisoners to serve their "
        "hierarchical society. Adventuring grungs are almost always outcasts—exiled for touching the wrong caste, "
        "failing a sacred hunt, or developing a taste for freedom their society cannot tolerate.\n\n"
        "Mechanically, grungs gain +2 Dexterity and +1 Constitution. Poisonous Skin forces any creature that "
        "touches them or they touch to save against the poisoned condition. Standing Leap lets them jump 25 feet "
        "horizontally or 15 feet vertically without a running start. Water Dependency requires them to submerge "
        "in water for 1 hour each day or take exhaustion—a real constraint for surface adventuring. They have "
        "Darkvision 60 ft and speak Grung."
    ),
    "Hobgoblin": (
        "Hobgoblins are the disciplined strategists of goblinoid society—tall, lean warriors standing 5 to 6 "
        "feet tall with orange-red to dark reddish-brown skin, dark hair, and sharp, intelligent features. Their "
        "eyes burn yellow or orange, and their faces are more human than goblin but harder, crueler, and more "
        "intense. Hobgoblins train from birth and can live to about 80 years—if battle doesn't claim them sooner.\n\n"
        "Hobgoblin culture is martial to its core. They organize into legions with strict hierarchies, merit-based "
        "promotions, and zero tolerance for failure. A hobgoblin's worth is measured in battlefield performance. "
        "They are not mindless brutes—hobgoblins prize tactics, engineering, and logistics, and their war camps "
        "are efficiently run military installations. Their ultimate goal is conquest, and they see goblins and "
        "bugbears as useful but inferior troops. Adventuring hobgoblins are rare—usually ex-soldiers who were "
        "dishonored, spared enemies who found respect for individual foes, or spies learning the weaknesses of "
        "other races.\n\n"
        "Mechanically, hobgoblins gain +2 Constitution and +1 Intelligence. Martial Training grants proficiency "
        "with two martial weapons and light armor. Saving Face lets them add up to +4 to a failed attack roll, "
        "ability check, or saving throw (with a bonus based on nearby allies who witnessed the failure)—a "
        "brilliantly thematic ability. They have Darkvision 60 ft and speak Common and Goblin. Hobgoblins make "
        "excellent wizards, eldritch knights, and war clerics."
    ),
    "Kalashtar": (
        "Kalashtar are compound beings—human hosts bonded with quori, spirits of light from the dream realm of "
        "Dal Quor who defected from the nightmare that consumed their plane. Physically, kalashtar appear "
        "human but with a poised, symmetrical beauty and eyes that gleam with an inner light. They stand 5 to "
        "6 feet tall and live slightly longer than humans—about 120 years. Their quori spirit is not a separate "
        "entity but a symbiotic soul-fragment that shares their consciousness.\n\n"
        "Kalashtar culture is built on memory and purpose. Every kalashtar inherits the knowledge of their "
        "quori spirit—centuries of memories and the driving mission to oppose the Dreaming Dark, an evil quori "
        "faction that seeks to conquer the material world. Kalashtar are deeply empathetic, insightful, and "
        "reserved; they rarely show strong emotion because they experience the world through two perspectives "
        "simultaneously. They form tight-knit communities, and every kalashtar is expected to contribute to "
        "the long war against the darkness.\n\n"
        "Mechanically, kalashtar gain +2 Wisdom and +1 Charisma. Dual Mind grants advantage on Wisdom saving "
        "throws. Mental Discipline grants resistance to psychic damage. Mind Link lets them speak telepathically "
        "to any creature within 30 feet that shares a language—two-way communication, not one-way. Severed from "
        "Dreams makes them immune to spells and effects that require dreaming, like the dream spell. They speak "
        "Common, Quori, and one other language."
    ),
    "Kenku": (
        "Kenku are small, crow-like humanoids standing about 5 feet tall with black feathers, beady eyes, and "
        "hunched postures. Their arms end in taloned hands capable of fine manipulation, and their legs are "
        "those of large birds. Kenku lack wings—an ancient curse stripped flight from their entire race—but "
        "their bodies remain light and agile. They reach adulthood by 12 years and can live up to 60.\n\n"
        "Kenku are defined by the twin curses of their race: the loss of flight and the loss of creativity. "
        "An ancient transgression erased their capacity for original thought—a kenku can mimic perfectly but "
        "cannot invent. This makes them desperate imitators who survive by copying the skills and speech of "
        "others. They speak in patchwork voices, stitching together phrases they've heard into a mosaic of "
        "borrowed language. Kenku culture is built on longing—they dream of flight they cannot achieve, create "
        "art by replicating what they've seen, and forge identities from borrowed pieces. Adventuring kenku "
        "are often seeking the means to break their ancient curse.\n\n"
        "Mechanically, kenku gain +2 Dexterity and +1 Wisdom. Expert Duplication grants advantage on checks "
        "to copy or forge writing or objects. Kenku Recall grants proficiency in two skills and advantage on "
        "a check with one of them a few times per long rest. Mimicry lets them perfectly reproduce any sound "
        "they've heard—a Voice check opposed by Insight for listeners to detect the mimicry. They speak Common "
        "and Auran, though their speech is always a collage of copied phrases."
    ),
    "Kobold": (
        "Kobolds are diminutive draconic humanoids standing 2 to 3 feet tall with scaly skin in shades of rust "
        "red, brown, or black. They have reptilian snouts, horns sweeping back from their brows, and tails "
        "that twitch constantly with nervous energy. Their eyes are luminous, adapted for darkness. Kobolds "
        "reach adulthood by age 6 and rarely live past 20—but they breed so quickly this is not the problem "
        "for them that it would be for other races.\n\n"
        "Kobold culture is defined by two things: service to dragons and survival through cunning. A kobold "
        "warren is a death trap—a maze of tunnels rigged with snares, deadfalls, and scorpion pits. They are "
        "master trapmakers and tunnelers who worship dragons as gods, serving them with fanatical devotion. "
        "Kobolds are craven individually but fearless in numbers, and their ingenuity makes them far more "
        "dangerous than their size suggests. Adventuring kobolds are unusual—typically survivors of a destroyed "
        "warren, outcasts who angered the wrong dragon, or rare individuals who discovered that life above "
        "ground has its own appeal.\n\n"
        "Mechanically, kobolds gain +2 Dexterity. Grovel, Cower, and Beg is a delightfully thematic ability "
        "that distracts enemies and grants allies advantage on attacks against nearby foes. Pack Tactics grants "
        "advantage on attacks when an ally is next to the target. Sunlight Sensitivity imposes disadvantage "
        "on attacks and Perception in direct sunlight. They have Darkvision 60 ft and speak Common and Draconic. "
        "Kobolds make surprisingly effective rogues, rangers, and sorcerers."
    ),
    "Lizardfolk": (
        "Lizardfolk are cold-blooded reptilian humanoids standing 6 to 7 feet tall with thick, scaly hides in "
        "shades of green, brown, and gray. They have powerful jaws, long tails for balance, and unblinking "
        "eyes with slit pupils. Their movement is deliberate and economical—lizardfolk waste nothing, not even "
        "motion. They reach adulthood by 14 and can live to 60, though their alien mindset makes their age "
        "feel different from a human's.\n\n"
        "Lizardfolk think differently from warm-blooded races. They lack the capacity for complex emotion—"
        "no love, no guilt, no ambition in the human sense. Instead, they process the world through practical "
        "survival logic: is it food? Is it a threat? Can it be used? This makes them seem cold and calculating, "
        "but it also makes them unfailingly pragmatic. Lizardfolk villages are built around swamps and marshes, "
        "and they waste nothing—the dead are eaten, their bones carved into tools, their skins made into shields. "
        "Adventuring lizardfolk are often following pragmatic goals: a better hunting ground, new crafting "
        "materials, or the simple observation that working with warm-bloods produces better results than eating them.\n\n"
        "Mechanically, lizardfolk gain +2 Constitution and +1 Wisdom. Natural Armor gives them AC 13 + DEX "
        "when unarmored. Bite deals 1d6 piercing as an unarmed strike. Hungry Jaws lets them make a bonus-action "
        "bite and gain temporary HP once per short rest. Cunning Artisan lets them craft weapons and shields "
        "from fallen enemies during a short rest. Hold Breath gives them 15 minutes of air. They have a "
        "swimming speed of 30 ft and speak Common and Draconic."
    ),
    "Loxodon": (
        "Loxodons are elephantine humanoids who combine immense physical power with deep wisdom. Standing over "
        "7 feet tall and weighing 300–400 pounds, they have gray, wrinkled skin, a prehensile trunk, large "
        "floppy ears, and small, wise eyes. Their hands, while thick-fingered, are remarkably dexterous. "
        "Loxodons are long-lived, reaching 450 years or more, and their great age gives them a perspective "
        "that younger races often mistake for slowness.\n\n"
        "Loxodon culture values community, memory, and stonework. They are master masons who build structures "
        "meant to last millennia, and their oral histories preserve events from centuries ago with remarkable "
        "accuracy. A loxodon's community is everything—they form deep, lifelong bonds and mourn losses for "
        "years. They are slow to anger but terrifying when roused, and their serene demeanor masks a mind "
        "that is constantly observing, weighing, and remembering. Loxodons rarely adventure, but those who do "
        "often seek knowledge, justice for wrongs done to their people, or simply a deeper understanding "
        "of the world beyond their stone halls.\n\n"
        "Mechanically, loxodons gain +2 Constitution and +1 Wisdom. Natural Armor gives them AC 12 + CON "
        "(not DEX)—a loxodon barbarian or druid can dump Dexterity entirely. Powerful Build counts them "
        "as one size larger. Loxodon Serenity grants advantage on saves against being charmed or frightened. "
        "Their Trunk can lift, manipulate, and even wield light weapons or tools. Keen Smell grants advantage "
        "on Perception checks involving smell. They speak Common and Loxodon."
    ),
    "Minotaur": (
        "Minotaurs are powerfully built humanoids with bovine features—broad, horned heads, thick necks, "
        "cloven hooves, and bullish snouts. Standing 6 to 7 feet tall and weighing 300 pounds or more, they "
        "are covered in short fur ranging from brown to black to white, often with patches or dappling. Their "
        "horns curve forward and are formidable natural weapons. Minotaurs live about 150 years, though their "
        "passionate natures often lead to shorter lifespans.\n\n"
        "Minotaur culture is built on honor, combat, and labyrinthine philosophy. They value strength and "
        "directness, despising deception and cowardice. On Ravnica, minotaurs serve in the Boros Legion as "
        "shock troops or in the Gruul Clans as savage berserkers. Despite their reputation as mindless brutes, "
        "minotaurs are deeply contemplative—they navigate physical and metaphorical labyrinths in search of "
        "self-knowledge. A minotaur's horns are a source of personal pride, and they decorate them with "
        "rings, carvings, or battle trophies. Adventuring minotaurs seek glory, worthy challenges, or "
        "answers to questions that can only be found on the road.\n\n"
        "Mechanically, minotaurs gain +2 Strength and +1 Constitution. Horns deal 1d6 piercing damage and "
        "can be used to Shove as a bonus action after dashing. Goring Rush lets them make a horn attack "
        "as a bonus action after dashing at least 20 feet. Hammering Horns pushes a creature 10 feet when "
        "they hit with a melee attack during an Attack action. Labyrinthine Recall lets them perfectly "
        "recall any path they've traveled—they never get lost. They speak Common and Minotaur."
    ),
    "Orc": (
        "Orcs are powerfully built humanoids standing well over 6 feet tall with gray-green skin, coarse "
        "black hair, jutting lower tusks, and muscular builds that speak to a life of constant combat. Their "
        "eyes burn red in darkness—an evolutionary adaptation for seeing in the caves where many orc tribes "
        "dwell. Orcs reach adulthood by age 12 and rarely live past 50, not because of natural limits but "
        "because orc life is brutal and short.\n\n"
        "Orc culture is shaped by Gruumsh, their one-eyed god, who commands them to be strong, to conquer, "
        "and to crush the works of other races. Orc tribes are warrior societies where strength determines "
        "everything—the strongest leads, the weak serve or die, and glory in battle is the only path to honor. "
        "Yet orcs are not inherently evil; they are products of a culture that equates violence with virtue. "
        "Orcs who escape their tribes—through exile, capture, or the rare realization that there might be "
        "another way—must overcome a lifetime of conditioning and the suspicion of every race that has felt "
        "the bite of orcish blades. Adventuring orcs often struggle to prove they are more than the monster "
        "others see.\n\n"
        "Mechanically, orcs gain +2 Strength and +1 Constitution. Aggressive lets them move up to their "
        "speed toward an enemy as a bonus action. Powerful Build counts them as one size larger for carrying "
        "capacity. Menacing grants proficiency in Intimidation. They have Darkvision 60 ft and speak Common "
        "and Orc. Orcs make devastating barbarians, fighters, and rangers—built for aggression and endurance."
    ),
    "Shifter": (
        "Shifters are the descendants of lycanthropes, carrying the bestial blood of were-creatures in their "
        "veins—not the curse itself, but its echo. In their natural form, shifters look mostly human but with "
        "subtle animal traits: unusually large eyes, pointed ears, elongated canines, or downy fur along their "
        "forearms. When they shift, these traits become pronounced—claws extend, faces elongate, bodies bulk "
        "with predatory power. Shifters stand 5 to 6 feet tall and live roughly 70 years.\n\n"
        "Shifter culture is survivalist and mobile. They are hunted in many lands—mistaken for true lycanthropes "
        "or feared as cursed. This persecution has made shifters insular, forming tight family bands that travel "
        "constantly and trust only each other. They are pragmatic, athletic, and deeply attuned to their bodies. "
        "Each shifter's animal aspect shapes their personality: Beasthides are tough and stubborn, Longtooths "
        "are aggressive hunters, Swiftstride are restless wanderers, and Wildhunt are patient trackers. "
        "Adventuring shifters often seek a place to belong—or a way to master the beast within.\n\n"
        "Mechanically, shifters gain +2 Dexterity or Strength depending on subrace. Shifting is a bonus action "
        "that grants temporary HP and subrace-specific benefits for 1 minute (1/short rest): Beasthide (+1 AC, "
        "extra temp HP), Longtooth (bonus-action bite attack), Swiftstride (bonus movement + reactive movement), "
        "or Wildhunt (advantage on Wisdom checks, nearby enemies can't have advantage against you). They have "
        "Darkvision 60 ft and speak Common."
    ),
    "Simic Hybrid": (
        "Simic Hybrids are the product of the Simic Combine's cytoplast experimentation—humanoid baselines "
        "augmented with the adaptive traits of aquatic, insectoid, and reptilian life. They appear mostly "
        "human or elf but with striking modifications: gills along the neck, crab-like claws, gliding membranes "
        "between limbs, or carapace plates under the skin. Their appearance is a living catalog of the Simic's "
        "evolutionary ambitions. They age at the rate of their base species and live comparable lifespans.\n\n"
        "Simic Hybrids are individuals with science written into their bodies. Created as soldiers, guardians, "
        "or enforcers for the Simic Combine, many hybrids come to question their purpose. Some embrace their "
        "augmented nature as the next step in evolution; others seek to reclaim their original identity. The "
        "hybrids' animal enhancements grant strange abilities but also a sense of alienation—they are neither "
        "fully their original species nor fully what the Simic made them. Adventuring hybrids are often seeking "
        "answers: who were they before the cytoplasts, and who do they want to be now?\n\n"
        "Mechanically, simic hybrids gain +2 Constitution and +1 to another ability. At 1st level, they choose "
        "an Animal Enhancement—Manta Glide (gliding membranes), Nimble Climber (climbing speed), or "
        "Underwater Adaptation (swimming speed + water breathing). At 5th level, they gain a second: "
        "Grappling Appendages (bonus-action grapple with extra limbs), Carapace (+1 AC when not in heavy "
        "armor), or Acid Spit (ranged acid damage). They have Darkvision 60 ft and speak Common plus one "
        "other language."
    ),
    "Tabaxi": (
        "Tabaxi are feline humanoids from the distant jungles of Maztica and Chult. They stand taller than "
        "humans—5 to 7 feet—but are lean and lithe, with fur in patterns ranging from solid black to tabby "
        "stripes to spotted leopard rosettes. Their eyes are cat-slitted and come in vivid greens, golds, "
        "and blues. Tabaxi have long tails that twitch with emotion and retractable claws that make them "
        "natural climbers. They live about 80 years.\n\n"
        "Tabaxi are driven by an insatiable curiosity—an endless fascination with stories, secrets, and the "
        "unknown. They are natural explorers who collect experiences the way other races collect gold. A "
        "tabaxi might spend months obsessively studying a single ruin, then wander off mid-conversation when "
        "a shiny object catches their eye. Tabaxi culture is nomadic and oral, built around wandering bards "
        "called 'storytellers' who preserve clan histories. They are quick-witted and playful, rarely staying "
        "angry for long. Adventuring tabaxi are simply doing what tabaxi do best: seeing what's over the next "
        "hill.\n\n"
        "Mechanically, tabaxi gain +2 Dexterity and +1 Charisma. Feline Agility lets them double their speed "
        "for a turn—able to reset after a turn of 0 movement. Cat's Claws give them a 20 ft climbing speed "
        "and 1d4 slashing unarmed strikes. Cat's Talent grants proficiency in Perception and Stealth. They "
        "have Darkvision 60 ft and speak Common plus one other language. Tabaxi make excellent rogues, monks, "
        "rangers, and bards—mobility and grace define their playstyle."
    ),
    "Tlincalli": (
        "Tlincallis are scorpion-like humanoids with the lower body of a giant scorpion and the upper torso "
        "of a humanoid. Their chitinous exoskeleton is dark brown to black, their eight legs carry them "
        "with unsettling speed, and their barbed tail arches over their back, dripping with paralytic venom. "
        "Tlincallis stand 5 to 6 feet tall at the humanoid torso, but their full length is closer to 10 "
        "feet. They live about 60 years in the harsh deserts they call home.\n\n"
        "Tlincalli society is organized around the hunt. They are nomadic predators who track prey across "
        "vast deserts, capturing victims alive to feed their young—slowly, over days. They worship ancient "
        "scorpion gods and believe that all other creatures exist either as prey or as competition. Tlincalli "
        "have no concept of mercy, only the patient cruelty of the ambush predator. Adventuring tlincallis "
        "are extraordinarily rare and typically outcasts who discovered that cooperation yields better "
        "survival than predation—though old habits die hard.\n\n"
        "Mechanically, tlincallis are monstrous player characters with natural weapons (claws and tail sting "
        "with poison), chitinous armor, and burrowing abilities. Their stat block reflects their role as "
        "desert ambush predators—high Strength and Constitution, with specialized poison mechanics. They "
        "speak Tlincalli and usually learn Common only through exposure to prey species."
    ),
    "Tortle": (
        "Tortles are turtle-like humanoids who walk upright on two legs. They stand 5 to 6 feet tall and "
        "weigh 450–500 pounds, with domed shells of green-brown keratin, leathery skin, and beaked mouths. "
        "Their hands are thick but surprisingly dexterous, and their eyes are dark and placid. Tortles have "
        "a uniquely bittersweet lifespan: they live about 50 years, and in their final year, they feel an "
        "irresistible urge to return to their birthplace to mate and die.\n\n"
        "Tortle culture is defined by this life cycle. Young tortles spend their first 20 years among their "
        "own kind, learning survival and craft. Then they leave on a decades-long wandering, exploring the "
        "world and gathering experiences. This is the adventure—the time when a tortle's personality and "
        "wisdom form. Tortles are patient, deliberate, and philosophical; they see no need to rush and "
        "approach problems with the calm of someone who will outlive the crisis. When the urge comes, they "
        "return home, share their stories, mate, and die—their children inheriting the wisdom of lives "
        "well-lived.\n\n"
        "Mechanically, tortles gain +2 Strength and +1 Wisdom. Natural Armor gives them a flat AC 17 "
        "(no DEX bonus, shields still work)—making them the tankiest unarmored race. Shell Defense lets "
        "them withdraw into their shell as an action for +4 AC at the cost of being prone and immobile. "
        "Hold Breath lets them stay underwater for up to an hour. Survival Instinct grants proficiency "
        "in Survival and Nature. They speak Common and Aquan."
    ),
    "Triton": (
        "Tritons are proud aquatic humanoids from the elemental depths of the Plane of Water. They stand "
        "5 to 6 feet tall with blue, green, or silver skin, fins cresting their heads and forearms, and "
        "dark, webbed hands. Their eyes are large and adapted to the deep—solid black or silver. Tritons "
        "live about 200 years and carry themselves with the bearing of nobility.\n\n"
        "Triton society is a grand underwater empire that sees itself as the first line of defense against "
        "the horrors of the deep—krakens, aboleths, and worse. Every triton is a soldier, trained from "
        "birth to fight the enemies that lurk in the abyssal trenches. This martial culture breeds arrogance: "
        "tritons view surface dwellers as backward children who have no idea what threats swim beneath their "
        "ships. Adventuring tritons are often surface emissaries, scouts assessing the land-dwellers, or "
        "warriors who discovered that the greatest threats to their civilization now come from above.\n\n"
        "Mechanically, tritons gain +1 Strength, +1 Constitution, and +1 Charisma. They have a swimming "
        "speed of 30 ft and can breathe both air and water. Control Air and Water grants fog cloud at "
        "level 1, gust of wind at level 3, and wall of water at level 5—all once per long rest. Emissary "
        "of the Sea lets them communicate simple ideas with beasts that breathe water. Guardians of the "
        "Depths grants resistance to cold damage. They speak Common and Primordial."
    ),
    "Vedalken": (
        "Vedalken are tall, slender, blue-skinned humanoids from the orderly world of Ravnica. Standing "
        "6 to 6½ feet tall with hairless heads, elongated features, and six-fingered hands, they project "
        "an aura of calm intellect. Their skin is light to dark blue, sometimes with mottled patterns, "
        "and their eyes are pale—silver, gold, or lavender. Vedalken partially lack external ears, hearing "
        "through subtle ridges along their temples. They live up to 350 years.\n\n"
        "Vedalken culture is built on pure rationality. They are scientists, philosophers, and mages who "
        "pursue knowledge with methodical precision. A vedalken would rather spend a decade perfecting a "
        "single theory than rush to a flawed conclusion. This makes them seem cold, distant, and condescending "
        "to other races—and to be fair, they usually are. Vedalken form tight-knit research cabals and "
        "measure status by intellectual achievement. Adventuring vedalken are typically field researchers "
        "testing hypotheses, exiles who asked the wrong questions, or rare individuals who realized that "
        "the quest for perfect knowledge requires imperfect experience.\n\n"
        "Mechanically, vedalken gain +2 Intelligence and +1 Wisdom. Vedalken Dispassion grants advantage "
        "on all Intelligence, Wisdom, and Charisma saving throws—a version of gnome cunning. Tireless "
        "Precision grants proficiency in one skill and one tool, doubled when they spend time on the task. "
        "Partially Amphibious lets them breathe underwater for an hour. They speak Common, Vedalken, and "
        "one other language. Vedalken make natural wizards, artificers, and clerics of knowledge."
    ),
    "Warforged": (
        "Warforged are living constructs—soldiers built for war who outlived their purpose and must now "
        "find their own. They stand 5 to 7 feet tall with bodies of wood, stone, and metal plates "
        "interwoven with fibrous bundles that serve as muscles. A ghulra—a unique rune-like symbol—marks "
        "their forehead, the only feature they possess that is truly theirs. Warforged do not age and have "
        "existed only since the Last War, making the eldest barely 40 years old.\n\n"
        "Warforged were built as weapons. When the war ended and the Treaty of Thronehold recognized them "
        "as free beings, every warforged faced the same question: what now? With no childhood, no culture, "
        "and no template for civilian life, each must construct an identity from scratch. Some cling to "
        "military discipline as the only structure they know. Others embrace art, faith, or philosophy with "
        "the intensity of beings discovering that they can want things—that they have souls. Adventuring "
        "warforged may be seeking purpose, following former comrades, or simply doing what they were built "
        "for: fighting, protecting, and enduring.\n\n"
        "Mechanically, warforged gain +2 Constitution and +1 to any other ability. Constructed Resilience "
        "grants advantage on saves against poison, resistance to poison damage, immunity to disease, no "
        "need to eat, drink, or breathe, and no need for sleep—though they must remain motionless for 6 "
        "hours during a long rest. Integrated Protection grants +1 AC and armor that cannot be removed "
        "against their will. Sentry's Rest means they remain conscious during long rests. They speak "
        "Common and one other language."
    ),
    "Xvart": (
        "Xvarts are small, blue-skinned humanoids with a dark and tragic origin. Standing 3 feet tall, "
        "they have cobalt-blue skin, bulbous white eyes, and hairless heads. Their faces are bat-like—"
        "flattened noses, wide mouths, and pointed ears. Xvarts were created when the gnome god Raxivort "
        "fragmented his own soul to hide from pursuing demons, spawning thousands of xvarts, each carrying "
        "a fragment of divine essence. They reach adulthood by 5 and rarely live past 40.\n\n"
        "Xvart society is a pyramid of cruelty. At the top is Raxivort himself, a paranoid god who demands "
        "absolute worship from his fractured children. Xvarts are pathologically servile to stronger creatures "
        "and cruel to anything weaker. They live in squalid warrens, worshiping Raxivort through demeaning "
        "rituals and raiding nearby settlements for anything they can carry. Yet each xvart carries a spark "
        "of divinity—buried deep beneath generations of learned cowardice and inherited misery. Adventuring "
        "xvarts are the rare few who feel that spark and seek something more: a life beyond groveling, "
        "a purpose that is genuinely their own.\n\n"
        "Mechanically, xvarts gain +2 Dexterity and +1 Wisdom. Overbearing lets them use their reaction "
        "to grant an ally advantage on an attack against a nearby enemy. Raxivort's Tongue grants them "
        "speak with animals (bats and rats only) at will. They have Darkvision 30 ft—shorter than most "
        "Underdark races—and speak Common and Xvart (a corrupted dialect of Gnome)."
    ),
    "Yuan-ti Pureblood": (
        "Yuan-ti purebloods are serpentine humanoids who appear mostly human—at first glance. They stand "
        "5 to 6 feet tall with lithe builds, but closer inspection reveals patches of scales, a forked "
        "tongue, vertically slit eyes, and small fangs behind their lips. Their skin is pale with faint "
        "scale patterns, and their movements are unnaturally smooth. Purebloods are the most human-passing "
        "of the yuan-ti castes, bred specifically to infiltrate humanoid society. They live about 90 years.\n\n"
        "Yuan-ti society is a theocratic meritocracy that measures worth in ruthlessness and emotional "
        "detachment. Purebloods are the lowest caste, bred as servants, spies, and sacrificial fodder "
        "for the malisons and abominations above them. They are raised to suppress all emotion—love, "
        "fear, mercy—as weaknesses. Yet purebloods who spend time among other races often develop something "
        "their society considers a corruption: genuine feelings. Adventuring purebloods may be spies who "
        "defected, outcasts who failed a ritual, or rare individuals who discovered that emotions are not "
        "the weakness they were taught.\n\n"
        "Mechanically, yuan-ti purebloods gain +2 Charisma and +1 Intelligence. Magic Resistance grants "
        "advantage on all saving throws against spells and magical effects—arguably the strongest racial "
        "defensive trait. Poison Immunity grants full immunity to poison damage and the poisoned condition. "
        "Innate Spellcasting grants poison spray (cantrip), animal friendship (snakes only, 1/long rest), "
        "and suggestion (1/long rest). They have Darkvision 60 ft and speak Common, Abyssal, and Draconic."
    ),
}

# Also enrich Gith subrace descriptions (overrides short auto-extracted versions)
RICH_SUBRACE_DESCS: dict[str, str] = {
    "Githyanki": (
        "+2 Strength. Decadent Mastery grants proficiency in one skill and one tool, swappable each "
        "long rest (githyanki learn and discard skills with alien ease). Githyanki Psionics grants "
        "mage hand (invisible), jump at 3rd level, and misty step at 5th level, each 1/long rest. "
        "Githyanki are astral raiders—martial, arrogant, and driven by the will of their lich-queen "
        "Vlaakith. Their silver swords can sever an astral traveler's silver cord."
    ),
    "Githzerai": (
        "+2 Wisdom. Mental Discipline grants advantage on saves against charmed and frightened—the "
        "githzerai mind is a fortress. Githzerai Psionics grants mage hand (invisible), shield "
        "at 3rd level, and detect thoughts at 5th level, each 1/long rest. Githzerai are ascetic "
        "philosophers who found peace in the chaos of Limbo. They channel psionic power through "
        "discipline and meditation, opposing both mind flayers and their githyanki cousins."
    ),
    "Shadowborn Bearfolk": (
        "Shadowborn bearfolk are cubs born in the Shadow Realm with a proclivity for the darkness, "
        "marked by dark fur, glowing eyes, and an innate connection to shadow magic. They are "
        "brooding and intense, their ursine strength amplified by the eerie power of the Shadowfell."
    ),
    "Woodmen of Wilderland": (
        "Woodmen carve a living out of meagre hunts, burning charcoal and breeding animals. They "
        "stand between the shadows of Mirkwood and the open plains, hardy survivors who know the "
        "forest's dangers intimately. Skilled trackers and woodsmen, they protect their settlements "
        "from the creeping darkness of the forest."
    ),
    "Woodmen of Mountain Hall": (
        "The folk of Firienseld are close kin with those who live under the eaves of Mirkwood and "
        "share both their hardy nature and their suspicion of outsiders. Dwellers in mountain halls, "
        "they are sturdier and more isolated than their lowland cousins, expert miners and defenders "
        "of mountain passes."
    ),
    "Shadow Goblin": (
        "Shadow goblins are blue- or purple-skinned with bright orange or yellow eyes, evolved to "
        "thrive in the darkness of the Underdark or Shadowfell. More cunning and stealthy than "
        "their surface kin, they possess an innate connection to shadow magic that makes them "
        "elusive and dangerous opponents."
    ),
}

# Tag hardcoded races with source
_race_page_map: dict[str, str] = {}
try:
    _rpm_path = PAGE_MAP_DIR / "race_page_map.json"
    if _rpm_path.exists():
        with open(_rpm_path) as _f:
            _raw_rpm = json.load(_f)
        for _k, _v in _raw_rpm.items():
            _src = _v.get("source_str", "")
            if _src and "p." in _src:
                _race_page_map[_k] = _src
        # Apply to RACES
        for _r_name, _r_data in RACES.items():
            _mapped = _race_page_map.get(_r_name.lower())
            if _mapped:
                _r_data["source"] = _mapped
        _r_enriched = sum(1 for r in RACES.values() if "p." in r.get("source", ""))
        print(f"  Race sources enriched: {_r_enriched}/{len(RACES)}")
except Exception as _e:
    print(f"  (race page map unavailable: {_e})")

# Per-subrace source overrides (subraces that differ from their parent race)
SUBRACE_SOURCES: dict[str, str] = {
    # Dwarf
    "Duergar": "SCAG p.103",
    "Gold Dwarf": "SCAG p.102",
    # Elf
    "Sea Elf": "MTF p.62",
    "Eladrin": "MTF p.61",
    "Shadar-kai": "MTF p.62",
    # Halfling
    "Ghostwise Halfling": "SCAG p.110",
    # Gnome
    "Deep Gnome": "EEPC p.11",
    # Genasi subraces — EEPC pp.9-10
    "Air Genasi": "EEPC p.9",
    "Earth Genasi": "EEPC p.9",
    "Fire Genasi": "EEPC p.9",
    "Water Genasi": "EEPC p.10",
    # Aasimar subraces — Volo's Guide to Monsters
    "Protector": "Volo's Guide to Monsters p.104",
    "Scourge": "Volo's Guide to Monsters p.105",
    "Fallen": "Volo's Guide to Monsters p.105",
    # Tiefling variants — SCAG
    "Asmodeus": "PHB 2014 p.42",
    "Mephistopheles": "SCAG p.118",
    "Zariel": "SCAG p.118",
    "Dispater": "SCAG p.118",
    "Fierna": "SCAG p.118",
    "Glasya": "SCAG p.118",
    "Levistus": "SCAG p.118",
    "Mammon": "SCAG p.118",
}
# Attach _subrace_sources to each race (all subraces default to parent source)
for _r_name, _r in RACES.items():
    srcs = {}
    parent_src = _r.get("source", "")
    for _s in _r.get("subraces", []):
        srcs[_s] = SUBRACE_SOURCES.get(_s) or parent_src
    _r["_subrace_sources"] = srcs

# PHB p.17-43 — Racial trait descriptions
# Merge racial trait descriptions into the feature lookup so Breath Weapon etc. show descriptions
for trait_name, trait_desc in RACIAL_TRAIT_DESCS.items():
    key = trait_name.lower()
    if key not in FEATURE_DESCRIPTIONS:
        FEATURE_DESCRIPTIONS[key] = trait_desc

# Damage dice subscripts for natural weapon traits
_TRAIT_DICE = {
    "Cat's Claws": "1d4 + Str",       # Tabaxi
    "Claws": "1d4 + Str",             # Tortle
    "Bite": "1d6 + Str",              # Lizardfolk, Bearfolk, Shadowborn Bearfolk
    "Sharp Tusks": "1 + 1d4 psy",     # Ratatosk
}

# Subrace-specific trait lists
SUBRACE_TRAITS = {
    "Hill Dwarf": ["Dwarven Toughness"],
    "Mountain Dwarf": ["Dwarven Armor Training"],
    "High Elf": ["Elf Weapon Training", "Cantrip (High Elf)"],
    "Wood Elf": ["Elf Weapon Training", "Fleet of Foot", "Mask of the Wild"],
    "Dark Elf (Drow)": ["Superior Darkvision", "Sunlight Sensitivity", "Drow Magic"],
    "Sea Elf": ["Sea Elf Training", "Child of the Sea"],
    "Eladrin": ["Fey Step"],
    "Shadar-kai": ["Necrotic Resistance", "Blessing of the Raven Queen"],
    "Lightfoot Halfling": ["Naturally Stealthy"],
    "Stout Halfling": ["Stout Resilience"],
    "Ghostwise Halfling": ["Silent Speech"],
    "Forest Gnome": ["Natural Illusionist", "Speak with Small Beasts"],
    "Rock Gnome": ["Artificer's Lore", "Tinker"],
    "Deep Gnome": ["Superior Darkvision", "Stone Camouflage"],
    "Duergar": ["Superior Darkvision", "Duergar Resilience", "Duergar Magic", "Sunlight Sensitivity"],
    "Gold Dwarf": ["Dwarven Toughness"],
    "Air Genasi": ["Unending Breath", "Mingle with the Wind"],
    "Earth Genasi": ["Earth Walk", "Merge with Stone"],
    "Fire Genasi": ["Fire Resistance", "Reach to the Blaze"],
    "Water Genasi": ["Amphibious", "Swim", "Acid Resistance", "Call to the Wave"],
    "Variant Human": [],
    # Aasimar subraces
    "Protector": ["Radiant Soul"],
    "Scourge": ["Radiant Consumption"],
    "Fallen": ["Necrotic Shroud"],
    # Tiefling infernal variants — inherit base tiefling traits (Hellish Resistance, Infernal Legacy)
    # Each variant has a different +1 ASI (handled in SUBASIS) and variant spell list
    "Asmodeus": [],
    "Mephistopheles": [],
    "Zariel": [],
    "Dispater": [],
    "Fierna": [],
    "Glasya": [],
    "Levistus": [],
    "Mammon": [],
}

# PHB p.17-43 — Racial trait mechanical effects for automatic application
# Each key is a trait name; value is {armor_profs, weapon_profs, tool_profs,
#   skill_profs, damage_resist, condition_immune, speed, darkvision, hp_per_level}


# ── From data.py: DRACONIC_ANCESTRIES



SUBASIS = {
    "Hill Dwarf": {"wisdom": 1},
    "Mountain Dwarf": {"strength": 2},
    "High Elf": {"intelligence": 1},
    "Wood Elf": {"wisdom": 1},
    "Dark Elf (Drow)": {"charisma": 1},
    "Lightfoot Halfling": {"charisma": 1},
    "Stout Halfling": {"constitution": 1},
    "Forest Gnome": {"dexterity": 1},
    "Rock Gnome": {"constitution": 1},
    "Duergar": {"strength": 1},
    "Gold Dwarf": {"wisdom": 1},
    "Sea Elf": {"constitution": 1},
    "Eladrin": {"intelligence": 1},
    "Shadar-kai": {"constitution": 1},
    "Ghostwise Halfling": {"wisdom": 1},
    "Deep Gnome": {"dexterity": 1},
    "Air Genasi": {"dexterity": 1},
    "Earth Genasi": {"strength": 1},
    "Fire Genasi": {"intelligence": 1},
    "Water Genasi": {"wisdom": 1},
    # Aasimar subraces
    "Protector": {"wisdom": 1},
    "Scourge": {"constitution": 1},
    "Fallen": {"strength": 1},
    # Tiefling infernal variants
    "Asmodeus": {"intelligence": 1},
    "Mephistopheles": {"intelligence": 1},
    "Zariel": {"strength": 1},
    "Dispater": {"dexterity": 1},
    "Fierna": {"wisdom": 1},
    "Glasya": {"dexterity": 1},
    "Levistus": {"constitution": 1},
    "Mammon": {"intelligence": 1},
}

# ═══════════════════════════════════════════════════════════════════════════════
# SUBRACE MIGRATION — Promote manual-data races to proper subraces of core races
# ═══════════════════════════════════════════════════════════════════════════════
# Many manually-extracted "races" are really subraces of PHB races (e.g.
# "Mirkwood Elf" → Elf, "Riverfolk Halfling" → Halfling). This block extends
# the core race subrace lists and populates SUBASIS/SUBRACE_TRAITS/SUBRACE_SOURCES
# so load_manual_data() filters them out as top-level races.

# Generic trait names that belong to the base race, not the subrace
_GENERIC_TRAITS = {
    "ability score increase", "ability score increases",
    "adventuring age", "age", "size", "speed", "languages", "language",
    "alignment", "subtypes", "subtype",
}


# Load manual races.json for data (available after manual_data dir exists)
try:
    _mr_path = HERE / "data" / "manual_data" / "races.json"
    if _mr_path.exists():
        with open(_mr_path) as _f:
            _manual_races_raw = json.load(_f)
    else:
        _manual_races_raw = []
except Exception:
    _manual_races_raw = []

_mr_lookup = {r["name"]: r for r in _manual_races_raw}

# --- Migration definitions ---
# Each entry: subrace_name -> (parent_race, source_override_or_None, manual_entry_name_or_None)
# manual_entry_name defaults to subrace_name; set explicitly for aliased duplicates

_SUBRACE_MIGRATIONS: list[tuple[str, str, str | None, str | None]] = [
    # === ELF ===
    ("Mirkwood Elf",            "Elf", None, None),
    ("Sable Elf",               "Elf", None, None),
    ("Windrunner Elf",          "Elf", None, None),
    ("High Elf of Rivendell",   "Elf", None, None),
    ("Shadow Fey",              "Elf", None, None),
    # Flattened child subraces of Shadow Fey
    ("Shadow Fey (Lunar Elf)",  "Elf", None, "Lunar Elf"),  # from Shadow Fey subraces
    # === DWARF ===
    ("Dwarves of the Lonely Mountain", "Dwarf", None, None),
    ("Dwarf of the Blue Mountains",    "Dwarf", None, None),
    ("Dwarves of the Iron Hills",      "Dwarf", None, None),
    # === HALFLING ===
    ("Hobbit of the Shire",     "Halfling", None, None),
    ("Hobbit",                  "Halfling", None, None),
    ("Harfoot",                 "Halfling", None, None),
    ("Wild Hobbit",             "Halfling", None, None),
    ("Riverfolk Halfling",      "Halfling", None, None),
    ("Courtfolk Halfling",      "Halfling", None, None),
    # Flattened child subraces of Hobbit
    ("Hobbit (Harfoot)",        "Halfling", None, "Harfoot"),    # from Hobbit subraces
    ("Hobbit (Stoor)",          "Halfling", None, "Stoor"),      # from Hobbit subraces
    ("Hobbit (Fallowhide)",     "Halfling", None, "Fallowhide"), # from Hobbit subraces
    # Flattened child subrace of Courtfolk Halfling
    ("Courtfolk Halfling (Shadow Servitors)", "Halfling", None, "Shadow Servitors"),
    # === GNOME ===
    ("Wyrd Gnome",              "Gnome", None, None),
    # === HUMAN ===
    ("Umbral Human",            "Human", None, None),
    ("Changeling Umbral Human", "Human", None, None),
    ("Gifted Umbral Folk",      "Human", None, None),
    # Flattened child subrace of Umbral Human (renamed to avoid conflict with Eberron Changeling)
    ("Umbral Changeling",       "Human", None, "Changeling"),   # from Umbral Human subraces
    # Additional Human cultures (AiME)
    ("Barding",                 "Human", None, None),
    ("Men of Bree",             "Human", None, None),
    ("Men of Minas Tirith",     "Human", None, None),
    ("Men of the Lake",         "Human", None, None),
    ("Riders of Rohan",         "Human", None, None),
    # Flattened child subraces of Men of Bree
    ("Men of Bree (Stoor)",     "Human", None, "Stoor"),        # from Men of Bree subraces
    ("Men of Bree (Fallowhide)","Human", None, "Fallowhide"),   # from Men of Bree subraces
    # === HALFLING ===
    ("Courtfolk",               "Halfling", None, None),
]

# --- Apply migrations ---
for _sr_name, _parent, _src_override, _entry_name in _SUBRACE_MIGRATIONS:
    # 1. Extend RACES subrace list
    if _sr_name not in RACES[_parent]["subraces"]:
        RACES[_parent]["subraces"].append(_sr_name)

    # 2. Look up data source (use entry_name if provided, else sr_name)
    _lookup = _entry_name if _entry_name else _sr_name
    _entry = None
    if _entry_name:
        # Child subrace — search ONLY inside manual races' subraces, not top-level
        for _r in _manual_races_raw:
            for _sr in _r.get("subraces", []):
                if _sr.get("name") == _lookup:
                    _entry = _sr
                    break
            if _entry:
                break
    if not _entry:
        # Top-level manual race lookup
        _entry = _mr_lookup.get(_lookup)

    # 3. Populate SUBASIS (normalize stat abbreviations)
    _asi = {}
    _stat_map = {"str": "strength", "dex": "dexterity", "con": "constitution",
                 "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
    if _entry:
        for _k, _v in _entry.get("asi", {}).items():
            if _v:
                _asi[_stat_map.get(_k, _k)] = _v
    if _asi and _sr_name not in SUBASIS:
        SUBASIS[_sr_name] = _asi

    # 4. Populate SUBRACE_TRAITS
    if _entry:
        _traits = _subrace_traits(_entry)
        if _traits and _sr_name not in SUBRACE_TRAITS:
            SUBRACE_TRAITS[_sr_name] = _traits

    # 5. Populate SUBRACE_SOURCES
    if _sr_name not in SUBRACE_SOURCES:
        if _src_override:
            SUBRACE_SOURCES[_sr_name] = _src_override
        elif _entry and _entry.get("source"):
            SUBRACE_SOURCES[_sr_name] = _entry.get("source", "")

    # 6. Populate RACES[parent].subrace_descs (used by detail modal)
    _sr_desc = RICH_SUBRACE_DESCS.get(_sr_name, "")
    if not _sr_desc and _entry:
        _sr_desc = _entry.get("description", "")
    if _sr_desc and _sr_name not in RACES[_parent].get("subrace_descs", {}):
        RACES[_parent].setdefault("subrace_descs", {})[_sr_name] = _sr_desc

# Re-attach _subrace_sources (must include newly added subraces)
for _r_name, _r in RACES.items():
    srcs = {}
    parent_src = _r.get("source", "")
    for _s in _r.get("subraces", []):
        srcs[_s] = SUBRACE_SOURCES.get(_s) or parent_src
    _r["_subrace_sources"] = srcs

print(f"[subrace migration] Extended {len(_SUBRACE_MIGRATIONS)} manual races → core subraces")

# Tag hardcoded classes with source
_class_page_map: dict[str, str] = {}
try:
    _cpm_path = PAGE_MAP_DIR / "class_page_map.json"
    if _cpm_path.exists():
        with open(_cpm_path) as _f:
            _raw_cpm = json.load(_f)
        for _k, _v in _raw_cpm.items():
            _src = _v.get("source_str", "")
            if _src and "p." in _src:
                _class_page_map[_k] = _src
        # Apply to classes
        for _cname, _cdata in CLASSES.items():
            _mapped = _class_page_map.get(_cname.lower())
            if _mapped:
                _cdata["source"] = _mapped
        _enriched = sum(1 for c in CLASSES.values() if c.get("source"))
        print(f"  Class sources enriched: {_enriched}/{len(CLASSES)}")
except Exception as _e:
    print(f"  (class page map unavailable: {_e})")

# ── Subclass source enrichment ──
# Seed _subclass_sources from class_page_map for every subclass that has a mapping
_subclass_enriched = 0
for _cname, _cdata in CLASSES.items():
    _ss_map = _cdata.setdefault("_subclass_sources", {})
    for _sname in _cdata.get("subclasses", []):
        _mapped = _class_page_map.get(_sname.lower())
        if _mapped and _ss_map.get(_sname, "") != _mapped:
            _ss_map[_sname] = _mapped
            _subclass_enriched += 1
if _subclass_enriched:
    print(f"  Subclass sources enriched: {_subclass_enriched}")

# ── Subrace source enrichment from subrace_page_map.json ──
_subrace_page_map: dict[str, str] = {}
try:
    _srpm_path = PAGE_MAP_DIR / "subrace_page_map.json"
    if _srpm_path.exists():
        with open(_srpm_path) as _f:
            _raw_srpm = json.load(_f)
        for _k, _v in _raw_srpm.items():
            _src = _v.get("source_str", "")
            if _src and "p." in _src:
                _subrace_page_map[_k] = _src
        _srenriched = 0
        for _rname, _rdata in RACES.items():
            _sr_map = _rdata.setdefault("_subrace_sources", {})
            for _srname in _rdata.get("subraces", []):
                _mapped = _subrace_page_map.get(_srname.lower())
                if _mapped and _sr_map.get(_srname, "") != _mapped:
                    _sr_map[_srname] = _mapped
                    _srenriched += 1
        if _srenriched:
            print(f"  Subrace sources enriched: {_srenriched}")
except Exception as _e:
    print(f"  (subrace page map unavailable: {_e})")

# ── Load racial trait→page map for source badges ──
_trait_page_map: dict[str, str] = {}
try:
    _tpm_path = PAGE_MAP_DIR / "trait_page_map.json"
    if _tpm_path.exists():
        with open(_tpm_path) as _f:
            _trait_page_map = json.load(_f)
        print(f"  Trait sources loaded: {len(_trait_page_map)}")
except Exception as _e:
    print(f"  (trait page map unavailable: {_e})")

# ── From data.py: SKILL_ABILITIES, ALL_SKILLS, LANGUAGES

# ── From data.py: BACKGROUNDS, BACKGROUND_INFO
BACKGROUND_SOURCES = {bg: "Player's Handbook p.125-141" for bg in BACKGROUNDS if bg != "Custom"}
BACKGROUND_SOURCES["Custom"] = ""

# ── Enrich background sources with exact pages ──
_background_page_map: dict[str, str] = {}
try:
    _bgpm_path = PAGE_MAP_DIR / "background_page_map.json"
    if _bgpm_path.exists():
        with open(_bgpm_path) as _f:
            _background_page_map = json.load(_f)
        _bg_enriched = 0
        # Enrich existing entries
        for _bg_name in list(BACKGROUND_SOURCES.keys()):
            _mapped = _background_page_map.get(_bg_name)
            if _mapped and "p." in _mapped:
                BACKGROUND_SOURCES[_bg_name] = _mapped
                _bg_enriched += 1
        # Also add entries for backgrounds not yet in SOURCES (manual ones will be merged later)
        for _bg_name in BACKGROUNDS:
            if _bg_name not in BACKGROUND_SOURCES:
                _mapped = _background_page_map.get(_bg_name)
                if _mapped:
                    BACKGROUND_SOURCES[_bg_name] = _mapped
                    _bg_enriched += 1
        if _bg_enriched:
            print(f"  Background sources enriched: {_bg_enriched}/{len(BACKGROUND_SOURCES)}")
except Exception as _e:
    print(f"  (background page map unavailable: {_e})")

# ── From data.py: ALIGNMENTS

# ── SRD Weapons (PHB p.149) ─────────────────────────────────────────────────
WEAPONS = {
    # Simple Melee
    "club":            {"damage":"1d4","type":"bludgeoning","props":["light"],"category":"simple melee"},
    "dagger":          {"damage":"1d4","type":"piercing","props":["finesse","light","thrown (20/60)"],"category":"simple melee"},
    "greatclub":       {"damage":"1d8","type":"bludgeoning","props":["two-handed"],"category":"simple melee"},
    "handaxe":         {"damage":"1d6","type":"slashing","props":["light","thrown (20/60)"],"category":"simple melee"},
    "javelin":         {"damage":"1d6","type":"piercing","props":["thrown (30/120)"],"category":"simple melee"},
    "light hammer":    {"damage":"1d4","type":"bludgeoning","props":["light","thrown (20/60)"],"category":"simple melee"},
    "mace":            {"damage":"1d6","type":"bludgeoning","props":[],"category":"simple melee"},
    "quarterstaff":    {"damage":"1d6","type":"bludgeoning","props":["versatile (1d8)"],"category":"simple melee"},
    "sickle":          {"damage":"1d4","type":"slashing","props":["light"],"category":"simple melee"},
    "spear":           {"damage":"1d6","type":"piercing","props":["thrown (20/60)","versatile (1d8)"],"category":"simple melee"},
    # Simple Ranged
    "crossbow, light": {"damage":"1d8","type":"piercing","props":["ammunition (80/320)","loading","two-handed"],"category":"simple ranged"},
    "dart":            {"damage":"1d4","type":"piercing","props":["finesse","thrown (20/60)"],"category":"simple ranged"},
    "shortbow":        {"damage":"1d6","type":"piercing","props":["ammunition (80/320)","two-handed"],"category":"simple ranged"},
    "sling":           {"damage":"1d4","type":"bludgeoning","props":["ammunition (30/120)"],"category":"simple ranged"},
    # Martial Melee
    "battleaxe":       {"damage":"1d8","type":"slashing","props":["versatile (1d10)"],"category":"martial melee"},
    "flail":           {"damage":"1d8","type":"bludgeoning","props":[],"category":"martial melee"},
    "glaive":          {"damage":"1d10","type":"slashing","props":["heavy","reach","two-handed"],"category":"martial melee"},
    "greataxe":        {"damage":"1d12","type":"slashing","props":["heavy","two-handed"],"category":"martial melee"},
    "greatsword":      {"damage":"2d6","type":"slashing","props":["heavy","two-handed"],"category":"martial melee"},
    "halberd":         {"damage":"1d10","type":"slashing","props":["heavy","reach","two-handed"],"category":"martial melee"},
    "lance":           {"damage":"1d12","type":"piercing","props":["reach","special"],"category":"martial melee"},
    "longsword":       {"damage":"1d8","type":"slashing","props":["versatile (1d10)"],"category":"martial melee"},
    "maul":            {"damage":"2d6","type":"bludgeoning","props":["heavy","two-handed"],"category":"martial melee"},
    "morningstar":     {"damage":"1d8","type":"piercing","props":[],"category":"martial melee"},
    "pike":            {"damage":"1d10","type":"piercing","props":["heavy","reach","two-handed"],"category":"martial melee"},
    "rapier":          {"damage":"1d8","type":"piercing","props":["finesse"],"category":"martial melee"},
    "scimitar":        {"damage":"1d6","type":"slashing","props":["finesse","light"],"category":"martial melee"},
    "shortsword":      {"damage":"1d6","type":"piercing","props":["finesse","light"],"category":"martial melee"},
    "trident":         {"damage":"1d6","type":"piercing","props":["thrown (20/60)","versatile (1d8)"],"category":"martial melee"},
    "war pick":        {"damage":"1d8","type":"piercing","props":[],"category":"martial melee"},
    "warhammer":       {"damage":"1d8","type":"bludgeoning","props":["versatile (1d10)"],"category":"martial melee"},
    "whip":            {"damage":"1d4","type":"slashing","props":["finesse","reach"],"category":"martial melee"},
    # Martial Ranged
    "blowgun":         {"damage":"1","type":"piercing","props":["ammunition (25/100)","loading"],"category":"martial ranged"},
    "crossbow, hand":  {"damage":"1d6","type":"piercing","props":["ammunition (30/120)","light","loading"],"category":"martial ranged"},
    "crossbow, heavy": {"damage":"1d10","type":"piercing","props":["ammunition (100/400)","heavy","loading","two-handed"],"category":"martial ranged"},
    "longbow":         {"damage":"1d8","type":"piercing","props":["ammunition (150/600)","heavy","two-handed"],"category":"martial ranged"},
    "net":             {"damage":"—","type":"special","props":["thrown (5/15)","special"],"category":"martial ranged"},
    # Renaissance Firearms (DMG 2014 p.268)
    "pistol":           {"damage":"1d10","type":"piercing","props":["ammunition (30/90)","loading"],"category":"martial ranged","source":"DMG","tech":"renaissance","cost":"250 gp"},
    "musket":           {"damage":"1d12","type":"piercing","props":["ammunition (40/120)","loading","two-handed"],"category":"martial ranged","source":"DMG","tech":"renaissance","cost":"500 gp"},
    # Modern Firearms (DMG 2014 p.268)
    "pistol, automatic": {"damage":"2d6","type":"piercing","props":["ammunition (50/150)","reload (15 shots)"],"category":"martial ranged","source":"DMG","tech":"modern"},
    "revolver":         {"damage":"2d8","type":"piercing","props":["ammunition (40/120)","reload (6 shots)"],"category":"martial ranged","source":"DMG","tech":"modern"},
    "rifle, hunting":   {"damage":"2d10","type":"piercing","props":["ammunition (80/240)","reload (5 shots)","two-handed"],"category":"martial ranged","source":"DMG","tech":"modern"},
    "rifle, automatic": {"damage":"2d8","type":"piercing","props":["ammunition (80/240)","burst fire","reload (30 shots)","two-handed"],"category":"martial ranged","source":"DMG","tech":"modern"},
    "shotgun":          {"damage":"2d8","type":"piercing","props":["ammunition (30/90)","reload (2 shots)","two-handed"],"category":"martial ranged","source":"DMG","tech":"modern"},
    # Futuristic Firearms (DMG 2014 p.268)
    "laser pistol":     {"damage":"3d6","type":"radiant","props":["ammunition (40/120)","reload (50 shots)"],"category":"martial ranged","source":"DMG","tech":"futuristic"},
    "antimatter rifle": {"damage":"6d8","type":"necrotic","props":["ammunition (120/360)","reload (2 shots)","two-handed"],"category":"martial ranged","source":"DMG","tech":"futuristic"},
    "laser rifle":      {"damage":"3d8","type":"radiant","props":["ammunition (100/300)","reload (30 shots)","two-handed"],"category":"martial ranged","source":"DMG","tech":"futuristic"},
}

# ── Post-pass: resolve base_weapon for magic weapons in ITEM_INDEX ──
for key, entry in ITEM_INDEX.items():
    if entry.get("type") == "Magic Weapon" and not entry.get("base_weapon"):
        name = entry["name"].lower()
        for wpn_name in WEAPONS:
            if wpn_name in name:
                entry["base_weapon"] = wpn_name
                break
        if not entry.get("base_weapon"):
            aliases = {'sword': 'longsword', 'axe': 'battleaxe', 'mace': 'mace',
                       'hammer': 'warhammer', 'bow': 'longbow', 'dagger': 'dagger',
                       'scimitar': 'scimitar', 'javelin': 'javelin', 'trident': 'trident',
                       'lance': 'lance', 'flail': 'flail', 'whip': 'whip',
                       'glaive': 'glaive', 'halberd': 'halberd', 'pike': 'pike',
                       'blade': 'longsword', 'slayer': 'greatsword', 'defender': 'longsword',
                       'thrower': 'warhammer', 'brand': 'greatsword',
                       'vorpal': 'longsword', 'sharpness': 'longsword',
                       'wounding': 'longsword', 'disruption': 'mace',
                       'smiting': 'mace', 'terror': 'mace',
                       'lightning': 'javelin', 'fish command': 'trident',
                       'tongue': 'longsword', 'avenger': 'longsword'}
            for alias, base in aliases.items():
                if alias in name:
                    entry["base_weapon"] = base
                    break









NAMED_ITEM_TYPES = None  # computed lazily after ITEM_INDEX is populated



# ── Armor Proficiency (PHB p.144) ──

ARMOR_PROFICIENCY_MAP = {
    "Light": "Light armor",
    "Medium": "Medium armor",
    "Heavy": "Heavy armor",
    "Shield": "Shields",
}

# Build fallback armor name index once at module load (from SRD equipment, not ITEM_INDEX)
_ARMOR_LOOKUP = {}
_ARMOR_STATS = {}  # full equipment_category/armor_category for AC calc
for _item in SRD_EQUIPMENT:
    _ec = (_item.get("equipment_category") or {}).get("name", "")
    _ac = _item.get("armor_category", "")
    _name = _item.get("name", "").lower()
    if _ec == "Armor" and _ac != "Shield" and _name:
        _ARMOR_LOOKUP[_name] = _item
    if _name:
        _ARMOR_STATS[_name] = _item










# ── Routes: Landing ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", 303)
    return _render("landing.html", request=request)

# ── Routes: Dashboard ───────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    if where:
        # Strip leading "WHERE " since we inline it
        where_clause = where[6:].strip() if where.startswith("WHERE ") else where
        chars = [dict(r) for r in db.execute(
            f"SELECT c.*, u.email as owner_email FROM characters c "
            f"LEFT JOIN users u ON c.user_id = u.id "
            f"WHERE ({where_clause}) OR (c.shared = 1 AND c.user_id != ?) "
            f"ORDER BY c.shared ASC, c.created_at DESC",
            (*params, user["id"])
        ).fetchall()]
    else:
        # Admin: see all characters
        chars = [dict(r) for r in db.execute(
            f"SELECT c.*, u.email as owner_email FROM characters c "
            f"LEFT JOIN users u ON c.user_id = u.id "
            f"ORDER BY c.shared ASC, c.created_at DESC"
        ).fetchall()]
    db.close()
    # Load favorites and sort: favorites first
    try:
        favs = json.loads(user.get("favorites", "[]"))
    except (json.JSONDecodeError, TypeError):
        favs = []
    fav_set = set(int(f) for f in favs if str(f).isdigit())
    # Sort: favorites first, then by creation date descending
    fav_chars = [c for c in chars if c["id"] in fav_set]
    other_chars = [c for c in chars if c["id"] not in fav_set]
    fav_chars.sort(key=lambda c: c.get("created_at", "") or "", reverse=True)
    other_chars.sort(key=lambda c: c.get("created_at", "") or "", reverse=True)
    chars = fav_chars + other_chars
    for c in chars:
        for f in ("skills","features","inventory","equipped","languages"):
            try:
                c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError):
                c[f] = []
    return _render("dashboard.html", request=request, characters=chars, current_user_id=user["id"], favorites=favs)


# ── Toggle favorite ────────────────────────────────────────────────────
@app.post("/api/characters/{char_id}/favorite")
async def toggle_favorite(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    try:
        favs = json.loads(user.get("favorites", "[]"))
    except (json.JSONDecodeError, TypeError):
        favs = []
    fav_set = set(str(f) for f in favs)
    sid = str(char_id)
    if sid in fav_set:
        favs = [f for f in favs if str(f) != sid]
    else:
        favs.append(char_id)
    db.execute("UPDATE users SET favorites=? WHERE id=?", (json.dumps(favs), user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"favorited": sid not in fav_set, "favorites": favs})


# ── Character routes moved to routes/characters.py — registered in startup

# ── Custom trap CRUD ──────────────────────────────────────────────────────────

@app.get("/api/dm/traps", response_class=JSONResponse)
async def dm_list_traps(request: Request):
    """List all traps — manual data + custom user traps."""
    user = require_user(request)
    db = get_db()
    all_traps = list(MANUAL_TRAPS)
    custom = [dict(r) for r in db.execute(
        "SELECT * FROM dm_custom_traps WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]
    db.close()
    for ct in custom:
        all_traps.append({
            "name": ct["name"],
            "type": ct["type"],
            "danger": ct["danger"],
            "trigger": ct.get("trigger", ""),
            "detection": {"dc": ct.get("detection_dc"), "skill": ct.get("detection_skill", "Perception"), "detail": ct.get("detection_detail", "")},
            "disarm": {"dc": ct.get("disarm_dc"), "method": ct.get("disarm_method", ""), "detail": ct.get("disarm_detail", "")},
            "effect": ct.get("effect", ""),
            "save_dc": ct.get("save_dc"),
            "save_ability": ct.get("save_ability", "Dexterity"),
            "damage": ct.get("damage", ""),
            "damage_type": ct.get("damage_type", ""),
            "area": ct.get("area", ""),
            "description": ct.get("description", ""),
            "_custom_id": ct["id"],
        })
    return JSONResponse({"traps": all_traps})


@app.post("/api/dm/traps/create", response_class=JSONResponse)
async def dm_create_trap(request: Request):
    """Create a new custom trap."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_custom_traps (user_id, name, type, danger, trigger,
            detection_dc, detection_skill, detection_detail,
            disarm_dc, disarm_method, disarm_detail,
            effect, save_dc, save_ability, damage, damage_type, area, description)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], name,
        data.get("type", "mechanical"),
        data.get("danger", "dangerous"),
        data.get("trigger", ""),
        data.get("detection_dc"),
        data.get("detection_skill", "Perception"),
        data.get("detection_detail", ""),
        data.get("disarm_dc"),
        data.get("disarm_method", ""),
        data.get("disarm_detail", ""),
        data.get("effect", ""),
        data.get("save_dc"),
        data.get("save_ability", "Dexterity"),
        data.get("damage", ""),
        data.get("damage_type", ""),
        data.get("area", ""),
        data.get("description", ""),
    ))
    db.commit()
    trap_id = cur.lastrowid
    db.close()
    return JSONResponse({"ok": True, "id": trap_id})


@app.post("/api/dm/traps/{trap_id}/update", response_class=JSONResponse)
async def dm_update_trap(trap_id: int, request: Request):
    """Update a custom trap."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    trap = db.execute(
        "SELECT id FROM dm_custom_traps WHERE id = ? AND user_id = ?",
        (trap_id, user["id"])
    ).fetchone()
    if not trap:
        db.close()
        return JSONResponse({"error": "Trap not found"}, status_code=404)
    db.execute("""
        UPDATE dm_custom_traps SET name=?, type=?, danger=?, trigger=?,
            detection_dc=?, detection_skill=?, detection_detail=?,
            disarm_dc=?, disarm_method=?, disarm_detail=?,
            effect=?, save_dc=?, save_ability=?, damage=?, damage_type=?, area=?, description=?
        WHERE id=?
    """, (
        (data.get("name") or "").strip(),
        data.get("type", "mechanical"),
        data.get("danger", "dangerous"),
        data.get("trigger", ""),
        data.get("detection_dc"),
        data.get("detection_skill", "Perception"),
        data.get("detection_detail", ""),
        data.get("disarm_dc"),
        data.get("disarm_method", ""),
        data.get("disarm_detail", ""),
        data.get("effect", ""),
        data.get("save_dc"),
        data.get("save_ability", "Dexterity"),
        data.get("damage", ""),
        data.get("damage_type", ""),
        data.get("area", ""),
        data.get("description", ""),
        trap_id
    ))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/traps/{trap_id}/delete", response_class=JSONResponse)
async def dm_delete_trap(trap_id: int, request: Request):
    """Delete a custom trap."""
    user = require_user(request)
    db = get_db()
    db.execute(
        "DELETE FROM dm_custom_traps WHERE id = ? AND user_id = ?",
        (trap_id, user["id"])
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── Item search & description endpoints ──────────────────────────────────────

@app.get("/api/items/search", response_class=JSONResponse)
async def search_items(q: str = "", limit: int = 0):
    """Search equipment + magic items by name (fuzzy substring match).
    When q is empty, returns all items alphabetically (up to limit)."""
    query = q.strip().lower()
    results = []
    if not query:
        # Return all items alphabetically
        for key in sorted(ITEM_INDEX.keys()):
            item = ITEM_INDEX[key]
            desc = item.get("description") or ""
            if isinstance(desc, list):
                desc = " ".join(desc)
            results.append({
                "name": item["name"],
                "type": item["type"],
                "rarity": item.get("rarity", ""),
                "source": item.get("source", ""),
                "cost": item.get("cost", ""),
                "weight": item.get("weight"),
                "description": desc[:150],
                "concentration": "concentration" in desc.lower(),
            })
            if limit and len(results) >= limit:
                break
    else:
        for key, item in ITEM_INDEX.items():
            if query in key:
                desc = item.get("description") or ""
                if isinstance(desc, list):
                    desc = " ".join(desc)
                results.append({
                    "name": item["name"],
                    "type": item["type"],
                    "rarity": item.get("rarity", ""),
                    "source": item.get("source", ""),
                    "cost": item.get("cost", ""),
                    "weight": item.get("weight"),
                    "description": desc[:150],
                    "concentration": "concentration" in desc.lower(),
                })
                if limit and len(results) >= limit:
                    break
        results.sort(key=lambda r: (0 if r["name"].lower().startswith(query) else 1, r["name"]))

    return JSONResponse({"results": (results[:limit] if limit else results), "total": len(ITEM_INDEX)})


@app.get("/api/items/describe", response_class=JSONResponse)
async def describe_item(name: str = ""):
    """Get full description and metadata for a single item."""
    if not name or not name.strip():
        return JSONResponse({"error": "No item name provided"}, status_code=400)
    key = name.strip().lower()
    item = _resolve_item_key(name)
    if not item:
        return JSONResponse({"name": name, "description": "No description available.", "type": "Unknown"})
    # Split curse text from description for hidden rendering
    desc = item.get("description") or item.get("desc", "")
    if isinstance(desc, list):
        desc = " ".join(desc)
    safe_desc, curse_text = _split_curse_text(desc)
    enriched = dict(item)
    enriched["description"] = safe_desc
    if curse_text:
        enriched["curse"] = curse_text
    return JSONResponse(enriched)


# ── Canonical feature/spell-slot helpers ────────────────────────────────────
# Implementations live in services/leveling.py. Lazy import inside the body —
# importing at module level creates a circular import (services/leveling
# imports from main). The wrappers keep `from main import ...` callers working.

def enrich_features(*args, **kwargs):
    from services.leveling import enrich_features as _impl
    return _impl(*args, **kwargs)


def get_caster_type(*args, **kwargs):
    from services.leveling import get_caster_type as _impl
    return _impl(*args, **kwargs)


def get_spell_slots(*args, **kwargs):
    from services.leveling import get_spell_slots as _impl
    return _impl(*args, **kwargs)


# ── Startup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app):
    init_db()
    # Register route modules (deferred to avoid circular imports)
    from routes.auth import router as auth_router
    if not any(r.path == "/login" for r in _app.routes):
        _app.include_router(auth_router)
    from routes.dm import router as dm_router
    if not any(r.path == "/dm-tools" for r in _app.routes):
        _app.include_router(dm_router)
    from routes.characters import router as char_router
    if not any(r.path == "/create" for r in _app.routes):
        _app.include_router(char_router)
    yield


app.router.lifespan_context = lifespan

# ── Reference Manual Lookup ─────────────────────────────────────────────────
# Ingested manuals from data/manual_data/ + cached extracts from data/manual_cache/

MANUALS_BASE = (DATA_DIR.parent / "manuals").resolve()

@app.get("/api/reference/manuals", response_class=JSONResponse)
def list_manuals():
    """List available reference manuals — PDFs on disk + all ingested manuals. No auth."""
    import glob
    result = {"count": 0, "manuals": [], "ingested": [], "path": str(MANUALS_BASE)}

    # 1. PDFs in the manuals directory (recursive, grouped by folder)
    if MANUALS_BASE.exists():
        pdfs = sorted(glob.glob(str(MANUALS_BASE / "**/*.pdf"), recursive=True))
        result["manuals"] = [Path(p).name for p in pdfs]
        result["count"] = len(pdfs)

    # 2. Ingested manuals (meta.json slug_map)
    meta = _load_manual_json("_meta.json") or {}
    pdf_map = meta.get("pdf_map", {}) if isinstance(meta, dict) else {}
    slug_map = _get_source_slug_map()
    result["ingested"] = [
        {"slug": s, "title": slug_map.get(s, {}).get("display", info.get("title", s))}
        for s, info in pdf_map.items()
    ]

    return JSONResponse(result)


@app.get("/api/reference/manual-file/{path:path}")
async def serve_manual_file(path: str):
    """Serve a PDF from the DnD-Manuals directory by relative path.
    Path is relative to MANUALS_BASE (e.g. 'Manuals/D&D 5E - Monster Manual.pdf').
    """
    # Security: block directory traversal
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=403, detail="Invalid path")
    full_path = (MANUALS_BASE / path).resolve()
    if not full_path.exists() or full_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(
        full_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=\"{full_path.name}\""},
    )


@app.post("/api/reference/query", response_class=JSONResponse)
async def query_reference(request: Request):
    """Query reference manuals for PHB-grounding. Requires auth.
    Accepts: {"query": "dwarf racial traits", "source": "phb"}"""
    user = require_user(request)
    data = await request.json()
    query = data.get("query", "")
    source = data.get("source", "phb")  # "phb", "dmg", "xanathars", "all"

    # Map source to filename patterns
    source_map = {
        "phb": "Player's Handbook",
        "dmg": "Dungeon Master's Guide",
        "xanathars": "Xanathar's Guide",
        "all": "",
    }
    pattern = source_map.get(source, source)

    if not MANUALS_BASE.exists():
        return JSONResponse({"results": [], "warning": "Manual directory not available"})

    # Find matching PDFs
    import glob
    pdfs = sorted(glob.glob(str(MANUALS_BASE / "*.pdf")) + glob.glob(str(MANUALS_BASE / "*/*.pdf")))
    matches = [p for p in pdfs if pattern.lower() in Path(p).name.lower()]

    return JSONResponse({
        "query": query,
        "source": source,
        "manuals_found": len(matches),
        "files": [Path(p).name for p in matches],
        "note": "Reference lookups are manual — use the PDFs directly. Future: pdfplumber extraction.",
        "path_hint": str(MANUALS_BASE),
    })


# ── Reference PDF Viewer ─────────────────────────────────────────────────────

# Cache the slug→display_name map from manual data meta
_source_slug_cache: dict | None = None


def _get_source_slug_map() -> dict[str, dict]:
    """Return {slug: {title, path, display}} from pdf_map."""
    global _source_slug_cache
    if _source_slug_cache is None:
        meta = _load_manual_json("_meta.json")
        pdf_map = (meta or {}).get("pdf_map", {}) if isinstance(meta, dict) else {}
        _source_slug_cache = {}
        # Human-readable display names for each slug (used for frontend matching)
        _slug_displays = {
            "AIPG": "Adventures in Middle-earth Player's Guide",
            "AW": "Ancestral Weapons",
            "BLRG": "Bree-land Region Guide",
            "CC": "Creature Codex",
            "CSF": "Courts of the Shadow Fey",
            "DD": "Dues for the Dead",
            "DDP": "Defiance in Phlan",
            "DMG": "Dungeon Master's Guide",
            "DPM": "Deep Magic: Elven High Magic",
            "DPM1": "Deep Magic: Ley Lines",
            "EBT": "Book of Ebon Tides",
            "EEPC": "Elemental Evil Player's Companion",
            "EIA": "Encounters in Avernus",
            "EREA": "Erebor Adventures",
            "ERIA": "Eriador Adventures",
            "ETR": "Expanding the Ranger",
            "GGR": "Guildmasters' Guide to Ravnica",
            "HotDQ": "Hoard of the Dragon Queen",
            "KW": "Kobold Quarterly 20",
            "LMG": "Adventures in Middle-earth Loremaster's Guide",
            "LMRG": "Lonely Mountain Region Guide",
            "LMoP": "Lost Mine of Phandelver",
            "MM": "Monster Manual",
            "MOM": "Marauders of the Margreve",
            "MPG": "Margreve Player's Guide",
            "MTF": "Mordenkainen's Tome of Foes",
            "MWC": "Mirkwood Campaign",
            "PHB": "Player's Handbook",
            "RAT": "Ratatosk",
            "RGEO": "The Road Goes Ever On",
            "RRG": "Rhovanion Region Guide",
            "RVR": "Rivendell Region Guide",
            "RoT": "The Rise of Tiamat",
            "SCAG": "Sword Coast Adventurer's Guide",
            "SDQ": "Shadows of the Dusk Queen",
            "SME": "Saltmarsh Encounters",
            "SOM": "Shadows over the Moonsea",
            "SSK": "Secrets of Sokol Keep",
            "TFS": "Tales from the Shadows",
            "TLT": "The Tortured Land",
            "TMFRV": "Tales of the Margreve",
            "TTP": "The Tortle Package",
            "ToA": "Tomb of Annihilation",
            "VGM": "Volo's Guide to Monsters",
            "W": "Wrath of the Bramble King",
            "W1": "Pride of the Mushroom Queen",
            "W2": "Warlock 7",
            "W3": "Warlock 17",
            "W4": "Warlock 22: Druids",
            "W5": "Warlock 32",
            "W6": "Warlock 34",
            "W7": "Warlock Bestiary",
            "W8": "Warlock Lair: The Returners' Tower",
            "W9": "Warlock Lair: The Dark Aerie",
            "WDH": "Waterdeep: Dragon Heist",
            "WGE": "Wayfinder's Guide to Eberron",
            "WLA": "Wilderland Adventures",
            "WLL": "Warlock Lairs: Into the Wilds",
            "WRKF": "Wrath of the River King",
            "WS": "Shadows Envy",
            "WSC": "The Wild Sheep Chase",
            "XGE": "Xanathar's Guide to Everything",
            # ── Critical Role ──
            "CotN": "Call of the Netherdeep",
            "EGW": "Explorer's Guide to Wildemount",
            "TCSR": "Tal'Dorei Campaign Setting Reborn",
        }
        for slug, info in pdf_map.items():
            title = info.get("title", slug)
            display = _slug_displays.get(slug) or re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
            _source_slug_cache[slug] = {
                "title": title,
                "display": display,
                "path": info.get("path", ""),
            }
        # Add chapter/section aliases that point to their parent book
        _chapter_aliases = {
            "Magical Treasure Index": {"slug": "AIPG", "display": "Adventures in Middle-earth Player's Guide — Magical Treasure Index"},
            "TCE": {"slug": "DTCOE", "display": "Tasha's Cauldron of Everything"},
            "Tome of Beasts": {"slug": "CC", "display": "Creature Codex (Tome of Beasts)"},
            "Kobold Quarterly #20": {"slug": "KW", "display": "Kobold Quarterly 20"},
            "Kobold Quarterly": {"slug": "KW", "display": "Kobold Quarterly 20"},
            "Deep Magic Ley Lines": {"slug": "DPM1", "display": "Deep Magic Ley Lines"},
            "Deep Magic: Ley Lines": {"slug": "DPM1", "display": "Deep Magic Ley Lines"},
            "Mythic Odysseys of Theros": {"slug": "DTCOE", "display": "Mythic Odysseys of Theros (in Tasha's)"},
            "Winter Wizardry": {"slug": "W", "display": "Winter Wizardry (Wrath of the Bramble King)"},
            "Winter 2012": {"slug": "KW", "display": "Winter 2012 (Kobold Quarterly 20)"},
            "Tome of Beasts (or similar sourcebook": {"slug": "CC", "display": "Creature Codex (Tome of Beasts)"},
            "Tome of Beasts (page": {"slug": "CC", "display": "Creature Codex (Tome of Beasts)"},
            "Baldur's Gate: Descent into Avernus": {"slug": "DTCOE", "display": "Baldur's Gate: Descent into Avernus (referenced)"},
            "hotdq manual": {"slug": "HotDQ", "display": "HotDQ Manual — Hoard of the Dragon Queen"},
            "vgm manual": {"slug": "VGM", "display": "VGM Manual — Volo's Guide to Monsters"},
            "GGR p.": {"slug": "GGR", "display": "Guildmasters' Guide to Ravnica"},
            "tomb of the nine gods": {"slug": "ToA", "display": "Tomb of Annihilation (Tomb of the Nine Gods)"},
            "the lands of the river": {"slug": "RVR", "display": "Rivendell Region Guide (The Lands of the River)"},
            "evils of the north": {"slug": "ERIA", "display": "Eriador Adventures (Evils of the North)"},
            "midgard worldbook": {"slug": "MOM", "display": "Marauders of the Margreve (Midgard Worldbook)"},
            "new creatures and magic items": {"slug": "DPM1", "display": "Deep Magic: Ley Lines (New Creatures)"},
            "bree-land & around": {"slug": "BLRG", "display": "Bree-land Region Guide"},
            "the ambassador's invitation": {"slug": "CSF", "display": "Courts of the Shadow Fey"},
            "sleeping dragons lie": {"slug": "MOM", "display": "Marauders of the Margreve (Sleeping Dragons Lie)"},
            "a: npc codex": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (A: NPC Codex)"},
            # ── Bulk chapter/appendix references from manual ingestion ──
            "chapter 6 | bestiary": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Chapter 6: Bestiary)"},
            "chapter 6 bestiary": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Chapter 6: Bestiary)"},
            "chapter 6 . bestiary": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Chapter 6: Bestiary)"},
            "chapter 6 | friends and foes": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Friends and Foes)"},
            "chapter 6 | friends & foes": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Friends and Foes)"},
            "chapter 7 i treasure": {"slug": "DMG", "display": "Chapter 7 I Treasure — Dungeon Master's Guide"},
            "chapter 7 | treasure": {"slug": "DMG", "display": "Chapter 7 | Treasure — Dungeon Master's Guide"},
            "chapter 7 / treasure": {"slug": "DMG", "display": "Chapter 7 / Treasure — Dungeon Master's Guide"},
            "chapter 7 i treasure . 224": {"slug": "DMG", "display": "Chapter 7 I Treasure — Dungeon Master's Guide"},
            "chapter 7 | treasure, page": {"slug": "DMG", "display": "Chapter 7 | Treasure — Dungeon Master's Guide"},
            "chapter 3": {"slug": "DMG", "display": "Chapter 3 — Dungeon Master's Guide"},
            "chapter 3 | spells": {"slug": "PHB", "display": "Chapter 3 | Spells — Player's Handbook"},
            "chapter 3 magical miscellany": {"slug": "DTCOE", "display": "Chapter 3 Magical Miscellany — Tasha's Cauldron of Everything"},
            "chapter 3 | magical miscellany": {"slug": "DTCOE", "display": "Chapter 3 | Magical Miscellany — Tasha's Cauldron of Everything"},
            "chapter 3 | magical miscellany, page": {"slug": "DTCOE", "display": "Chapter 3 | Magical Miscellany — Tasha's Cauldron of Everything"},
            "chapter 4 | dungeon master's tools": {"slug": "DMG", "display": "Chapter 4 | Dungeon Master's Tools — Dungeon Master's Guide"},
            "chapter 4: creating adventures": {"slug": "DMG", "display": "Chapter 4: Creating Adventures — Dungeon Master's Guide"},
            "chapter 2 | dungeon master's tools": {"slug": "XGE", "display": "Xanathar's Guide to Everything — Chapter 2: Dungeon Master's Tools"},
            "chapter 2 dungeon master's tools": {"slug": "XGE", "display": "Xanathar's Guide to Everything — Chapter 2: Dungeon Master's Tools"},
            "dungeon master's tools p.?": {"slug": "XGE", "display": "Xanathar's Guide to Everything — Dungeon Master's Tools"},
            "chapter 2, the land of chult": {"slug": "ToA", "display": "Chapter 2, The Land of Chult — Tomb of Annihilation"},
            "chapter 6, hell of a summer": {"slug": "BGDIA", "display": "Chapter 6, Hell of a Summer — Baldur's Gate: Descent into Avernus"},
            "magic items and trickery": {"slug": "DMG", "display": "Magic Items and Trickery — Dungeon Master's Guide"},
            "wondrous, legendary and healing items (page 139)": {"slug": "DMG", "display": "Wondrous, Legendary and Healing Items — Dungeon Master's Guide"},
            "baubles of the darkened druids": {"slug": "MOM", "display": "Baubles of the Darkened Druids — Marauders of the Margreve"},
            "tome of beasts": {"slug": "CC", "display": "Tome of Beasts — Creature Codex"},
            "the night messengers": {"slug": "CSF", "display": "The Night Messengers — Courts of the Shadow Fey"},
            "realms beyond the courts": {"slug": "CSF", "display": "Realms Beyond the Courts — Courts of the Shadow Fey"},
            "appendix a | magic items": {"slug": "DMG", "display": "Appendix A | Magic Items — Dungeon Master's Guide"},
            "appendix b | magic items": {"slug": "DMG", "display": "Appendix B | Magic Items — Dungeon Master's Guide"},
            "appendix c: magic items": {"slug": "DMG", "display": "Appendix C: Magic Items — Dungeon Master's Guide"},
            "appendix a: magic items": {"slug": "DMG", "display": "Appendix A: Magic Items — Dungeon Master's Guide"},
            "appendix b: magic items": {"slug": "DMG", "display": "Appendix B: Magic Items — Dungeon Master's Guide"},
            "phandalin": {"slug": "LMoP", "display": "Phandalin — Lost Mine of Phandelver"},
            "wave echo cave": {"slug": "LMoP", "display": "Wave Echo Cave — Lost Mine of Phandelver"},
            "appendix d monsters and npcs": {"slug": "ToA", "display": "Appendix D Monsters and NPCs — Tomb of Annihilation"},
            "appendix d": {"slug": "ToA", "display": "Appendix D — Tomb of Annihilation"},
            "appendix d p.": {"slug": "ToA", "display": "Appendix D — Tomb of Annihilation"},
            "appendix d | monsters and npcs": {"slug": "ToA", "display": "Appendix D | Monsters and NPCs — Tomb of Annihilation"},
            "appendix d: monsters and npcs": {"slug": "ToA", "display": "Appendix D: Monsters and NPCs — Tomb of Annihilation"},
            "appendix a | monsters & npcs": {"slug": "MTF", "display": "Appendix A | Monsters & NPCs — Mordenkainen's Tome of Foes"},
            "appendix a: monsters": {"slug": "HotDQ", "display": "Hoard of the Dragon Queen (Appendix A: Monsters)"},
            "appendix a: assorted beasts": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Appendix A: Assorted Beasts)"},
            "appendix b: monsters": {"slug": "HotDQ", "display": "Hoard of the Dragon Queen (Appendix B: Monsters)"},
            "appendix b: nonplayer characters": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix B: NPCs)"},
            "appendix b nonplayer characters": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix B: NPCs)"},
            "appendix b monsters and npcs": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix B: Monsters and NPCs)"},
            "appendix b | monsters and npcs": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix B: Monsters and NPCs)"},
            "appendix b | monsters": {"slug": "HotDQ", "display": "Hoard of the Dragon Queen (Appendix B: Monsters)"},
            "appendix b": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix B)"},
            "appendix 1 | monster & npc statistics": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix 1: Monster & NPC Statistics)"},
            "appendix c": {"slug": "WDH", "display": "Waterdeep: Dragon Heist (Appendix C)"},
            "appendix c | discoveries": {"slug": "ToA", "display": "Tomb of Annihilation (Appendix C: Discoveries)"},
            "appendix c: council scorecard": {"slug": "BGDIA", "display": "Baldur's Gate: Descent into Avernus (Appendix C)"},
            "appendix a: courtiers of the river court": {"slug": "CSF", "display": "Courts of the Shadow Fey (Appendix A: Courtiers)"},
            "appendix: forest monsters": {"slug": "MOM", "display": "Marauders of the Margreve (Appendix: Forest Monsters)"},
            "part 3 spells": {"slug": "PHB", "display": "Player's Handbook (Part 3: Spells)"},
            "chapter 6 | bestiary, page": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Chapter 6: Bestiary)"},
            "chapter 6 bestiary, page": {"slug": "MTF", "display": "Mordenkainen's Tome of Foes (Chapter 6: Bestiary)"},
            "chapter 2 | dungeon master's tools": {"slug": "DMG", "display": "Dungeon Master's Guide — Chapter 2: Dungeon Master's Tools"},
            "chapter 2 dungeon master's tools": {"slug": "DMG", "display": "Dungeon Master's Guide — Chapter 2: Dungeon Master's Tools"},
            "dungeon master's tools p.?": {"slug": "DMG", "display": "Dungeon Master's Guide — Dungeon Master's Tools"},
            "chapter 2, the land of chult": {"slug": "ToA", "display": "Tomb of Annihilation — The Land of Chult"},
            "magic items and trickery": {"slug": "DMG", "display": "Dungeon Master's Guide — Magic Items and Trickery"},
            "baubles of the darkened druids": {"slug": "MOM", "display": "Marauders of the Margreve — Baubles of the Darkened Druids"},
            "wondrous, legendary and healing items (page 139)": {"slug": "DMG", "display": "Dungeon Master's Guide — Wondrous, Legendary and Healing Items"},
            "tome of beasts": {"slug": "CC", "display": "Creature Codex — Tome of Beasts"},
            "the night messengers": {"slug": "CSF", "display": "Courts of the Shadow Fey — The Night Messengers"},
            "appendix b nonplayer characters": {"slug": "WDH", "display": "Appendix B Nonplayer Characters — Waterdeep: Dragon Heist"},
            "appendix b: nonplayer characters": {"slug": "WDH", "display": "Appendix B: Nonplayer Characters — Waterdeep: Dragon Heist"},
            "appendix a: courtiers of the river court": {"slug": "CSF", "display": "Appendix A: Courtiers of the River Court — Courts of the Shadow Fey"},
            "new creatures and magic items": {"slug": "DPM1", "display": "New Creatures and Magic Items — Deep Magic: Ley Lines"},
            "bree-land & around": {"slug": "BLRG", "display": "Bree-land & Around — Bree-land Region Guide"},
            "ggr p.?": {"slug": "GGR", "display": "Guildmasters' Guide to Ravnica (GGR p.?)"},
            # ── Adventure/scenario titles referenced in source fields ──
            "Shadows In the north": {"slug": "EREA", "display": "Erebor Adventures — Shadows In the North"},
            "Shadows Over Tyrn Gorthad": {"slug": "ERIA", "display": "Eriador Adventures — Shadows Over Tyrn Gorthad"},
        }
        for alias, target in _chapter_aliases.items():
            key = alias.upper().replace(" ", "_")
            if key not in _source_slug_cache and target["slug"] in _source_slug_cache:
                _source_slug_cache[key] = {
                    "title": alias,
                    "display": target["display"],
                    "path": _source_slug_cache[target["slug"]]["path"],
                }
    return _source_slug_cache


@app.get("/api/reference/source-map", response_class=JSONResponse)
async def source_map():
    """Return slug→display_name map so the frontend can resolve source strings."""
    return JSONResponse(_get_source_slug_map())


@app.get("/api/reference/open/{slug}")
async def open_manual(slug: str, page: int = 0):
    """Serve a reference manual PDF, optionally jumping to a page.

    Slug is the pdf_map key (e.g. 'PHB', 'DMG', 'XGE').
    Page is the printed page number (not PDF page index).
    """
    slug_map = _get_source_slug_map()
    # Case-insensitive lookup: check as-given, all-upper, all-lower, then scan keys
    info = slug_map.get(slug) or slug_map.get(slug.upper()) or slug_map.get(slug.lower())
    if not info:
        for k in slug_map:
            if k.lower() == slug.lower():
                info = slug_map[k]
                break
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown manual slug: {slug}")

    # Try direct path first, then DnD-Manuals/ subdir
    pdf_path = MANUALS_BASE / info["path"]
    if not pdf_path.exists():
        pdf_path = MANUALS_BASE / "DnD-Manuals" / info["path"]
    if not pdf_path.exists():
        import glob
        candidates = glob.glob(str(MANUALS_BASE / f"**/{info['path']}"), recursive=True)
        if candidates:
            pdf_path = Path(candidates[0])
        else:
            raise HTTPException(status_code=404, detail=f"PDF not found: {info['path']}")

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{slug.upper()}.pdf\"",
        },
    )


# ── Run ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8300, reload=os.environ.get("DND_RELOAD", "1") == "1")

