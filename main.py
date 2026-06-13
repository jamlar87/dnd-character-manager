"""D&D Character Manager — FastAPI webapp with multi-user character tracking."""
from __future__ import annotations

import json
import os
import asyncio
import random
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt
import httpx
from fastapi import FastAPI, Request, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

# ── Config ──────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DND_DATA_DIR", str(HERE / "data")))
DB_PATH = DATA_DIR / "characters.db"
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
SECRET_KEY = os.environ.get("SECRET_KEY", "dnd-dev-secret-change-me")
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

_CE_PATH = Path("/home/james/dnd-campaign-expert")
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
FEATURE_DESCRIPTIONS: dict[str, str] = {}
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

def _load_manual_json(filename: str) -> list[dict]:
    try:
        with open(MANUAL_DATA / filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_manual_data():
    """Merge extracted manual data into runtime structures. Called at startup."""
    global SRD_SPELLS, SRD_MAGIC_ITEMS, RACES, FEATS, BACKGROUNDS, CLASSES, SUBCLASS_FEATURES, LIMITED_USE

    meta = _load_manual_json("_meta.json")
    if not meta or isinstance(meta, list):
        return  # No manual data yet

    print(f"[manual_data] Loading from {meta.get('source_manuals', [])}")

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
    for race in manual_races:
        name = race.get("name", "")
        if not name or name in RACES:
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
                        "recharge": "short" if "short" in trecharge.lower() else "long",
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
                            "recharge": "short" if "short" in strecharge.lower() else "long",
                            "class": "", "per": "fixed"}
        sub_text = f", {len(subrace_names)} subraces" if subrace_names else ""
        print(f"  + Race: {name} ({len(traits)} traits{sub_text})")

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
            _post_desc = _post_child.get("desc", "")
            if _post_desc and _post_name not in RICH_SUBRACE_DESCS:
                RICH_SUBRACE_DESCS[_post_name] = _post_desc
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
    existing_spell_names = {s.get("name", "").lower() for s in SRD_SPELLS}
    for spell in manual_spells:
        if spell.get("name", "").lower() not in existing_spell_names:
            # Normalize classes: strings → {name: ...} dicts matching SRD format
            raw_classes = spell.get("classes", [])
            if raw_classes and isinstance(raw_classes[0], str):
                spell["classes"] = [{"name": c, "index": c.lower().replace(" ", "-")} for c in raw_classes]
            # Normalize school: string → {name: ...} dict
            school = spell.get("school")
            if isinstance(school, str):
                spell["school"] = {"name": school}
            SRD_SPELLS.append(spell)
            existing_spell_names.add(spell["name"].lower())
    if manual_spells:
        print(f"  + Spells: {len(manual_spells)}")

    # ── Magic Items ── append to SRD_MAGIC_ITEMS (ITEM_INDEX auto-picks them up)
    manual_items = _load_manual_json("magic_items.json")
    existing_item_names = {i.get("name", "").lower() for i in SRD_MAGIC_ITEMS}
    for item in manual_items:
        if item.get("name", "").lower() not in existing_item_names:
            # Enrich source from _source_manual + pdf_map
            source = (item.get("source") or "").strip()
            if (not source or "Unknown" in source) and item.get("_source_manual"):
                slug = item["_source_manual"]
                book_info = (meta.get("pdf_map", {}) if isinstance(meta, dict) else {}).get(slug, {})
                if book_info:
                    title = book_info.get("title", slug)
                    title = re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
                    source = title
                else:
                    source = slug
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
                if (not source or "Unknown" in source) and item.get("_source_manual"):
                    slug = item["_source_manual"]
                    book_info = (meta.get("pdf_map", {}) if isinstance(meta, dict) else {}).get(slug, {})
                    if book_info:
                        title = book_info.get("title", slug)
                        title = re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
                        source = title
                rarity = item.get("rarity", "varies")
                ITEM_INDEX[key] = {
                    "name": name,
                    "type": "Magic Item",
                    "description": item.get("description", ""),
                    "cost": "—",
                    "weight": None,
                    "rarity": rarity,
                    "source": _resolve_source(key, source or ""),
                }

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
        elif manual_desc and len(manual_desc) > len(FEATS[key].get("description", "")):
            # Skip OCR-garbled descriptions — never replace clean text with garbage
            _ocr_markers = ["Vou ", "lhal ", "maslered", "lhree", "disadvanlage",
                           "prolicienl", "benelits", "PART I CUSTOMIZ"]
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
        _fpm_path = DATA_DIR / "feat_page_map.json"
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
        MANUAL_TRAPS = manual_traps
        print(f"  + Traps: {len(manual_traps)}")

    print(f"  Manual data loaded: {meta.get('totals', {})}")

# ── Enrich spell sources with page numbers ──
_spell_page_map: dict[str, str] = {}
try:
    _spm_path = DATA_DIR / "spell_page_map.json"
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

def _resolve_item_key(item_name: str):
    """Look up an item in ITEM_INDEX, handling SRD reference format.
    'Flavor Name (SRD: Reference)' → extracts Reference for the lookup."""
    if not item_name:
        return None
    key = item_name.strip().lower()
    item = ITEM_INDEX.get(key)
    if item:
        return item
    # Try resolving SRD reference
    srd_match = re.search(r'\(srd:\s*(.+?)\)', key)
    if srd_match:
        srd_ref = srd_match.group(1).strip().lower()
        # Normalize: strip special chars for fuzzy matching
        srd_norm = re.sub(r'[—–()\[\]{}]', ' ', srd_ref)
        srd_norm = re.sub(r'\s+', ' ', srd_norm).strip()
        # Try exact match
        item = ITEM_INDEX.get(srd_ref)
        if not item:
            item = ITEM_INDEX.get(srd_norm)
        if not item:
            # Try partial match on normalized ref — prefer best match
            best = None
            best_score = 999
            for k, v in ITEM_INDEX.items():
                k_norm = re.sub(r'[—–()\[\]{}]', ' ', k)
                k_norm = re.sub(r'\s+', ' ', k_norm).strip()
                if srd_norm in k_norm or k_norm in srd_norm:
                    # Score: prefer shorter distance between normalized lengths
                    score = abs(len(k_norm) - len(srd_norm))
                    if score < best_score:
                        best_score = score
                        best = v
            if best:
                return best
        if item:
            return item
    # Try partial match on original key
    for k, v in ITEM_INDEX.items():
        if key in k:
            return v
    # ── Quantity stripping: "Light Crossbow + 20 Bolt" → "Light Crossbow" ──
    qty_stripped = re.sub(r'\s*\+\s*\d+.*', '', key).strip()
    if qty_stripped and qty_stripped != key:
        item = ITEM_INDEX.get(qty_stripped)
        if not item:
            for k, v in ITEM_INDEX.items():
                if qty_stripped in k:
                    item = v
                    break
        if item:
            return item
    # ── Prefix stripping: "Vial of X", "Bag of X" → "X" ──
    for prefix in ("vial of ", "bag of ", "flask of ", "pouch of "):
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            item = ITEM_INDEX.get(suffix)
            if not item:
                for k, v in ITEM_INDEX.items():
                    if suffix in k:
                        item = v
                        break
            if item:
                return item
    # ── Em-dash splitting: "Spell Scroll — Cantrip" → try base + variant ──
    for sep in (" — ", " – ", " – ", " -- "):
        if sep in key:
            parts = key.split(sep, 1)
            base = parts[0].strip()
            variant = parts[1].strip() if len(parts) > 1 else ""
            # Try "base (variant)" key
            paren_key = f"{base} ({variant})"
            item = ITEM_INDEX.get(paren_key)
            if item:
                return item
            # Try just the base name
            item = ITEM_INDEX.get(base)
            if item:
                return item
            # Try variant in parentheses for any key starting with base
            for k, v in ITEM_INDEX.items():
                if k.startswith(base) and variant in k:
                    return v
            # Try partial match for base
            for k, v in ITEM_INDEX.items():
                if base in k:
                    return v
            break
    return None

def _build_item_description(item: dict) -> str:
    """Generate a PHB 2014-accurate description for any equipment item.
    Uses SRD desc if available, otherwise derives from metadata."""
    cat = item.get("equipment_category", {}).get("name", "")
    desc_list = item.get("desc", [])
    if desc_list:
        return " ".join(desc_list)

    # ── Weapons ──
    if cat == "Weapon":
        wcat = item.get("weapon_category", "Simple")
        wrange = item.get("weapon_range", "Melee")
        dmg = item.get("damage", {})
        dmg_dice = dmg.get("damage_dice", "")
        dmg_type = (dmg.get("damage_type", {}) or {}).get("name", "")
        props = [p.get("name", "") for p in item.get("properties", [])]

        # Build range string
        range_part = wrange
        normal = item.get("range", {}).get("normal")
        if normal:
            long_range = item.get("range", {}).get("long", "")
            range_part = f"Range {normal}/{long_range} ft"

        # Build properties string
        prop_strs = []
        for p in props:
            if p == "Versatile":
                vdmg = item.get("two_handed_damage", {}).get("damage_dice", "")
                prop_strs.append(f"versatile ({vdmg})" if vdmg else "versatile")
            elif p == "Thrown":
                thr = item.get("throw_range", {})
                tn = thr.get("normal", "")
                tl = thr.get("long", "")
                prop_strs.append(f"thrown (range {tn}/{tl})" if tn else "thrown")
            elif p == "Ammunition":
                prop_strs.append("ammunition")
            elif p == "Finesse":
                prop_strs.append("finesse")
            elif p == "Heavy":
                prop_strs.append("heavy")
            elif p == "Light":
                prop_strs.append("light")
            elif p == "Loading":
                prop_strs.append("loading")
            elif p == "Reach":
                prop_strs.append("reach")
            elif p == "Two-Handed":
                prop_strs.append("two-handed")
            elif p == "Special":
                prop_strs.append("special")
            elif p == "Monk":
                prop_strs.append("monk")
            else:
                prop_strs.append(p.lower())

        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        parts = [f"{wcat} {wrange.lower()} weapon"]
        if dmg_dice:
            parts.append(f"{dmg_dice} {dmg_type.lower()}" if dmg_type else dmg_dice)
        if prop_strs:
            parts.append(". ".join(prop_strs))
        parts.append(f"{weight} lb. {cost_str}.")
        return " ".join(f"{p}{'.' if i==0 and not p.endswith('.') else ''}" if i > 0 and not p.startswith('.') else p for i, p in enumerate(parts)).replace("..", ".").replace(" .", ".")

    # ── Armor ──
    if cat == "Armor":
        ac = item.get("armor_class", {}).get("base", "?")
        armor_cat = item.get("armor_category", "Armor")
        ac_parts = [str(ac)]
        if item.get("armor_class", {}).get("dex_bonus"):
            dex = item["armor_class"]["dex_bonus"]
            ac_parts.append(f"+ Dex modifier (max {item['armor_class'].get('max_bonus', dex)})")
        ac_str = " + ".join(ac_parts)
        notes = []
        if item.get("str_minimum", 0) > 0:
            notes.append(f"Requires STR {item['str_minimum']}")
        if item.get("stealth_disadvantage"):
            notes.append("disadvantage on Stealth")
        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        desc = f"{armor_cat} armor. AC {ac_str}. "
        if notes:
            desc += ", ".join(notes) + ". "
        desc += f"{weight} lb. {cost_str}."
        return desc

    # ── Tools ──
    if cat == "Tools":
        tname = item.get("name", "")
        tool_descs = {
            "Alchemist's supplies": "Used for alchemical crafting. Proficiency lets you add your bonus to checks made to identify potions, poisons, and alchemical substances. 8 lb. 50 gp.",
            "Brewer's supplies": "Used for brewing beer and ale. Proficiency lets you add your bonus to checks made to identify or craft beverages. 9 lb. 20 gp.",
            "Calligrapher's supplies": "Used for calligraphy, illuminating manuscripts, and detecting forgeries. Proficiency adds your bonus to checks related to writing. 5 lb. 10 gp.",
            "Carpenter's tools": "Used for woodworking and construction. Proficiency lets you add your bonus to checks to build, repair, or identify wooden structures. 6 lb. 8 gp.",
            "Cartographer's tools": "Used for mapping and charting. Proficiency lets you add your bonus to checks to navigate, create maps, or identify landmarks. 6 lb. 15 gp.",
            "Cobbler's tools": "Used for shoemaking and leatherwork for footwear. Proficiency adds your bonus to checks to repair or identify footwear. 5 lb. 5 gp.",
            "Cook's utensils": "Used for preparing meals. Proficiency lets you add your bonus to checks to cook, identify ingredients, or detect spoiled food. 8 lb. 1 gp.",
            "Glassblower's tools": "Used for shaping molten glass. Proficiency adds your bonus to checks to create, identify, or repair glass objects. 5 lb. 30 gp.",
            "Jeweler's tools": "Used for crafting jewelry and identifying gems. Proficiency lets you add your bonus to checks to appraise jewelry or identify gemstones. 2 lb. 25 gp.",
            "Leatherworker's tools": "Used for working with leather and hides. Proficiency adds your bonus to checks to create, repair, or identify leather goods. 5 lb. 5 gp.",
            "Mason's tools": "Used for stonework and construction. Proficiency lets you add your bonus to checks to build, demolish, or identify stone structures. 8 lb. 10 gp.",
            "Painter's supplies": "Used for painting and creating artwork. Proficiency adds your bonus to checks to create, identify, or authenticate paintings. 5 lb. 10 gp.",
            "Potter's tools": "Used for shaping clay and ceramics. Proficiency adds your bonus to checks to create, identify, or repair ceramic objects. 3 lb. 10 gp.",
            "Smith's tools": "Used for metalworking, forging, and repairing metal objects. Proficiency lets you add your bonus to checks to craft, identify, or repair metal items. 8 lb. 20 gp.",
            "Tinker's tools": "Used for tinkering with small mechanical devices. Proficiency lets you add your bonus to checks to repair or create small mechanical objects. 10 lb. 50 gp.",
            "Weaver's tools": "Used for working with cloth, thread, and textiles. Proficiency adds your bonus to checks to create, identify, or repair fabric items. 5 lb. 1 gp.",
            "Woodcarver's tools": "Used for carving wood into small objects. Proficiency adds your bonus to checks to create, identify, or repair wooden crafts. 5 lb. 1 gp.",
            "Navigator's tools": "Used for navigation at sea. Proficiency lets you add your bonus to checks to determine location, avoid getting lost, and chart courses. 2 lb. 25 gp.",
            "Thieves' tools": "Used for picking locks and disarming traps. Proficiency lets you add your bonus to ability checks made to disarm traps or pick locks. 1 lb. 25 gp.",
            "Disguise kit": "Used for creating disguises and costumes. Proficiency lets you add your bonus to checks made to create a visual disguise. 3 lb. 25 gp.",
            "Forgery kit": "Used for forging documents. Proficiency lets you add your bonus to checks made to create a physical counterfeit of a document. 5 lb. 15 gp.",
            "Herbalism kit": "Used for creating herbal remedies, antitoxins, and potions of healing. Proficiency lets you add your bonus to checks made to identify or apply herbs. 3 lb. 5 gp.",
            "Poisoner's kit": "Used for creating and applying poisons. Proficiency lets you add your bonus to checks made to create, identify, or handle poisons. 2 lb. 50 gp.",
            "Dice set": "A set of dice for games of chance. Proficiency lets you add your bonus to checks made to play that game. 0 lb. 1 sp.",
            "Dragonchess set": "A complex strategy board game popular among nobles. Proficiency lets you add your bonus to checks made to play Dragonchess. 0.5 lb. 1 gp.",
            "Playing card set": "A deck of cards for gambling and games. Proficiency lets you add your bonus to checks made to play card games. 0 lb. 5 sp.",
            "Three-Dragon Ante set": "A betting card game themed around dragons. Proficiency lets you add your bonus to checks to play Three-Dragon Ante. 0 lb. 1 gp.",
            "Bagpipes": "A musical wind instrument using enclosed reeds. Proficiency lets you add your bonus to Performance checks with this instrument. 6 lb. 30 gp.",
            "Drum": "A percussion instrument. Proficiency lets you add bonus to Performance checks. 3 lb. 6 gp.",
            "Dulcimer": "A stringed instrument played with hammers. Proficiency adds bonus to Performance checks. 10 lb. 25 gp.",
            "Flute": "A woodwind instrument. Proficiency adds bonus to Performance checks. 1 lb. 2 gp.",
            "Lute": "A stringed instrument similar to a guitar. Proficiency adds bonus to Performance checks. 2 lb. 35 gp.",
            "Lyre": "A small harp-like stringed instrument. Proficiency adds bonus to Performance checks. 2 lb. 30 gp.",
            "Horn": "A brass wind instrument. Proficiency adds bonus to Performance checks. 2 lb. 3 gp.",
            "Pan flute": "A set of graduated pipes. Proficiency adds bonus to Performance checks. 2 lb. 12 gp.",
            "Shawm": "A woodwind instrument (double reed, precursor to oboe). Proficiency adds bonus to Performance checks. 1 lb. 2 gp.",
            "Viol": "A bowed string instrument. Proficiency adds bonus to Performance checks. 1 lb. 30 gp.",
        }
        if tname in tool_descs:
            return tool_descs[tname]
        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        return f"A set of tools for {tname.lower()}. {weight} lb. {cost_str}."

    # ── Adventuring Gear ──
    gear_descriptions = {
        "Abacus": "A portable calculating device — a wooden frame with beads on rods. Used for arithmetic and accounting. 2 gp. 2 lb.",
        "Acid (vial)": "As an action, splash the contents of this vial onto a creature within 5 feet, or throw the vial up to 20 feet, shattering it on impact. On a hit, the target takes 2d6 acid damage. 25 gp. 1 lb.",
        "Alchemist's fire (flask)": "A sticky, adhesive fluid that ignites when exposed to air. As an action, throw this flask up to 20 feet, shattering on impact. Target takes 1d4 fire damage at the start of each of its turns until it uses an action to extinguish the flames. 50 gp. 1 lb.",
        "Antitoxin (vial)": "Drink this vial to gain advantage on saving throws against poison for 1 hour. 50 gp.",
        "Arcane focus — Crystal": "An arcane focus is a special item designed to channel the power of arcane spells. A sorcerer, warlock, or wizard can use such an item as a spellcasting focus. 10 gp. 1 lb.",
        "Arcane focus — Orb": "An arcane focus designed to channel the power of arcane spells. 20 gp. 3 lb.",
        "Arcane focus — Rod": "An arcane focus designed to channel arcane spells. 10 gp. 2 lb.",
        "Arcane focus — Staff": "An arcane focus designed to channel arcane spells (also counts as a quarterstaff). 5 gp. 4 lb.",
        "Arcane focus — Wand": "An arcane focus designed to channel arcane spells. 10 gp. 1 lb.",
        "Arrows (20)": "Ammunition for shortbows and longbows. 1 gp. 1 lb. per 20.",
        "Blowgun needles (50)": "Ammunition for blowguns. 1 gp. 1 lb. per 50.",
        "Crossbow bolts (20)": "Ammunition for crossbows. 1 gp. 1.5 lb. per 20.",
        "Sling bullets (20)": "Ammunition for slings. 4 cp. 1.5 lb. per 20.",
        "Backpack": "A sturdy leather backpack that can hold up to 30 pounds of gear. 2 gp. 5 lb.",
        "Ball bearings (bag of 1,000)": "As an action, spill these tiny metal balls to cover a 10-foot square area. Any creature moving across the area must succeed on a DC 10 Dexterity saving throw or fall prone. 1 gp. 2 lb.",
        "Barrel": "A wooden barrel that can hold 40 gallons of liquid or 4 cubic feet of solid goods. 2 gp. 70 lb.",
        "Basket": "A woven container for carrying goods. 4 sp. 2 lb.",
        "Bedroll": "A cloth bedroll and blanket for sleeping. 1 gp. 7 lb.",
        "Bell": "A small hand bell that rings clearly. 1 gp.",
        "Blanket": "A warm woolen blanket. 5 sp. 3 lb.",
        "Block and tackle": "A set of pulleys with a cable threaded through them. Lets you hoist up to four times the normal weight you could lift. 1 gp. 5 lb.",
        "Book": "A leather-bound tome containing lore, records, or stories — typically worth 25 gp depending on content. 25 gp. 5 lb.",
        "Bottle, glass": "A glass bottle that holds 1½ pints of liquid. 2 gp. 2 lb.",
        "Bucket": "A wooden or metal bucket holding 3 gallons. 5 cp. 2 lb.",
        "Caltrops (bag of 20)": "As an action, spread a bag of caltrops to cover a 5-foot square. A creature entering the area must succeed on a DC 15 Dexterity saving throw or stop moving and take 1 piercing damage. 1 gp. 2 lb.",
        "Candle": "Provides dim light in a 5-foot radius for 1 hour. 1 cp.",
        "Case, crossbow bolt": "A wooden case that holds up to 20 crossbow bolts. 1 gp. 1 lb.",
        "Case, map or scroll": "A cylindrical leather case that protects maps, scrolls, or documents from water and wear. 1 gp. 1 lb.",
        "Chain (10 feet)": "A 10-foot iron chain. Has 10 hit points and can be burst with a DC 20 Strength check. 5 gp. 10 lb.",
        "Chalk (1 piece)": "A piece of white chalk for marking surfaces. 1 cp.",
        "Chest": "A wooden chest that holds 12 cubic feet or 300 pounds of gear. Has a lock (DC 15 to pick). 5 gp. 25 lb.",
        "Climber's kit": "Includes special pitons, boot tips, gloves, and a harness. Gives you a climbing speed equal to your walking speed for 10 minutes, once per short rest. While using it, you can't fall more than 25 feet. 25 gp. 12 lb.",
        "Clothes, common": "Simple, durable work clothes — tunic, trousers, boots. 5 sp. 3 lb.",
        "Clothes, costume": "An outfit for a specific role or costume. 5 gp. 4 lb.",
        "Clothes, fine": "High-quality fabric and tailoring suitable for nobility. 15 gp. 6 lb.",
        "Clothes, traveler's": "Sturdy clothes designed for long journeys, with reinforced stitching and multiple pockets. 2 gp. 4 lb.",
        "Component pouch": "A small watertight leather belt pouch with compartments to hold all the material components and other special items you need to cast your spells (except for those with a specific cost). 25 gp. 2 lb.",
        "Crowbar": "Using a crowbar grants advantage on Strength checks where leverage can be applied. 2 gp. 5 lb.",
        "Druidic focus — Sprig of mistletoe": "A druidic focus used to channel nature magic. Druids can use it as a spellcasting focus. 1 gp.",
        "Druidic focus — Totem": "A druidic focus incorporating feathers, fur, bones, and teeth from sacred animals. 1 gp.",
        "Druidic focus — Wooden staff": "A druidic focus (also counts as a quarterstaff). 5 gp. 4 lb.",
        "Druidic focus — Yew wand": "A druidic focus carved from yew wood. 10 gp. 1 lb.",
        "Fishing tackle": "Includes a wooden rod, silken line, corkwood bobbers, steel hooks, lead sinkers, velvet lures, and narrow netting. 1 gp. 4 lb.",
        "Flask or tankard": "A metal container that holds 1 pint of liquid. 2 cp. 1 lb.",
        "Grappling hook": "A metal hook attached to a rope. Throw with a DC 10 Strength (Athletics) check to secure it. 2 gp. 4 lb.",
        "Hammer": "A one-handed metal hammer for driving pitons and nails. 1 gp. 3 lb.",
        "Hammer, sledge": "A two-handed heavy hammer for demolition. 2 gp. 10 lb.",
        "Healer's kit": "A leather pouch containing bandages, salves, and splints. Has 10 uses. As an action, expend one use to stabilize a dying creature without needing a Wisdom (Medicine) check. 5 gp. 3 lb.",
        "Holy symbol — Amulet": "A holy symbol representing a deity or pantheon. A cleric or paladin can use it as a spellcasting focus. 5 gp. 1 lb.",
        "Holy symbol — Emblem": "A holy symbol on a shield or tabard. 5 gp.",
        "Holy symbol — Reliquary": "A holy symbol containing a sacred relic. 5 gp. 2 lb.",
        "Holy water (flask)": "As an action, splash holy water onto a creature within 5 feet, or throw the flask up to 20 feet. On a hit against a fiend or undead, the target takes 2d6 radiant damage. A cleric or paladin can create holy water with a 1-hour ritual using 25 gp of powdered silver. 25 gp. 1 lb.",
        "Hourglass": "A sand-filled glass timer for measuring time in 1-hour increments. 25 gp. 1 lb.",
        "Hunting trap": "A saw-toothed steel trap. As an action, set it. A creature stepping on it must succeed on a DC 13 Dexterity saving throw or take 1d4 piercing damage and stop moving. The creature can be freed with a DC 13 Strength check. 5 gp. 25 lb.",
        "Ink (1 ounce bottle)": "Black ink for writing. 10 gp.",
        "Ink pen": "A writing implement, typically a quill. 2 cp.",
        "Jug or pitcher": "A ceramic container holding 1 gallon of liquid. 2 cp. 4 lb.",
        "Ladder (10-foot)": "A 10-foot wooden ladder. 1 sp. 25 lb.",
        "Lamp": "A hooded lantern casting bright light in a 30-foot radius and dim light for an additional 30 feet for 6 hours on a flask of oil. 5 sp. 2 lb.",
        "Lantern, bullseye": "Casts bright light in a 60-foot cone and dim light for an additional 60 feet for 6 hours on a flask of oil. 10 gp. 2 lb.",
        "Lantern, hooded": "Casts bright light in a 30-foot radius and dim light for an additional 30 feet for 6 hours on a flask of oil. Lowering the hood reduces light to dim in a 5-foot radius. 5 gp. 2 lb.",
        "Lock": "A simple lock with a key. DC 15 Dexterity check with thieves' tools to pick. 10 gp. 1 lb.",
        "Magnifying glass": "A lens for inspecting small details. Grants advantage on ability checks to appraise or inspect small or detailed items. Also useful for starting fires in sunlight. 100 gp.",
        "Manacles": "Metal restraints that bind a Small or Medium creature. Escaping requires a DC 20 Dexterity check; breaking them a DC 20 Strength check. 2 gp. 6 lb.",
        "Mess kit": "A tin box containing a cup, simple cutlery, and a plate. 2 sp. 1 lb.",
        "Mirror, steel": "A polished steel mirror for signaling or grooming. 5 gp. 0.5 lb.",
        "Oil (flask)": "A flask of lantern oil. As an action, splash oil on a creature or pour on the ground (covers a 5-foot square). If lit, burns for 2 rounds dealing 5 fire damage per round. 1 sp. 1 lb.",
        "Paper (one sheet)": "A single sheet of parchment or vellum for writing. 2 sp.",
        "Parchment (one sheet)": "A single sheet of animal-skin parchment. 1 sp.",
        "Perfume (vial)": "A vial of fragrant perfume. 5 gp.",
        "Pick, miner's": "A mining pick for breaking stone. 2 gp. 10 lb.",
        "Piton": "A metal spike driven into rock for climbing. Holds up to 500 lb. 5 cp. 0.25 lb.",
        "Poison, basic (vial)": "Apply to a weapon or up to three pieces of ammunition. The poison retains potency for 1 minute. A creature hit must succeed on a DC 10 Constitution saving throw or take 1d4 poison damage. 100 gp.",
        "Pole (10-foot)": "A 10-foot wooden pole. Useful for prodding suspicious objects from a safe distance. 5 cp. 7 lb.",
        "Pot, iron": "An iron cooking pot holding 1 gallon. 2 gp. 10 lb.",
        "Potion of healing": "Drink this potion as an action to regain 2d4+2 hit points. 50 gp. 0.5 lb.",
        "Pouch": "A small leather or cloth pouch that holds 6 pounds or ⅕ cubic foot of gear. 5 sp. 1 lb.",
        "Quiver": "A leather quiver holding up to 20 arrows or bolts. 1 gp. 1 lb.",
        "Ram, portable": "A portable battering ram. Gives you a +4 bonus on Strength checks to break open doors, and advantage if a second character helps. 4 gp. 35 lb.",
        "Rations (1 day)": "Dried and preserved food suitable for travel — jerky, dried fruit, hardtack, and nuts. 5 sp. 2 lb.",
        "Robes": "Floor-length cloth robes worn by clergy, scholars, and wizards. 1 gp. 4 lb.",
        "Rope, hempen (50 feet)": "50 feet of hempen rope. Has 2 hit points and can be burst with a DC 17 Strength check. 1 gp. 10 lb.",
        "Rope, silk (50 feet)": "50 feet of silk rope. Has 2 hit points and can be burst with a DC 17 Strength check. Lighter and stronger than hempen. 10 gp. 5 lb.",
        "Sack": "A cloth sack holding 30 pounds or 1 cubic foot of gear. 1 cp. 0.5 lb.",
        "Scale, merchant's": "A balance scale with weights for measuring goods. 5 gp. 3 lb.",
        "Sealing wax": "A stick of wax for sealing letters and documents. 5 sp.",
        "Shovel": "A digging tool. 2 gp. 5 lb.",
        "Signal whistle": "A shrill whistle audible up to 600 feet away. 5 cp.",
        "Signet ring": "A ring bearing an engraved family or guild seal for stamping wax. 5 gp.",
        "Soap": "A bar of soap for washing. 2 cp.",
        "Spellbook": "A leather-bound tome essential for wizards to record spells. Contains 100 blank pages. 50 gp. 3 lb.",
        "Spikes, iron (10)": "A set of 10 iron spikes used to wedge doors shut or anchor ropes for climbing. 1 gp. 5 lb.",
        "Spyglass": "A telescope that magnifies distant objects to twice their apparent size. 1,000 gp. 1 lb.",
        "Tent, two-person": "A simple canvas tent for two people. 2 gp. 20 lb.",
        "Tinderbox": "A small box containing flint, fire steel, and tinder. Using it to start a fire requires an action. 5 sp. 1 lb.",
        "Torch": "A torch burns for 1 hour, providing bright light in a 20-foot radius and dim light for an additional 20 feet. Make a melee attack with a lit torch to deal 1 fire damage. 1 cp. 1 lb.",
        "Vial": "A small glass vial holding up to 4 ounces of liquid. 1 gp.",
        "Waterskin": "A leather waterskin holding 4 pints of liquid. 2 sp. 5 lb. (full).",
        "Whetstone": "A stone for sharpening blades. 1 cp. 1 lb.",
    }

    name = item.get("name", "")
    if name in gear_descriptions:
        return gear_descriptions[name]

    # Fallback: use gear category + cost/weight
    subcat = (item.get("gear_category") or {}).get("name", "")
    cost = item.get("cost", {})
    cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
    weight = item.get("weight", "?")
    if subcat:
        return f"{subcat}. {weight} lb. {cost_str}."
    return f"{weight} lb. {cost_str}."


def _build_item_type(item: dict) -> str:
    """Get a display type string for an item."""
    cat = item.get("equipment_category", {}).get("name", "")
    if cat == "Weapon":
        wcat = item.get("weapon_category", "Weapon")
        wrange = item.get("weapon_range", "Melee")
        return f"{wcat} {wrange} Weapon"
    if cat == "Armor":
        return item.get("armor_category", cat)
    subcat = (item.get("gear_category") or {}).get("name", "")
    if subcat:
        return subcat
    return cat or "Equipment"


# Build unified item index (equipment + magic items)

# ── Load item→page map for source badges ──
_item_page_map: dict[str, str] = {}
try:
    _ppm_path = DATA_DIR / "item_page_map.json"
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


def _resolve_source(item_key: str, fallback: str) -> str:
    """Return source string with page number from map, or fallback."""
    mapped = _item_page_map.get(item_key.lower())
    if mapped:
        # Don't replace if fallback already has better info (e.g., specific adventure page)
        if "p." in fallback.lower():
            return fallback
        return mapped
    return fallback


ITEM_INDEX: dict[str, dict] = {}
for item in SRD_EQUIPMENT:
    name = item.get("name", "")
    if name:
        key = name.lower()
        cost = item.get("cost", {})
        ITEM_INDEX[key] = {
            "name": name,
            "type": _build_item_type(item),
            "description": _build_item_description(item),
            "cost": f"{cost.get('quantity', '?')} {cost.get('unit', 'gp')}",
            "weight": item.get("weight", None),
            "rarity": "",
            "source": _resolve_source(key, "PHB 2014"),
        }

# ── Firearms, Ammo & Explosives (DMG 2014 p.267-268) ──
_FIREARM_ITEMS = [
    # Renaissance
    {"name":"Pistol","type":"Martial Ranged Weapon (Renaissance)","description":"1d10 piercing — Ammunition (30/90), loading. Loading: you can fire only one piece of ammunition per action, bonus action, or reaction, regardless of your number of attacks.","cost":"250 gp","weight":3,"rarity":"","source":"DMG 2014 p.267"},
    {"name":"Musket","type":"Martial Ranged Weapon (Renaissance)","description":"1d12 piercing — Ammunition (40/120), loading, two-handed. Loading: you can fire only one piece of ammunition per action, bonus action, or reaction, regardless of your number of attacks.","cost":"500 gp","weight":10,"rarity":"","source":"DMG 2014 p.267"},
    {"name":"Bullets (10)","type":"Ammunition (Renaissance)","description":"Ten lead bullets for use with Renaissance firearms (pistol, musket).","cost":"3 gp","weight":2,"rarity":"","source":"DMG 2014 p.267"},
    # Modern
    {"name":"Pistol, automatic","type":"Martial Ranged Weapon (Modern)","description":"2d6 piercing — Ammunition (50/150), reload (15 shots). Reload: after 15 shots, use an action or bonus action to reload the magazine.","cost":"—","weight":3,"rarity":"","source":"DMG 2014 p.267","charges":15,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Revolver","type":"Martial Ranged Weapon (Modern)","description":"2d8 piercing — Ammunition (40/120), reload (6 shots). Reload: after 6 shots, use an action or bonus action to reload all six chambers.","cost":"—","weight":3,"rarity":"","source":"DMG 2014 p.267","charges":6,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Rifle, hunting","type":"Martial Ranged Weapon (Modern)","description":"2d10 piercing — Ammunition (80/240), reload (5 shots), two-handed. Reload: after 5 shots, use an action or bonus action to reload the internal magazine.","cost":"—","weight":8,"rarity":"","source":"DMG 2014 p.267","charges":5,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Rifle, automatic","type":"Martial Ranged Weapon (Modern)","description":"2d8 piercing — Ammunition (80/240), burst fire, reload (30 shots), two-handed. Burst Fire: spend 10 shots to force every creature in a 10-ft cube within range to make a DC 15 DEX save, taking the weapon's damage on a failure (no damage on success). Reload: after 30 shots, use an action or bonus action to reload.","cost":"—","weight":8,"rarity":"","source":"DMG 2014 p.267","charges":30,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Shotgun","type":"Martial Ranged Weapon (Modern)","description":"2d8 piercing — Ammunition (30/90), reload (2 shots), two-handed. Reload: after 2 shots, use an action or bonus action to reload both barrels.","cost":"—","weight":7,"rarity":"","source":"DMG 2014 p.267","charges":2,"charge_recharge":"reload (action or bonus action)"},
    # Futuristic
    {"name":"Laser pistol","type":"Martial Ranged Weapon (Futuristic)","description":"3d6 radiant — Ammunition (40/120), reload (50 shots). Reload: after 50 shots, use an action or bonus action to swap the energy cell. Radiant damage bypasses some resistances.","cost":"—","weight":2,"rarity":"","source":"DMG 2014 p.268","charges":50,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Antimatter rifle","type":"Martial Ranged Weapon (Futuristic)","description":"6d8 necrotic — Ammunition (120/360), reload (2 shots), two-handed. Reload: after 2 shots, use an action or bonus action to swap the energy cell. Necrotic damage withers flesh and ignores some defenses. Highest single-shot damage of any weapon.","cost":"—","weight":10,"rarity":"","source":"DMG 2014 p.268","charges":2,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Laser rifle","type":"Martial Ranged Weapon (Futuristic)","description":"3d8 radiant — Ammunition (100/300), reload (30 shots), two-handed. Reload: after 30 shots, use an action or bonus action to swap the energy cell. Radiant damage bypasses some resistances.","cost":"—","weight":7,"rarity":"","source":"DMG 2014 p.268","charges":30,"charge_recharge":"reload (action or bonus action)"},
    {"name":"Energy cell","type":"Ammunition (Futuristic)","description":"A power cell for futuristic firearms (laser pistol, antimatter rifle, laser rifle).","cost":"—","weight":0.3,"rarity":"","source":"DMG 2014 p.268"},
    # Explosives
    {"name":"Bomb","type":"Explosive","description":"As an action, light and throw up to 60 ft. Explodes at the start of your next turn. DC 12 DEX save; 3d6 fire damage on failure, half on success.","cost":"150 gp","weight":1,"rarity":"","source":"DMG 2014 p.267"},
    {"name":"Gunpowder, powder horn","type":"Explosive","description":"A water-resistant horn of gunpowder. Set fire to cause 3d6 fire damage in 10 ft (DC 12 DEX half). One ounce flares for 1 round.","cost":"35 gp","weight":2,"rarity":"","source":"DMG 2014 p.267"},
    {"name":"Gunpowder, keg","type":"Explosive","description":"A small wooden keg of gunpowder. Set fire to cause 7d6 fire damage in 10 ft (DC 12 DEX half).","cost":"250 gp","weight":20,"rarity":"","source":"DMG 2014 p.267"},
    {"name":"Dynamite (stick)","type":"Explosive","description":"As an action, light and throw up to 60 ft. Explodes at the start of your next turn. DC 12 DEX save; 3d6 bludgeoning damage in 5 ft, half on success.","cost":"—","weight":1,"rarity":"","source":"DMG 2014 p.267"},
    {"name":"Grenade, fragmentation","type":"Explosive","description":"As an action, throw up to 60 ft. DC 15 DEX save; 5d6 piercing damage in 20-ft radius, half on success.","cost":"—","weight":1,"rarity":"","source":"DMG 2014 p.268"},
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
def _item_rarity_for_level(level: int) -> list[str]:
    if level <= 4:   return []           # No magic items
    if level <= 10:  return ["uncommon"]
    if level <= 16:  return ["rare", "uncommon"]
    return ["very rare", "rare", "uncommon"]

# bcrypt 4.x native — avoid passlib (broken on Python 3.13)
BCRYPT_MAX = 72  # bcrypt's byte limit; truncate to be safe

def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:BCRYPT_MAX], bcrypt.gensalt()).decode()

def _verify(password: str, hash_: str) -> bool:
    return bcrypt.checkpw(password.encode()[:BCRYPT_MAX], hash_.encode())

_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES)))

# ── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="D&D Character Manager")

# ── DB ──────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL,
            subrace TEXT DEFAULT '',
            class_name TEXT NOT NULL,
            subclass TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            background TEXT DEFAULT '',
            alignment TEXT DEFAULT '',
            personality TEXT DEFAULT '',
            backstory TEXT DEFAULT '',
            strength INTEGER DEFAULT 10,
            dexterity INTEGER DEFAULT 10,
            constitution INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            wisdom INTEGER DEFAULT 10,
            charisma INTEGER DEFAULT 10,
            hp_max INTEGER DEFAULT 10,
            hp_current INTEGER DEFAULT 10,
            temp_hp INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            speed INTEGER DEFAULT 30,
            proficiency_bonus INTEGER DEFAULT 2,
            hit_dice TEXT DEFAULT '1d8',
            hit_dice_used INTEGER DEFAULT 0,
            class_levels TEXT DEFAULT '{}',
            death_saves_success INTEGER DEFAULT 0,
            death_saves_fail INTEGER DEFAULT 0,
            skills TEXT DEFAULT '[]',
            tool_proficiencies TEXT DEFAULT '[]',
            weapon_proficiencies TEXT DEFAULT '[]',
            armor_proficiencies TEXT DEFAULT '[]',
            languages TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            inventory TEXT DEFAULT '[]',
            equipped TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            cp INTEGER DEFAULT 0,
            gp INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS character_spells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            spell_name TEXT NOT NULL,
            spell_level INTEGER DEFAULT 0,
            prepared INTEGER DEFAULT 0,
            slots_max INTEGER DEFAULT 0,
            slots_used INTEGER DEFAULT 0,
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL DEFAULT 'Human',
            class_name TEXT DEFAULT '',
            subclass TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            is_enemy INTEGER DEFAULT 0,
            is_party_npc INTEGER DEFAULT 0,
            strength INTEGER DEFAULT 10,
            dexterity INTEGER DEFAULT 10,
            constitution INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            wisdom INTEGER DEFAULT 10,
            charisma INTEGER DEFAULT 10,
            hp_max INTEGER DEFAULT 10,
            hp_current INTEGER DEFAULT 10,
            temp_hp INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            speed INTEGER DEFAULT 30,
            proficiency_bonus INTEGER DEFAULT 2,
            hit_dice TEXT DEFAULT '1d8',
            hit_dice_used INTEGER DEFAULT 0,
            skills TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            inventory TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            portrait_url TEXT DEFAULT '',
            alignment TEXT DEFAULT 'True Neutral',
            role TEXT DEFAULT 'NPC',
            faction TEXT DEFAULT '',
            xp_reward INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            source_book TEXT DEFAULT '',
            source_page TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            environment TEXT DEFAULT '',
            difficulty TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'planned',
            xp_total INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_encounter_npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_id INTEGER NOT NULL,
            npc_id INTEGER NOT NULL,
            initiative INTEGER DEFAULT 0,
            hp_current INTEGER DEFAULT 0,
            hp_max INTEGER DEFAULT 0,
            ac INTEGER DEFAULT 10,
            defeated INTEGER DEFAULT 0,
            spell_slots_used TEXT DEFAULT '{}',
            notes TEXT DEFAULT '',
            FOREIGN KEY (encounter_id) REFERENCES dm_encounters(id) ON DELETE CASCADE,
            FOREIGN KEY (npc_id) REFERENCES dm_npcs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            party_level INTEGER DEFAULT 1,
            party_size INTEGER DEFAULT 4,
            notes TEXT DEFAULT '',
            session_notes TEXT DEFAULT '',
            quests TEXT DEFAULT '[]',
            locations TEXT DEFAULT '[]',
            characters TEXT DEFAULT '[]',
            npcs TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_campaign_characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            notes TEXT DEFAULT '',
            FOREIGN KEY (campaign_id) REFERENCES dm_campaigns(id) ON DELETE CASCADE,
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS campaign_team_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qty INTEGER DEFAULT 1,
            description TEXT DEFAULT '',
            gp_value INTEGER DEFAULT 0,
            added_by_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (campaign_id) REFERENCES dm_campaigns(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dm_custom_traps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'mechanical',
            danger TEXT NOT NULL DEFAULT 'dangerous',
            trigger TEXT DEFAULT '',
            detection_dc INTEGER,
            detection_skill TEXT DEFAULT 'Perception',
            detection_detail TEXT DEFAULT '',
            disarm_dc INTEGER,
            disarm_method TEXT DEFAULT '',
            disarm_detail TEXT DEFAULT '',
            effect TEXT DEFAULT '',
            save_dc INTEGER,
            save_ability TEXT DEFAULT 'Dexterity',
            damage TEXT DEFAULT '',
            damage_type TEXT DEFAULT '',
            area TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    # Migration: add personality/backstory columns if missing
    try:
        db.execute("ALTER TABLE characters ADD COLUMN personality TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE characters ADD COLUMN backstory TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Migration: build data columns
    for col, coltype in [("feature_data","TEXT DEFAULT '[]'"),
                          ("attacks_data","TEXT DEFAULT '[]'"),
                          ("spell_slot_data","TEXT DEFAULT '{}'"),
                          ("passive_perception","INTEGER DEFAULT 10"),
                          ("inspiration","INTEGER DEFAULT 0"),
                          ("exhaustion","INTEGER DEFAULT 0"),
                          ("portrait_url","TEXT DEFAULT ''"),
                          ("portrait_prompt","TEXT DEFAULT ''"),
                          ("save_proficiencies","TEXT DEFAULT '[]'"),
                          ("damage_resistances","TEXT DEFAULT '[]'"),
                          ("damage_immunities","TEXT DEFAULT '[]'"),
                          ("damage_vulnerabilities","TEXT DEFAULT '[]'"),
                          ("condition_immunities","TEXT DEFAULT '[]'"),
                          ("background_data","TEXT DEFAULT ''"),
                          ("spell_slots_used","TEXT DEFAULT '{}'")]:
        try:
            db.execute(f"ALTER TABLE characters ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Migration: dm_encounter_npcs new columns
    for col, coltype in [("defeated", "INTEGER DEFAULT 0"),
                          ("spell_slots_used", "TEXT DEFAULT '{}'"),
                          ("creature_data", "TEXT DEFAULT ''")]:
        try:
            db.execute(f"ALTER TABLE dm_encounter_npcs ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    # Migration: dm_encounters combat_state
    try:
        db.execute("ALTER TABLE dm_encounters ADD COLUMN combat_state TEXT DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass
    # Migration: dm_campaigns characters column
    try:
        db.execute("ALTER TABLE dm_campaigns ADD COLUMN characters TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: dm_campaigns npcs column
    try:
        db.execute("ALTER TABLE dm_campaigns ADD COLUMN npcs TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: class_levels for multiclass support
    try:
        db.execute("ALTER TABLE characters ADD COLUMN class_levels TEXT DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass
    # Migration: cp tracker for copper pieces
    try:
        db.execute("ALTER TABLE characters ADD COLUMN cp INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Migration: gp tracker for gold pieces
    try:
        db.execute("ALTER TABLE characters ADD COLUMN gp INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Migration: attuned_items for item attunement tracking
    try:
        db.execute("ALTER TABLE characters ADD COLUMN attuned_items TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass
    # Migration: dragonborn_ancestry for Dragonborn draconic ancestry choice
    try:
        db.execute("ALTER TABLE characters ADD COLUMN dragonborn_ancestry TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Backfill: populate class_levels from class_name + level for existing characters
    db.execute("UPDATE characters SET class_levels = json_object(class_name, level) WHERE class_levels = '{}' OR class_levels IS NULL OR class_levels = ''")
    # Migration: character_relationships for History & Relationships tab
    db.execute("""
        CREATE TABLE IF NOT EXISTS character_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            relationship_type TEXT DEFAULT 'ally',
            description TEXT DEFAULT '',
            prompt TEXT DEFAULT '',
            npc_data TEXT DEFAULT '{}',
            ai_generated INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    db.commit()
    db.close()

    # ── Migration: add source columns to dm_npcs (manual ingestion) ──
    _migrate_npc_source_columns()


def _migrate_npc_source_columns():
    """Add source tracking columns to dm_npcs if they don't exist."""
    db = get_db()
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(dm_npcs)")}
        for col, col_type in [("source", "TEXT DEFAULT ''"),
                              ("source_book", "TEXT DEFAULT ''"),
                              ("source_page", "TEXT DEFAULT ''")]:
            if col not in cols:
                db.execute(f"ALTER TABLE dm_npcs ADD COLUMN {col} {col_type}")
                print(f"[migration] Added dm_npcs.{col}")
        db.commit()
    except Exception as e:
        print(f"[migration] dm_npcs source columns: {e}")
    finally:
        db.close()

    # Migration: is_admin column on users
    db = get_db()
    try:
        db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Migration: combat_notes on characters
    try:
        db.execute("ALTER TABLE characters ADD COLUMN combat_notes TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Seed ADMIN account if not exists
    admin_row = db.execute("SELECT id FROM users WHERE email = 'admin'").fetchone()
    if not admin_row:
        db.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
            ("admin", _hash("admin"))
        )
        db.commit()
        print("[init] ADMIN account created (admin / admin)")
    db.close()

def _get_user(email: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    db.close()
    return dict(row) if row else None

def _create_session(user_id: int) -> str:
    import secrets
    token = secrets.token_hex(32)
    db = get_db()
    db.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (user_id, token))
    db.commit()
    db.close()
    return token

def _get_user_by_token(token: str) -> dict | None:
    db = get_db()
    row = db.execute("""
        SELECT u.* FROM users u
        JOIN sessions s ON s.user_id = u.id
        WHERE s.token = ?
    """, (token,)).fetchone()
    db.close()
    return dict(row) if row else None

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
    return bool(user.get("is_admin"))

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
RACES = {
    "Dwarf": {"subraces": ["Hill Dwarf", "Mountain Dwarf", "Duergar", "Gold Dwarf"], "asi": {"constitution": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Dwarvish"], "traits": ["Dwarven Resilience", "Stonecunning"], "desc": "Bold and hardy, dwarves are known as skilled warriors, miners, and workers of stone and metal. Standing 4 to 5 feet tall, they are broad and compact, weighing around 150 pounds. Their skin ranges from deep tan to light brown, and their hair—worn long—ranges from black to red, graying to white with age. Dwarves can live to be over 400 years old.\n\nDwarven culture is built on three pillars: clan, craft, and honor. They inhabit great stone halls carved deep into mountains, forging legendary weapons and armor. Slow to trust but fiercely loyal once earned, dwarves hold grudges for centuries and friendships for millennia. Their kingdoms are ordered by ancient tradition, with kings and queens descended from the first dwarves.\n\nMechanically, dwarves gain +2 Constitution, Darkvision 60 ft, Dwarven Resilience (advantage on poison saves + poison resistance), and Stonecunning (expertise on History checks related to stonework). Even heavily armored dwarves maintain full speed.", "subrace_descs": {"Hill Dwarf": "+1 Wisdom. Dwarven Toughness grants +1 HP per level. Hill dwarves are the heartiest of their kind, with keen senses and a deeper connection to the living earth. They are the most common dwarven merchants, farmers, and diplomats.", "Mountain Dwarf": "+2 Strength. Dwarven Armor Training grants proficiency with light and medium armor. Mountain dwarves are the martial backbone of dwarven society— soldiers, smiths, and sentinels who guard the deep roads. Hardy and strong, they thrive in the most forbidding peaks.", "Duergar": "+1 Strength. Superior Darkvision (120 ft), Duergar Resilience (advantage on saves vs illusions, charms, and paralysis), Duergar Magic (enlarge/reduce and invisibility at levels 3 and 5), Sunlight Sensitivity. The gray dwarves of the Underdark—grim, psionic, and hardened by eons beneath the earth.", "Gold Dwarf": "+1 Wisdom. Dwarven Toughness (+1 HP per level). The southern dwarves of the Great Rift in Faerûn—confident, shrewd merchants with golden-brown skin and a proud, unbroken lineage. They are resistant to the scheming of other races but generous to those who earn their trust."}, "source": "PHB 2014 p.18"},
    "Elf": {"subraces": ["High Elf", "Wood Elf", "Dark Elf (Drow)", "Sea Elf", "Eladrin", "Shadar-kai"], "asi": {"dexterity": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Keen Senses", "Fey Ancestry", "Trance"], "desc": "Elves are a magical people of otherworldly grace, living in the world but not entirely of it. They are slender, standing 5 to 6 feet tall, with delicate features and pointed ears. Their skin ranges from pale alabaster to deep brown, and their eyes shine with colors not seen in humans—gold, silver, violet, deep green. Elves live up to 750 years, and their perspective is shaped by this long view of history.\n\nElven culture values freedom, beauty, and artistic expression above all. They build elegant spires that blend with the forest canopy or rise from ancient woods. Music, poetry, and bladecraft are all refined to an art form. Elves love nature and magic, and their trance—a four-hour meditative state that replaces sleep—allows them to relive memories and reflect on their long lives.\n\nMechanically, elves gain +2 Dexterity, Darkvision 60 ft, Keen Senses (proficiency in Perception), Fey Ancestry (advantage on saves vs charms, immune to magical sleep), and Trance (4-hour meditation replaces 8-hour sleep). They are fluent in Common and Elvish.", "subrace_descs": {"High Elf": "+1 Intelligence. Gain a wizard cantrip and one extra language. High elves are the most magically gifted— scholars, wizards, and keepers of elven high culture. Their kingdoms are bastions of arcane learning.", "Wood Elf": "+1 Wisdom. Fleet of Foot (+5 ft speed) and Mask of the Wild (hide in light natural obscurement). Wood elves are reclusive guardians of deep forests—swift, perceptive, and deadly with a bow.", "Dark Elf (Drow)": "+1 Charisma. Superior Darkvision (120 ft), Sunlight Sensitivity, and Drow Magic (dancing lights at will; faerie fire and darkness at levels 3 and 5). The drow are a dark-skinned, white-haired subrace of the Underdark, living in a matriarchal society devoted to the spider goddess Lolth.", "Sea Elf": "+1 Constitution. Swim speed 30 ft, amphibious (breathe air and water), Sea Elf Training (proficiency with spear, trident, light crossbow, and net). Sea elves fell in love with the ocean in the earliest days and now live in hidden shallows and the Elemental Plane of Water.", "Eladrin": "+1 Intelligence. Fey Step (misty step 1/short rest). Eladrin are elves of the Feywild, their appearance and personality shifting with the seasons—spring (joyful, green), summer (fierce, golden), autumn (generous, russet), winter (contemplative, pale blue).", "Shadar-kai": "+1 Constitution. Necrotic resistance, Blessing of the Raven Queen (teleport 30 ft 1/long rest; at level 3+, gain resistance to all damage for 1 round after teleporting). Shadar-kai serve the Raven Queen in the Shadowfell, their souls bound to her eternal duty."}, "source": "PHB 2014 p.21"},
    "Halfling": {"subraces": ["Lightfoot Halfling", "Stout Halfling", "Ghostwise Halfling"], "asi": {"dexterity": 2}, "speed": 25, "darkvision": 0, "languages": ["Common", "Halfling"], "traits": ["Lucky", "Brave", "Halfling Nimbleness"], "desc": "Halflings are small, cheerful folk who stand about 3 feet tall and weigh around 40 pounds. With round faces, rosy cheeks, and curly hair, they project an aura of comfort and contentment. They favor simple, colorful clothing and go barefoot whenever possible. Halflings live about 150 years, and their outlook is practical and grounded—they value home, hearth, and a well-told story over grand ambitions.\n\nHalfling communities are pastoral and peaceful, built around farms, mills, and cozy burrows. They dislike pomp and ceremony, preferring to govern by family consensus and the quiet wisdom of elders. Despite their peaceful nature, halflings are surprisingly brave when their homes or friends are threatened—a bravery born of loyalty, not recklessness.\n\nMechanically, halflings gain +2 Dexterity, Lucky (reroll 1s on attack rolls, ability checks, and saves), Brave (advantage on saves vs frightened), and Halfling Nimbleness (move through spaces of larger creatures). They speak Common and Halfling.", "subrace_descs": {"Lightfoot Halfling": "+1 Charisma. Naturally Stealthy lets you hide behind creatures larger than you. Lightfoot halflings are charming, gregarious travelers who love meeting new people and can slip away from trouble unnoticed.", "Stout Halfling": "+1 Constitution. Stout Resilience grants advantage on poison saves and resistance to poison damage. Known as Strongheart halflings in the Forgotten Realms, they have dwarven blood in their lineage— hardy, durable, and fond of good ale.", "Ghostwise Halfling": "+1 Wisdom. Silent Speech grants telepathy to any creature within 30 ft that shares a language. Ghostwise halflings are fiercely reclusive, living deep in the Chondalwood and speaking mind-to-mind rather than aloud. They have a deep, spiritual bond with the natural world."}, "source": "PHB 2014 p.26"},
    "Human": {"subraces": ["Variant Human"], "asi": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common"], "traits": [], "desc": "Humans are the youngest of the common races and the most ambitious. Standing 5 to 6 feet tall with skin tones ranging from nearly black to very pale, hair from black to blond, and facial hair from sparse to thick, humans are the most physically diverse race in the multiverse. They live less than a century, yet their drive and adaptability have spread them to every corner of every world.\n\nHuman culture is as varied as their appearance—no single god, philosophy, or way of life defines them. They build empires and topple them within the span of an elf's youth. This brevity of life fuels an intensity that other races find both admirable and alarming: humans achieve in decades what dwarves take centuries to accomplish.\n\nMechanically, humans gain +1 to all six ability scores—an unparalleled breadth of talent. They start with one extra language. The standard human is the ultimate generalist, capable of excelling in any class.", "subrace_descs": {"Variant Human": "+1 to two different abilities of your choice, one feat, and one extra skill proficiency. The variant human trades the jack-of-all-trades approach for focused specialization, making them the most customizable race in the game—particularly powerful for builds that need an early feat."}, "source": "PHB 2014 p.29"},
    "Dragonborn": {"subraces": [], "asi": {"strength": 2, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common", "Draconic"], "traits": ["Draconic Ancestry", "Breath Weapon", "Damage Resistance"], "desc": "Dragonborn are tall, muscular humanoids with the blood of dragons running through their veins. Standing well over 6 feet tall and weighing 250 pounds or more, they have scaly hide, a draconic snout, sharp claws, and a powerful tail. Their scales mirror the color of their draconic ancestry—brass, bronze, copper, gold, silver, black, blue, green, red, or white. Dragonborn live about 80 years.\n\nDragonborn society revolves around clan and honor above all else. To a dragonborn, one's word is one's bond, and failure to uphold it brings dishonor not just to the individual but to their entire clan. They are proud warriors who approach life with the gravity of a sacred duty. Dragonborn are rare outside their own insular communities, and those who adventure do so to prove their worth or to seek a new destiny for their clan.\n\nMechanically, dragonborn gain +2 Strength, +1 Charisma, a Breath Weapon (2d6 damage in a 15-ft cone or 30-ft line based on ancestry, DC 8 + CON + prof, recharge on short rest), and Damage Resistance matching their draconic ancestry. They speak Common and Draconic.", "source": "PHB 2014 p.32"},
    "Gnome": {"subraces": ["Forest Gnome", "Rock Gnome", "Deep Gnome"], "asi": {"intelligence": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Gnomish"], "traits": ["Gnome Cunning"], "desc": "Gnomes are small, energetic humanoids standing 3 to 4 feet tall and weighing 40 to 45 pounds. Their skin ranges from tan to woody brown, their hair is fair, and their eyes are bright, often blue or violet. Male gnomes favor short, well-trimmed beards. Gnomes live 350 to 500 years—their boundless enthusiasm for life never dims with age.\n\nGnomish culture is defined by curiosity and creativity. They are natural inventors, alchemists, and illusionists, always tinkering with some device or perfecting a new trick. Their communities are hidden burrows in wooded hills, connected by winding tunnels and lit by cleverly engineered mirrors. Gnomes laugh easily, love puzzles, and treat knowledge as the greatest treasure.\n\nMechanically, gnomes gain +2 Intelligence, Darkvision 60 ft, and Gnome Cunning (advantage on all Intelligence, Wisdom, and Charisma saves against magic). They speak Common and Gnomish. This makes gnomes exceptional wizards, artificers, and arcane tricksters.", "subrace_descs": {"Forest Gnome": "+1 Dexterity. Natural Illusionist grants the minor illusion cantrip. Speak with Small Beasts allows simple communication with Tiny and Small animals. Forest gnomes are shy, reclusive tricksters of the deep woods—masters of stealth and woodland magic.", "Rock Gnome": "+1 Constitution. Artificer's Lore doubles proficiency bonus on History checks related to magic items, alchemical objects, or technological devices. Tinker lets you build a tiny clockwork toy, fire starter, or music box. Rock gnomes are the engineers of gnomish society—gadgeteers and jewelers who craft wonders from clockwork and gemstone.", "Deep Gnome": "+1 Dexterity. Superior Darkvision (120 ft), Stone Camouflage (advantage on Stealth in rocky terrain). The svirfneblin—secretive Underdark gnomes with gray skin and a talent for survival in the deepest darkness. They are more serious than their surface cousins, hardened by life among the horrors of the deep earth."}, "source": "PHB 2014 p.35"},
    "Half-Elf": {"subraces": [], "asi": {"charisma": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Fey Ancestry", "Skill Versatility"], "desc": "Half-elves are born of two worlds—human passion and elven grace. They stand 5 to 6 feet tall, with features that blend the best of both parents: the pointed ears and delicate features of elves with the sturdy build and varied coloration of humans. Their eyes are particularly striking, often green or gold. Half-elves live about 180 years.\n\nHalf-elves are natural diplomats, bridging the gap between cultures. They inherit the elven love of art and the human drive for achievement. Many half-elves feel torn between two heritages, never fully belonging to either world—a loneliness that often drives them to the adventuring life, where skill matters more than bloodline. They are easygoing and charismatic, with a gift for making friends wherever they go.\n\nMechanically, half-elves gain +2 Charisma, +1 to two other abilities of their choice, Fey Ancestry (advantage on saves vs charms, immune to magical sleep), Darkvision 60 ft, and Skill Versatility (two extra skill proficiencies). With their unmatched flexibility, they excel as bards, sorcerers, paladins, and warlocks.", "source": "PHB 2014 p.38"},
    "Half-Orc": {"subraces": [], "asi": {"strength": 2, "constitution": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Orc"], "traits": ["Relentless Endurance", "Savage Attacks"], "desc": "Half-orcs are towering figures of strength and endurance, standing 5 to 7 feet tall with powerful builds, gray-green skin, pronounced lower canines, and jutting jaws. They typically weigh 180 to 250 pounds of muscle and bone. Their orcish blood gives them a fearsome appearance, but half-orcs raised among humans often develop remarkable self-control and a deep loyalty to those who accept them.\n\nHalf-orc life is one of constant challenge. Whether in orc tribes where they must prove their strength daily, or in human societies where they must overcome fear and prejudice, half-orcs learn early that respect is earned through deeds, not words. Those who take up the adventuring life do so to find a place where their strength is valued and their loyalty rewarded.\n\nMechanically, half-orcs gain +2 Strength, +1 Constitution, Darkvision 60 ft, Relentless Endurance (when reduced to 0 HP but not killed, drop to 1 HP instead—1/long rest), and Savage Attacks (roll one extra weapon damage die on critical hits). They speak Common and Orc. They make devastating barbarians, fighters, and paladins.", "source": "PHB 2014 p.40"},
    "Custom Lineage": {"subraces": [], "asi": {}, "speed": 30, "darkvision": 0, "languages": ["Common"], "traits": ["Feat", "Variable Trait"], "desc": "Instead of choosing one of the game's races for your character at 1st level, you can use the following traits to represent your character's lineage, giving you full control over how your character's origin shaped them.\n\nCustom lineage is the ultimate blank canvas. Perhaps you're the result of generations of intermarriage between multiple races, a being touched by planar energies, or a unique creation with no precedent. Your appearance, backstory, and very nature are yours to define. You aren't bound by any racial stereotype—you write your own origin story.\n\nMechanically, you gain +2 to one ability score of your choice, one feat of your choice (for which you qualify), and your choice of either darkvision 60 ft or proficiency in one skill. You are Small or Medium size, speak Common and one other language, and your creature type is humanoid. You are whatever you imagine yourself to be.", "subrace_descs": {}},
    "Tiefling": {"subraces": [], "asi": {"charisma": 2, "intelligence": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Infernal"], "traits": ["Hellish Resistance", "Infernal Legacy"], "desc": "Tieflings bear the mark of an ancient infernal pact—a sin of their ancestors that manifests in their bloodline. They have large horns, thick tails, sharply pointed teeth, and solid-colored eyes (black, red, white, silver, or gold). Their skin ranges from human tones through deep reds and purples. They stand 5 to 6 feet tall and live slightly longer than humans. No two tieflings look exactly alike.\n\nTieflings are met with suspicion and prejudice in most societies. Their fiendish appearance triggers instinctive fear in common folk, and they grow up knowing they are outsiders. This breeds either bitter resentment or a fierce independence—tieflings who rise above prejudice often become self-reliant adventurers, proving their worth through heroic deeds. Their natural charisma can be unsettling or magnetic, depending on how they choose to wield it.\n\nMechanically, tieflings gain +2 Charisma, +1 Intelligence, Hellish Resistance (resistance to fire damage), Darkvision 60 ft, and Infernal Legacy—the thaumaturgy cantrip, hellish rebuke (1/day at level 3 as 2nd-level), and darkness (1/day at level 5). They speak Common and Infernal. Tieflings make natural warlocks, sorcerers, and bards.", "source": "PHB 2014 p.42"},
    "Genasi": {"subraces": ["Air Genasi", "Earth Genasi", "Fire Genasi", "Water Genasi"], "asi": {"constitution": 2}, "speed": 30, "darkvision": 0, "languages": ["Common", "Primordial"], "traits": [], "desc": "Genasi are the children of mortals and genies—elemental spirits of air, earth, fire, and water. They carry the power of the Elemental Planes in their blood. Standing 5 to 6 feet tall, they are built like humans but marked by their elemental heritage: skin that glitters with moisture, hair that ripples like flame, a voice that echoes like shifting stone. Genasi live about 120 years.\n\nGenasi are rare and often solitary. Their elemental nature sets them apart from both their mortal and genie parents. They are self-reliant, independent, and tend toward neutrality—reflecting the primal forces within them. A genasi's elemental subrace defines not just their abilities but their entire outlook: air genasi are swift and detached, earth genasi are stoic and patient, fire genasi are passionate and impulsive, water genasi are adaptable and deep.\n\nMechanically, all genasi gain +2 Constitution, and their subrace grants additional traits including innate spellcasting, damage resistances, and movement abilities tied to their element. They speak Common and Primordial—the language of elemental beings.", "subrace_descs": {"Air Genasi": "+1 Dexterity. Unending Breath (hold breath indefinitely), Mingle with the Wind (levitate 1/long rest at level 3+). Air genasi are light of frame and quick of wit, with pale blue skin and hair that perpetually stirs in an unfelt breeze. Children of the djinn.", "Earth Genasi": "+1 Strength. Earth Walk (ignore difficult terrain of earth or stone), Merge with Stone (pass without trace 1/long rest at level 3+). Earth genasi are solid, deliberate, and patient, with skin in shades of gray and brown, sometimes marked with crystalline growths. Children of the dao.", "Fire Genasi": "+1 Intelligence. Darkvision 60 ft, Fire Resistance, Reach to the Blaze (produce flame cantrip; burning hands 1/long rest at level 3+). Fire genasi burn with inner heat—their skin smolders in shades of coal and ash, their hair a corona of flame. Children of the efreet.", "Water Genasi": "+1 Wisdom. Amphibious (breathe water and air), Swim speed 30 ft, Acid Resistance, Call to the Wave (shape water cantrip; create or destroy water 1/long rest at level 3+). Water genasi appear perpetually fresh from a swim, with blue-green skin and hair that floats as if underwater. Children of the marid."}},
}

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
}

# Tag hardcoded races with source
_race_page_map: dict[str, str] = {}
try:
    _rpm_path = DATA_DIR / "race_page_map.json"
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
}
# Attach _subrace_sources to each race (all subraces default to parent source)
for _r_name, _r in RACES.items():
    srcs = {}
    parent_src = _r.get("source", "")
    for _s in _r.get("subraces", []):
        srcs[_s] = SUBRACE_SOURCES.get(_s) or parent_src
    _r["_subrace_sources"] = srcs

# PHB p.17-43 — Racial trait descriptions
RACIAL_TRAIT_DESCS = {
    # Dwarf
    "Dwarven Resilience": "You have advantage on saving throws against poison, and you have resistance against poison damage.",
    "Stonecunning": "Whenever you make an Intelligence (History) check related to the origin of stonework, you are considered proficient in the History skill and add double your proficiency bonus to the check, instead of your normal proficiency bonus.",
    "Dwarven Toughness": "Your hit point maximum increases by 1, and it increases by 1 every time you gain a level.",
    "Dwarven Armor Training": "You have proficiency with light and medium armor.",
    # Elf
    "Keen Senses": "You have proficiency in the Perception skill.",
    "Fey Ancestry": "You have advantage on saving throws against being charmed, and magic can't put you to sleep.",
    "Trance": "Elves don't need to sleep. Instead, they meditate deeply, remaining semiconscious, for 4 hours a day. After resting in this way, you gain the same benefit that a human does from 8 hours of sleep.",
    "Elf Weapon Training": "You have proficiency with the longsword, shortsword, shortbow, and longbow.",
    "Cantrip (High Elf)": "You know one cantrip of your choice from the wizard spell list. Intelligence is your spellcasting ability for it.",
    "Fleet of Foot": "Your base walking speed increases to 35 feet.",
    "Mask of the Wild": "You can attempt to hide even when you are only lightly obscured by foliage, heavy rain, falling snow, mist, and other natural phenomena.",
    "Superior Darkvision": "Your darkvision has a radius of 120 feet.",
    "Sunlight Sensitivity": "You have disadvantage on attack rolls and on Wisdom (Perception) checks that rely on sight when you, the target of your attack, or whatever you are trying to perceive is in direct sunlight.",
    "Drow Magic": "You know the dancing lights cantrip. At 3rd level, you can cast faerie fire once per long rest. At 5th level, you can cast darkness once per long rest. Charisma is your spellcasting ability for these spells.",
    # Halfling
    "Lucky": "When you roll a 1 on the d20 for an attack roll, ability check, or saving throw, you can reroll the die and must use the new roll.",
    "Brave": "You have advantage on saving throws against being frightened.",
    "Halfling Nimbleness": "You can move through the space of any creature that is of a size larger than yours.",
    "Naturally Stealthy": "You can attempt to hide even when you are obscured only by a creature that is at least one size larger than you.",
    "Stout Resilience": "You have advantage on saving throws against poison, and you have resistance against poison damage.",
    # Ghostwise Halfling
    "Silent Speech": "You can speak telepathically to any creature within 30 feet of you. The creature understands you only if the two of you share a language. You can speak telepathically in this way to one creature at a time.",
    # Dragonborn
    "Draconic Ancestry": "You have draconic ancestry. Choose one type of dragon from the Draconic Ancestry table. Your breath weapon and damage resistance are determined by the dragon type.",
    "Breath Weapon": "You can use your action to exhale destructive energy in a 15 ft cone or 5 by 30 ft line (by ancestry). Each creature in the area must make a saving throw (DC = 8 + Con mod + proficiency bonus). A creature takes 2d6 damage on a failed save, half on success. Damage increases to 3d6 at 6th level, 4d6 at 11th, and 5d6 at 16th. Recharges on a short or long rest.",
    "Damage Resistance": "You have resistance to the damage type associated with your draconic ancestry.",
    # Gnome
    "Gnome Cunning": "You have advantage on all Intelligence, Wisdom, and Charisma saving throws against magic.",
    "Natural Illusionist": "You know the minor illusion cantrip. Intelligence is your spellcasting ability for it.",
    "Speak with Small Beasts": "Through sounds and gestures, you can communicate simple ideas with Small or smaller beasts.",
    "Artificer's Lore": "Whenever you make an Intelligence (History) check related to magic items, alchemical objects, or technological devices, you can add twice your proficiency bonus.",
    "Tinker": "You have proficiency with tinker's tools. Using those tools, you can spend 1 hour and 10 gp to construct a Tiny clockwork device (AC 5, 1 hp). The device ceases to function after 24 hours. You can have up to three such devices active at a time.",
    # Deep Gnome
    "Stone Camouflage": "You have advantage on Dexterity (Stealth) checks to hide in rocky terrain.",
    # Air Genasi
    "Unending Breath": "You can hold your breath indefinitely while you're not incapacitated.",
    "Mingle with the Wind": "You can cast the levitate spell once with this trait, requiring no material components, and you regain the ability to cast it this way when you finish a long rest. Constitution is your spellcasting ability for this spell.",
    # Earth Genasi
    "Earth Walk": "You can move across difficult terrain made of earth or stone without expending extra movement.",
    "Merge with Stone": "You can cast the pass without trace spell once with this trait, requiring no material components, and you regain the ability to cast it this way when you finish a long rest. Constitution is your spellcasting ability for this spell.",
    # Fire Genasi
    "Fire Resistance": "You have resistance to fire damage.",
    "Reach to the Blaze": "You know the produce flame cantrip. Starting at 3rd level, you can cast burning hands once with this trait as a 1st-level spell, and you regain the ability to cast it this way when you finish a long rest. Constitution is your spellcasting ability for these spells.",
    # Water Genasi
    "Amphibious": "You can breathe air and water.",
    "Swim": "You have a swimming speed of 30 feet.",
    "Acid Resistance": "You have resistance to acid damage.",
    "Call to the Wave": "You know the shape water cantrip. Starting at 3rd level, you can cast create or destroy water once with this trait as a 2nd-level spell, and you regain the ability to cast it this way when you finish a long rest. Constitution is your spellcasting ability for these spells.",
    # Half-Elf
    "Skill Versatility": "You gain proficiency in two skills of your choice.",
    # Half-Orc
    "Relentless Endurance": "When you are reduced to 0 hit points but not killed outright, you can drop to 1 hit point instead. You can't use this feature again until you finish a long rest.",
    "Savage Attacks": "When you score a critical hit with a melee weapon attack, you can roll one of the weapon's damage dice one additional time and add it to the extra damage of the critical hit.",
    # Tiefling
    "Hellish Resistance": "You have resistance to fire damage.",
    "Infernal Legacy": "You know the thaumaturgy cantrip. At 3rd level, you can cast hellish rebuke as a 2nd-level spell once per long rest. At 5th level, you can cast darkness once per long rest. Charisma is your spellcasting ability for these spells.",
    # Duergar (PHB Dwarf subrace)
    "Superior Darkvision": "Your darkvision has a radius of 120 feet.",
    "Duergar Resilience": "You have advantage on saving throws against illusions and against being charmed or paralyzed.",
    "Duergar Magic": "Starting at 3rd level, you can cast the enlarge/reduce spell with this trait, without a material component. Starting at 5th level, you can also cast the invisibility spell with this trait, without a material component. Once you cast either spell, you can't cast it again until you finish a long rest. Intelligence is your spellcasting ability for these spells.",
    "Sunlight Sensitivity": "You have disadvantage on attack rolls and on Wisdom (Perception) checks that rely on sight when you, the target of your attack, or whatever you are trying to perceive is in direct sunlight.",
    # Sea Elf
    "Sea Elf Training": "You have proficiency with the spear, trident, light crossbow, and net.",
    "Child of the Sea": "You have a swimming speed of 30 feet, and you can breathe air and water.",
    # Eladrin
    "Fey Step": "As a bonus action, you can magically teleport up to 30 feet to an unoccupied space you can see. Once you use this trait, you can't do so again until you finish a short or long rest. When you reach 3rd level, your Fey Step gains an additional effect based on your season; if the effect requires a saving throw, the DC is 8 + your proficiency bonus + your Intelligence modifier.",
    # Shadar-kai
    "Necrotic Resistance": "You have resistance to necrotic damage.",
    "Blessing of the Raven Queen": "As a bonus action, you can magically teleport up to 30 feet to an unoccupied space you can see. Once you use this trait, you can't do so again until you finish a long rest. Starting at 3rd level, you also gain resistance to all damage when you teleport using this trait. The resistance lasts until the start of your next turn, and during that time you appear ghostly and translucent.",

    # ── Custom Lineage (TCE) ──
    "Feat": "You gain one feat of your choice for which you qualify. This represents a specialized talent, training, or innate ability that sets your character apart.",
    "Variable Trait": "You gain your choice of one of the following options: (a) darkvision with a range of 60 feet, or (b) proficiency in one skill of your choice.",

    # ── AiME Dwarf Variants ──
    "Dwarven Combat Training": "You have proficiency with the battleaxe, handaxe, light hammer, and warhammer.",
    "Night Vision": "Accustomed to twilit forests and the night sky, you have superior vision in dark and dim conditions. You can see in dim light within 60 feet of you as if it were bright light, and in darkness as if it were dim light. You can't discern color in darkness, only shades of gray.",
    "Road Wisdom": "You have proficiency in the Survival skill. When you make an Intelligence or Wisdom check related to the lands of your people, you can add twice your proficiency bonus instead of your normal proficiency bonus.",
    "Night Vision (Dwarf)": "Accustomed to life underground and twilit forges, you have superior vision in dark and dim conditions. You can see in dim light within 60 feet of you as if it were bright light, and in darkness as if it were dim light. You can't discern color in darkness, only shades of gray.",
    "Weapons of the Trade": "You have proficiency with light hammers, handaxes, battleaxes, and throwing hammers.",
    "Tool Proficiency": "You gain proficiency with one set of artisan's tools of your choice: smith's tools, brewer's supplies, or mason's tools.",
    "Singer of the Old Songs": "You know the history of your people and the great deeds of your ancestors. You have proficiency in the Performance skill, and you can add twice your proficiency bonus to any Intelligence (History) check related to dwarven history.",
    "Tales of Days Gone By": "You have advantage on saving throws against being frightened.",
    "Tools for War": "You have proficiency with the smith's tools, and you can add twice your proficiency bonus to any ability check you make with them.",

    # ── AiME Elf Variants ──
    "The Eyes of Elves": "You have proficiency in the Perception skill. When in a forest, you can add twice your proficiency bonus to any Wisdom (Perception) check that relies on sight.",
    "Elvish Dreams": "Elves don't need to sleep. Instead, they meditate deeply, remaining semiconscious, for 4 hours a day. After resting in this way, you gain the same benefit that a human does from 8 hours of sleep.",
    "The Tools of War": "You have proficiency with the longsword, shortsword, shortbow, and longbow.",
    "A Whisper Through the Leaves": "You can attempt to hide even when you are only lightly obscured by foliage, heavy rain, falling snow, mist, and other natural phenomena.",
    "Against the Unseen": "You have advantage on saving throws against being frightened, and you can add your proficiency bonus to any Intelligence (Arcana) check made to identify or recall information about the Enemy (Sauron's forces) and their works.",
    "Elf-wise": "You have advantage on Wisdom saving throws against spells and other magical effects.",
    "Beset by Woe": "Elves of Rivendell have witnessed much sorrow. When you take a long rest, you can choose to have a vision of the past or future. The Loremaster will describe what you see.",

    # ── AiME Hobbit/Halfling Variants ──
    "Resilient": "You have advantage on saving throws against being frightened, and you can add your proficiency bonus to saving throws against being charmed.",
    "Hobbit Nimbleness": "You can move through the space of any creature that is of a size larger than yours.",
    "Noble Pursuits": "You have proficiency in one of the following skills of your choice: History, Performance, or Persuasion. You also gain proficiency with one musical instrument or gaming set of your choice.",
    "Hobbit Elusiveness": "When you take damage, you can use your reaction to halve the damage. Once you use this trait, you can't use it again until you finish a short or long rest.",
    "Family Ties (Pick One)": "Choose one of the three hobbit families: Harfoot, Stoor, or Fallowhide. Each grants additional traits reflecting your family's character and traditions.",
    "Harfoot": "Harfoots are the most common hobbits — brown-skinned, smaller than the others, and most inclined to settle in hillsides. You have proficiency in the Stealth skill.",
    "Stoor": "Stoors are broader, heavier hobbits who favor riversides and flatlands — the only hobbits comfortable with boats and swimming. You have proficiency with water vehicles and a swim speed of 20 feet.",
    "Fallowhide": "Fallowhides are fair-skinned hobbits, taller and slimmer than most, with a love of the woods and a keen interest in Elves. You have proficiency in the Nature skill and can speak, read, and write Elvish.",
    "Keen-eyed": "You have proficiency in the Perception skill. When you make a Wisdom (Perception) check that relies on sight, you can add twice your proficiency bonus.",
    "Story-telling": "You have proficiency in the Performance skill. When telling stories, singing, or reciting poetry, you have advantage on Charisma (Performance) checks.",
    "Unobtrusive": "You can attempt to hide even when you are obscured only by a creature that is at least one size larger than you.",
    "Known Lands": "You have an excellent memory for maps and geography, and you can always recall the general layout of terrain, settlements, and other features around you. In addition, you can find food and fresh water for yourself and up to five other people each day, provided that the land offers berries, small game, water, and so forth.",
    "Ways of the Wild": "You have proficiency in the Survival skill. When tracking other creatures, you can add twice your proficiency bonus to the check.",
    "Weather Lore": "By observing the sky, winds, and wildlife, you can accurately predict the weather for the next 24 hours. You have advantage on Wisdom (Survival) checks related to predicting weather or navigating by natural signs.",
    "Cultural Virtue: None": "Not all hobbits embrace a specific cultural virtue — some forge their own path. You gain proficiency in one skill or tool of your choice.",
    "Untroubled by Shadows": "You have advantage on saving throws against being frightened, and against the corrupting influence of the Shadow. When you fail a saving throw against fear, you can reroll it — you must use the new roll.",
    "Clever Beyond Compare": "You have a knack for finding simple solutions to complex problems. You can add your proficiency bonus to any Intelligence check made to devise or recognize a clever plan, riddle, or puzzle solution. If you are already proficient, you add twice your proficiency bonus.",
    "Preternatural Navigator": "You have an innate sense of direction and an excellent memory for routes. You have proficiency in the Survival skill, and can add twice your proficiency bonus to any check made to avoid becoming lost.",
    "Animal Ken": "You have proficiency in the Animal Handling skill. Beasts of the riverlands — otters, waterfowl, fish — are naturally inclined to trust you.",
    "Riverfolk Toughness": "Your life on the water has made you hardy. Your hit point maximum increases by 1, and it increases by 1 every time you gain a level.",
    "Boon Companion": "You are remarkably skilled at making people feel at ease. You have proficiency in the Persuasion skill, and you can add twice your proficiency bonus to any Charisma check made to befriend or charm a humanoid with a noble or courtly background.",
    "Wee Glamour": "You know the minor illusion cantrip. Charisma is your spellcasting ability for this spell.",
    "Student of Old Lore": "You have spent many hours in libraries and archives. You have proficiency in the History skill, and you can add twice your proficiency bonus to any Intelligence (History) check related to ancient kingdoms, lineages, or artifacts.",
    "Disquiet": "As an action, you can cause one creature you can see within 30 feet to become unsettled. The target must succeed on a Wisdom saving throw (DC 8 + your proficiency bonus + your Charisma modifier) or be frightened of you until the end of your next turn. Once you use this trait, you can't use it again until you finish a short or long rest.",
    "Silent Steps": "You have proficiency in the Stealth skill. When moving through dim light or darkness, you can add twice your proficiency bonus to Dexterity (Stealth) checks.",

    # ── Other Middle-earth variants ──
    "Clear Eyed": "You have advantage on Wisdom (Insight) checks to determine if someone is lying, and on saving throws against being chararmed.",
    "Crossroad Glance": "You have proficiency in the Insight skill. When you first meet someone, you can make a Wisdom (Insight) check to gain a general sense of their intentions.",
    "Proud Heritage": "You have proficiency in the History skill. When making a check related to Gondor's history, lineages, or military traditions, you can add twice your proficiency bonus.",
    "Natural Born Traders": "You have proficiency in the Persuasion skill, and you can add twice your proficiency bonus to any ability check made to negotiate prices or barter.",
    "Horse Lords": "You have proficiency in the Animal Handling skill, and when you use a mount, you can add twice your proficiency bonus to any check to control or remain mounted.",

    # ── Kobold Press — Shadow Fey ──
    "Shadow Fey Weapon Training": "You have proficiency with rapiers, shortswords, hand crossbows, and longbows.",
    "Path of Shadows": "As a bonus action, you can teleport up to 30 feet to an unoccupied space you can see that is in dim light or darkness. Once you use this trait, you can't use it again until you finish a short or long rest.",
    "Traveler in Darkness": "You have advantage on Dexterity (Stealth) checks made in dim light or darkness. You can also see in dim light within 120 feet of you as if it were bright light, and in darkness as if it were dim light.",
    "Luminous": "You know the light cantrip. When you reach 3rd level, you can cast the faerie fire spell once per long rest. When you reach 5th level, you can cast the moonbeam spell once per long rest. Charisma is your spellcasting ability for these spells.",
    "Moon Child": "You have resistance to necrotic damage. While in moonlight, you have advantage on Wisdom saving throws.",

    # ── Kobold Press — Sable Elf ──
    "Blood Affinity": "When you reduce a hostile creature to 0 hit points, you gain temporary hit points equal to your proficiency bonus. These temporary hit points last for 1 minute.",

    # ── Kobold Press — Wyrd Gnome ──
    "Natural Diviner": "You know the guidance cantrip. When you reach 3rd level, you can cast the augury spell once per long rest. When you reach 5th level, you can cast the clairvoyance spell once per long rest. Intelligence is your spellcasting ability for these spells.",
    "Prescience": "When you finish a long rest, roll a d20 and record the number rolled. You can replace any attack roll, saving throw, or ability check made by you or a creature you can see with this foretelling roll. You must choose to do so before the roll. Once you use this trait, you can't use it again until you finish a long rest.",

    # ── Kobold Press — Umbral Human variants ──
    "Dark Infusion": "You know the thaumaturgy cantrip. When you reach 3rd level, you can cast the hex spell once per long rest. When you reach 5th level, you can cast the darkness spell once per long rest. Charisma is your spellcasting ability for these spells.",
    "Fade Away": "When you take damage, you can use your reaction to become invisible until the end of your next turn. Once you use this trait, you can't use it again until you finish a short or long rest.",
    "Cover Story": "You have proficiency in the Deception skill and the disguise kit. You can mimic the speech, writing, and mannerisms of another humanoid you have observed for at least one hour.",
    "Shadow Glamour": "You know the friends cantrip. When you reach 3rd level, you can cast the disguise self spell once per long rest. Charisma is your spellcasting ability for these spells.",
    "Cursed Infusion": "You know the chill touch cantrip. When you reach 3rd level, you can cast the ray of sickness spell once per long rest. When you reach 5th level, you can cast the bestow curse spell once per long rest. Charisma is your spellcasting ability for these spells.",
    "Shadow Gift": "As a bonus action, you can grant one creature you touch darkvision out to 60 feet for 1 hour. If the creature already has darkvision, its range increases by 30 feet for the duration. Once you use this trait, you can't use it again until you finish a short or long rest.",

    # ── Silvan Elf Sentinel (Eberron monster stat block, not a race) ──
    "Disabling Strike": "When you hit a creature with a weapon attack, you can force the target to make a Constitution saving throw (DC 8 + your proficiency bonus + your Strength or Dexterity modifier). On a failure, the target's speed is reduced to 0 until the end of its next turn.",
    "Focused": "You have advantage on saving throws against being charmed or frightened.",
    "Multiattack": "You can make two weapon attacks when you take the Attack action.",
    "Great Spear": "You have proficiency with the greatspear. This weapon has the heavy, reach, and two-handed properties and deals 1d12 piercing damage.",
    "Great Bow": "You have proficiency with the greatbow. This weapon has the heavy and two-handed properties, a range of 150/600 feet, and deals 1d10 piercing damage.",
    "Parry": "When another creature damages you with a melee attack, you can use your reaction to add your proficiency bonus to your AC against that attack, potentially causing it to miss.",
}

# Merge racial trait descriptions into the feature lookup so Breath Weapon etc. show descriptions
for trait_name, trait_desc in RACIAL_TRAIT_DESCS.items():
    key = trait_name.lower()
    if key not in FEATURE_DESCRIPTIONS:
        FEATURE_DESCRIPTIONS[key] = trait_desc

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
}

# PHB p.17-43 — Racial trait mechanical effects for automatic application
# Each key is a trait name; value is {armor_profs, weapon_profs, tool_profs,
#   skill_profs, damage_resist, condition_immune, speed, darkvision, hp_per_level}
RACIAL_TRAIT_EFFECTS = {
    # ── Dwarf (base) ──
    "Dwarven Resilience": {"damage_resist": ["Poison"]},
    "Stonecunning": {},  # ribbon

    # ── Hill Dwarf ──
    "Dwarven Toughness": {"hp_per_level": 1},

    # ── Mountain Dwarf ──
    "Dwarven Armor Training": {"armor_profs": ["Light armor", "Medium armor"]},

    # ── Elf (base) ──
    "Keen Senses": {"skill_profs": ["Perception"]},
    "Fey Ancestry": {"condition_immune": ["Sleep"]},
    "Trance": {},  # ribbon

    # ── High Elf ──
    "Elf Weapon Training": {"weapon_profs": ["Longsword", "Shortsword", "Shortbow", "Longbow"]},
    "Cantrip (High Elf)": {},  # choice-based

    # ── Wood Elf ──
    "Fleet of Foot": {"speed": 35},
    "Mask of the Wild": {},  # ribbon

    # ── Dark Elf (Drow) ──
    "Superior Darkvision": {"darkvision": 120},
    "Sunlight Sensitivity": {},  # ribbon
    "Drow Magic": {},  # ribbon

    # ── Sea Elf ──
    "Sea Elf Training": {"weapon_profs": ["Spear", "Trident", "Light Crossbow", "Net"]},
    "Child of the Sea": {},  # swim 30ft + amphibious (ribbon)

    # ── Eladrin ──
    "Fey Step": {},  # misty step 1/short rest (ribbon)

    # ── Shadar-kai ──
    "Necrotic Resistance": {"damage_resist": ["Necrotic"]},
    "Blessing of the Raven Queen": {},  # teleport 1/long rest (ribbon)

    # ── Halfling (base) ──
    "Lucky": {},  # ribbon
    "Brave": {},
    "Halfling Nimbleness": {},  # ribbon

    # ── Lightfoot Halfling ──
    "Naturally Stealthy": {},  # ribbon

    # ── Stout Halfling ──
    "Stout Resilience": {"damage_resist": ["Poison"]},

    # ── Ghostwise Halfling ──
    "Silent Speech": {},  # telepathy 30ft (ribbon)

    # ── Dragonborn ──
    "Draconic Ancestry": {},  # choice-based resistance
    "Breath Weapon": {},  # ribbon (attack feature)
    "Damage Resistance": {},  # handled by ancestry choice

    # ── Gnome (base) ──
    "Gnome Cunning": {},  # advantage on INT/WIS/CHA saves vs magic

    # ── Forest Gnome ──
    "Natural Illusionist": {},  # ribbon
    "Speak with Small Beasts": {},  # ribbon

    # ── Rock Gnome ──
    "Artificer's Lore": {},  # ribbon
    "Tinker": {"tool_profs": ["Tinker's tools"]},

    # ── Deep Gnome ──
    "Stone Camouflage": {},  # advantage on Stealth in rocky terrain (ribbon)

    # ── Air Genasi ──
    "Unending Breath": {},  # hold breath indefinitely (ribbon)
    "Mingle with the Wind": {},  # levitate 1/long rest (ribbon)
    # ── Earth Genasi ──
    "Earth Walk": {},  # ignore earth/stone difficult terrain (ribbon)
    "Merge with Stone": {},  # pass without trace 1/long rest (ribbon)
    # ── Fire Genasi ──
    "Fire Resistance": {"damage_resist": ["Fire"]},
    "Reach to the Blaze": {},  # produce flame cantrip + burning hands 1/long (ribbon)
    # ── Water Genasi ──
    "Amphibious": {},  # breathe air + water (ribbon)
    "Swim": {},  # swim 30ft (ribbon)
    "Acid Resistance": {"damage_resist": ["Acid"]},
    "Call to the Wave": {},  # shape water cantrip + create/destroy water 1/long (ribbon)

    # ── Half-Elf ──
    "Skill Versatility": {},  # choice-based

    # ── Half-Orc ──
    "Relentless Endurance": {},  # ribbon
    "Savage Attacks": {},  # ribbon

    # ── Tiefling ──
    "Hellish Resistance": {"damage_resist": ["Fire"]},
    "Infernal Legacy": {},  # ribbon
}


def get_racial_trait_effects(race_name: str, subrace: str = "", ancestry: str = "") -> dict:
    """Return merged mechanical effects for a race/subrace combination.

    Returns {armor_profs, weapon_profs, tool_profs, skill_profs,
             damage_resist, condition_immune, speed, darkvision, hp_per_level}.

    For Dragonborn, pass the draconic ancestry color (e.g. 'Gold') to get
    the correct damage resistance from the Draconic Ancestry table (PHB p.34).

    Callers should merge these into DB-stored values for display (render-time)
    and into the character record at creation time.
    """
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


# PHB p.34 — Draconic Ancestry table
# color → {resist, damage_type, shape, save_stat}
DRACONIC_ANCESTRIES = {
    "Black":     {"resist": "Acid",      "damage": "Acid",       "shape": "5 by 30 ft. line",  "save": "Dexterity"},
    "Blue":      {"resist": "Lightning", "damage": "Lightning",  "shape": "5 by 30 ft. line",  "save": "Dexterity"},
    "Brass":     {"resist": "Fire",      "damage": "Fire",       "shape": "5 by 30 ft. line",  "save": "Dexterity"},
    "Bronze":    {"resist": "Lightning", "damage": "Lightning",  "shape": "5 by 30 ft. line",  "save": "Dexterity"},
    "Copper":    {"resist": "Acid",      "damage": "Acid",       "shape": "5 by 30 ft. line",  "save": "Dexterity"},
    "Gold":      {"resist": "Fire",      "damage": "Fire",       "shape": "15 ft. cone",       "save": "Dexterity"},
    "Green":     {"resist": "Poison",    "damage": "Poison",     "shape": "15 ft. cone",       "save": "Constitution"},
    "Red":       {"resist": "Fire",      "damage": "Fire",       "shape": "15 ft. cone",       "save": "Dexterity"},
    "Silver":    {"resist": "Cold",      "damage": "Cold",       "shape": "15 ft. cone",       "save": "Constitution"},
    "White":     {"resist": "Cold",      "damage": "Cold",       "shape": "15 ft. cone",       "save": "Constitution"},
}


def _build_racial_traits(char: dict) -> list:
    """Build a list of {name, desc, source} for the character's race and subrace traits."""
    result = []
    race_name = char.get("race", "")
    subrace = char.get("subrace", "")

    race_data = RACES.get(race_name)
    if race_data:
        for t in race_data.get("traits", []):
            # Generic traits are stored per-race to avoid cross-race pollution
            if t.lower() in _GENERIC_TRAITS:
                desc = RACIAL_TRAIT_DESCS.get(f"{race_name}::{t}", "")
            else:
                desc = RACIAL_TRAIT_DESCS.get(t, "")
            if desc:
                # Look up page-accurate source
                src = _trait_page_map.get(t, "")
                if not src:
                    src = race_name
                result.append({"name": t, "desc": desc, "source": src})

    if subrace:
        sub_traits = SUBRACE_TRAITS.get(subrace, [])
        for t in sub_traits:
            # Generic subrace traits also per-race
            if t.lower() in _GENERIC_TRAITS:
                desc = RACIAL_TRAIT_DESCS.get(f"{subrace}::{t}", "")
            else:
                desc = RACIAL_TRAIT_DESCS.get(t, "")
            if desc:
                # Look up page-accurate source
                src = _trait_page_map.get(t, "")
                if not src:
                    src = subrace
                result.append({"name": t, "desc": desc, "source": src})

    return result

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

def _subrace_traits(manual_entry: dict) -> list[str]:
    """Extract subrace-specific trait names, filtering out generic ones."""
    traits = []
    for t in manual_entry.get("traits", []):
        name = t.get("name", "")
        if name and name.lower() not in _GENERIC_TRAITS:
            traits.append(name)
    return traits

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
    ("Silvan Elf Sentinel",     "Elf", None, None),
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

CLASSES = {
    "Artificer": {"hd": 8, "skills": ["Arcana","History","Investigation","Medicine","Nature","Perception","Sleight of Hand"], "skill_count": 2, "saves": ["constitution","intelligence"], "subclasses": ["Alchemist","Armorer","Artillerist","Battle Smith"], "armor": ["Light armor","Medium armor","Shields"], "weapons": ["Simple weapons","Firearms"], "tools": ["Thieves' tools","Tinker's tools","One type of artisan's tools"], "desc": "Artificers are master inventors and magical engineers \u2014 they don't just wield magic, they build it into physical form. Where a wizard studies dusty tomes and a sorcerer channels innate power, an artificer picks up a wrench and a coil of copper wire and gets to work. They see magic as a form of technology \u2014 a system of rules that can be understood, manipulated, and embedded into objects.\n\nArtificers use Intelligence as their spellcasting ability, and their spells are expressed through tools and inventions rather than incantations. Every artificer spell requires a set of artisan's tools or thieves' tools as a focus \u2014 their magic is literally crafted. They are half-casters (up to 5th-level spells) who prepare spells daily from the full artificer list.\n\nAt 2nd level, Infuse Item lets them turn ordinary objects into temporary magic items, choosing from a list of Infusions. This is the artificer's signature ability \u2014 they can grant +1 weapons, +1 armor, repeating crossbows, bags of holding, and more to themselves and their party. At higher levels, they can attune to more magic items than other classes and craft magic items faster and cheaper.\n\nMechanically, artificers are d8 hit die half-casters who fill a unique support/utility role. Flash of Genius at 7th lets them add Intelligence to any ability check or saving throw as a reaction. Spell-Storing Item at 11th lets them store a 1st or 2nd level spell in an object for anyone to use. At 20th, Soul of Artifice grants +1 to all saving throws per attuned magic item (up to +6). They are the ultimate item-crafters and party force-multipliers.", "subclass_descs": {"Alchemist": "Masters of potions and elixirs. At 3rd level, Experimental Elixir lets you create one random elixir per long rest (bonus elixirs for spell slots). At 5th, Alchemical Savant adds INT to one damage or healing roll of spells cast through alchemist's supplies. At 9th, Restorative Reagents grants temporary HP when drinking elixirs and free lesser restoration castings. At 15th, Chemical Mastery grants resistance to acid/poison and free heal/greater restoration per long rest.", "Armorer": "Heavy-armor inventors who treat armor as a second skin. At 3rd level, Arcane Armor turns a suit of heavy armor into a spellcasting focus with no Strength requirement. Choose Guardian (thunder gauntlets that taunt enemies) or Infiltrator (lightning launcher for stealth). At 5th, Extra Attack. At 9th, Armor Modifications splits armor into multiple pieces for more infusions. At 15th, Perfected Armor pulls enemies toward you (Guardian) or grants advantage on attacks (Infiltrator).", "Artillerist": "Wielders of arcane cannons. At 3rd level, Eldritch Cannon can be summoned as a bonus action \u2014 choose Flamethrower (15 ft cone), Force Ballista (ranged push), or Protector (temporary HP aura). At 5th, Arcane Firearm wand adds d8 to spell damage. At 9th, cannons explode on destruction. At 15th, Fortified Position summons two cannons and grants half cover.", "Battle Smith": "Combat engineers with a Steel Defender companion. At 3rd level, Battle Ready lets you use INT for magic weapon attacks and grants martial weapon proficiency. Steel Defender (bonus action commands, force-empowered rend, Deflect Attack reaction). At 5th, Extra Attack. At 9th, Arcane Jolt adds 2d6 force damage or healing to weapon attacks (INT mod per long rest). At 15th, Improved Defender adds Arcane Jolt to the defender's attacks and grants it increased damage."}, "source": "TCE 2020 / Eberron: Rising from the Last War"},
    "Barbarian": {"hd": 12, "skills": ["Animal Handling","Athletics","Intimidation","Nature","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Path of the Berserker","Path of the Totem Warrior"], "armor": ["Light armor","Medium armor","Shields"], "desc": "Barbarians are warriors defined by their rage — a primal fury that transforms them into seemingly unstoppable forces of destruction. Standing at the front of any battle, their muscular frames bear the scars of countless fights. Where other warriors rely on technique and discipline, the barbarian trusts in raw power, instinct, and an almost supernatural resilience that lets them shrug off wounds that would fell lesser combatants.\n\nIn combat, a barbarian enters a Rage as a bonus action, gaining advantage on Strength checks and saves, bonus melee damage, and resistance to bludgeoning, piercing, and slashing damage. They fight recklessly, trading defense for devastating offense with Reckless Attack. Their Danger Sense gives them advantage on Dexterity saves against effects they can see, and their unarmored defense lets them calculate AC from Constitution and Dexterity while eschewing heavy armor.\n\nMechanically, barbarians are d12 hit die melee strikers and damage sponges. At higher levels, they gain Brutal Critical (extra dice on critical hits), Relentless Rage (drop to 1 HP instead of 0), and eventually Primal Champion (+4 to Strength and Constitution, breaking the normal ability score cap). They are unmatched at absorbing punishment while dishing out consistent, heavy damage.", "subclass_descs": {"Path of the Berserker": "At 3rd level, you can go into a Frenzy during your rage, allowing a bonus action melee weapon attack each turn at the cost of a level of exhaustion when the rage ends. Mindless Rage at 6th prevents being charmed or frightened while raging. Intimidating Presence at 10th lets you frighten foes with a display of raw menace. At 14th, Retaliation lets you strike back at anyone who damages you — no action required.", "Path of the Totem Warrior": "At 3rd level, choose a spirit totem: Bear (resistance to all damage except psychic while raging), Eagle (bonus action Dash and enemies have disadvantage on opportunity attacks), or Wolf (allies within 5 feet gain advantage on melee attacks). At 6th, gain an animal aspect: Bear (double carrying capacity), Eagle (see a mile), or Wolf (track at fast pace). At 14th, gain a totemic attunement: Bear (enemies within 5 feet have disadvantage on attacks against others), Eagle (limited flight), or Wolf (bonus action knock prone on hit)."}, "weapons": "Simple weapons, Martial weapons", "armor": "Light armor, Medium armor, Shields", "tools": "", "source": "PHB 2014 p.46"},
    "Bard": {"hd": 8, "skills": "all", "skill_count": 3, "saves": ["dexterity","charisma"], "subclasses": ["College of Lore","College of Valor"], "desc": "Bards are the ultimate storytellers, weaving magic through words, music, and performance. Whether a skald chanting sagas of ancient heroes, a cunning jester who mocks enemies into submission, or a loremaster collecting lost knowledge from forgotten libraries, bards understand that the right word at the right moment can change the course of history. They are charming, quick-witted, and impossibly versatile — a bard can fill nearly any role in an adventuring party.\n\nIn combat, bards wield Bardic Inspiration — a pool of d6s (growing to d12s) that they grant to allies, who can add them to ability checks, attack rolls, or saving throws. Their spellcasting is Charisma-based and draws from a broad list that includes healing, control, enchantment, and utility magic. Jack of All Trades adds half their proficiency bonus to every ability check they're not proficient in, making bards remarkably competent at everything.\n\nMechanically, bards are d8 hit die full spellcasters who know a fixed number of spells rather than preparing them daily. Song of Rest improves short-rest healing for the party. Expertise doubles proficiency for chosen skills. At 10th level, Magical Secrets lets them steal spells from any class list — a bard can learn fireball, find steed, or counterspell. At 20th, Superior Inspiration guarantees they start every combat with at least one use of Bardic Inspiration. They naturally excel as faces, supports, and skill monkeys.", "subclass_descs": {"College of Lore": "At 3rd level, gain three bonus skill proficiencies and Cutting Words: spend a Bardic Inspiration die as a reaction to subtract from an enemy's attack roll, ability check, or damage roll. At 6th, Additional Magical Secrets lets you learn two spells from any class — the Lore bard becomes a magical Swiss Army knife. At 14th, Peerless Skill lets you add Bardic Inspiration to your own ability checks, turning failure into success on demand.", "College of Valor": "At 3rd level, gain proficiency with medium armor, shields, and martial weapons. Combat Inspiration allows allies to add Bardic Inspiration to weapon damage rolls or use it as a reaction to boost AC against an attack. At 6th, Extra Attack grants a second attack when you take the Attack action. At 14th, Battle Magic lets you make a weapon attack as a bonus action after casting a bard spell — the spellblade who sings and swings."}, "weapons": "Simple weapons, Hand crossbows, Longswords, Rapiers, Shortswords", "armor": "Light armor", "tools": "Three musical instruments of your choice", "source": "PHB 2014 p.51"},
    "Cleric": {"hd": 8, "skills": ["History","Insight","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"], "desc": "Clerics are mortal agents of the gods, chosen to wield divine power in the world. A cleric might be a war priest blessing soldiers before battle, a cloistered scholar uncovering forbidden knowledge, or a healer tending to plague victims in the slums. Their power flows from faith and devotion — not study or bloodline — and a cleric's choice of deity and domain shapes their entire identity and playstyle.\n\nAll clerics share core divine abilities. Channel Divinity grants powerful effects (often Turning Undead) that recharge on short rests. They prepare spells daily from the full cleric list, and their Divine Domain at 1st level grants bonus spells and features that define their role — a Life cleric heals more, a Light cleric blasts with radiant fire, a War cleric wades into melee with heavy armor and martial weapons.\n\nMechanically, clerics are d8 hit die full spellcasters with medium armor and shield proficiency. Their spell list includes the best healing magic in the game, powerful buffs (bless, shield of faith), control (spirit guardians, banishment), and offensive staples (guiding bolt, spiritual weapon, flame strike). At 10th level, Divine Intervention gives a percentage chance to call on their deity directly for aid. At 20th, it succeeds automatically. Clerics are the most flexible full casters — a single subclass choice can turn them into a blaster, tank, healer, or controller.", "subclass_descs": {"Knowledge Domain": "Blessings of Knowledge grants expertise in two knowledge skills. Channel Divinity: Read Thoughts lets you read surface thoughts and cast suggestion. Visions of the Past at 17th lets you psychically experience an object's or location's history. The ultimate lore-seeker.", "Life Domain": "Disciple of Life adds 2 + spell level bonus healing to every healing spell. Preserve Life (Channel Divinity) restores HP to allies up to half their max. Blessed Healer heals you when you heal others. Supreme Healing at 17th maximizes all healing dice.", "Light Domain": "Warding Flare imposes disadvantage on attackers as a reaction. Radiance of the Dawn (Channel Divinity) deals radiant damage in a 30-ft radius and dispels magical darkness. Corona of Light at 17th gives enemies disadvantage on saves against your fire and radiant spells. The burning light of truth.", "Nature Domain": "Acolyte of Nature grants a druid cantrip and skill. Charm Animals and Plants (Channel Divinity) pacifies beasts and plants. Dampen Elements at 6th grants resistance to elemental damage as a reaction. Master of Nature at 17th lets you command animals and plants.", "Tempest Domain": "Wrath of the Storm deals thunder or lightning damage as a reaction. Destructive Wrath (Channel Divinity) maximizes thunder/lightning damage instead of rolling. Thunderbolt Strike at 6th pushes foes 10 ft on lightning damage. Stormborn at 17th grants a flying speed.", "Trickery Domain": "Invoke Duplicity (Channel Divinity) creates a perfect illusory double that you can cast spells through. Cloak of Shadows at 6th lets you turn invisible for a round. Divine Strike at 8th adds poison damage. The god of shadows and mischief smiles.", "War Domain": "War Priest grants bonus action weapon attacks (limited WIS mod times per long rest). Guided Strike (Channel Divinity) adds +10 to attack rolls. War God's Blessing at 6th lets allies add +10 to their attacks. Avatar of Battle at 17th grants resistance to nonmagical weapon damage."}, "weapons": "Simple weapons", "armor": "Light armor, Medium armor, Shields", "tools": "", "source": "PHB 2014 p.56"},
    "Druid": {"hd": 8, "skills": ["Arcana","Animal Handling","Insight","Medicine","Nature","Perception","Religion","Survival"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Circle of the Land","Circle of the Moon"], "desc": "Druids are priests of the old faith — guardians of the natural world who draw their magic from the divine essence of nature itself. They are shapeshifters, storm-callers, and beast-speakers who stand between civilization and the untamed wilds. A druid might be a wizened hermit protecting an ancient grove, a feral wanderer running with wolf packs, or a coastal sage commanding the tides and winds.\n\nA druid's signature ability is Wild Shape, which at 2nd level lets them transform into beasts they've seen. This grants extraordinary utility — a druid can become a spider to infiltrate, a horse to carry allies, or a bear to fight. At higher levels, they can become elementals. Their spellcasting is Wisdom-based, drawn from a nature-themed list heavy on control, summoning, healing, and elemental damage. They prepare spells daily from the full druid list.\n\nMechanically, druids are d8 hit die full spellcasters with medium armor and shield proficiency (though they refuse to wear metal). They gain Timeless Body at 18th (age at 1/10th the normal rate) and Beast Spells at 18th (cast spells while in Wild Shape). Archdruid at 20th grants unlimited Wild Shape uses. They are the most adaptable full casters — capable of tanking as a bear, blasting as a storm, or healing as a forest guardian, all in the same day.", "subclass_descs": {"Circle of the Land": "At 2nd level, gain a bonus druid cantrip and Natural Recovery (recover spell slots on a short rest, like a wizard's Arcane Recovery). At 3rd and higher, Circle Spells grant terrain-based bonus spells: Arctic, Coast, Desert, Forest, Grassland, Mountain, Swamp, or Underdark. Land's Stride at 6th ignores nonmagical difficult terrain. Nature's Ward at 10th grants immunity to poison, disease, and charm/frighten from fey and elementals. Nature's Sanctuary at 14th makes beasts and plants hesitate to attack you.", "Circle of the Moon": "At 2nd level, Combat Wild Shape lets you transform as a bonus action and expend spell slots to heal 1d8 per slot level while transformed. Your Wild Shape CR caps at 1 (instead of 1/4), scaling to CR 6 at 18th. Primal Strike at 6th makes your beast form attacks magical. Elemental Wild Shape at 10th lets you expend both uses to become an air, earth, fire, or water elemental. Thousand Forms at 14th grants alter self at will."}, "weapons": "Clubs, Daggers, Darts, Javelins, Maces, Quarterstaffs, Scimitars, Sickles, Slings, Spears", "armor": "Light armor, Medium armor, Shields (druids will not wear metal armor or shields)", "tools": "Herbalism kit", "source": "PHB 2014 p.64"},
    "Fighter": {"hd": 10, "skills": ["Acrobatics","Animal Handling","Athletics","History","Insight","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Champion","Battle Master","Eldritch Knight"], "desc": "Fighters are the undisputed masters of combat — warriors who have trained their bodies and minds for one purpose: victory in battle. They come from every walk of life: knights in shining plate, grizzled mercenaries, elven archers who never miss, dwarven defenders who hold the line against impossible odds. What unites them is absolute mastery of weapons, armor, and tactics.\n\nFighters gain more Ability Score Improvements than any other class, and their Fighting Style at 1st level defines their combat identity — Archery, Defense, Dueling, Great Weapon Fighting, Protection, or Two-Weapon Fighting. Action Surge at 2nd level grants a second full action once per short rest. Second Wind provides a bonus-action self-heal. But the fighter's true claim to greatness is Extra Attack — at 5th, 11th, and 20th level, they gain additional attacks, swinging four times for every one swing of other warriors.\n\nMechanically, fighters are d10 hit die martial characters who can use every weapon and wear every armor. They are the most reliable damage-dealers and the most durable front-liners. Their subclass choice dramatically expands their toolkit: the Champion is simplicity and lethality, the Battle Master is tactical control, and the Eldritch Knight blends swordplay with wizardry. At 20th, they attack four times per Attack action — eight times with Action Surge.", "subclass_descs": {"Champion": "At 3rd level, Improved Critical scores a critical hit on 19–20 (later 18–20 at 15th). Remarkable Athlete at 7th adds half your proficiency bonus to any Strength, Dexterity, or Constitution check you aren't proficient in. Additional Fighting Style at 10th broadens your combat options. Survivor at 18th regenerates 5 + CON mod HP each turn while below half HP.", "Battle Master": "At 3rd level, Combat Superiority grants four d8 superiority dice and three maneuvers chosen from 16 options: Precision Attack (+die to hit), Trip Attack (+die to damage + knock prone), Riposte (counterattack as reaction), Menacing Attack (frighten), Disarming Attack, and more. Know Your Enemy at 7th lets you size up foes' stats relative to yours. Superiority dice grow to d10 at 10th and d12 at 18th. Relentless at 15th ensures you start every combat with at least one die.", "Eldritch Knight": "At 3rd level, gain wizard spellcasting (abjuration/evocation mostly) with 1/3 caster progression — slots up to 4th level. Weapon Bond prevents you from being disarmed and lets you summon bonded weapons across planes as a bonus action. War Magic at 7th lets you make a weapon attack as a bonus action after casting a cantrip. Eldritch Strike at 10th imposes disadvantage on saves against your next spell. Arcane Charge at 15th lets you teleport before Action Surge."}, "weapons": "Simple weapons, Martial weapons", "armor": "All armor, Shields", "tools": "", "source": "PHB 2014 p.70"},
    "Monk": {"hd": 8, "skills": ["Acrobatics","Athletics","History","Insight","Religion","Stealth"], "skill_count": 2, "saves": ["strength","dexterity"], "subclasses": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"], "armor": [], "desc": "Monks are living weapons — martial artists who have honed their bodies into instruments of supernatural precision through rigorous discipline and meditation. They need no sword or shield; their fists, feet, and ki — a mystical life energy that flows through all living things — are all the tools they require. A monk might be a serene master atop a mountain peak, a shadowy infiltrator moving without sound, or a wandering ascetic who can catch arrows and run across water.\n\nAll monks share core abilities powered by Ki points, which recharge on short rests. Flurry of Blows lets them attack twice as a bonus action. Patient Defense dodges as a bonus action. Step of the Wind dashes or disengages as a bonus action and doubles jump distance. Deflect Missiles catches and throws back arrows. Stunning Strike at 5th lets them stun enemies with a well-placed blow. Their Unarmored Defense calculates AC from Wisdom and Dexterity, and Unarmored Movement grants increasing speed bonuses — eventually letting them run up walls and across water.\n\nMechanically, monks are d8 hit die skirmishers who excel at mobility, single-target control, and sustained damage through multiple attacks. They gain Evasion at 7th, Purity of Body (immunity to disease and poison) at 10th, Diamond Soul (proficiency in all saves + reroll) at 14th, and Empty Body (invisibility + astral projection resistance) at 18th. At 20th, Perfect Self ensures they start every combat with 4 ki points.", "subclass_descs": {"Way of the Open Hand": "At 3rd level, Open Hand Technique modifies Flurry of Blows: each hit can knock prone, push 15 ft, or prevent reactions. Wholeness of Body at 6th heals 3 × monk level HP per long rest. Tranquility at 11th grants a permanent sanctuary effect. Quivering Palm at 17th delivers a death touch — on a failed CON save, the target dies; on success, it takes 10d10 necrotic damage.", "Way of Shadow": "At 3rd level, Shadow Arts lets you cast darkness, darkvision, pass without trace, or silence for 2 ki each. Shadow Step at 6th allows bonus action teleport between dim light/darkness up to 60 ft, granting advantage on your next attack. Cloak of Shadows at 11th grants invisibility in dim light/darkness. Opportunist at 17th lets you make an opportunity attack against anyone hit by an ally." , "Way of the Four Elements": "At 3rd level, Elemental Attunement grants a minor elemental cantrip and access to Elemental Disciplines — spell-like effects powered by ki: Fangs of the Fire Snake, Water Whip, Fist of Unbroken Air, Shape the Flowing River. At 6th, 11th, and 17th, learn additional disciplines including fireball, fly, stoneskin, and wall of fire. The monk who commands the elements."}, "weapons": "Simple weapons, Shortswords", "armor": "", "tools": "One type of artisan's tools or one musical instrument", "source": "PHB 2014 p.76"},
    "Paladin": {"hd": 10, "skills": ["Athletics","Insight","Intimidation","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"], "desc": "Paladins are holy warriors, sworn to a sacred oath that is the source of their divine power. More than just fighters with divine magic, paladins are living embodiments of their ideals — a Devotion paladin is a shining beacon of hope, an Ancients paladin is a guardian of joy and life, a Vengeance paladin is an unstoppable force of righteous fury. Their power comes not from a god, but from the sheer force of their own conviction.\n\nIn combat, paladins are devastating melee combatants who can channel divine energy through Divine Smite — expending spell slots to add radiant damage to weapon attacks, with extra damage against fiends and undead. Lay on Hands provides a pool of healing they can distribute as they choose. Their Aura of Protection at 6th adds Charisma to all saving throws for themselves and nearby allies. At higher levels, their auras expand with subclass-specific effects that can turn the tide of battle.\n\nMechanically, paladins are d10 hit die half-casters who prepare spells daily. Fighting Style at 2nd, Extra Attack at 5th, and Aura of Courage (immunity to frightened) at 10th. Cleansing Touch at 14th ends spells on allies. At 20th, their capstone transformation is subclass-specific: Devotion becomes an avatar of divine light, Ancients becomes a force of primeval nature, and Vengeance becomes an avenging angel with flight and frightful presence.", "subclass_descs": {"Oath of Devotion": "Tenets: honesty, courage, compassion, honor, duty. At 3rd, Sacred Weapon (Channel Divinity) adds CHA to attack rolls and makes the weapon magical and glowing. Turn the Unholy frightens fiends and undead. Aura of Devotion at 7th prevents charm within 10 ft. Purity of Spirit at 15th grants permanent protection from evil and good. Holy Nimbus at 20th deals 10 radiant damage per round to enemies within 30 ft — the ultimate holy avatar.", "Oath of the Ancients": "Tenets: kindle light, shelter joy, preserve life, be the light. At 3rd, Nature's Wrath (Channel Divinity) restrains a foe with spectral vines. Turn the Faithless frightens fey and fiends. Aura of Warding at 7th grants resistance to all spell damage within 10 ft — one of the strongest defensive features in the game. Undying Sentinel at 15th drops you to 1 HP instead of 0 once per long rest. Elder Champion at 20th grants fast healing, quickened smite spells, and disadvantage on enemy saves.", "Oath of Vengeance": "Tenets: fight evil, no mercy, by any means, restitution. At 3rd, Vow of Enmity (Channel Divinity) grants advantage on all attacks against one foe for 1 minute. Abjure Enemy frightens and immobilizes. Relentless Avenger at 7th lets you move half speed after opportunity attacks without provoking. Soul of Vengeance at 15th lets you make an attack as a reaction against the target of your Vow. Avenging Angel at 20th grants 60-ft flight, a frightful presence aura, and advantage on Vow of Enmity attacks."}, "weapons": "Simple weapons, Martial weapons", "armor": "All armor, Shields", "tools": "", "source": "PHB 2014 p.82"},
    "Ranger": {"hd": 10, "skills": ["Animal Handling","Athletics","Insight","Investigation","Nature","Perception","Stealth","Survival"], "skill_count": 3, "saves": ["strength","dexterity"], "subclasses": ["Hunter","Beast Master"], "desc": "Rangers are the scouts, trackers, and wilderness warriors who thrive at the boundary between civilization and the wild. They are expert hunters who know their prey's every habit, master archers who can loose a volley into a crowd, and lonely wanderers who follow ancient trails through trackless forests. A ranger's connection to nature grants them primal magic — not the full power of a druid, but enough to heal, ensnare, and strike from the shadows.\n\nAt their core, rangers combine martial skill with nature magic. They gain Fighting Style at 2nd, Spellcasting (Wisdom-based, from the ranger list) at 2nd, and Extra Attack at 5th. Their signature abilities focus on exploration and terrain mastery: Natural Explorer grants doubled proficiency and benefits in favored terrain, Favored Enemy grants advantage on tracking and knowledge about specific creature types. Primeval Awareness at 3rd lets them sense the presence of favored enemies within miles.\n\nMechanically, rangers are d10 hit die half-casters who excel at ranged combat, stealth, and exploration. Land's Stride at 8th ignores nonmagical difficult terrain, Hide in Plain Sight at 10th lets them camouflage themselves with natural materials, and Vanish at 14th lets them Hide as a bonus action. Foe Slayer at 20th adds Wisdom to one attack or damage roll against a favored enemy per turn. Rangers are the ultimate wilderness specialists.", "subclass_descs": {"Hunter": "At 3rd level, choose a Hunter's Prey: Colossus Slayer (extra 1d8 damage to wounded foes once per turn), Giant Killer (reaction attack when Large+ creatures miss you), or Horde Breaker (free extra attack against a nearby creature once per turn). Defensive Tactics at 7th: Escape the Horde (opportunity attacks have disadvantage), Multiattack Defense (+4 AC against subsequent attacks), or Steel Will (advantage on saves vs frightened). Multiattack at 11th: Volley (attack everything in a 10-ft radius) or Whirlwind Attack (melee attack everything within 5 ft). Superior Hunter's Defense at 15th: Evasion, Stand Against the Tide (force attacker to hit someone else), or Uncanny Dodge.", "Beast Master": "At 3rd level, gain an animal companion — a beast of CR 1/4 or lower that acts on your initiative and obeys your commands. At 7th, Exceptional Training lets your companion Dash, Disengage, Dodge, or Help as a bonus action. At 11th, Bestial Fury grants your companion two attacks. At 15th, Share Spells lets spells you cast on yourself also affect your companion. The bond between ranger and beast is unbreakable — if it dies, you can spend 8 hours magically bonding with a new one."}, "weapons": "Simple weapons, Martial weapons", "armor": "Light armor, Medium armor, Shields", "tools": "", "source": "PHB 2014 p.89"},
    "Rogue": {"hd": 8, "skills": ["Acrobatics","Athletics","Deception","Insight","Intimidation","Investigation","Perception","Performance","Persuasion","Sleight of Hand","Stealth"], "skill_count": 4, "saves": ["dexterity","intelligence"], "subclasses": ["Thief","Assassin","Arcane Trickster"], "desc": "Rogues are masters of stealth, precision, and misdirection. They live by their wits — slipping through shadows, disarming traps, picking pockets, and striking when their enemies least expect it. A rogue might be a charming con artist, a silent assassin, a cat burglar who can scale any wall, or an investigator who notices what everyone else misses. What defines them is not their weapons or armor, but their cunning.\n\nA rogue's defining combat feature is Sneak Attack — once per turn, when they attack with advantage or when an ally is adjacent to the target, they deal massive extra damage (1d6 at 1st, scaling to 10d6 at 20th). Cunning Action at 2nd lets them Dash, Disengage, or Hide as a bonus action — unmatched tactical mobility. Uncanny Dodge at 5th halves damage from one attack per round. Evasion at 7th negates damage entirely on successful Dexterity saves. They gain more skill proficiencies and Expertise than any other class.\n\nMechanically, rogues are d8 hit die martial strikers who avoid direct confrontation in favor of hit-and-run tactics. Reliable Talent at 11th makes any proficient skill check they roll treat a 9 or lower as a 10 — rogues almost never fail at what they're good at. Blindsense at 14th detects hidden and invisible creatures. Slippery Mind at 15th grants Wisdom save proficiency. Stroke of Luck at 20th turns a missed attack into a hit or a failed ability check into a natural 20 once per short rest.", "subclass_descs": {"Thief": "At 3rd level, Fast Hands lets you use objects, pick locks, and disarm traps as a bonus action — plus make Sleight of Hand checks. Second-Story Work adds climbing speed equal to your walking speed and increased running jump distance. Supreme Sneak at 9th grants advantage on Stealth checks when moving slowly. Use Magic Device at 13th ignores all class, race, and level restrictions on magic items. Thief's Reflexes at 17th grants an extra turn in the first round of combat.", "Assassin": "At 3rd level, Assassinate gives advantage against creatures that haven't acted yet and auto-crits surprised creatures. Bonus proficiencies with disguise kit and poisoner's kit. Infiltration Expertise at 9th lets you create false identities over 7 days of preparation. Impostor at 13th lets you perfectly mimic a studied person's speech, writing, and behavior. Death Strike at 17th forces a CON save on surprised targets you hit — on failure, double the damage.", "Arcane Trickster": "At 3rd level, gain wizard spellcasting (illusion/enchantment mostly) with 1/3 caster progression. Mage Hand Legerdemain makes your mage hand invisible and capable of pickpocketing, lockpicking, and stowing objects. Magical Ambush at 9th imposes disadvantage on spell saves when you're hidden. Versatile Trickster at 13th lets you use your bonus action to grant advantage via mage hand. Spell Thief at 17th lets you steal a spell from an enemy and cast it yourself."}, "weapons": "Simple weapons, Hand crossbows, Longswords, Rapiers, Shortswords", "armor": "Light armor", "tools": "Thieves' tools", "source": "PHB 2014 p.94"},
    "Sorcerer": {"hd": 6, "skills": ["Arcana","Deception","Insight","Intimidation","Persuasion","Religion"], "skill_count": 2, "saves": ["constitution","charisma"], "subclasses": ["Draconic Bloodline","Wild Magic"], "armor": [], "desc": "Sorcerers are born with magic in their blood — not learned, not granted, but innate. The source of their power might be a draconic ancestor, an encounter with a being of raw chaos, or some cosmic event that awakened latent potential. Unlike wizards who study dusty tomes, sorcerers wield magic by instinct and force of personality. They don't prepare spells; they know a smaller, carefully chosen repertoire and can bend those spells in ways no other caster can.\n\nA sorcerer's defining feature is Metamagic — the ability to reshape spells on the fly using Sorcery Points. They can Twin a spell to hit two targets, Quicken a spell to cast as a bonus action, Subtle a spell to cast without components, Heighten a spell to impose disadvantage on saves, and more. Sorcery Points can also be converted into spell slots and vice versa — sorcerers have more flexibility with their spell slots than any other full caster.\n\nMechanically, sorcerers are d6 hit die full casters who use Charisma as their spellcasting ability. They know a limited number of spells but can cast them flexibly. Font of Magic at 2nd creates their Sorcery Point pool. At 20th, Sorcerous Restoration recovers 4 Sorcery Points on short rests. Sorcerers are the most specialized full casters — a Draconic sorcerer is a durable elemental blaster, while a Wild Magic sorcerer is an unpredictable chaos engine that can grant advantage at will.", "subclass_descs": {"Draconic Bloodline": "At 1st level, choose a dragon color (determining your damage affinity). Draconic Resilience grants +1 HP per sorcerer level and natural AC of 13 + DEX. At 6th, Elemental Affinity adds CHA to spell damage of your chosen element and grants resistance to that element for 1 hour per Sorcery Point. Dragon Wings at 14th grant a flying speed of your walking speed. Draconic Presence at 18th frightens or charms creatures within 60 ft.", "Wild Magic": "At 1st level, Wild Magic Surge: after casting a non-cantrip spell, the DM can have you roll a d20 — on a 1, roll on the Wild Magic table (50 random effects from self-fireball to feather beard to flumph summoning). Tides of Chaos grants advantage on any d20 roll, recharging after your next surge. Bend Luck at 6th spends 2 Sorcery Points to add or subtract 1d4 from any creature's roll as a reaction. Controlled Chaos at 14th lets you roll twice on the Wild Magic table and choose. Spell Bombardment at 18th lets you reroll damage dice on spells and take the higher."}, "weapons": "Daggers, Darts, Slings, Quarterstaffs, Light crossbows", "armor": "", "tools": "", "source": "PHB 2014 p.99"},
    "Warlock": {"hd": 8, "skills": ["Arcana","Deception","History","Intimidation","Investigation","Nature","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["The Archfey","The Fiend","The Great Old One"], "desc": "Warlocks are seekers of forbidden knowledge who have struck a bargain with an otherworldly patron — a being of immense power, neither wholly benevolent nor entirely malevolent. The patron might be an ancient fey lord, a demon prince of the Abyss, or a slumbering entity from beyond the stars. In exchange for power, the warlock serves their patron's interests — or at least, they pay lip service while pursuing their own goals.\n\nWarlocks use Pact Magic — a unique spellcasting system with very few spell slots (starting at 1, maxing at 4) that all cast at the same level (scaling up to 5th) and recharge on short rests. This means warlocks can cast their most powerful spells every fight but must be judicious about when to use them. To compensate for limited slots, warlocks rely on Eldritch Blast — the best damaging cantrip in the game, which they can enhance with Eldritch Invocations to add Charisma to damage, push enemies, or pull them closer.\n\nMechanically, warlocks are d8 hit die full casters who use Charisma. At 3rd level they choose a Pact Boon: Pact of the Chain (improved familiar), Pact of the Blade (summonable magic weapon), or Pact of the Tome (Book of Shadows with extra cantrips). Eldritch Invocations at 2nd and beyond grant permanent abilities — agonizing blast, devil's sight, mask of many faces, and more. Mystic Arcanum at 11th+ grants one 6th, 7th, 8th, and 9th level spell per long rest. Warlocks are the most modular class — no two are built the same.", "subclass_descs": {"The Archfey": "At 1st level, Fey Presence (once per short rest) charms or frightens creatures in a 10-ft cube. Misty Escape at 6th lets you teleport 60 ft and turn invisible until your next turn when you take damage. Beguiling Defenses at 10th grants immunity to charm and reflects charm attempts back at the source. Dark Delirium at 14th sends a creature into an illusory nightmare realm where it is charmed or frightened of you.", "The Fiend": "At 1st level, Dark One's Blessing grants temporary HP equal to CHA + warlock level when you reduce a hostile creature to 0 HP. Dark One's Own Luck at 6th adds 1d10 to an ability check or saving throw once per short rest. Fiendish Resilience at 10th grants resistance to one damage type (changeable on short rest). Hurl Through Hell at 14th sends a creature on a psychic journey through the lower planes — they return at the end of your next turn, taking 10d10 psychic damage.", "The Great Old One": "At 1st level, Awakened Mind grants two-way telepathy with any creature within 30 ft that understands a language. Entropic Ward at 6th imposes disadvantage on an attacker's roll as a reaction — if they miss, you gain advantage on your next attack against them. Thought Shield at 10th grants resistance to psychic damage and prevents your thoughts from being read. Create Thrall at 14th lets you permanently charm a humanoid touched while incapacitated."}, "weapons": "Simple weapons", "armor": "Light armor", "tools": "", "source": "PHB 2014 p.105"},
    "Wizard": {"hd": 6, "skills": ["Arcana","History","Insight","Investigation","Medicine","Religion"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["School of Abjuration","School of Conjuration","School of Divination","School of Enchantment","School of Evocation","School of Illusion","School of Necromancy","School of Transmutation"], "armor": [], "desc": "Wizards are the ultimate students of magic — scholars who have spent years, decades, or centuries poring over ancient tomes, deciphering arcane formulae, and mastering the fundamental laws of reality. Their power comes from intellect and discipline, not bloodline or pact. A wizard's spellbook is their most prized possession, a growing encyclopedia of magical knowledge that represents years of research and discovery.\n\nWizards have the largest spell list in the game and the unique ability to learn new spells by copying scrolls or spellbooks into their own — for a cost in gold and time, a wizard can theoretically learn every wizard spell in existence. They prepare spells daily from their spellbook, choosing a flexible loadout tailored to the challenges ahead. Arcane Recovery at 1st level lets them recover spell slots on short rests. Ritual Casting allows them to cast any ritual spell in their spellbook without preparing it or expending a slot.\n\nMechanically, wizards are d6 hit die full casters who use Intelligence — the ultimate utility and control casters. They gain no armor proficiency and have the smallest hit die, so positioning and defensive spells are critical. At higher levels, they gain Spell Mastery (at-will 1st and 2nd level spells at 18th) and Signature Spells (two 3rd-level spells always prepared at 20th). Their Arcane Tradition (school specialization) at 2nd level dramatically shapes their role: blaster (Evocation), controller (Enchantment), defender (Abjuration), or summoner (Conjuration).", "subclass_descs": {"School of Abjuration": "At 2nd level, Arcane Ward creates a magical HP buffer equal to wizard level × 2 + INT mod, recharged by casting abjuration spells. Projected Ward at 6th lets you extend your ward to protect allies. Improved Abjuration at 10th adds proficiency to ability checks when casting abjuration spells (like counterspell and dispel magic). Spell Resistance at 14th grants advantage on all saving throws against spells and resistance to spell damage.", "School of Conjuration": "At 2nd level, Minor Conjuration creates a nonmagical object (max 10 lbs, 3 ft per side) that glows faintly and lasts 1 hour. Benign Transposition at 6th teleports you 30 ft or swaps places with a willing ally — recharges when you cast a conjuration spell. Focused Conjuration at 10th prevents concentration from being broken by damage on conjuration spells. Durable Summons at 14th grants 30 temporary HP to any creature you summon.", "School of Divination": "At 2nd level, Portent: after each long rest, roll two d20s and record the results. Before any creature you can see makes an attack roll, saving throw, or ability check, you can replace their roll with one of your portent dice. Expert Divination at 6th recovers a lower-level spell slot when you cast a divination spell. The Third Eye at 10th grants darkvision, ethereal sight, greater comprehension, or see invisibility. Greater Portent at 14th adds a third portent die.", "School of Enchantment": "At 2nd level, Hypnotic Gaze incapacitates a creature within 5 ft until your next turn. Instinctive Charm at 6th redirects an attack against you to the nearest creature as a reaction. Split Enchantment at 10th lets you target two creatures with any enchantment spell that normally targets one. Alter Memories at 14th makes a charmed creature unaware of being charmed and forgets some of the time spent charmed.", "School of Evocation": "At 2nd level, Sculpt Spells lets you designate 1 + spell level creatures to automatically succeed on saves and take no damage from your evocation spells. Potent Cantrip at 6th makes your cantrips deal half damage on successful saves. Empowered Evocation at 10th adds INT mod to evocation spell damage. Overchannel at 14th maximizes the damage of a 5th-level-or-lower evocation spell — at the cost of necrotic damage to yourself on repeat uses.", "School of Illusion": "At 2nd level, Improved Minor Illusion creates both sound and image with one casting. Malleable Illusions at 6th lets you reshape ongoing illusion spells as an action. Illusory Self at 10th creates an illusory double that intercepts an attack, making it miss — recharges on short rest. Illusory Reality at 14th makes one inanimate object in your illusion temporarily real — a bridge that can be crossed, a wall that blocks attacks, a cage that holds prisoners.", "School of Necromancy": "At 2nd level, Grim Harvest heals you for 2 × spell level (or 3 × for necromancy spells) when you kill a creature with a spell. Undead Thralls at 6th lets you animate more undead and gives them extra HP and your proficiency bonus to damage. Inured to Undeath at 10th grants resistance to necrotic damage and prevents your HP maximum from being reduced. Command Undead at 14th lets you permanently control any undead that fails an INT save — even a mummy lord or a lich, if you're lucky.", "School of Transmutation": "At 2nd level, Minor Alchemy temporarily transforms wood, stone, iron, copper, or silver into another of those materials for 1 hour per 10 minutes spent. Transmuter's Stone at 6th creates a stone that grants you or a holder a buff: darkvision, speed +10 ft, proficiency in CON saves, or resistance to one element. Shapechanger at 10th lets you cast polymorph on yourself once per short rest (CR 1 or lower). Master Transmuter at 14th lets you destroy your stone to raise dead, de-age, restore youth, or cure all diseases."}, "weapons": "Daggers, Darts, Slings, Quarterstaffs, Light crossbows", "armor": "", "tools": "", "source": "PHB 2014 p.112"},
    # ── AiME class stubs (Adventures in Middle-earth) ──
    "Slayer": {"hd": 12, "skills": ["Animal Handling","Athletics","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": [], "armor": ['Light armor', 'Medium armor', 'Shields'], "subclass_descs": {}, "desc": "The Slayer fights with raw ferocity — equivalent to a Barbarian.", "weapons": "All", "armor": "Light, medium, shields", "tools": "", "source": "Adventures in Middle-earth Player's Guide"},
    "Warden": {"hd": 10, "skills": ["Athletics","Insight","Investigation","Medicine","Persuasion","Survival"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": [], "armor": ['All armor', 'Shields'], "subclass_descs": {}, "desc": "The Warden is a stalwart defender and leader — akin to a Paladin.", "weapons": "All", "armor": "All, shields", "tools": "", "source": "Adventures in Middle-earth Player's Guide"},
    "Warrior": {"hd": 10, "skills": ["Acrobatics","Animal Handling","Athletics","History","Insight","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": [], "armor": ['All armor', 'Shields'], "subclass_descs": {}, "desc": "The Warrior is a master of arms — equivalent to a Fighter.", "weapons": "All", "armor": "All, shields", "tools": "", "source": "Adventures in Middle-earth Player's Guide"},
    "Scholar": {"hd": 6, "skills": ["Arcana","History","Insight","Investigation","Medicine","Nature","Religion"], "skill_count": 3, "saves": ["intelligence","wisdom"], "subclasses": [], "armor": ['Light armor'], "subclass_descs": {}, "desc": "The Scholar masters ancient lore and healing — akin to a Cleric.", "weapons": "Simple", "armor": "Light", "tools": "", "source": "Adventures in Middle-earth Player's Guide"},
    "Treasure Hunter": {"hd": 8, "skills": ["Acrobatics","Deception","Insight","Investigation","Perception","Sleight of Hand","Stealth"], "skill_count": 4, "saves": ["dexterity","intelligence"], "subclasses": [], "armor": ['Light armor'], "subclass_descs": {}, "desc": "The Treasure Hunter is a cunning explorer — equivalent to a Rogue.", "weapons": "Simple, hand crossbows, longswords, rapiers, shortswords", "armor": "Light", "tools": "Thieves' tools", "source": "Adventures in Middle-earth Player's Guide"},
    "Wanderer": {"hd": 10, "skills": ["Animal Handling","Athletics","Insight","Investigation","Nature","Perception","Stealth","Survival"], "skill_count": 3, "saves": ["strength","dexterity"], "subclasses": [], "armor": ['Light armor', 'Medium armor', 'Shields'], "subclass_descs": {}, "desc": "The Wanderer is a wilderness expert — equivalent to a Ranger.", "weapons": "All", "armor": "Light, medium, shields", "tools": "", "source": "Adventures in Middle-earth Player's Guide"},
}

# Tag hardcoded classes with source
_class_page_map: dict[str, str] = {}
try:
    _cpm_path = DATA_DIR / "class_page_map.json"
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
    _srpm_path = DATA_DIR / "subrace_page_map.json"
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
    _tpm_path = DATA_DIR / "trait_page_map.json"
    if _tpm_path.exists():
        with open(_tpm_path) as _f:
            _trait_page_map = json.load(_f)
        print(f"  Trait sources loaded: {len(_trait_page_map)}")
except Exception as _e:
    print(f"  (trait page map unavailable: {_e})")

SKILL_ABILITIES = {
    "Acrobatics":"dexterity","Animal Handling":"wisdom","Arcana":"intelligence",
    "Athletics":"strength","Deception":"charisma","History":"intelligence",
    "Insight":"wisdom","Intimidation":"charisma","Investigation":"intelligence",
    "Medicine":"wisdom","Nature":"intelligence","Perception":"wisdom",
    "Performance":"charisma","Persuasion":"charisma","Religion":"intelligence",
    "Sleight of Hand":"dexterity","Stealth":"dexterity","Survival":"wisdom",
}

ALL_SKILLS = sorted(SKILL_ABILITIES.keys())

# PHB standard languages (p.123)
LANGUAGES = ["Common", "Dwarvish", "Elvish", "Giant", "Gnomish", "Goblin", "Halfling", "Orc",
             "Abyssal", "Celestial", "Draconic", "Deep Speech", "Infernal", "Primordial", "Sylvan", "Undercommon"]

BACKGROUNDS = ["Acolyte","Charlatan","Criminal","Entertainer","Folk Hero","Guild Artisan","Hermit","Noble","Outlander","Sage","Sailor","Soldier","Urchin","Custom"]
BACKGROUND_INFO = {
    "Acolyte":       "You served in a temple. Skill Proficiencies: Insight, Religion. Languages: Two of your choice. Equipment: Holy symbol, prayer book, 5 sticks of incense, vestments, common clothes, 15 gp. Feature: Shelter of the Faithful.",
    "Charlatan":     "You've made a living by your wits. Skill Proficiencies: Deception, Sleight of Hand. Tool Proficiencies: Disguise kit, forgery kit. Equipment: Fine clothes, disguise kit, tools of the con, 15 gp. Feature: False Identity.",
    "Criminal":      "You are an experienced criminal. Skill Proficiencies: Deception, Stealth. Tool Proficiencies: One gaming set, thieves' tools. Equipment: Crowbar, dark clothes, 15 gp. Feature: Criminal Contact.",
    "Entertainer":   "You thrive before an audience. Skill Proficiencies: Acrobatics, Performance. Tool Proficiencies: Disguise kit, one musical instrument. Equipment: Musical instrument, costume, 15 gp. Feature: By Popular Demand.",
    "Folk Hero":     "You come from a humble social rank. Skill Proficiencies: Animal Handling, Survival. Tool Proficiencies: One artisan's tools, land vehicles. Equipment: Artisan's tools, shovel, iron pot, common clothes, 10 gp. Feature: Rustic Hospitality.",
    "Guild Artisan": "You are a member of an artisan's guild. Skill Proficiencies: Insight, Persuasion. Tool Proficiencies: One artisan's tools. Languages: One of your choice. Equipment: Artisan's tools, letter of introduction, traveler's clothes, 15 gp. Feature: Guild Membership.",
    "Hermit":        "You lived in seclusion. Skill Proficiencies: Medicine, Religion. Tool Proficiencies: Herbalism kit. Equipment: Scroll case of notes, winter blanket, common clothes, herbalism kit, 5 gp. Feature: Discovery.",
    "Noble":         "You were born into wealth and power. Skill Proficiencies: History, Persuasion. Tool Proficiencies: One gaming set. Languages: One of your choice. Equipment: Fine clothes, signet ring, scroll of pedigree, 25 gp. Feature: Position of Privilege.",
    "Outlander":     "You grew up in the wilds. Skill Proficiencies: Athletics, Survival. Tool Proficiencies: One musical instrument. Languages: One of your choice. Equipment: Staff, hunting trap, trophy, traveler's clothes, 10 gp. Feature: Wanderer.",
    "Sage":          "You spent years learning lore. Skill Proficiencies: Arcana, History. Languages: Two of your choice. Equipment: Black ink, quill, small knife, letter from dead colleague, common clothes, 10 gp. Feature: Researcher.",
    "Sailor":        "You have sailed the high seas. Skill Proficiencies: Athletics, Perception. Tool Proficiencies: Navigator's tools, water vehicles. Equipment: Belaying pin, 50 ft silk rope, lucky charm, common clothes, 10 gp. Feature: Ship's Passage.",
    "Soldier":       "You served in a military force. Skill Proficiencies: Athletics, Intimidation. Tool Proficiencies: One gaming set, land vehicles. Equipment: Insignia of rank, trophy, bone dice, common clothes, 10 gp. Feature: Military Rank.",
    "Urchin":        "You grew up on the streets alone. Skill Proficiencies: Sleight of Hand, Stealth. Tool Proficiencies: Disguise kit, thieves' tools. Equipment: Small knife, city map, pet mouse, token of parents, common clothes, 10 gp. Feature: City Secrets.",
    "Custom":        "Define your own background. Equipment: 3 useful items of your choice, traveler's clothes, 10 gp. Feature: Your own unique story.",
}
BACKGROUND_SOURCES = {bg: "Player's Handbook p.125-141" for bg in BACKGROUNDS if bg != "Custom"}
BACKGROUND_SOURCES["Custom"] = ""

# ── Enrich background sources with exact pages ──
_background_page_map: dict[str, str] = {}
try:
    _bgpm_path = DATA_DIR / "background_page_map.json"
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

ALIGNMENTS = ["Lawful Good","Neutral Good","Chaotic Good","Lawful Neutral","True Neutral","Chaotic Neutral","Lawful Evil","Neutral Evil","Chaotic Evil"]

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

def _find_weapon(item_name: str) -> dict | None:
    """Match an inventory item name to a known SRD weapon. Fuzzy match."""
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

def _build_attack_for_weapon(item_name: str, weapon_data: dict, abilities: dict, prof_bonus: int, qty: int = 1) -> dict:
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
    attack_bonus = ab_mod + prof_bonus

    # Damage string
    if damage == "—":
        dmg_str = "Special"
    else:
        dmg_str = f"{damage} + {ab_mod} {dmg_type}"

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
                atk = _build_attack_for_weapon(name, wpn, abilities, prof_bonus, qty)
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
    """Convert equipped items to [{name, qty}] format. Handles old string-list format."""
    if not equipped:
        return []
    result = []
    for item in equipped:
        if isinstance(item, dict):
            result.append({"name": item.get("name", ""), "qty": item.get("qty", 1)})
        else:
            result.append({"name": str(item), "qty": 1})
    return result

def _equipped_names(equipped: list) -> list:
    """Extract just the names from a [{name, qty}] equipped list or string-list."""
    result = []
    for e in (equipped or []):
        if isinstance(e, dict) and e.get("name"):
            result.append(e["name"])
        elif isinstance(e, str):
            result.append(e)
    return result

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


# ── Routes: Auth ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", 303)
    return _render("landing.html", request=request)

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return _render("register.html", request=request)

@app.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    if email.lower().strip() == "admin":
        return _render("register.html", request=request, error="That email is unavailable")
    if len(password) < 6:
        return _render("register.html", request=request, error="Password must be at least 6 characters")
    db = get_db()
    try:
        db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                   (email.lower().strip(), _hash(password)))
        db.commit()
        user = _get_user(email.lower().strip())
        token = _create_session(user["id"])
        resp = RedirectResponse("/dashboard", 303)
        resp.set_cookie("dnd_token", token, httponly=True, max_age=60*60*24*30, samesite="lax")
        return resp
    except sqlite3.IntegrityError:
        return _render("register.html", request=request, error="Email already registered")
    finally:
        db.close()

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render("login.html", request=request)

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = _get_user(email.lower().strip())
    if not user or not _verify(password, user["password_hash"]):
        return _render("login.html", request=request, error="Invalid email or password")
    token = _create_session(user["id"])
    resp = RedirectResponse("/dashboard", 303)
    resp.set_cookie("dnd_token", token, httponly=True, max_age=60*60*24*30, samesite="lax")
    return resp

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("dnd_token")
    if token:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
        db.close()
    resp = RedirectResponse("/", 303)
    resp.delete_cookie("dnd_token")
    return resp

# ── Routes: Dashboard ───────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    chars = [dict(r) for r in db.execute(
        f"SELECT * FROM characters {where} ORDER BY created_at DESC", params
    ).fetchall()]
    db.close()
    for c in chars:
        for f in ("skills","features","inventory","equipped","languages"):
            try:
                c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError):
                c[f] = []
    return _render("dashboard.html", request=request, characters=chars)

# ── Routes: Character Creation Wizard ──────────────────────────────────────

@app.get("/create", response_class=HTMLResponse)
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
        infusion_options=INFUSION_OPTIONS, infusion_levels=INFUSION_LEVELS, infusion_picks=INFUSION_PICKS,
        source_map_json=json.dumps(_get_source_slug_map()))

@app.post("/api/character/create", response_class=JSONResponse)
async def api_create_character(request: Request):
    user = require_user(request)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

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
        return JSONResponse({"error": "Name, race, and class required"}, status_code=400)

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
    # Append racial limited-use features (e.g. Dragonborn Breath Weapon)
    if race_name == "Dragonborn" and data.get("dragonborn_ancestry"):
        build_features.append(f"{level}: Breath Weapon")
    enriched = enrich_features(build_features, class_name=class_name, level=level, mods={a: (stats[a] - 10) // 2 for a in stats}, subclass=subclass)
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
        metamagic, metamagic_history, invocations, pact_boon, maneuvers, magical_secrets, totem_spirits, hunters_prey, infusions)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], name, race_name, subrace, class_name, subclass, level,
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
    return JSONResponse({"id": char_id, "name": name})

# ── Starting spells lookup (no character needed — creation wizard) ──────────

@app.get("/api/spells/starting", response_class=JSONResponse)
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
            "description": " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else "",
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

MANUAL_MONSTERS: list[dict] = []
MANUAL_TRAPS: list[dict] = []

def _load_monster_cache() -> list[dict]:
    global MANUAL_MONSTERS
    base = _load_json_cache("monsters.json")
    # Load monster→page map for source badges
    _monster_page_map: dict[str, int] = {}
    try:
        _mpm_path = DATA_DIR / "monster_page_map.json"
        if _mpm_path.exists():
            with open(_mpm_path) as _f:
                _monster_page_map = json.load(_f)
    except Exception:
        pass
    # Tag SRD monsters with source — all SRD monsters are from the Monster Manual
    # Also tag mounts and vehicles so encounter builder can filter them
    _MOUNT_NAMES = {
        'Riding Horse', 'Draft Horse', 'Warhorse', 'Pony', 'Camel', 'Mastiff', 'Mule', 'Elephant',
        'Griffon', 'Hippogriff', 'Pegasus', 'Nightmare', 'Unicorn', 'Wyvern',
        'Giant Eagle', 'Giant Owl', 'Giant Vulture', 'Giant Bat', 'Giant Elk',
        'Giant Goat', 'Giant Lizard', 'Giant Sea Horse', 'Giant Weasel',
        'Worg', 'Winter Wolf', 'Dire Wolf', 'Axe Beak', 'Roc',
        'Saber-Toothed Tiger', 'Mammoth', 'Dragon Turtle',
        'Warhorse Skeleton', 'Sea Horse',
    }
    _VEHICLE_NAMES = {'Animated Armor', 'Flying Sword', 'Rug of Smothering'}
    for m in base:
        name = m.get("name", "")
        # Source badge
        if "source" not in m:
            page = _monster_page_map.get(name)
            if page:
                m["source"] = f"Monster Manual p.{page}"
            else:
                m["source"] = "Monster Manual"
        # Tags
        tags = m.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        if name in _MOUNT_NAMES:
            tags.append("mount")
        if name in _VEHICLE_NAMES:
            tags.append("vehicle")
        if tags:
            m["tags"] = tags
    if not MANUAL_MONSTERS:
        manual = _load_manual_json("monsters.json")
        # Normalize manual monster format to SRD format
        for m in manual:
            _normalize_manual_monster(m)
        MANUAL_MONSTERS = manual
    return base + MANUAL_MONSTERS


def _normalize_manual_monster(m: dict):
    """Normalize a manually-extracted monster to match SRD cache format."""
    # Generate index from name
    if not m.get("index"):
        m["index"] = re.sub(r"[^a-z0-9]+", "-", m.get("name", "").lower().strip()).strip("-")

    # Fix broken sources that just say "Chapter N" without the book name
    src = m.get("source", "")
    if src:
        src_lower = src.lower()
        # Mordenkainen's Tome of Foes Chapter 6 Bestiary
        if "chapter 6" in src_lower and "bestiary" in src_lower:
            page = re.search(r"p\.?\s*(\d+)", src)
            if page:
                m["source"] = f"Mordenkainen's Tome of Foes p.{page.group(1)}"
            else:
                m["source"] = "Mordenkainen's Tome of Foes"
        # DMG Chapter 4
        elif "chapter 4" in src_lower and "dungeon master" in src_lower:
            page = re.search(r"p\.?\s*(\d+)", src)
            if page:
                m["source"] = f"Dungeon Master's Guide p.{page.group(1)}"
            else:
                m["source"] = "Dungeon Master's Guide"
        # DMG Chapter 7 Treasure
        elif "chapter 7" in src_lower and "treasure" in src_lower:
            m["source"] = "Dungeon Master's Guide"
        # Generic Chapter patterns — try to infer from context
        elif "chapter 3" in src_lower and "magical" in src_lower:
            m["source"] = "Dungeon Master's Guide"

    # armor_class: int → [{value: int, type: "natural"}]
    ac = m.get("armor_class")
    if isinstance(ac, (int, float)):
        m["armor_class"] = [{"value": int(ac), "type": "natural"}]
    elif isinstance(ac, str):
        try:
            m["armor_class"] = [{"value": int(ac), "type": "natural"}]
        except ValueError:
            m["armor_class"] = [{"value": 10, "type": "natural"}]

    # hit_points: str "75 (10d10 + 20)" → int 75, extract dice
    hp = m.get("hit_points")
    if isinstance(hp, str):
        match = re.match(r"(\d+)", hp)
        if match:
            m["hit_points"] = int(match.group(1))
        # Extract hit dice from parenthetical: "45 (10d8)" → "10d8"
        dice_match = re.search(r"\((\d+d\d+[^)]*)\)", hp)
        if dice_match and not m.get("hit_dice"):
            m["hit_dice"] = dice_match.group(1)

    # challenge_rating: normalize to float
    cr = m.get("challenge_rating")
    if cr is None:
        m["challenge_rating"] = 0
    elif isinstance(cr, (int, float)):
        m["challenge_rating"] = float(cr)
    elif isinstance(cr, str):
        cr_str = cr.strip()
        if not cr_str:
            m["challenge_rating"] = 0
        elif "/" in cr_str:
            try:
                parts = cr_str.split("/")
                m["challenge_rating"] = float(parts[0]) / float(parts[1])
            except (ValueError, ZeroDivisionError):
                m["challenge_rating"] = 0
        else:
            try:
                m["challenge_rating"] = float(cr_str)
            except ValueError:
                m["challenge_rating"] = 0
    elif isinstance(cr, dict):
        cr_val = cr.get("cr") or cr.get("challenge_rating") or cr.get("value") or 0
        try:
            m["challenge_rating"] = float(cr_val)
        except (ValueError, TypeError):
            m["challenge_rating"] = 0

    # Compute proficiency bonus from CR
    cr_val = m.get("challenge_rating", 0)
    if not m.get("proficiency_bonus"):
        m["proficiency_bonus"] = max(2, 2 + int((cr_val - 1) / 4))

    # Ensure required SRD fields exist with defaults
    m.setdefault("type", "humanoid")
    m.setdefault("size", "Medium")
    m.setdefault("alignment", "unaligned")

    # Speed: normalize string "0 ft., fly 40 ft." → dict
    speed = m.get("speed")
    if isinstance(speed, str):
        speed_dict = {}
        for part in speed.split(","):
            part = part.strip()
            sp_match = re.match(r"(\d+)\s*ft\.?\s*(.*)", part)
            if sp_match:
                val = int(sp_match.group(1))
                label = sp_match.group(2).strip().lower()
                # Strip parenthetical annotations like "(hover)"
                label = re.sub(r"\(.*\)", "", label).strip()
                if label in ("fly", "flying"):
                    speed_dict["fly"] = f"{val} ft."
                    if "hover" in sp_match.group(2).lower():
                        speed_dict["fly"] += " (hover)"
                elif label in ("swim", "swimming"):
                    speed_dict["swim"] = f"{val} ft."
                elif label in ("burrow", "burrowing"):
                    speed_dict["burrow"] = f"{val} ft."
                elif label in ("climb", "climbing"):
                    speed_dict["climb"] = f"{val} ft."
                else:
                    speed_dict["walk"] = f"{val} ft."
        if speed_dict:
            m["speed"] = speed_dict
        else:
            m["speed"] = {"walk": "30 ft."}
    elif not isinstance(speed, dict):
        m["speed"] = {"walk": "30 ft."}

    m.setdefault("actions", [])
    m.setdefault("special_abilities", [])
    m.setdefault("senses", {})
    m.setdefault("languages", "")
    m.setdefault("damage_vulnerabilities", [])
    m.setdefault("damage_resistances", [])
    m.setdefault("damage_immunities", [])
    m.setdefault("condition_immunities", [])
    m.setdefault("proficiencies", [])
    m.setdefault("legendary_actions", [])

    # senses: string "darkvision 60 ft., passive Perception 11" → dict
    senses = m.get("senses")
    if isinstance(senses, str) and senses:
        sense_dict = {}
        for part in senses.split(","):
            part = part.strip()
            if "passive" in part.lower():
                m["passive_perception"] = int(re.search(r"(\d+)", part).group(1)) if re.search(r"(\d+)", part) else 10
            else:
                match = re.match(r"(\w[\w\s]*?)\s+(\d+)\s*ft\.?", part)
                if match:
                    key = match.group(1).strip().lower().replace(" ", "_")
                    sense_dict[key] = f"{match.group(2)} ft."
        if sense_dict:
            m["senses"] = sense_dict
        elif not sense_dict:
            m["senses"] = {}
    elif not isinstance(senses, dict):
        m["senses"] = {}

    # actions: rename description → desc (SRD format)
    for action in m.get("actions", []):
        if "description" in action and "desc" not in action:
            action["desc"] = action.pop("description")
        # Normalize damage format if present
        damage = action.get("damage")
        if isinstance(damage, str):
            action["damage"] = [{"damage_dice": damage, "damage_type": {"name": "bludgeoning"}}]

    # features → special_abilities: rename description → desc
    for feat in m.get("features", []):
        if "description" in feat and "desc" not in feat:
            feat["desc"] = feat.pop("description")
    if m.get("features") and not m.get("special_abilities"):
        m["special_abilities"] = m.pop("features")
    elif m.get("features"):
        m["special_abilities"].extend(m.pop("features"))

    # reactions
    for rxn in m.get("reactions", []):
        if "description" in rxn and "desc" not in rxn:
            rxn["desc"] = rxn.pop("description")

    # legendary_actions
    for la in m.get("legendary_actions", []):
        if "description" in la and "desc" not in la:
            la["desc"] = la.pop("description")

    # ability_scores: flatten to top-level strength/dex/con/int/wis/cha
    scores = m.get("ability_scores", {})
    if scores:
        stat_map = {
            "str": "strength", "strength": "strength",
            "dex": "dexterity", "dexterity": "dexterity",
            "con": "constitution", "constitution": "constitution",
            "int": "intelligence", "intelligence": "intelligence",
            "wis": "wisdom", "wisdom": "wisdom",
            "cha": "charisma", "charisma": "charisma",
        }
        for k, v in scores.items():
            target = stat_map.get(k.lower().strip())
            if target and target not in m:
                m[target] = int(v) if isinstance(v, (int, float, str)) and str(v).replace("-", "").isdigit() else 10
    # Ensure all six stats exist
    for stat in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        m.setdefault(stat, 10)

    # hit_dice fallback
    m.setdefault("hit_dice", "1d8")

    # xp from CR if missing
    if not m.get("xp"):
        cr_val = m.get("challenge_rating", 0)
        m["xp"] = _xp_for_cr(cr_val)

    # Enrich source from _source_manual + pdf_map for manual monsters
    source = m.get("source", "")
    if (not source or "Unknown page" in str(source)) and m.get("_source_manual"):
        slug = m["_source_manual"]
        manual_meta = _load_manual_json("_meta.json")
        pdf_map = manual_meta.get("pdf_map", {}) if isinstance(manual_meta, dict) else {}
        book_info = pdf_map.get(slug, {})
        if book_info:
            title = book_info.get("title", slug)
            # Strip "D&D 5E - " prefix for cleaner display
            title = re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
            m["source"] = title
        else:
            m["source"] = slug  # Fallback to the slug itself
    elif not m.get("source"):
        m["source"] = ""

def _monster_cr_sort_key(m: dict) -> float:
    cr = m.get("challenge_rating", 0)
    if isinstance(cr, dict):
        cr = cr.get("challenge_rating", cr)
    try:
        return float(cr)
    except (TypeError, ValueError):
        return 99.0

def _xp_for_cr(cr) -> int:
    """Return XP reward for a challenge rating (PHB p.274 / DMG p.275)."""
    table = {0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700,
             4: 1100, 5: 1800, 6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900,
             11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000, 16: 15000,
             17: 18000, 18: 20000, 19: 22000, 20: 25000, 21: 33000, 22: 41000,
             23: 50000, 24: 62000, 25: 75000, 26: 90000, 27: 105000, 28: 120000,
             29: 135000, 30: 155000}
    try:
        return table.get(float(cr), 0)
    except (TypeError, ValueError):
        return 0


def _encounter_mult(count):
    """DMG p.83 encounter multiplier based on monster count."""
    if count == 1: return 1.0
    elif count == 2: return 1.5
    elif count <= 6: return 2.0
    elif count <= 10: return 2.5
    elif count <= 14: return 3.0
    return 4.0


def _assign_encounter_counts(picks, xp_budget, encounter_type="skirmish"):
    """Assign monster counts to hit XP budget, accounting for DMG p.83 multiplier.
    AI picks which monsters; this function does the math.
    Aggressively fills to ≥85% of adjusted XP budget.
    Capped at MAX_CREATURES total (default 12).
    Returns (composition, raw_xp)."""
    MAX_CREATURES = 12  # raised from 10
    if not picks:
        return [], 0

    def _total():
        return sum(c["count"] for c in composition)

    role_order = {"boss": 0, "elite": 1, "minion": 2, "faction_a": 0, "faction_b": 0}
    sorted_picks = sorted(picks, key=lambda p: (role_order.get(p.get("role", "minion"), 2), -p["cr"]))

    composition = []
    raw_xp = 0

    # Swarm: compute exact counts upfront to fill budget
    if encounter_type == "swarm":
        # For swarm, use ONE creature type repeated to hit budget
        # Try each pick, compute how many needed for ~95% budget
        best = None
        best_score = float('inf')
        best_count = 0
        for m in sorted_picks:
            xp_each = m["xp"]
            if xp_each <= 0:
                continue
            for count in range(3, MAX_CREATURES + 1):
                raw = xp_each * count
                mult = _encounter_mult(count)
                adj = raw * mult
                score = abs(adj - xp_budget)
                if score < best_score:
                    best_score = score
                    best = m
                    best_count = count
        if best:
            composition.append({**best, "count": best_count})
            raw_xp = best["xp"] * best_count
        return composition, raw_xp

    # Solo lair: boss only + guards if needed
    if encounter_type == "solo_lair":
        boss = sorted_picks[0]
        composition.append({**boss, "count": 1})
        raw_xp += boss["xp"]
        # Add guards if undershooting budget
        if raw_xp < xp_budget * 0.65 and len(sorted_picks) > 1 and _total() < MAX_CREATURES:
            guard = sorted_picks[1]
            # Fill remaining budget with guards
            remaining_adj = xp_budget - raw_xp  # no mult for solo
            guard_xp_each = guard["xp"]
            guard_count = max(1, min(int(remaining_adj / guard_xp_each), MAX_CREATURES - _total()))
            composition.append({**guard, "count": guard_count})
            raw_xp += guard["xp"] * guard_count
        return composition, raw_xp

    # Default: boss + elites + minions (skirmish, ambush, social, rival)
    # Fill using adjusted XP directly — add minions while budget allows
    boss = sorted_picks[0]
    composition.append({**boss, "count": 1})
    raw_xp += boss["xp"]

    rest = sorted_picks[1:] if len(sorted_picks) > 1 else []

    def _adj(count, xp):
        return int(xp * _encounter_mult(count))

    if rest:
        minions = [m for m in rest if m.get("role") != "elite"] or rest
        # Add minions while adjusted XP stays within 130% of budget
        # (DMG multiplier curve makes small encounters overshoot easily)
        overshoot_cap = 1.30
        ri = 0
        while ri < 50 and _total() < MAX_CREATURES:
            m = minions[ri % len(minions)]
            trial_total = _total() + 1
            trial_xp = raw_xp + m["xp"]
            trial_adj = _adj(trial_total, trial_xp)
            if trial_adj <= xp_budget * overshoot_cap:
                existing = next((c for c in composition if c["index"] == m["index"]), None)
                if existing:
                    existing["count"] += 1
                else:
                    composition.append({**m, "count": 1})
                raw_xp += m["xp"]
            elif _total() <= 1:
                # Force at least 1 minion even if overshoot (solo boss is boring)
                composition.append({**m, "count": 1})
                raw_xp += m["xp"]
                break
            else:
                break
            ri += 1

    # Multi-pass padding: if under 80% budget, add more of cheapest creature
    total_count = _total()
    mult = _encounter_mult(total_count)
    adjusted = int(raw_xp * mult)
    budget_pct = adjusted / xp_budget if xp_budget > 0 else 0

    for _pass in range(3):  # up to 3 padding passes
        if budget_pct >= 0.85 or total_count >= MAX_CREATURES:
            break
        if not rest and not composition:
            break
        # Find cheapest available creature
        pad_pool = [c for c in composition] + (rest if rest else [])
        if not pad_pool:
            break
        cheapest = min(pad_pool, key=lambda m: m["xp"])
        # How many more can we add?
        raw_needed = int((xp_budget * 0.90 / max(_encounter_mult(_total() + 1), 1)) - raw_xp)
        extra = max(1, raw_needed // max(cheapest["xp"], 1))
        extra = min(extra, MAX_CREATURES - total_count)
        if extra <= 0:
            break
        existing = next((c for c in composition if c["index"] == cheapest["index"]), None)
        if existing:
            existing["count"] += extra
        else:
            composition.append({**cheapest, "count": extra})
        raw_xp += cheapest["xp"] * extra
        total_count = _total()
        mult = _encounter_mult(total_count)
        adjusted = int(raw_xp * mult)
        budget_pct = adjusted / xp_budget if xp_budget > 0 else 0
    # Trim if overshooting budget (>130%)
    if xp_budget > 0 and adjusted > xp_budget * 1.30:
        # Remove cheapest creatures one at a time until within 130%
        # Never trim below 2 creature types (keep boss + at least 1 minion type)
        for _ in range(50):
            if not composition:
                break
            total_count = _total()
            total_types = len(composition)
            # Never remove the LAST creature
            if total_count <= 1:
                break
            # Don't remove if down to boss alone (keep boss + 1 minion type minimum)
            if total_types <= 1:
                break
            mult = _encounter_mult(total_count - 1) if total_count > 1 else 1.0
            # Find entry with most duplicates to trim
            cheapest = min(composition, key=lambda c: c["xp"])
            if cheapest["count"] > 1:
                cheapest["count"] -= 1
                raw_xp -= cheapest["xp"]
                if cheapest["count"] == 0:
                    composition.remove(cheapest)
            else:
                raw_xp -= cheapest["xp"]
                composition.remove(cheapest)
            total_count = _total()
            mult = _encounter_mult(total_count)
            adjusted = int(raw_xp * mult)
            if adjusted <= xp_budget * 1.10:
                break

    return composition, raw_xp


def _format_monster_action(action: dict) -> dict:
    """Flatten a monster action for template display."""
    return {
        "name": action.get("name", ""),
        "desc": action.get("desc", ""),
        "attack_bonus": action.get("attack_bonus"),
        "damage": ", ".join(
            f"{d.get('damage_dice','')} {d.get('damage_type',{}).get('name','').lower()}"
            for d in action.get("damage", [])
        ) if action.get("damage") else "",
        "dc": action.get("dc", {}).get("dc_value") if action.get("dc") else None,
    }

MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
MANUAL_CACHE = DATA_DIR / "manual_cache"


def _ensure_manual_cache() -> dict[str, Path]:
    """Ensure all manual PDFs have been extracted to text cache.
    Returns {book_label: path_to_txt}.
    Uses _meta.json pdf_map to discover all ingested manuals.
    """
    MANUAL_CACHE.mkdir(parents=True, exist_ok=True)

    # ── Build book list from _meta.json pdf_map ──
    book_labels: dict[str, str] = {}  # pdf_path → label (slug)
    try:
        meta = _load_manual_json("_meta.json")
        pdf_map = (meta or {}).get("pdf_map", {}) if isinstance(meta, dict) else {}
        for slug, info in pdf_map.items():
            rel_path = info.get("path", "")
            if rel_path:
                book_labels[rel_path] = slug
    except Exception:
        pass

    # Fallback: hardcoded WotC core books (in case _meta.json is unavailable)
    if not book_labels:
        book_labels = {
            "D&D 5E - Player's Handbook.pdf": "PHB",
            "D&D 5E - Dungeon Master's Guide.pdf": "DMG",
            "D&D 5E - Monster Manual.pdf": "MM",
            "D&D 5E - Xanathar's Guide to Everything.pdf": "XGE",
            "D&D 5E - Volo's Guide to Monsters.pdf": "VGM",
            "D&D 5E - Mordenkainen's Tome of Foes.pdf": "MTF",
            "D&D 5E - Sword Coast Adventurer's Guide.pdf": "SCAG",
            "D&D 5E - Elemental Evil Player's Companion.pdf": "EEPC",
            "D&D 5E - Guildmasters' Guide to Ravnica.pdf": "GGR",
            "D&D 5E - Wayfinders Guide to Eberron.pdf": "WGE",
            "D&D 5E - The Tortle Package.pdf": "TTP",
        }

    cached = {}
    for pdf_rel, label in book_labels.items():
        pdf_path = MANUALS_DIR / pdf_rel
        txt_path = MANUAL_CACHE / f"{label}.txt"
        if pdf_path.exists():
            if not txt_path.exists() or pdf_path.stat().st_mtime > txt_path.stat().st_mtime:
                print(f"  [cache] Extracting {label}...")
                _extract_pdf(pdf_path, txt_path)
            if txt_path.exists():
                cached[label] = txt_path
    return cached


def _extract_pdf(pdf_path: Path, txt_path: Path):
    """Extract text from a PDF using pdftotext."""
    import subprocess
    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
            capture_output=True, timeout=120
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


# ── OCR fuzzy matching helpers ──────────────────────────────────────────
def _fuzzy_variants(word: str) -> list[str]:
    """Generate OCR-tolerant variants of a word for pdftotext artifacts.
    Common confusions: l↔t (lhe/the), I↔l (aI/at), i↔l, rn↔m, cl↔d."""
    variants = {word}
    # Character-level substitutions for each position
    confusions = [
        # (original, replacements) — one substitution per word max
        ("l", "t1|I"),
        ("t", "lI"),
        ("i", "l1|!"),
        ("I", "lt1"),
        ("rn", "m"),
        ("m", "rn"),
        ("cl", "d"),
        ("d", "cl"),
    ]
    for orig, reps in confusions:
        if orig in word:
            for rep in reps:
                variants.add(word.replace(orig, rep, 1))
    # Also try common multi-char: "the"<>"lhe", "th"<>"lh"
    if "th" in word:
        variants.add(word.replace("th", "lh"))
        variants.add(word.replace("th", "tn"))
    return list(variants)


def _search_manuals(query: str, max_results: int = 20) -> list[dict]:
    """Search all cached manual text files with multi-word AND, relevance
    scoring, source priority, paragraph context, and OCR-tolerant fuzzy matching.

    Returns [{book, snippet, line, page, score}].
    """
    import subprocess, re

    cached = _ensure_manual_cache()
    if not cached:
        return []

    words = [w.strip().lower() for w in query.split() if w.strip()]
    if not words:
        return []

    # ── Source priority weights ──────────────────────────────────────────
    SOURCE_WEIGHT = {
        "PHB": 1.00, "DMG": 0.95, "MM": 0.90,
        "XGE": 0.85,
        "VGM": 0.75, "MTF": 0.75,
        "SCAG": 0.70, "EEPC": 0.65,
        "GGR": 0.60, "WGE": 0.60, "TTP": 0.55,
        # Common ingested manuals
        "EBT": 0.55, "CC": 0.50, "KW": 0.45,
        "AIPG": 0.50, "LMG": 0.50, "BLRG": 0.45,
        "RRG": 0.45, "RVR": 0.45, "LMRG": 0.45,
        "EREA": 0.45, "ERIA": 0.45, "MWC": 0.45,
        "WLA": 0.45, "RGEO": 0.45,
    }
    # Human-readable book names for search results
    _book_names = _get_source_slug_map()  # returns {slug: {display, ...}}

    PROXIMITY_WINDOW = 5   # lines within this range = same paragraph
    CONTEXT_MARGIN = 3     # extra lines above/below for snippet
    FRONT_MATTER_SKIP = 100  # skip first N lines (TOC, credits, legalese)

    all_scored = []
    total_raw_hits = 0

    for label, txt_path in cached.items():
        source_w = SOURCE_WEIGHT.get(label, 0.50)

        # ── Step 1: find lines matching each word ─────────────────────
        word_lines: dict[str, set[int]] = {}
        for word in words:
            # Build OCR-tolerant -e patterns
            patterns = _fuzzy_variants(word)
            cmd = ["rg", "-i", "-n", "--no-heading"]
            for p in patterns:
                cmd.extend(["-e", p])
            cmd.append(str(txt_path))

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                lines_set: set[int] = set()
                if proc.returncode == 0:
                    for line in proc.stdout.strip().split("\n"):
                        m = re.match(r"^(\d+):", line)
                        if m:
                            ln = int(m.group(1))
                            if ln > FRONT_MATTER_SKIP:
                                lines_set.add(ln)
                word_lines[word] = lines_set
                total_raw_hits += len(lines_set)
            except subprocess.TimeoutExpired:
                word_lines[word] = set()

        # Quick skip: if any word has zero matches, no AND window possible
        if any(len(ls) == 0 for ls in word_lines.values()):
            continue

        # ── Step 2: cluster lines by proximity ────────────────────────
        all_line_nums = sorted(set().union(*word_lines.values()))
        if not all_line_nums:
            continue

        clusters: list[list[int]] = []
        cur = [all_line_nums[0]]
        for ln in all_line_nums[1:]:
            if ln - cur[-1] <= PROXIMITY_WINDOW:
                cur.append(ln)
            else:
                clusters.append(cur)
                cur = [ln]
        clusters.append(cur)

        # ── Step 3: for each cluster where ALL words appear, score ────
        book_results: list[dict] = []
        for cluster in clusters:
            # Check all words present
            presence = {}
            for w, wlines in word_lines.items():
                in_cluster = [l for l in cluster if l in wlines]
                if not in_cluster:
                    break
                presence[w] = in_cluster
            else:
                # All words found in this cluster — score it
                match_lines = sorted(set().union(*presence.values()))
                cluster_span = max(match_lines) - min(match_lines)

                # 1) Proximity: smaller span = better (max at 0 span)
                prox = 1.0 if len(match_lines) <= 1 else max(0.1, 1.0 - (cluster_span / (PROXIMITY_WINDOW * 3)))

                # 2) Density: matches per cluster line
                density = min(1.0, len(match_lines) / max(1, len(words) * 2))

                # 3) Exact-phrase bonus: full query appears verbatim?
                exact = 0.0
                try:
                    start = max(0, match_lines[0] - 3)
                    end = match_lines[-1] + 3
                    with open(txt_path) as f:
                        region_lines = []
                        for i, line in enumerate(f, 1):
                            if i > end: break
                            if i >= start:
                                region_lines.append(line)
                    region_text = " ".join(region_lines).lower()
                    if query.lower() in region_text:
                        exact = 0.35
                except Exception:
                    pass

                # 4) Composite score (0.0–1.0 scale, then × 10 for readability)
                # Proximity (35%) + Density (25%) + Exact match (30%) + Source (10%)
                raw = (prox * 0.35 + density * 0.25 + exact * 0.30 + source_w * 0.10)
                score = round(raw * 10, 2)

                # ── Extract paragraph snippet ─────────────────────────
                snippet_start = max(1, match_lines[0] - CONTEXT_MARGIN)
                snippet_end = match_lines[-1] + CONTEXT_MARGIN
                try:
                    with open(txt_path) as f:
                        para_lines = []
                        for i, line in enumerate(f, 1):
                            if i > snippet_end: break
                            if i >= snippet_start:
                                para_lines.append(line.strip())
                    snippet = " ".join(para_lines)[:400]
                except Exception:
                    snippet = " ".join(para_lines)[:400] if para_lines else ""

                est_page = max(1, match_lines[0] // 45)
                book_name = _book_names.get(label, {}).get("display", label) if _book_names else label
                book_results.append({
                    "book": label,
                    "book_name": book_name,
                    "snippet": snippet,
                    "line": match_lines[0],
                    "page": est_page,
                    "score": score,
                    "_prox": prox, "_density": density, "_exact": exact,
                })

        # ── Step 4: deduplicate within book, keep best scored ─────────
        seen_snippets = set()
        for r in sorted(book_results, key=lambda x: x["score"], reverse=True):
            key = r["snippet"][:80].strip().lower()
            if key and key not in seen_snippets:
                seen_snippets.add(key)
                all_scored.append(r)

    # ── If results are sparse, retry without AND (OR-only fallback) ────
    if len(all_scored) < 3 and len(words) > 1:
        # Fallback: merge per-word line sets, use broader windows
        for label, txt_path in cached.items():
            source_w = SOURCE_WEIGHT.get(label, 0.50)
            all_lines: set[int] = set()
            for word in words:
                patterns = _fuzzy_variants(word)
                cmd = ["rg", "-i", "-n", "--no-heading"]
                for p in patterns:
                    cmd.extend(["-e", p])
                cmd.append(str(txt_path))
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    if proc.returncode == 0:
                        for line in proc.stdout.strip().split("\n"):
                            m = re.match(r"^(\d+):", line)
                            if m:
                                ln = int(m.group(1))
                                if ln > FRONT_MATTER_SKIP:
                                    all_lines.add(ln)
                except subprocess.TimeoutExpired:
                    pass

            if not all_lines:
                continue

            # Deduplicate nearby lines — take best representative per region
            sorted_lines = sorted(all_lines)
            regions = []
            cur_region = [sorted_lines[0]]
            for ln in sorted_lines[1:]:
                if ln - cur_region[-1] <= PROXIMITY_WINDOW * 2:
                    cur_region.append(ln)
                else:
                    regions.append(cur_region)
                    cur_region = [ln]
            regions.append(cur_region)

            for region in regions[:5]:  # max 5 per book in fallback
                center = region[len(region)//2]
                est_page = max(1, center // 45)
                start = max(1, center - 2)
                end = center + 3
                try:
                    with open(txt_path) as f:
                        para_lines = []
                        for i, line in enumerate(f, 1):
                            if i > end: break
                            if i >= start:
                                para_lines.append(line.strip())
                    snippet = " ".join(para_lines)[:300]
                except Exception:
                    snippet = ""
                if snippet:
                    score = round(source_w * 8, 2)  # lower base score for fallback
                    all_scored.append({
                        "book": label, "snippet": snippet,
                        "line": center, "page": est_page,
                        "score": score,
                        "_fallback": True,
                    })

    # ── Final ranking: sort by score descending ─────────────────────────
    all_scored.sort(key=lambda r: r["score"], reverse=True)

    # Ensure diversity: take top results but guarantee each book
    # that has results gets at least its first entry in the top N
    seen_books: set[str] = set()
    diverse: list[dict] = []
    rest: list[dict] = []
    for r in all_scored:
        if r["book"] not in seen_books:
            seen_books.add(r["book"])
            diverse.append(r)
        else:
            rest.append(r)

    # Strip internal scoring keys before returning
    final = diverse + rest
    for r in final:
        for k in ("_prox", "_density", "_exact", "_fallback"):
            r.pop(k, None)

    return final[:max_results]


@app.get("/dm-tools", response_class=HTMLResponse)
async def dm_tools(request: Request):
    """DM Tools main page — encounter builder, NPC manager, monster lookup."""
    user = require_user(request)
    db = get_db()

    # Load monsters from SRD cache
    all_monsters = _load_monster_cache()
    monsters_by_env = {}
    for m in all_monsters:
        m_type = m.get("type", "other")
        monsters_by_env.setdefault(m_type, []).append(m)

    # Load DM's NPCs (DB)
    npcs = [dict(r) for r in db.execute(
        "SELECT * FROM dm_npcs WHERE user_id = ? ORDER BY is_enemy DESC, name",
        (user["id"],)
    ).fetchall()]

    # Merge manual NPCs from extracted data
    manual_npcs = _load_manual_json("npcs.json")
    for i, mn in enumerate(manual_npcs):
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
        try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
        except: hp = 10
        # Detect narrative NPCs (no combat stat block)
        scores = mn.get("ability_scores", {})
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        is_narrative = not has_stats and str(hp_raw).lower() in ("unknown", "?", "0", "")
        npcs.append({
            "id": -(i + 1),  # negative ID = manual NPC
            "user_id": user["id"],
            "name": mn.get("name", "Unknown"),
            "race": mn.get("race", "Unknown"),
            "class_name": mn.get("class_name", ""),
            "subclass": mn.get("subclass", ""),
            "level": mn.get("level", 1),
            "hp_current": hp,
            "hp_max": hp,
            "ac": mn.get("ac", mn.get("armor_class", 10)) if has_stats else 10,
            "is_enemy": 0,
            "is_party_npc": 0,
            "role": mn.get("role", "NPC"),
            "alignment": mn.get("alignment", ""),
            "speed": mn.get("speed", "30 ft."),
            "skills": json.dumps(mn.get("skills", [])),
            "features": json.dumps(mn.get("features", [])),
            "inventory": json.dumps(mn.get("equipment", [])),
            "notes": mn.get("description", ""),
            "source": mn.get("source", "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "strength": scores.get("strength", 0) if has_stats else 0,
            "dexterity": scores.get("dexterity", 0) if has_stats else 0,
            "constitution": scores.get("constitution", 0) if has_stats else 0,
            "intelligence": scores.get("intelligence", 0) if has_stats else 0,
            "wisdom": scores.get("wisdom", 0) if has_stats else 0,
            "charisma": scores.get("charisma", 0) if has_stats else 0,
            "proficiency_bonus": 2, "hit_dice": "1d8", "hit_dice_used": 0,
            "temp_hp": 0, "portrait_url": "", "faction": "",
            "_manual": True,
            "_narrative": is_narrative,
        })

    # Load encounters
    encounters = [dict(r) for r in db.execute(
        "SELECT * FROM dm_encounters WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Load campaigns
    campaigns = [dict(r) for r in db.execute(
        "SELECT * FROM dm_campaigns WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Parse JSON fields
    for npc in npcs:
        for f in ("skills", "features", "inventory"):
            try: npc[f] = json.loads(npc[f])
            except (json.JSONDecodeError, TypeError): npc[f] = []
    for c in campaigns:
        for f in ("quests", "locations", "characters"):
            try: c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError): c[f] = []

    # Monster types for filtering
    monster_types = sorted(monsters_by_env.keys())
    # Challenge rating options
    cr_ranges = [(0, 0.25), (0.5, 2), (3, 5), (6, 10), (11, 16), (17, 30)]

    db.close()
    # Merge custom traps with manual traps
    all_traps = list(MANUAL_TRAPS)
    try:
        db2 = get_db()
        custom_traps = [dict(r) for r in db2.execute(
            "SELECT * FROM dm_custom_traps WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],)
        ).fetchall()]
        db2.close()
        for ct in custom_traps:
            all_traps.append({
                "name": ct["name"],
                "type": ct["type"],
                "danger": ct["danger"],
                "trigger": ct["trigger"] or "",
                "detection": {
                    "dc": ct["detection_dc"] or 10,
                    "skill": ct["detection_skill"] or "Perception",
                    "detail": ct["detection_detail"] or ""
                },
                "disarm": {
                    "dc": ct["disarm_dc"],
                    "method": ct["disarm_method"] or "",
                    "detail": ct["disarm_detail"] or ""
                },
                "effect": ct["effect"] or "",
                "save_dc": ct["save_dc"],
                "save_ability": ct["save_ability"] or "",
                "damage": ct["damage"] or "",
                "damage_type": ct["damage_type"] or "",
                "area": ct["area"] or "",
                "description": ct["description"] or "",
                "source": "Custom",
                "_custom_id": ct["id"],
                "_custom": True
            })
    except Exception:
        pass
    return _render("dm_tools.html", request=request,
                   monsters=all_monsters, monster_types=monster_types,
                   cr_ranges=cr_ranges, npcs=npcs,
                   encounters=encounters, campaigns=campaigns,
                   traps=all_traps,
                   source_map_json=json.dumps(_get_source_slug_map()))


@app.get("/api/dm/monster/{index}", response_class=JSONResponse)
async def dm_monster_detail(index: str, request: Request):
    """Full monster detail from SRD cache."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    for m in all_monsters:
        if m.get("index", "").lower() == index.lower():
            return JSONResponse(_enrich_monster(m))
    raise HTTPException(status_code=404, detail="Monster not found")


def _enrich_monster(m: dict) -> dict:
    """Enrich monster data with computed saves, skills, and action tags."""
    import copy
    m = copy.deepcopy(m)
    
    # Ensure flat ability scores
    scores = m.get("ability_scores", {})
    stat_map = {"str":"strength","strength":"strength","dex":"dexterity","dexterity":"dexterity",
                "con":"constitution","constitution":"constitution","int":"intelligence",
                "intelligence":"intelligence","wis":"wisdom","wisdom":"wisdom",
                "cha":"charisma","charisma":"charisma"}
    for k, v in scores.items():
        target = stat_map.get(k.lower().strip())
        if target and target not in m:
            try: m[target] = int(v)
            except: m[target] = 10
    for stat in ("strength","dexterity","constitution","intelligence","wisdom","charisma"):
        m.setdefault(stat, 10)
    if "ability_scores" in m:
        del m["ability_scores"]  # clean up now-useless dict
    
    # Proficiency bonus
    cr_val = m.get("challenge_rating", 0)
    try: cr_val = float(cr_val)
    except: cr_val = 0
    pb = int(m.get("proficiency_bonus", 0) or max(2, 2 + int((cr_val - 1) / 4)))
    
    # Compute saving throws if not in proficiencies
    has_saves = any(
        p.get("proficiency", {}).get("name", "").startswith("Saving Throw")
        for p in m.get("proficiencies", [])
    ) if m.get("proficiencies") else False
    
    if not has_saves:
        m.setdefault("proficiencies", [])
        # Check for manual saving_throws field
        raw_saves = m.get("saving_throws", {})
        if isinstance(raw_saves, dict) and raw_saves:
            for save_key, save_val in raw_saves.items():
                try: save_val = int(save_val)
                except: save_val = 0
                m["proficiencies"].append({
                    "proficiency": {"name": f"Saving Throw: {save_key.upper()}"},
                    "value": save_val
                })
        else:
            # Compute from ability scores
            save_stats = {"STR":"strength","DEX":"dexterity","CON":"constitution",
                           "INT":"intelligence","WIS":"wisdom","CHA":"charisma"}
            for save_name, stat_key in save_stats.items():
                stat_val = m.get(stat_key, 10)
                save_bonus = (stat_val - 10) // 2
                m["proficiencies"].append({
                    "proficiency": {"name": f"Saving Throw: {save_name}"},
                    "value": save_bonus
                })
    
    # Compute skills from manual data
    raw_skills = m.get("skills", {})
    if isinstance(raw_skills, dict) and raw_skills:
        m.setdefault("proficiencies", [])
        skill_abilities = {
            "acrobatics":"dexterity","animal handling":"wisdom","arcana":"intelligence",
            "athletics":"strength","deception":"charisma","history":"intelligence",
            "insight":"wisdom","intimidation":"charisma","investigation":"intelligence",
            "medicine":"wisdom","nature":"intelligence","perception":"wisdom",
            "performance":"charisma","persuasion":"charisma","religion":"intelligence",
            "sleight of hand":"dexterity","stealth":"dexterity","survival":"wisdom",
        }
        for skill_name, bonus in raw_skills.items():
            try: bonus = int(bonus)
            except: continue
            m["proficiencies"].append({
                "proficiency": {"name": f"Skill: {skill_name.title()}"},
                "value": bonus
            })
        del m["skills"]  # cleanup
    
    # Ensure reactions array
    m.setdefault("reactions", [])
    
    # Ensure lair_actions
    m.setdefault("lair_actions", [])
    m.setdefault("lair_desc", "")
    m.setdefault("legendary_desc", "")
    
    return m


@app.get("/api/dm/monsters", response_class=JSONResponse)
async def dm_monster_list(request: Request):
    """List monsters with optional filters."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    return JSONResponse({"count": len(all_monsters), "monsters": all_monsters})


@app.get("/api/dm/monsters/search", response_class=JSONResponse)
async def dm_monster_search(request: Request, q: str = "", type: str = "", cr_min: float = 0, cr_max: float = 30, cr: str = ""):
    """Search/filter monsters by name, type, and CR range."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    results = []

    # Parse CR from string param as alternative
    if cr:
        try:
            cr_f = float(cr)
            cr_min = cr_f
            cr_max = cr_f
        except (TypeError, ValueError):
            pass

    for m in all_monsters:
        name = m.get("name", "").lower()
        m_type = m.get("type", "").lower()
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            m_cr = 0

        if q and q.lower() not in name:
            continue
        if type and type.lower() != m_type:
            continue
        if m_cr < cr_min or m_cr > cr_max:
            continue
        # Flatten for display
        results.append({
            "index": m["index"],
            "name": m["name"],
            "type": m.get("type", ""),
            "size": m.get("size", ""),
            "armor_class": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hit_points": m.get("hit_points", 0),
            "challenge_rating": m.get("challenge_rating", 0),
            "xp": m.get("xp", 0),
            "source": m.get("source", ""),
        })

    return JSONResponse({"count": len(results), "monsters": results, "total": len(all_monsters)})


@app.get("/api/dm/monsters/by-cr", response_class=JSONResponse)
async def dm_monsters_by_cr(request: Request):
    """Grouped monsters by CR tier for encounter building."""
    user = require_user(request)
    all_monsters = _load_monster_cache()
    tiers = {
        "trivial": [m for m in all_monsters if _monster_cr_sort_key(m) <= 0.25],
        "low": [m for m in all_monsters if 0.5 <= _monster_cr_sort_key(m) <= 2],
        "medium": [m for m in all_monsters if 3 <= _monster_cr_sort_key(m) <= 5],
        "high": [m for m in all_monsters if 6 <= _monster_cr_sort_key(m) <= 10],
        "deadly": [m for m in all_monsters if 11 <= _monster_cr_sort_key(m) <= 16],
        "legendary": [m for m in all_monsters if _monster_cr_sort_key(m) >= 17],
    }
    result = {}
    for tier, monsters in tiers.items():
        result[tier] = [{
            "index": m["index"], "name": m["name"], "cr": m.get("challenge_rating", 0),
            "type": m.get("type", ""), "size": m.get("size", ""),
            "hp": m.get("hit_points", 0), "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "source": m.get("source", ""),
        } for m in sorted(monsters, key=_monster_cr_sort_key)]
    return JSONResponse(result)


# ── DM Tools: NPC Management ─────────────────────────────────────────────

@app.post("/api/dm/npc/create", response_class=JSONResponse)
async def dm_npc_create(request: Request):
    """Create a new NPC (or enemy)."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_npcs (user_id, name, race, class_name, subclass, level, is_enemy, is_party_npc,
            strength, dexterity, constitution, intelligence, wisdom, charisma,
            hp_max, hp_current, ac, speed, proficiency_bonus, hit_dice,
            skills, features, inventory, notes, alignment, role, faction, xp_reward)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New NPC"),
        data.get("race", "Human"),
        data.get("class_name", ""),
        data.get("subclass", ""),
        int(data.get("level", 1)),
        1 if data.get("is_enemy") else 0,
        1 if data.get("is_party_npc") else 0,
        int(data.get("strength", 10)),
        int(data.get("dexterity", 10)),
        int(data.get("constitution", 10)),
        int(data.get("intelligence", 10)),
        int(data.get("wisdom", 10)),
        int(data.get("charisma", 10)),
        int(data.get("hp_max", 10)),
        int(data.get("hp_current", int(data.get("hp_max", 10)))),
        int(data.get("ac", 10)),
        int(data.get("speed", 30)),
        int(data.get("proficiency_bonus", 2)),
        data.get("hit_dice", "1d8"),
        json.dumps(data.get("skills", [])),
        json.dumps(data.get("features", [])),
        json.dumps(data.get("inventory", [])),
        data.get("notes", ""),
        data.get("alignment", "True Neutral"),
        data.get("role", "NPC"),
        data.get("faction", ""),
        int(data.get("xp_reward", 0)),
    ))
    db.commit()
    npc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": npc_id, "ok": True})


@app.get("/api/dm/npcs", response_class=JSONResponse)
async def dm_npcs_list(request: Request):
    """List all DM's NPCs."""
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_npcs {where} ORDER BY is_enemy DESC, name", params
    ).fetchall()]
    db.close()
    for r in rows:
        for f in ("skills", "features", "inventory"):
            try: r[f] = json.loads(r[f])
            except: r[f] = []
    # Merge manual NPCs from extracted data
    manual_npcs = _load_manual_json("npcs.json")
    for i, mn in enumerate(manual_npcs):
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
        try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
        except: hp = 10
        scores = mn.get("ability_scores", {})
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        rows.append({
            "id": -(i + 1),  # negative ID to distinguish manual NPCs
            "user_id": user["id"],
            "name": mn.get("name", "Unknown"),
            "race": mn.get("race", "Unknown"),
            "class_name": mn.get("class_name", ""),
            "subclass": mn.get("subclass", ""),
            "level": mn.get("level", 1),
            "hp_current": hp,
            "hp_max": hp,
            "ac": mn.get("ac", mn.get("armor_class", 10)),
            "is_enemy": 0,
            "role": mn.get("role", "NPC"),
            "alignment": mn.get("alignment", ""),
            "speed": mn.get("speed", "30 ft."),
            "ability_scores": mn.get("ability_scores", {}),
            "spellcasting": mn.get("spellcasting"),
            "skills": mn.get("skills", []),
            "features": mn.get("features", []),
            "actions": mn.get("actions", []),
            "reactions": mn.get("reactions", []),
            "legendary_actions": mn.get("legendary_actions", []),
            "saving_throws": mn.get("saving_throws", {}),
            "senses": mn.get("senses", ""),
            "languages": mn.get("languages", []),
            "damage_resistances": mn.get("damage_resistances", []),
            "damage_immunities": mn.get("damage_immunities", []),
            "condition_immunities": mn.get("condition_immunities", []),
            "challenge_rating": mn.get("challenge_rating"),
            "inventory": mn.get("equipment", []),
            "notes": mn.get("description", ""),
            "source": mn.get("source", "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "_narrative": not has_stats,
        })
    return JSONResponse({"npcs": rows})


@app.get("/api/dm/npc/{npc_id}", response_class=JSONResponse)
async def dm_npc_detail(npc_id: int, request: Request):
    """Full NPC detail — DB NPCs and manual extracted NPCs."""
    user = require_user(request)

    # Manual NPCs use negative IDs
    if npc_id < 0:
        manual_npcs = _load_manual_json("npcs.json")
        idx = -(npc_id) - 1  # id -1 → idx 0, id -2 → idx 1
        if idx < 0 or idx >= len(manual_npcs):
            raise HTTPException(status_code=404, detail="Manual NPC not found")
        mn = manual_npcs[idx]
        scores = mn.get("ability_scores", {})
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
        try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
        except: hp = 10
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        return JSONResponse({
            "id": npc_id,
            "user_id": user["id"],
            "name": mn.get("name", "Unknown"),
            "race": mn.get("race", "Unknown"),
            "class_name": mn.get("class_name", ""),
            "subclass": mn.get("subclass", ""),
            "level": mn.get("level", 1),
            "hp_current": hp,
            "hp_max": hp,
            "ac": mn.get("ac", mn.get("armor_class", 10)),
            "is_enemy": 0,
            "is_party_npc": 0,
            "role": mn.get("role", "NPC"),
            "alignment": mn.get("alignment", ""),
            "speed": mn.get("speed", "30 ft."),
            "skills": mn.get("skills", []),
            "features": mn.get("features", []),
            "inventory": mn.get("equipment", []),
            "notes": mn.get("description", ""),
            "source": mn.get("source", "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "strength": scores.get("strength", 0) if has_stats else 0,
            "dexterity": scores.get("dexterity", 0) if has_stats else 0,
            "constitution": scores.get("constitution", 0) if has_stats else 0,
            "intelligence": scores.get("intelligence", 0) if has_stats else 0,
            "wisdom": scores.get("wisdom", 0) if has_stats else 0,
            "charisma": scores.get("charisma", 0) if has_stats else 0,
            "proficiency_bonus": 2,
            "hit_dice": "1d8",
            "hit_dice_used": 0,
            "temp_hp": 0,
            "portrait_url": "",
            "faction": "",
            "_manual": True,
            "_narrative": not has_stats,
        })

    db = get_db()
    row = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="NPC not found")
    npc = dict(row)
    for f in ("skills", "features", "inventory"):
        try: npc[f] = json.loads(npc[f])
        except: npc[f] = []
    return JSONResponse(npc)


@app.post("/api/dm/npc/{npc_id}/update", response_class=JSONResponse)
async def dm_npc_update(npc_id: int, request: Request):
    """Update NPC fields."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = db.execute("SELECT id FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")

    allowed = {"name","race","class_name","subclass","level","strength","dexterity","constitution",
               "intelligence","wisdom","charisma","hp_max","hp_current","temp_hp","ac","speed",
               "proficiency_bonus","hit_dice","hit_dice_used","skills","features","inventory",
               "notes","alignment","role","faction","xp_reward","portrait_url",
               "is_enemy","is_party_npc"}
    updates = {}
    for k, v in data.items():
        if k in allowed:
            # Serialize list fields
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [npc_id, user["id"]]
        db.execute(f"UPDATE dm_npcs SET {sets} WHERE id=? AND user_id=?", vals)
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/npc/{npc_id}/delete", response_class=JSONResponse)
async def dm_npc_delete(npc_id: int, request: Request):
    """Delete an NPC."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_npcs WHERE id = ? AND user_id = ?", (npc_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


def _build_party_profile(db, campaign_id: int) -> dict | None:
    """Build a combat profile of all characters in a campaign for AI counter-play.
    Returns {characters: [...], summary: {...}} or None if no characters."""
    rows = db.execute("""
        SELECT c.* FROM characters c
        JOIN dm_campaign_characters cc ON cc.campaign_id = ? AND cc.character_id = c.id
        WHERE cc.status = 'active'
    """, (campaign_id,)).fetchall()

    # Fallback: read from campaign's characters JSON column
    if not rows:
        camp = db.execute("SELECT characters FROM dm_campaigns WHERE id=?", (campaign_id,)).fetchone()
        if camp and camp["characters"]:
            try:
                char_entries = json.loads(camp["characters"])
                char_ids = [e["id"] for e in char_entries if isinstance(e, dict) and e.get("status", "active") == "active"]
                if char_ids:
                    placeholders = ",".join("?" * len(char_ids))
                    rows = db.execute(
                        f"SELECT * FROM characters WHERE id IN ({placeholders})", char_ids
                    ).fetchall()
            except (json.JSONDecodeError, KeyError):
                pass

    if not rows:
        return None

    chars = []
    total_level = 0
    ability_mods = {"STR": [], "DEX": [], "CON": [], "INT": [], "WIS": [], "CHA": []}
    all_resistances = set()
    all_immunities = set()
    all_vulns = set()
    all_cond_immunities = set()

    for r in rows:
        c = dict(r)
        # Parse JSON fields
        for f in ("skills", "features", "equipped", "attuned_items",
                   "save_proficiencies", "damage_resistances", "damage_immunities",
                   "damage_vulnerabilities", "condition_immunities", "asi_history"):
            val = c.get(f)
            if isinstance(val, str) and val:
                try: c[f] = json.loads(val)
                except: pass
            if c.get(f) is None:
                c[f] = []

        ac = c.get("ac", 10)
        hp = c.get("hp_current", c.get("hp_max", 10)) or c.get("hp_max", 10)
        lvl = c.get("level", 1)
        total_level += lvl

        scores = {
            "STR": c.get("strength", 10), "DEX": c.get("dexterity", 10),
            "CON": c.get("constitution", 10), "INT": c.get("intelligence", 10),
            "WIS": c.get("wisdom", 10), "CHA": c.get("charisma", 10),
        }
        mods = {k: (v - 10) // 2 for k, v in scores.items()}

        for ab in ability_mods:
            ability_mods[ab].append(mods[ab])

        saves = {s.replace("_save", "").upper(): mods.get(s.replace("_save", "").upper(), 0)
                 for s in (c.get("save_proficiencies", []) or [])}
        # Add prof bonus to proficient saves
        prof = c.get("proficiency_bonus", 2)
        for s in saves:
            saves[s] = mods.get(s, 0) + prof

        # Collect defenses
        res = [r for r in (c.get("damage_resistances", []) or []) if r]
        imm = [r for r in (c.get("damage_immunities", []) or []) if r]
        vul = [r for r in (c.get("damage_vulnerabilities", []) or []) if r]
        cond_imm = [r for r in (c.get("condition_immunities", []) or []) if r]
        all_resistances.update(res)
        all_immunities.update(imm)
        all_vulns.update(vul)
        all_cond_immunities.update(cond_imm)

        # Spellcasting check
        spells = db.execute(
            "SELECT spell_name, spell_level FROM character_spells WHERE character_id=? AND prepared=1",
            (c["id"],)
        ).fetchall()
        spell_names = [s[0] for s in spells] if spells else []

        chars.append({
            "name": c.get("name", ""),
            "race": c.get("race", ""),
            "class": c.get("class_name", ""),
            "subclass": c.get("subclass", ""),
            "level": lvl,
            "ac": ac,
            "hp": hp,
            "scores": {k: v for k, v in scores.items()},
            "mods": mods,
            "saves": saves,
            "resistances": res,
            "immunities": imm,
            "vulnerabilities": vul,
            "condition_immunities": cond_imm,
            "spells": spell_names,
            "features": [f.get("name", f) if isinstance(f, dict) else f
                        for f in (c.get("features", []) or [])],
        })

    avg_level = round(total_level / len(chars), 1)
    # Find party's weakest saves (lowest avg modifier)
    weakest_saves = sorted(ability_mods.items(), key=lambda x: sum(x[1])/len(x[1]))[:2]
    strongest_saves = sorted(ability_mods.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:2]

    summary = {
        "size": len(chars),
        "avg_level": avg_level,
        "total_level": total_level,
        "weakest_saves": [w[0] for w in weakest_saves],
        "strongest_saves": [s[0] for s in strongest_saves],
        "collective_resistances": sorted(all_resistances - all_immunities - all_vulns),
        "collective_immunities": sorted(all_immunities),
        "collective_vulnerabilities": sorted(all_vulns),
        "condition_immunities": sorted(all_cond_immunities),
    }
    return {"characters": chars, "summary": summary}


@app.get("/api/dm/ai/party-profile", response_class=JSONResponse)
async def dm_ai_party_profile(request: Request):
    """Return a combat profile summary of all characters in a campaign.
    Used by the AI Encounter Builder frontend to show party preview."""
    user = require_user(request)
    cid = request.query_params.get("campaign_id", "")
    if not cid:
        return JSONResponse({"profile": None})
    db = get_db()
    profile = _build_party_profile(db, int(cid))
    # Fallback: always include campaign's stored party_level/party_size
    camp = db.execute("SELECT party_level, party_size FROM dm_campaigns WHERE id=?", (int(cid),)).fetchone()
    fallback = {"party_level": camp["party_level"] if camp else 1, "party_size": camp["party_size"] if camp else 4}
    db.close()
    return JSONResponse({"profile": profile, "campaign": fallback})


@app.post("/api/dm/ai/build-encounter", response_class=JSONResponse)
async def dm_ai_build_encounter(request: Request):
    """AI-suggested encounter composition based on party size/level, environment, and difficulty.
    When campaign_id is provided, loads all party characters and tailors the encounter
    to their stats — exploiting weak saves, bypassing resistances, and countering strengths."""
    user = require_user(request)
    db = get_db()
    data = await request.json()
    party_level = int(data.get('party_level', 5))
    party_size = int(data.get('party_size', 4))
    environment = data.get('environment', 'dungeon')
    difficulty = data.get('difficulty', 'medium')
    theme = data.get('theme', '')
    tone = data.get('tone', '')
    target_cr_raw = data.get('target_cr', '')
    campaign_id = data.get('campaign_id', '')

    # Determine effective CR range for filtering
    encounter_type = data.get('encounter_type', 'skirmish')
    if target_cr_raw:
        try:
            if '/' in target_cr_raw:
                parts = target_cr_raw.split('/')
                target_cr = float(parts[0]) / float(parts[1])
            else:
                target_cr = float(target_cr_raw)
        except (ValueError, ZeroDivisionError):
            target_cr = None
    else:
        target_cr = None

    # Effective level for XP budget lookup
    if target_cr is not None:
        eff_level = min(20, max(1, int(target_cr * 2) + 1))
    else:
        eff_level = party_level

    # XP budgets (DMG p.82 — Adjusted XP thresholds for party of N)
    # Per-character thresholds for medium encounters at each level
    MEDIUM_XP = {1: 50, 2: 100, 3: 150, 4: 250, 5: 500, 6: 600, 7: 750, 8: 900,
                 9: 1100, 10: 1200, 11: 1600, 12: 2000, 13: 2200, 14: 2500,
                 15: 2800, 16: 3200, 17: 3900, 18: 4200, 19: 4900, 20: 5700}
    HARD_XP = {1: 75, 2: 150, 3: 225, 4: 375, 5: 750, 6: 900, 7: 1100, 8: 1400,
               9: 1600, 10: 1900, 11: 2400, 12: 3000, 13: 3400, 14: 3800,
               15: 4300, 16: 4800, 17: 5900, 18: 6300, 19: 7300, 20: 8500}
    DEADLY_XP = {1: 100, 2: 200, 3: 400, 4: 500, 5: 1100, 6: 1400, 7: 1700, 8: 2100,
                 9: 2400, 10: 2800, 11: 3600, 12: 4500, 13: 5100, 14: 5700,
                 15: 6400, 16: 7200, 17: 8800, 18: 9500, 19: 10900, 20: 12700}

    xp_budgets = {"easy": MEDIUM_XP, "medium": MEDIUM_XP, "hard": HARD_XP, "deadly": DEADLY_XP}
    if difficulty == "easy":
        xp_per_char = int(MEDIUM_XP.get(eff_level, 500) * 0.5)
    else:
        xp_per_char = xp_budgets.get(difficulty, MEDIUM_XP).get(eff_level, 500)
    xp_budget = xp_per_char * party_size

    # Load monsters
    all_monsters = _load_monster_cache()

    # CR range for filtering (computed once, not per-monster)
    if encounter_type == "swarm":
        max_cr = party_level
        min_cr = 0.125
    elif target_cr is not None:
        max_cr = target_cr + 3
        min_cr = max(0, target_cr - 2)
    else:
        max_cr = party_level + 2
        min_cr = max(0, party_level - 3)

    # Filter by environment/theme hints
    candidates = []
    for m in all_monsters:
        m_type = m.get("type", "").lower()
        m_name = m.get("name", "").lower()
        m_env = m.get("environment", [])
        if isinstance(m_env, list):
            m_env = [e.lower() for e in m_env]

        # Environment/climate matching
        env_match = True
        if environment and environment.lower() not in ("any", ""):
            env_keywords = {
                "dungeon": ["dungeon", "underdark", "cavern", "underground"],
                "forest": ["forest", "woodland", "jungle"],
                "mountain": ["mountain", "hill", "peak"],
                "coastal": ["coastal", "coast", "ocean", "sea", "water"],
                "swamp": ["swamp", "marsh", "wetland"],
                "arctic": ["arctic", "tundra", "ice", "cold", "frozen"],
                "desert": ["desert", "sandy", "arid"],
                "grassland": ["grassland", "plain", "savanna"],
                "urban": ["urban", "city", "town", "settlement"],
                "underdark": ["underdark", "cavern", "underground", "dark"],
                "planar": ["planar", "outer", "inner", "abyss", "heaven"],
            }
            env_words = env_keywords.get(environment.lower(), [environment.lower()])
            # Check monster type alignment against environment
            type_env_map = {
                "aberration": ["underdark", "dungeon"],
                "beast": ["forest", "grassland", "mountain", "arctic", "desert", "coastal", "swamp"],
                "celestial": ["planar"],
                "construct": ["dungeon", "urban"],
                "dragon": ["mountain", "coastal", "forest", "swamp", "arctic", "desert"],
                "elemental": ["planar", "mountain"],
                "fey": ["forest", "grassland"],
                "fiend": ["planar", "underdark"],
                "giant": ["mountain", "hill", "grassland"],
                "humanoid": ["urban", "dungeon", "grassland", "mountain", "forest", "arctic", "desert", "swamp", "coastal"],
                "monstrosity": ["underdark", "dungeon", "forest", "mountain", "swamp", "desert"],
                "ooze": ["underdark", "dungeon", "swamp"],
                "plant": ["forest", "swamp", "jungle"],
                "undead": ["underdark", "dungeon", "urban", "swamp"],
            }
            suitable = type_env_map.get(m_type, [])
            # Expanded: allow thematic pairings across all environments
            if environment.lower() not in ("any", ""):
                # "forest" undead: haunted woods, druid groves with blights
                cross_env = {
                    "undead": ["forest", "mountain", "desert", "coastal", "arctic"],
                    "fey": ["swamp", "mountain", "underdark"],
                    "fiend": ["swamp", "desert", "forest"],
                    "aberration": ["forest", "swamp", "mountain", "urban"],
                    "elemental": ["desert", "coastal", "forest", "swamp"],
                    "ooze": ["forest", "mountain", "desert", "coastal"],
                    "plant": ["mountain", "coastal"],
                    "monstrosity": ["coastal", "urban", "arctic"],
                    "dragon": ["underdark", "urban", "grassland"],
                }
                extra = cross_env.get(m_type, [])
                if not any(e in env_words or e in suitable or e in extra for e in env_words) and not any(kw in m_name for kw in env_words):
                    env_match = False

        if not env_match:
            continue

        # CR filtering: split into combat candidates and atmosphere (CR 0)
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            continue

        if m_cr > max_cr or (m_cr < min_cr and m_cr > 0.125):
            continue

        m_xp = _xp_for_cr(m_cr)
        if m_xp == 0 and m_cr != 0:
            continue

        # Skip mounts and vehicles — DM can add manually
        m_tags = m.get("tags", [])
        if "mount" in m_tags or "vehicle" in m_tags:
            continue

        entry = {
            "index": m["index"], "name": m["name"], "cr": m_cr, "xp": m_xp,
            "type": m_type, "size": m.get("size", ""),
            "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hp": m.get("hit_points", 0),
            "source": m.get("source", ""),
        }

        # Theme-based filtering
        theme_match = True
        if theme:
            theme_lower = theme.lower()
            theme_map = {
                "undead": ["undead", "skeleton", "zombie", "ghost", "wraith", "lich", "vampire",
                           "wight", "spect", "shadow", "ghoul", "mummy", "revenant", "banshee",
                           "death", "necro", "bone", "spirit", "haunt"],
                "dragon": ["dragon", "wyrm", "drake", "wyvern"],
                "demon": ["demon", "fiend", "devil", "abyssal", "infernal", "hell", "imp"],
                "goblin": ["goblin", "hobgoblin", "bugbear"],
                "orc": ["orc", "ogre", "troll", "goblinoid"],
                "beast": ["beast", "wolf", "bear", "cat", "snake", "spider", "hawk", "eagle",
                          "rat", "bat", "owl", "lizard", "crocodile", "shark", "dinosaur"],
                "cult": ["cult", "fanatic", "priest", "acolyte", "cultist", "archmage", "mage"],
                "bandit": ["bandit", "thug", "scout", "assassin", "spy", "veteran", "berserker",
                           "guard", "noble", "commoner"],
                "elemental": ["elemental", "fire", "water", "earth", "air", "mephit", "genie"],
                "giant": ["giant", "ogre", "troll", "ettin", "cyclops"],
                "fey": ["fey", "sprite", "pixie", "dryad", "satyr", "hag", "centaur"],
                "celestial": ["celestial", "angel", "deva", "planetar", "solar", "unicorn", "pegasus"],
                "construct": ["construct", "golem", "animated", "armor", "homunculus", "shield guardian"],
                "aberration": ["aberration", "beholder", "mind flayer", "aboleth", "slaad", "nothic"],
                "ooze": ["ooze", "slime", "jelly", "pudding", "cube"],
                "plant": ["plant", "treant", "shambling", "blight", "vine", "fungus"],
                "monstrosity": ["monstrosity", "chimera", "manticore", "owlbear", "basilisk",
                                "bulette", "roper", "rust monster", "yeti"],
                "swarm": ["swarm", "horde", "pack"],
            }
            theme_tags = None
            for k, v in theme_map.items():
                if k in theme_lower:
                    theme_tags = v
                    break
            if theme_tags:
                theme_match = any(
                    t in m_type or t in m_name
                    or any(t in tag.lower() for tag in m.get("tags", []) if isinstance(tag, str))
                    for t in theme_tags
                )
                # Also check the monster's tags
                for tag in m.get("tags", []) if isinstance(m.get("tags"), list) else []:
                    tag_lower = tag.lower() if isinstance(tag, str) else ""
                    if any(t in tag_lower for t in theme_tags):
                        theme_match = True
                        break

        if not theme_match:
            continue

        candidates.append(entry)

    # AI composition suggestion — send all candidates so AI has full choice
    if not candidates:
        for m in all_monsters:
            try:
                m_cr = float(m.get("challenge_rating", 0))
            except (TypeError, ValueError):
                continue
            if m_cr > max_cr or (m_cr < min_cr and m_cr > 0.125):
                continue
            if m_cr < 0.125:
                continue
            m_xp = _xp_for_cr(m_cr)
            if m_xp == 0:
                continue
            candidates.append({
                "index": m["index"], "name": m["name"], "cr": m_cr, "xp": m_xp,
                "type": m.get("type", "").lower(), "size": m.get("size", ""),
                "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
                "hp": m.get("hit_points", 0),
                "source": m.get("source", ""),
            })
    # Shuffle to vary AI picks, then sort by CR descending for readability
    random.shuffle(candidates)
    candidates.sort(key=lambda c: c["cr"], reverse=True)

    # Build party profile if campaign selected
    party_profile = None
    campaign_name = ""
    if campaign_id:
        try:
            cid = int(campaign_id)
            party_profile = _build_party_profile(db, cid)
            if party_profile:
                # Also get campaign name
                camp_row = db.execute(
                    "SELECT name FROM dm_campaigns WHERE id=?", (cid,)
                ).fetchone()
                if camp_row:
                    campaign_name = camp_row["name"]
                # Override party_size/party_level with actual values
                party_size = party_profile["summary"]["size"]
                party_level = max(1, int(party_profile["summary"]["avg_level"]))
                xp_budget = xp_per_char * party_size
        except (ValueError, TypeError):
            pass

    cr_info = f"Target CR: {target_cr_raw}" if target_cr_raw else f"Party: {party_size} level {party_level}"

    # Build party-specific prompt section
    party_section = ""
    if party_profile:
        chars = party_profile["characters"]
        s = party_profile["summary"]
        party_section = f"\n--- PARTY PROFILE (tailor encounter to counter these characters) ---\n"
        party_section += f"Campaign: {campaign_name}. {s['size']} characters, avg level {s['avg_level']}.\n"
        for ch in chars:
            saves_str = ", ".join(f"{k}+{v}" for k, v in sorted(ch["saves"].items()))
            subclass_str = f" ({ch['subclass']})" if ch.get("subclass") else ""
            party_section += (
                f"  {ch['name']} — {ch['race']} L{ch['level']} {ch['class']}"
                f"{subclass_str} | "
                f"AC{ch['ac']} HP{ch['hp']} | "
                f"Saves: {saves_str}\n"
            )
            if ch["resistances"]:
                party_section += f"    Resists: {', '.join(ch['resistances'])}\n"
            if ch["immunities"]:
                party_section += f"    Immune: {', '.join(ch['immunities'])}\n"
            if ch["vulnerabilities"]:
                party_section += f"    Vulnerable: {', '.join(ch['vulnerabilities'])}\n"
            if ch["spells"]:
                spell_preview = ", ".join(ch["spells"][:8])
                if len(ch["spells"]) > 8:
                    spell_preview += f" +{len(ch['spells']) - 8} more"
                party_section += f"    Spells: {spell_preview}\n"
            if ch["features"]:
                feat_str = ", ".join(str(f) for f in ch["features"][:5])
                if len(ch["features"]) > 5:
                    feat_str += f" +{len(ch['features']) - 5} more"
                party_section += f"    Features: {feat_str}\n"
        party_section += (
            f"\nParty summary: weakest saves = {', '.join(s['weakest_saves'])}, "
            f"strongest = {', '.join(s['strongest_saves'])}.\n"
        )
        if s["collective_resistances"]:
            party_section += f"Shared resistances: {', '.join(s['collective_resistances'])}.\n"
        if s["collective_immunities"]:
            party_section += f"Shared immunities: {', '.join(s['collective_immunities'])} — AVOID these damage types.\n"
        if s["collective_vulnerabilities"]:
            party_section += f"Shared vulnerabilities: {', '.join(s['collective_vulnerabilities'])} — EXPLOIT these.\n"
        if s["condition_immunities"]:
            party_section += f"Condition immunities: {', '.join(s['condition_immunities'])} — DON'T rely on these conditions.\n"
        party_section += (
            "COUNTER-PLAY STRATEGY: target weakest saves, bypass resistances, "
            "use attacks the party is vulnerable to, and pick monsters that counter "
            "the party's spellcasters and tanks. Make this encounter tactically challenging.\n"
        )

    # Boss rotation: track recently used bosses for this campaign
    boss_rotation_context = ""
    if campaign_id:
        try:
            recent = db.execute("""
                SELECT name, combat_state FROM dm_encounters
                WHERE campaign_id=? AND status='complete' AND combat_state IS NOT NULL
                ORDER BY updated_at DESC LIMIT 5
            """, (int(campaign_id),)).fetchall()
            if recent:
                boss_rotation_context = "\nRECENTLY USED BOSSES (avoid repeating these):\n"
                for r in recent:
                    cs = r["combat_state"]
                    try:
                        if isinstance(cs, str):
                            cs = json.loads(cs)
                        if isinstance(cs, dict):
                            monsters = cs.get("monsters", []) or cs.get("npcs", [])
                            boss_rotation_context += f"- {r['name']}: {', '.join(m[:30] for m in monsters[:3])}\n"
                    except:
                        pass
                boss_rotation_context += "Choose DIFFERENT monsters than those listed above. Be creative.\n"
        except:
            pass

    # ── Budget guidance: compute target raw XP and per-role budgets ──
    # DMG p.83 multiplier depends on expected monster count
    if encounter_type == "swarm":
        expected_mult = 2.5  # DMG p.83: 7-10 creatures
        expected_count_hint = "10 creatures"
        target_raw = xp_budget / expected_mult if xp_budget > 0 else 500
        minion_budget = int(target_raw / 10)  # per-creature budget
        boss_budget = 0
        elite_budget = 0
    elif encounter_type == "solo_lair":
        expected_mult = 1.0
        expected_count_hint = "1-3 creatures"
        boss_budget = int(xp_budget / expected_mult * 0.85) if xp_budget > 0 else 500
        elite_budget = 0
        minion_budget = int(xp_budget / expected_mult * 0.15) if xp_budget > 0 else 50
    else:
        expected_mult = 2.0  # skirmish, ambush, social: target 3-6
        expected_count_hint = "3-6 creatures"
        boss_budget = int(xp_budget / expected_mult * 0.45) if xp_budget > 0 else 500
        elite_budget = int(xp_budget / expected_mult * 0.25) if xp_budget > 0 else 200
        minion_budget = int(xp_budget / expected_mult * 0.12) if xp_budget > 0 else 50

    target_raw = xp_budget / expected_mult if xp_budget > 0 else 500

    def _fit_label(xp_val, budget_target):
        """Label how well a monster's XP fits a role budget."""
        if budget_target <= 0:
            return ""
        ratio = xp_val / budget_target
        if 0.5 <= ratio <= 1.4:
            return "PERFECT"
        elif 0.2 <= ratio < 0.5:
            return "CHEAP"
        elif ratio < 0.2:
            return "VERY CHEAP"
        elif 1.4 < ratio <= 2.2:
            return "PRICEY"
        else:
            return "TOO EXPENSIVE"

    # Categorize candidates by role budget fit
    boss_pool = []
    elite_pool = []
    minion_pool = []

    for c in candidates:
        xp = c["xp"]
        cr = c["cr"]
        # Exclude CR 0 creatures (bats, crabs, etc.) — they're not combat threats
        is_trivial = (cr == 0 and xp <= 10)
        if encounter_type == "swarm":
            if not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)
        elif encounter_type == "solo_lair":
            if cr >= party_level - 1:
                boss_pool.append(c)
            if not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)
        else:
            if boss_budget > 0 and xp >= boss_budget * 0.3:
                boss_pool.append(c)
            if elite_budget > 0 and xp >= elite_budget * 0.2 and xp <= elite_budget * 2.5:
                elite_pool.append(c)
            if minion_budget > 0 and not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)

    boss_pool.sort(key=lambda c: abs(c["xp"] - boss_budget) if boss_budget > 0 else c["xp"])
    elite_pool.sort(key=lambda c: abs(c["xp"] - elite_budget) if elite_budget > 0 else c["xp"])
    minion_pool.sort(key=lambda c: c["xp"])

    # Build role-labeled candidate lists for the prompt
    def _fmt_cr(cr_val):
        """Format CR value for display: 0.125 → 1/8, 0.25 → 1/4, etc."""
        if cr_val == 0.125: return "1/8"
        if cr_val == 0.25: return "1/4"
        if cr_val == 0.5: return "1/2"
        if cr_val == int(cr_val): return str(int(cr_val))
        return str(cr_val)

    def _cand_line(c, budget_target):
        label = _fit_label(c["xp"], budget_target) if budget_target > 0 else ""
        idx = c.get("index", c["name"].lower().replace(" ", "-"))
        return f"  [{idx}] {c['name']} | CR {_fmt_cr(c['cr'])} | {c['xp']} XP | {c['type']} | AC{c['ac']} HP{c['hp']} | {label}"

    boss_lines = "\n".join(_cand_line(c, boss_budget) for c in boss_pool[:15]) if boss_pool else "  (none available)"
    elite_lines = "\n".join(_cand_line(c, elite_budget) for c in elite_pool[:15]) if elite_pool else "  (none available)"
    minion_lines = "\n".join(_cand_line(c, minion_budget) for c in minion_pool[:18]) if minion_pool else "  (none available)"

    # Build budget guidance lines
    budget_lines = [f"BUDGET: {xp_budget} adjusted XP (DMG p.82 {difficulty} threshold)",
                    f"  Target raw XP: ~{int(target_raw)} (×{expected_mult} for {expected_count_hint})",
                    f"  Fill to ≥85% of budget — do NOT leave XP unused."]
    if boss_budget:
        budget_lines.append(f"  Boss target: ~{boss_budget} XP")
    if elite_budget:
        budget_lines.append(f"  Elite target: ~{elite_budget} XP each")
    if minion_budget:
        budget_lines.append(f"  Minion target: ≤{minion_budget} XP each" if encounter_type != "swarm" else f"  Per creature target: ~{minion_budget} XP (×10 to fill)")
    budget_section = "\n".join(budget_lines)

    # Archetype-specific prompt templates
    archetype_guides = {
        "skirmish": (
            "Pick 2-5 monsters. Start with a boss suited to a {environment} setting, "
            "then pick elites and minions that are thematically allied with or subservient to that boss — "
            "they should feel like a coherent faction (e.g., dragon + kobolds, vampire + spawn + bats, "
            "orc chief + orcs + wolves, beholder + cultists). For each, assign a role:\n"
            "- \"boss\": main threat, CR near party level (at most 1)\n"
            "- \"elite\": strong support, CR slightly below party\n"
            "- \"minion\": weaker filler"
        ),
        "swarm": (
            "Pick 1-2 creature types that make sense as a swarm/horde in a {environment}. "
            "Do NOT pick a boss — this is a many-vs-party encounter. Pick creatures that are weaker "
            "individually but dangerous in numbers (CR 0.125 to CR 2 preferred). "
            "The system will add lots of them. Assign role \"minion\" to all picks."
        ),
        "ambush": (
            "Pick 2-4 creatures suited to a stealthy ambush in a {environment}. "
            "Pick at least one with high Stealth or surprise abilities (goblins, bugbears, assassins, etc.). "
            "Describe how the ambush is set up — terrain, cover, surprise round. "
            "Tactics should include: who ambushes from where, what the first round looks like.\n"
            "Assign roles: \"boss\" (ambush leader), \"elite\", \"minion\" as appropriate."
        ),
        "solo_lair": (
            "Pick 1 boss creature for a solo + lair encounter. CR should be 2-4 above party level "
            "(a solo boss needs to be tougher). Add 0-2 \"minion\" picks for lair guards or hazards. "
            "The description should set up the lair environment, and tactics should include "
            "lair actions, terrain advantages, and escape contingencies. "
            "Tactics should be 3-4 sentences (longer than usual for lair encounters)."
        ),
        "rival_faction": (
            "Pick two rival groups (2-4 monsters each) that would fight each other AND the party. "
            "e.g., goblins vs. hobgoblins, cultists vs. guards, wolves vs. bears. "
            "For the JSON, assign roles as \"faction_a\" and \"faction_b\" instead of boss/elite/minion. "
            "Description: set up the three-way conflict. Tactics: how each faction behaves."
        ),
        "social_combat": (
            "Pick 1-3 creatures that could be negotiated with or fought, in a {environment} setting. "
            "They should be intelligent enough for social interaction. "
            "Description: set up the social tension — why might they fight? What do they want? "
            "Tactics: first sentence = what they want (negotiation hook), second = what happens if combat starts."
        ),
    }
    guide = archetype_guides.get(encounter_type, archetype_guides["skirmish"]).replace("{environment}", environment)

    # Build role-labeled candidate section
    role_section = ""
    if encounter_type != "swarm" and boss_pool:
        role_section += f"\nBOSS CANDIDATES (target ~{boss_budget} XP):\n{boss_lines}\n"
    if encounter_type not in ("swarm", "solo_lair") and elite_pool:
        role_section += f"\nELITE CANDIDATES (target ~{elite_budget} XP each):\n{elite_lines}\n"
    if minion_pool:
        role_section += f"\nMINION CANDIDATES (≤{minion_budget} XP each):\n{minion_lines}\n"

    # AI picks monsters and roles; algorithm assigns counts to hit budget
    ai_prompt = f"""Design a {difficulty.upper()} difficulty D&D 5e encounter for {cr_info}.
Setting: {environment} environment{f' — {theme}' if theme else ''}{f' ({tone} tone)' if tone else ''}
Encounter type: {encounter_type}
{budget_section}{party_section}{boss_rotation_context}
{guide}

Pick monsters that stay within the per-role budget targets above.
DO NOT guess counts — the system calculates those to fill the budget.

{role_section}

Return ONLY valid JSON (no markdown). Vary choices each time.
Use the EXACT [index] shown in brackets above for each monster:
{{"name": "encounter name", "description": "1-2 sentence setup vignette",
  "picks": [{{"index": "monster-index", "role": "boss"}}, {{"index": "monster-index", "role": "minion"}}],
  "tactics": "1-2 sentence tactics (describe terrain advantage, opening move, or counter-play)"}}"""
    print(f"[AI Encounter] env={environment} diff={difficulty} budget={xp_budget} candidates={len(candidates)}")

    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    ai = _extract_json(text) if text else None
    if ai:
        print(f"[AI Encounter] parsed: name={bool(ai.get('name'))} desc={bool(ai.get('description'))} tactics={bool(ai.get('tactics'))} picks={len(ai.get('picks', ai.get('composition', [])))}")
    else:
        print(f"[AI Encounter] _extract_json returned None, raw text length={len(text) if text else 0}")

    # Resolve AI picks into candidate objects
    picks = []
    if ai:
        raw_entries = ai.get("picks") or ai.get("composition") or []
        for entry in raw_entries:
            idx = str(entry.get("index", "")).lower()
            role = entry.get("role", "minion").lower()
            if encounter_type == "swarm":
                role = "minion"
            m = next((c for c in candidates if str(c["index"]).lower() == idx), None)
            if m:
                picks.append({**m, "role": role})

    # Swarm: override AI picks with algorithmic selection
    # AI is bad at picking budget-appropriate creatures for swarms
    if encounter_type == "swarm":
        if minion_pool:
            # Find the best creature that fills budget with 8-12 of them
            best = None
            best_score = float('inf')
            for m in minion_pool:
                for count in range(8, 13):
                    raw = m["xp"] * count
                    adj = raw * _encounter_mult(count)
                    score = abs(adj - xp_budget)
                    if score < best_score:
                        best_score = score
                        best = (m, count)
            if best:
                m, count = best
                picks = [{**m, "role": "minion"}]
        else:
            print(f"[AI Encounter] Swarm override SKIPPED: minion_pool is EMPTY (candidates={len(candidates)})")

    # Algorithmic count assignment — AI doesn't do math
    composition, xp_total = _assign_encounter_counts(picks, xp_budget, encounter_type) if picks else ([], 0)

    # Fallback: fully algorithmic if AI returned nothing usable
    if not composition:
        boss_candidates = [c for c in candidates if abs(c["cr"] - party_level) <= 1 and c["cr"] >= 1]
        if not boss_candidates:
            boss_candidates = candidates[:20]
        boss = random.choice(boss_candidates) if boss_candidates else None
        if boss:
            picks = [{**boss, "role": "boss"}]
            # Prefer minions of same type as boss (thematic cohesion)
            minion_pool = [c for c in candidates
                          if c["cr"] < party_level and c["index"] != boss["index"]]
            same_type = [c for c in minion_pool if c["type"] == boss["type"]]
            random.shuffle(same_type)
            random.shuffle(minion_pool)
            chosen = (same_type + minion_pool)[:3]
            for m in chosen:
                picks.append({**m, "role": "minion"})
        composition, xp_total = _assign_encounter_counts(picks, xp_budget, encounter_type)

    # Calculate adjusted XP multiplier (DMG p.83 — Encounter Multipliers)
    total_monsters = sum(c.get("count", 1) for c in composition)
    if total_monsters == 1: mult = 1.0
    elif total_monsters == 2: mult = 1.5
    elif 3 <= total_monsters <= 6: mult = 2.0
    elif 7 <= total_monsters <= 10: mult = 2.5
    elif 11 <= total_monsters <= 14: mult = 3.0
    else: mult = 4.0
    adjusted_xp = int(xp_total * mult)
    budget_pct = int((adjusted_xp / xp_budget * 100)) if xp_budget > 0 else 100

    return JSONResponse({
        "name": (ai.get("name") or f"{environment.title()} Encounter") if ai else f"{environment.title()} Encounter",
        "description": (ai.get("description") or f"A {difficulty} encounter in a {environment} setting.") if ai else f"A {difficulty} encounter in a {environment} setting.",
        "tactics": (ai.get("tactics") or "") if ai else "",
        "composition": composition,
        "xp": {"raw_total": xp_total, "adjusted": adjusted_xp, "budget": xp_budget, "budget_pct": budget_pct},
        "difficulty": difficulty.capitalize(),
        "party": {"level": party_level, "size": party_size},
    })


@app.post("/api/dm/ai/build-npc", response_class=JSONResponse)
async def dm_ai_build_npc(request: Request):
    """AI-generated NPC with full build. Uses same PHB-grounded engine as character builder."""
    user = require_user(request)
    data = await request.json()
    race = data.get('race', 'Human')
    subrace = data.get('subrace', '')
    class_name = data.get('class_name', 'Fighter')
    subclass = data.get('subclass', '')
    is_enemy = data.get('is_enemy', False)
    personality_hint = data.get('personality_hint', '')
    role_hint = data.get('role', 'NPC')
    target_cr_raw = data.get('target_cr', '')

    # If target CR provided, auto-derive level; otherwise use manual level
    if target_cr_raw:
        try:
            if '/' in target_cr_raw:
                parts = target_cr_raw.split('/')
                target_cr = float(parts[0]) / float(parts[1])
            else:
                target_cr = float(target_cr_raw)
            # Rough CR→level mapping: L ≈ CR*2 + 1 (DMG p.274 approximation)
            level = min(20, max(1, int(target_cr * 2) + 1))
        except (ValueError, ZeroDivisionError):
            level = min(max(int(data.get('level', 1)), 1), 20)
            target_cr = None
    else:
        level = min(max(int(data.get('level', 1)), 1), 20)
        target_cr = None

    # Use the same build engine as PCs
    abilities = allocate_ability_scores(class_name, race, subrace)
    mods = {ability: (abilities[ability] - 10) // 2 for ability in abilities}
    pb = PROFICIENCY_BONUS.get(level, 2)
    con_mod = mods["constitution"]
    hp = calc_hp(class_name, level, con_mod)
    racial_eff = get_racial_trait_effects(race, subrace)
    ac = _calculate_ac(class_name, level, mods, racial_eff.get("natural_armor"))
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    saves = {ability: mod + (pb if ability in class_data.get("saves", []) else 0)
             for ability, mod in mods.items()}
    skills = _pick_skills(class_name, mods)
    equipment = get_equipment_for_level(class_name, level)
    raw_features = get_class_features(class_name, level, subclass)
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods, subclass=subclass)
    spells = get_spells_for_level(class_name, level) if get_caster_type(class_name) != "none" else {}
    spell_slots = get_spell_slots(class_name, level) if get_caster_type(class_name) != "none" else {}

    # AI flavor
    cr_note = f' (target CR {target_cr_raw})' if target_cr_raw else ''
    ai_prompt = f"""Generate a D&D 5e NPC concept for a {'villain/enemy' if is_enemy else 'friendly NPC'}{cr_note}.
Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name} L{level}{' — ' + subclass if subclass else ''}
Role: {role_hint}
{personality_hint}
Return: {{\"name\": \"NPC Name\", \"personality\": \"2 traits\", \"backstory\": \"1-2 sentences\", \"alignment\": \"one from PHB list\", \"faction\": \"group name or empty\"}}"""

    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    ai = _extract_json(text) if text else None
    if ai:
        if ai.get("alignment") not in ALIGNMENTS:
            ai["alignment"] = random.choice(ALIGNMENTS)

    return JSONResponse({
        "name": ai.get("name", f"{race} {role_hint}") if ai else f"{race} {role_hint}",
        "personality": ai.get("personality", "") if ai else "",
        "backstory": ai.get("backstory", "") if ai else "",
        "alignment": ai.get("alignment", "True Neutral") if ai else "True Neutral",
        "faction": ai.get("faction", "") if ai else "",
        "build": {
            "level": level,
            "class": class_name,
            "subclass": subclass or None,
            "race": race,
            "subrace": subrace or None,
            "target_cr": target_cr_raw or None,
            "ability_scores": abilities,
            "modifiers": mods,
            "hit_points": hp,
            "armor_class": ac,
            "proficiency_bonus": pb,
            "saving_throws": saves,
            "skills": skills,
            "equipment": equipment,
            "features": enriched_features,
            "spells": spells.get("spells", {}),
            "cantrips": spells.get("cantrips", []),
            "spell_slots": spell_slots.get("by_level", {}),
            "hit_dice": f"{level}d{class_data['hd']}",
            "speed": RACES.get(race, RACES["Human"]).get("speed", 30),
        }
    })


# ── AI Trap Builder ───────────────────────────────────────────────────────────

@app.post("/api/dm/ai/build-trap", response_class=JSONResponse)
async def dm_ai_build_trap(request: Request):
    """AI-generated custom trap following DMG/UA trap creation guidelines."""
    user = require_user(request)
    data = await request.json()
    danger = data.get("danger", "dangerous")
    trap_type = data.get("type", "mechanical")
    theme = data.get("theme", "")
    location = data.get("location", "")
    party_level = data.get("party_level", "")

    # DMG presets for the AI to reference
    danger_guidelines = {
        "setback": "DC 10-11, attack +3-5, 1d10 damage, minor inconvenience — a trip wire that tangles, a minor poison that sickens for an hour",
        "dangerous": "DC 12-15, attack +6-8, 2d10 damage, likely to injure — collapsing floor, scything blade, glyph of warding",
        "deadly": "DC 16-20, attack +9-12, 4d10+ damage, could kill — rolling boulder trap, sphere of annihilation, prismatic wall"
    }
    guidelines = danger_guidelines.get(danger, danger_guidelines["dangerous"])
    level_note = f"\nParty level: ~{party_level}" if party_level else ""

    prompt = f"""Design a creative D&D 5e trap following these guidelines:

Type: {trap_type}
Danger: {danger} ({guidelines}){level_note}
Theme/location: {theme or 'any'}{' — ' + location if location else ''}

Return a single JSON object with these keys:
{{
  "name": "Short evocative trap name (4-6 words)",
  "trigger": "What activates it (1 sentence)",
  "detection_dc": <int, appropriate for {danger}>,
  "detection_skill": "Perception or Investigation",
  "detection_detail": "What observant characters notice (1 sentence)",
  "disarm_dc": <int, same as detection_dc or slightly higher>,
  "disarm_method": "How to disable (e.g. 'Dexterity (thieves\\' tools)', 'Arcana', 'Strength')",
  "disarm_detail": "What happens when disarmed successfully (1 sentence)",
  "effect": "Full mechanical description with save DC, damage, and consequences (2-3 sentences)",
  "save_dc": <int, appropriate for {danger}>,
  "save_ability": "Dexterity, Constitution, Wisdom, etc.",
  "damage": "e.g. '2d10' or '4d10+5'",
  "damage_type": "piercing, fire, poison, etc.",
  "area": "e.g. '10 ft. square' or '30 ft. line'",
  "description": "Flavor text and DM tips (1-2 sentences)"
}}

Use the DMG guidelines for DCs and damage. Make the trap feel unique and cinematic.
Keep within the danger level bounds — don't overpower a setback trap or underpower a deadly one."""

    text = await _call_gemini(prompt) or await _call_openrouter(prompt) or await _call_ollama(prompt)
    ai = _extract_json(text) if text else None

    if not ai:
        # Fallback: generate a basic trap from the danger presets
        presets = {
            "setback": {"name": f"{theme or 'Simple'} {trap_type.title()} Trap", "save_dc": 10, "damage": "1d10",
                        "trigger": "Pressure plate or trip wire", "effect": "A minor hazard is triggered."},
            "dangerous": {"name": f"{theme or 'Hidden'} {trap_type.title()} Trap", "save_dc": 14, "damage": "2d10",
                          "trigger": "A concealed mechanism activates", "effect": "A dangerous hazard strikes the party."},
            "deadly": {"name": f"{theme or 'Devastating'} {trap_type.title()} Trap", "save_dc": 18, "damage": "4d10",
                       "trigger": "An intricate trigger mechanism fires", "effect": "A lethal hazard threatens to destroy everything in range."},
        }
        preset = presets.get(danger, presets["dangerous"])
        ai = preset

    return JSONResponse({
        "name": ai.get("name", f"{danger.title()} {trap_type.title()} Trap"),
        "trigger": ai.get("trigger", ""),
        "detection_dc": ai.get("detection_dc", 10),
        "detection_skill": ai.get("detection_skill", "Perception"),
        "detection_detail": ai.get("detection_detail", ""),
        "disarm_dc": ai.get("disarm_dc", ai.get("detection_dc", 10)),
        "disarm_method": ai.get("disarm_method", f"Dexterity (thieves' tools)"),
        "disarm_detail": ai.get("disarm_detail", ""),
        "effect": ai.get("effect", ""),
        "save_dc": ai.get("save_dc", 15),
        "save_ability": ai.get("save_ability", "Dexterity"),
        "damage": ai.get("damage", ""),
        "damage_type": ai.get("damage_type", ""),
        "area": ai.get("area", ""),
        "description": ai.get("description", ""),
        "danger": danger,
        "type": trap_type,
    })


# ── DM Tools: Manual Search ───────────────────────────────────────────────
@app.post("/api/dm/search-manuals", response_class=JSONResponse)
async def dm_search_manuals(request: Request):
    """Full-text search across all D&D reference manuals (cached PDF text)."""
    user = require_user(request)
    data = await request.json()
    query = (data.get("query", "") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"results": [], "error": "Query too short"})

    results = _search_manuals(query, max_results=25)
    return JSONResponse({"results": results, "query": query, "total": len(results)})


@app.post("/api/dm/search-manuals/summarize", response_class=JSONResponse)
async def dm_search_manuals_summarize(request: Request):
    """AI-powered research summary: search manuals, then distill with LLM."""
    user = require_user(request)
    data = await request.json()
    query = (data.get("query", "") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"summary": "", "error": "Query too short"})

    results = _search_manuals(query, max_results=30)

    if not results:
        return JSONResponse({"summary": "No matches found across any reference manuals.", "results": []})

    # Build a research context from the search results
    context_blocks = []
    for r in results:
        page_str = f" (est. p.{r['page']})" if r.get("page") else ""
        context_blocks.append(f"[{r['book']}{page_str}] {r['snippet']}")
    research_text = "\n\n".join(context_blocks[:4000])  # cap for token budget

    ai_prompt = f"""You are a D&D 5e rules researcher. The user searched the reference manuals for:
\"{query}\"

Below are the relevant excerpts found across the manuals. Synthesize them into a comprehensive, well-organized summary. Include:
1. A clear, concise answer to what was searched
2. Key rules, mechanics, and page references where available
3. Any nuances, edge cases, or related rules
4. Differences between sources if applicable (e.g. PHB vs DMG vs XGE)

Format the response in plain text with clear section breaks. Keep it grounded in the excerpts — don't invent rules not present.

RESEARCH EXCERPTS:
{research_text}

RULES SUMMARY:"""

    summary = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    if not summary:
        # Fallback: return raw results without AI
        return JSONResponse({"summary": None, "results": results, "note": "AI unavailable — raw results shown"})

    return JSONResponse({"summary": summary.strip(), "results": results, "query": query})


# ── DM Tools: Encounter Management
# ── DM Tools: Encounter Management ───────────────────────────────────────

@app.post("/api/dm/encounter/create", response_class=JSONResponse)
async def dm_encounter_create(request: Request):
    """Create a new encounter."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_encounters (user_id, name, description, location, environment, difficulty, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New Encounter"),
        data.get("description", ""),
        data.get("location", ""),
        data.get("environment", ""),
        data.get("difficulty", "medium"),
        data.get("notes", ""),
    ))
    db.commit()
    enc_id = cur.lastrowid
    db.close()
    return JSONResponse({"id": enc_id, "ok": True})


@app.get("/api/dm/encounters", response_class=JSONResponse)
async def dm_encounters_list(request: Request):
    """List all encounters."""
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_encounters {where} ORDER BY created_at DESC", params
    ).fetchall()]
    # Count participants
    for r in rows:
        npcs = db.execute(
            "SELECT COUNT(*) as cnt FROM dm_encounter_npcs WHERE encounter_id = ?",
            (r["id"],)
        ).fetchone()
        r["npc_count"] = npcs["cnt"] if npcs else 0
    db.close()
    return JSONResponse({"encounters": rows})


@app.get("/api/dm/encounter/{enc_id}", response_class=JSONResponse)
async def dm_encounter_detail(enc_id: int, request: Request):
    """Full encounter detail with NPC participants."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT * FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404)
    enc = dict(row)
    # Get NPC participants
    participants = [dict(r) for r in db.execute("""
        SELECT en.*, n.name as npc_name, n.race, n.class_name, n.level, n.is_enemy,
               n.hp_max as npc_hp_max, n.ac as npc_ac, n.role, n.xp_reward
        FROM dm_encounter_npcs en
        LEFT JOIN dm_npcs n ON n.id = en.npc_id
        WHERE en.encounter_id = ?
        ORDER BY en.initiative DESC
    """, (enc_id,)).fetchall()]
    for p in participants:
        # Creature-only entries (npc_id = -1) use creature_data, not the sentinel NPC
        if p.get("npc_id") == -1 or not p.get("npc_name"):
            try: cd = json.loads(p.get("creature_data") or "{}")
            except: cd = {}
            p["npc_name"] = cd.get("name", "Unknown")
            p["race"] = cd.get("race", "")
            p["class_name"] = cd.get("class_name", "")
            p["level"] = cd.get("level", 1)
            p["is_enemy"] = cd.get("is_enemy", 0)
            p["npc_hp_max"] = cd.get("hp_max", p.get("hp_max", 10))
            p["npc_ac"] = cd.get("ac", p.get("ac", 10))
            p["role"] = cd.get("role", "")
            p["xp_reward"] = cd.get("xp_reward", 0)
            p["_monster_index"] = cd.get("_monster_index", "")
    # Compute spell slots for caster NPCs
    for p in participants:
        cls = p.get("class_name", "")
        lvl = p.get("level", 1)
        if cls and get_caster_type(cls) != "none":
            slots = get_spell_slots(cls, lvl)
            p["npc_spell_slots"] = slots.get("by_level", {})
        else:
            p["npc_spell_slots"] = {}
    enc["participants"] = participants
    xp_total = sum(p.get("xp_reward", 0) or 0 for p in participants)
    db.close()
    return JSONResponse({"encounter": enc, "xp_total": xp_total})


@app.post("/api/dm/encounter/{enc_id}/add-npc", response_class=JSONResponse)
async def dm_encounter_add_npc(enc_id: int, request: Request):
    """Add an NPC to an encounter."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    # Verify encounter exists
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    # Verify NPC exists
    npc = db.execute("SELECT * FROM dm_npcs WHERE id = ? AND user_id = ?",
                     (npc_id, user["id"])).fetchone()
    if not npc:
        db.close()
        raise HTTPException(status_code=404, detail="NPC not found")
    # Add to encounter
    init = int(data.get("initiative", 0))
    db.execute("""
        INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, ac)
        VALUES (?,?,?,?,?,?)
    """, (enc_id, npc_id, init, npc["hp_current"], npc["hp_max"], npc["ac"]))
    db.commit()
    en_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": en_id, "ok": True})


@app.post("/api/dm/encounter/{enc_id}/add-creature", response_class=JSONResponse)
async def dm_encounter_add_creature(enc_id: int, request: Request):
    """Add a monster or manual NPC to an encounter — doesn't need a dm_npcs row."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    init = int(data.get("initiative", 0))
    hp = int(data.get("hp", data.get("hp_current", 10)))
    hp_max = int(data.get("hp_max", hp))
    ac = int(data.get("ac", 10))
    creature_data = json.dumps({
        "name": data.get("name", "Unknown"),
        "race": data.get("race", ""),
        "class_name": data.get("class_name", ""),
        "level": data.get("level", 1),
        "is_enemy": data.get("is_enemy", 0),
        "hp_max": hp_max,
        "ac": ac,
        "role": data.get("role", ""),
        "xp_reward": data.get("xp_reward", 0),
        "_monster_index": data.get("_monster_index", ""),
        # Full stat block (from manual NPCs, SRD monsters)
        "ability_scores": data.get("ability_scores"),
        "spellcasting": data.get("spellcasting"),
        "features": data.get("features", []),
        "actions": data.get("actions", []),
        "skills": data.get("skills", {}),
        "saving_throws": data.get("saving_throws", {}),
        "speed": data.get("speed", ""),
        "alignment": data.get("alignment", ""),
        "description": data.get("description", ""),
        "equipment": data.get("equipment", []),
        "senses": data.get("senses", ""),
        "languages": data.get("languages", []),
        "damage_resistances": data.get("damage_resistances", []),
        "damage_immunities": data.get("damage_immunities", []),
        "condition_immunities": data.get("condition_immunities", []),
        "challenge_rating": data.get("challenge_rating"),
    })
    # Ensure a placeholder NPC exists for creature-only entries (FK constraint)
    sentinel = db.execute("SELECT id FROM dm_npcs WHERE id = -1").fetchone()
    if not sentinel:
        db.execute("INSERT INTO dm_npcs (id, user_id, name, race, class_name, level, hp_current, hp_max, ac) VALUES (-1, ?, '__sentinel__', '', '', 0, 1, 1, 10)", (user["id"],))
    db.execute("""
        INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, ac, creature_data)
        VALUES (?, -1, ?, ?, ?, ?, ?)
    """, (enc_id, init, hp, hp_max, ac, creature_data))
    db.commit()
    en_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": en_id, "ok": True})


@app.post("/api/dm/encounter/{enc_id}/remove-npc", response_class=JSONResponse)
async def dm_encounter_remove_npc(enc_id: int, request: Request):
    """Remove an NPC from an encounter."""
    user = require_user(request)
    data = await request.json()
    en_id = int(data.get("en_id", 0))
    db = get_db()
    db.execute("""
        DELETE FROM dm_encounter_npcs
        WHERE id = ? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id = ?)
    """, (en_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/encounter/{enc_id}/update-initiative", response_class=JSONResponse)
async def dm_encounter_update_init(enc_id: int, request: Request):
    """Update initiative, HP, defeated state, and individual spell slots for encounter NPCs."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    for entry in data.get("participants", []):
        en_id = int(entry.get("id", 0))
        init = int(entry.get("initiative", 0))
        defeated = 1 if entry.get("defeated", False) else 0
        spell_slots_used = entry.get("spell_slots_used", {})
        if isinstance(spell_slots_used, dict):
            spell_slots_used = json.dumps(spell_slots_used)
        if "hp_current" in entry:
            hp_cur = int(entry["hp_current"])
            set_parts = ["initiative=?", "hp_current=?"]
            set_vals = [init, hp_cur]
            if "defeated" in entry:
                set_parts.append("defeated=?")
                set_vals.append(defeated)
            if "spell_slots_used" in entry:
                set_parts.append("spell_slots_used=?")
                set_vals.append(spell_slots_used)
            set_vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(set_parts)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", set_vals)
        else:
            # Only update fields explicitly present (safe partial update)
            set_parts = ["initiative=?"]
            set_vals = [init]
            if "defeated" in entry:
                set_parts.append("defeated=?")
                set_vals.append(defeated)
            if "spell_slots_used" in entry:
                set_parts.append("spell_slots_used=?")
                set_vals.append(spell_slots_used)
            set_vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(set_parts)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", set_vals)

    # If a single participant should be updated (mark defeated / update HP)
    if "single" in data:
        s = data["single"]
        en_id = int(s.get("id", 0))
        updates = []
        vals = []
        if "hp_current" in s:
            updates.append("hp_current=?")
            vals.append(int(s["hp_current"]))
        if "defeated" in s:
            updates.append("defeated=?")
            vals.append(1 if s["defeated"] else 0)
        if "spell_slots_used" in s:
            up = s["spell_slots_used"]
            if isinstance(up, dict):
                up = json.dumps(up)
            updates.append("spell_slots_used=?")
            vals.append(up)
        if updates:
            vals += [en_id, user["id"]]
            db.execute(f"UPDATE dm_encounter_npcs SET {', '.join(updates)} WHERE id=? AND encounter_id IN (SELECT id FROM dm_encounters WHERE user_id=?)", vals)

    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/encounter/{enc_id}/roll-initiative", response_class=JSONResponse)
async def dm_encounter_roll_initiative(enc_id: int, request: Request):
    """Roll d20 + DEX modifier for all encounter participants. PHB p.189."""
    import random
    user = require_user(request)
    db = get_db()
    enc = db.execute("SELECT id FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404)

    participants = [dict(r) for r in db.execute("""
        SELECT en.id as en_id, en.initiative, en.hp_current, en.hp_max, en.ac, en.defeated,
               en.creature_data,
               n.id as npc_id, n.name, n.race, n.class_name, n.level, n.is_enemy,
               n.dexterity, n.role, n.hp_max as npc_hp_max
        FROM dm_encounter_npcs en
        LEFT JOIN dm_npcs n ON n.id = en.npc_id
        WHERE en.encounter_id = ?
    """, (enc_id,)).fetchall()]

    # Override sentinel data for creature-only entries (npc_id = -1)
    for p in participants:
        if p.get("npc_id") == -1 or not p.get("name"):
            try: cd = json.loads(p.get("creature_data") or "{}")
            except: cd = {}
            p["name"] = cd.get("name", p.get("name") or "Unknown")
            p["race"] = cd.get("race", p.get("race") or "")
            p["class_name"] = cd.get("class_name", p.get("class_name") or "")
            p["level"] = cd.get("level", p.get("level") or 1)
            p["is_enemy"] = cd.get("is_enemy", p.get("is_enemy") or 0)
            p["npc_hp_max"] = cd.get("hp_max", p.get("npc_hp_max") or p.get("hp_max", 10))
            p["ac"] = cd.get("ac", p.get("ac") or 10)
            p["role"] = cd.get("role", p.get("role") or "")
            # Use creature DEX for initiative if available, otherwise default to 10
            if not p.get("dexterity"):
                p["dexterity"] = 10

    results = []
    for p in participants:
        dex_mod = (p.get("dexterity", 10) - 10) // 2
        roll = random.randint(1, 20)
        total = roll + dex_mod
        results.append({
            "en_id": p["en_id"],
            "npc_id": p["npc_id"],
            "name": p["name"],
            "race": p.get("race", ""),
            "class_name": p.get("class_name", ""),
            "level": p.get("level", 1),
            "is_enemy": p.get("is_enemy", 0),
            "role": p.get("role", ""),
            "ac": p.get("ac", 10),
            "hp_current": p["hp_current"],
            "hp_max": p["hp_max"],
            "defeated": p.get("defeated", 0),
            "dex_mod": dex_mod,
            "roll": roll,
            "initiative": total,
            "creature_data": p.get("creature_data"),
        })

    # Sort: alive first by initiative desc, then defeated
    results.sort(key=lambda r: (r["defeated"] or 0, -(r["initiative"])))

    # Save to DB
    for r in results:
        db.execute("UPDATE dm_encounter_npcs SET initiative=? WHERE id=?",
                   (r["initiative"], r["en_id"]))
    db.commit()
    db.close()
    return JSONResponse({"participants": results, "ok": True})


@app.post("/api/dm/encounter/{enc_id}/combat-state", response_class=JSONResponse)
async def dm_encounter_combat_state(enc_id: int, request: Request):
    """Save or load combat state (round, turn_index, participant order)."""
    user = require_user(request)
    db = get_db()
    enc = db.execute("SELECT id, combat_state FROM dm_encounters WHERE id = ? AND user_id = ?",
                     (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404)

    data = await request.json()
    action = data.get("action", "load")

    if action == "save":
        state = json.dumps({
            "round": data.get("round", 1),
            "turn_index": data.get("turn_index", 0),
            "initiative_order": data.get("initiative_order", []),
            "benched_en_ids": data.get("benched_en_ids", []),
            "player_participants": data.get("player_participants", []),
            "campaign_id": data.get("campaign_id")
        })
        db.execute("UPDATE dm_encounters SET combat_state=? WHERE id=?", (state, enc_id))
        db.commit()
        db.close()
        return JSONResponse({"ok": True})

    # Load
    try:
        state = json.loads(enc["combat_state"] or "{}")
    except (json.JSONDecodeError, TypeError):
        state = {}
    db.close()
    return JSONResponse({
        "round": state.get("round", 1),
        "turn_index": state.get("turn_index", 0),
        "initiative_order": state.get("initiative_order", []),
        "benched_en_ids": state.get("benched_en_ids", []),
        "player_participants": state.get("player_participants", []),
        "campaign_id": state.get("campaign_id"),
        "ok": True
    })


@app.post("/api/dm/encounter/{enc_id}/update", response_class=JSONResponse)
async def dm_encounter_update(enc_id: int, request: Request):
    """Update encounter fields."""
    user = require_user(request)
    data = await request.json()
    allowed = {"name","description","location","environment","difficulty","status","notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        db = get_db()
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [enc_id, user["id"]]
        db.execute(f"UPDATE dm_encounters SET {sets} WHERE id=? AND user_id=?", vals)
        db.commit()
        db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/encounter/{enc_id}/delete", response_class=JSONResponse)
async def dm_encounter_delete(enc_id: int, request: Request):
    """Delete an encounter."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_encounter_npcs WHERE encounter_id IN (SELECT id FROM dm_encounters WHERE id=? AND user_id=?)",
               (enc_id, user["id"]))
    db.execute("DELETE FROM dm_encounters WHERE id=? AND user_id=?", (enc_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


# ── DM Tools: Campaign Management ────────────────────────────────────────

@app.post("/api/dm/campaign/create", response_class=JSONResponse)
async def dm_campaign_create(request: Request):
    """Create a new campaign."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    cur = db.execute("""
        INSERT INTO dm_campaigns (user_id, name, description, party_level, party_size, notes, quests, locations, characters)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        user["id"],
        data.get("name", "New Campaign"),
        data.get("description", ""),
        int(data.get("party_level", 1)),
        int(data.get("party_size", 4)),
        data.get("notes", ""),
        json.dumps(data.get("quests", [])),
        json.dumps(data.get("locations", [])),
        json.dumps(data.get("characters", [])),
    ))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return JSONResponse({"id": cid, "ok": True})


@app.get("/api/dm/campaigns", response_class=JSONResponse)
async def dm_campaigns_list(request: Request):
    """List all campaigns with live character stats."""
    user = require_user(request)
    db = get_db()
    where, params = _user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dm_campaigns {where} ORDER BY created_at DESC", params
    ).fetchall()]

    # Enrich each campaign's characters with live data from the characters table
    for camp in rows:
        try:
            char_ids = json.loads(camp.get("characters") or "[]")
        except (json.JSONDecodeError, TypeError):
            camp["characters"] = []
            continue

        enriched = []
        for entry in char_ids:
            cid = entry.get("id") if isinstance(entry, dict) else entry
            row = db.execute("""
                SELECT id, name, race, subrace, class_name, subclass, level,
                       hp_current, hp_max, temp_hp, ac, strength, dexterity, constitution,
                       intelligence, wisdom, charisma, proficiency_bonus, speed,
                       inspiration, exhaustion, passive_perception, hit_dice, hit_dice_used
                FROM characters WHERE id=?
            """, (cid,)).fetchone()
            if row:
                ch = dict(row)
                # Compute modifiers
                mods = {s: (ch[s] - 10) // 2 for s in
                        ["strength","dexterity","constitution","intelligence","wisdom","charisma"]}
                ch["modifiers"] = mods
                ch["status"] = entry.get("status", "active") if isinstance(entry, dict) else "active"
                ch["subrace"] = ch.get("subrace") or ""
                ch["subclass"] = ch.get("subclass") or ""
                # Get spell slots if caster
                cls = ch.get("class_name", "")
                lvl = ch.get("level", 1)
                if cls and get_caster_type(cls) != "none":
                    slots = get_spell_slots(cls, lvl)
                    ch["spell_slots"] = slots.get("by_level", {})
                else:
                    ch["spell_slots"] = {}
                enriched.append(ch)

        camp["characters"] = enriched

        # Parse NPCs JSON
        try:
            camp["npcs"] = json.loads(camp.get("npcs") or "[]")
        except (json.JSONDecodeError, TypeError):
            camp["npcs"] = []

    db.close()
    return JSONResponse({"campaigns": rows})


@app.post("/api/dm/campaign/{camp_id}/update", response_class=JSONResponse)
async def dm_campaign_update(camp_id: int, request: Request):
    """Update campaign — supports field updates and array mutations."""
    user = require_user(request)
    data = await request.json()
    allowed = {"name","description","party_level","party_size","notes","session_notes","quests","locations","characters"}
    updates = {}

    # Handle add/remove quest operations
    if "addQuest" in data:
        new_quest = json.loads(data["addQuest"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            quests = json.loads(row["quests"] or "[]")
            quests.append(new_quest)
            updates["quests"] = json.dumps(quests)
        db.close()
    elif "removeQuest" in data:
        idx = int(data["removeQuest"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            quests = json.loads(row["quests"] or "[]")
            if 0 <= idx < len(quests):
                quests.pop(idx)
            updates["quests"] = json.dumps(quests)
        db.close()

    # Handle add/remove location operations
    if "addLocation" in data:
        new_loc = json.loads(data["addLocation"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            locs = json.loads(row["locations"] or "[]")
            locs.append(new_loc)
            updates["locations"] = json.dumps(locs)
        db.close()
    elif "removeLocation" in data:
        idx = int(data["removeLocation"])
        db = get_db()
        row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
        if row:
            locs = json.loads(row["locations"] or "[]")
            if 0 <= idx < len(locs):
                locs.pop(idx)
            updates["locations"] = json.dumps(locs)
        db.close()

    # Regular field updates
    for k, v in data.items():
        if k in allowed and k not in ("quests", "locations"):
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v
        elif k in allowed and k in ("quests", "locations") and k not in updates:
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            updates[k] = v

    if updates:
        db = get_db()
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [camp_id, user["id"]]
        db.execute(f"UPDATE dm_campaigns SET {sets} WHERE id=? AND user_id=?", vals)
        db.commit()
        db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/delete", response_class=JSONResponse)
async def dm_campaign_delete(camp_id: int, request: Request):
    """Delete a campaign."""
    user = require_user(request)
    db = get_db()
    # Verify ownership
    row = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not row:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    db.execute("DELETE FROM dm_campaigns WHERE id=?", (camp_id,))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/add-character", response_class=JSONResponse)
async def dm_campaign_add_character(camp_id: int, request: Request):
    """Add a character to a campaign."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    db = get_db()
    # Verify campaign ownership
    camp = _require_owned(db, user, "dm_campaigns", camp_id)
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    # Verify character exists (any account — DMs can add any character)
    char_row = db.execute("SELECT id, name, class_name, level, race FROM characters WHERE id=?",
                          (char_id,)).fetchone()
    if not char_row:
        db.close()
        return JSONResponse({"error": "Character not found"}, status_code=404)

    chars = json.loads(camp["characters"] or "[]")
    # Don't add duplicates
    if any(c.get("id") == char_id for c in chars):
        db.close()
        return JSONResponse({"ok": True, "message": "Already added"})
    chars.append({
        "id": char_id,
        "name": char_row["name"],
        "class_name": char_row["class_name"],
        "level": char_row["level"],
        "race": char_row["race"],
        "status": "active",
    })
    db.execute("UPDATE dm_campaigns SET characters=? WHERE id=?", (json.dumps(chars), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/remove-character", response_class=JSONResponse)
async def dm_campaign_remove_character(camp_id: int, request: Request):
    """Remove a character from a campaign."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    chars = json.loads(camp["characters"] or "[]")
    chars = [c for c in chars if c.get("id") != char_id]
    db.execute("UPDATE dm_campaigns SET characters=? WHERE id=?", (json.dumps(chars), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/add-npc", response_class=JSONResponse)
async def dm_campaign_add_npc(camp_id: int, request: Request):
    """Add an NPC to a campaign."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id)
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    # Fetch NPC details
    npc_data = None
    if npc_id < 0:
        # Manual NPC — look up from JSON
        manual_npcs = _load_manual_json("npcs.json")
        idx = -npc_id - 1
        if 0 <= idx < len(manual_npcs):
            mn = manual_npcs[idx]
            hp_raw = mn.get("hp_current") or mn.get("hit_points", "10")
            try: hp = int(re.match(r"(\d+)", str(hp_raw)).group(1))
            except: hp = 10
            npc_data = {
                "id": npc_id,
                "name": mn.get("name", "Unknown"),
                "race": mn.get("race", "Unknown"),
                "class_name": mn.get("class_name", ""),
                "subclass": mn.get("subclass", ""),
                "level": mn.get("level", 1),
                "hp_current": hp,
                "hp_max": hp,
                "ac": mn.get("ac", mn.get("armor_class", 10)),
                "is_enemy": 0,
                "role": mn.get("role", "NPC"),
                "alignment": mn.get("alignment", ""),
                "notes": "",
                "faction": "",
                "xp_reward": mn.get("xp_reward", 0),
            }
    else:
        npc_row = db.execute(
            "SELECT id, name, race, class_name, subclass, level, hp_current, hp_max, ac, is_enemy, role, alignment, notes, faction, xp_reward FROM dm_npcs WHERE id=?",
            (npc_id,)
        ).fetchone()
        if npc_row:
            npc_data = dict(npc_row)
            npc_data["notes"] = ""  # Per-campaign notes (separate from NPC's own notes)

    if not npc_data:
        db.close()
        return JSONResponse({"error": "NPC not found"}, status_code=404)

    npcs = json.loads(camp["npcs"] or "[]")
    if any(n.get("id") == npc_id for n in npcs):
        db.close()
        return JSONResponse({"ok": True, "message": "Already added"})
    npcs.append(npc_data)
    db.execute("UPDATE dm_campaigns SET npcs=? WHERE id=?", (json.dumps(npcs), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/remove-npc", response_class=JSONResponse)
async def dm_campaign_remove_npc(camp_id: int, request: Request):
    """Remove an NPC from a campaign."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    npcs = json.loads(camp["npcs"] or "[]")
    npcs = [n for n in npcs if n.get("id") != npc_id]
    db.execute("UPDATE dm_campaigns SET npcs=? WHERE id=?", (json.dumps(npcs), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/dm/campaign/{camp_id}/update-npc-notes", response_class=JSONResponse)
async def dm_campaign_update_npc_notes(camp_id: int, request: Request):
    """Update DM notes for an NPC in a campaign."""
    user = require_user(request)
    data = await request.json()
    npc_id = int(data.get("npc_id", 0))
    notes = data.get("notes", "")
    db = get_db()
    camp = _require_owned(db, user, "dm_campaigns", camp_id, id_col="id")
    if not camp:
        db.close()
        return JSONResponse({"error": "Campaign not found"}, status_code=404)
    npcs = json.loads(camp["npcs"] or "[]")
    for n in npcs:
        if n.get("id") == npc_id:
            n["notes"] = notes
            break
    db.execute("UPDATE dm_campaigns SET npcs=? WHERE id=?", (json.dumps(npcs), camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.get("/api/dm/user-characters", response_class=JSONResponse)
async def dm_user_characters(request: Request):
    """List user's characters (for campaign party picker)."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT id, name, race, class_name, level, subclass FROM characters ORDER BY name"
    ).fetchall()]
    db.close()
    return JSONResponse({"characters": rows})


# ── Routes: Campaign Team Items ─────────────────────────────────────────────

@app.get("/api/character/{char_id}/campaign", response_class=JSONResponse)
async def character_campaign(char_id: int, request: Request):
    """Get the campaign this character belongs to (if any)."""
    user = require_user(request)
    db = get_db()
    # Check legacy table first, then JSON field
    row = db.execute("""
        SELECT c.id, c.name, c.user_id as dm_user_id
        FROM dm_campaigns c
        JOIN dm_campaign_characters cc ON cc.campaign_id = c.id
        WHERE cc.character_id = ?
    """, (char_id,)).fetchone()
    if not row:
        all_camps = db.execute("SELECT id, name, user_id, characters FROM dm_campaigns").fetchall()
        for c in all_camps:
            try:
                chars = json.loads(c["characters"] or "[]")
                if any(ch.get("id") == char_id for ch in chars if isinstance(ch, dict)):
                    row = (c["id"], c["name"], c["user_id"])
                    break
            except (json.JSONDecodeError, TypeError):
                pass
    db.close()
    if not row:
        return JSONResponse({"campaign": None})
    return JSONResponse({"campaign": {"id": row[0], "name": row[1], "dm_user_id": row[2]}})


@app.get("/api/campaign/{camp_id}/team-items", response_class=JSONResponse)
async def campaign_team_items(camp_id: int, request: Request):
    """List all team items for a campaign (any campaign member can view)."""
    user = require_user(request)
    db = get_db()
    # Verify user is in this campaign — DM, JSON characters, or legacy table
    is_dm = db.execute("SELECT 1 FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone() if _is_admin(user) else db.execute("SELECT 1 FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    # Check JSON characters field for membership
    in_json = False
    camp_row = db.execute("SELECT characters FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone()
    if camp_row:
        try:
            chars = json.loads(camp_row["characters"] or "[]")
            char_ids = [c.get("id") for c in chars if isinstance(c, dict)]
            if char_ids:
                owned = db.execute(
                    "SELECT 1 FROM characters WHERE id IN ({}) AND user_id=?".format(','.join('?'*len(char_ids))),
                    char_ids + [user["id"]]
                ).fetchone()
                in_json = bool(owned)
        except (json.JSONDecodeError, TypeError):
            pass
    is_member = db.execute("""
        SELECT 1 FROM dm_campaign_characters cc
        JOIN characters ch ON ch.id = cc.character_id
        WHERE cc.campaign_id = ? AND ch.user_id = ?
    """, (camp_id, user["id"])).fetchone()
    if not is_dm and not in_json and not is_member:
        db.close()
        return JSONResponse({"error": "Not a member of this campaign"}, status_code=403)

    items = [dict(r) for r in db.execute(
        "SELECT * FROM campaign_team_items WHERE campaign_id=? ORDER BY created_at DESC",
        (camp_id,)
    ).fetchall()]
    # Enrich with source info from item index
    for item in items:
        key = (item.get("name") or "").strip().lower()
        idx_entry = _resolve_item_key(key)
        if idx_entry and idx_entry.get("source"):
            item["source"] = idx_entry["source"]
    db.close()
    return JSONResponse({"items": items})


@app.post("/api/campaign/{camp_id}/team-items", response_class=JSONResponse)
async def campaign_add_team_item(camp_id: int, request: Request):
    """Add an item to the campaign team pool (DM or player)."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Item name required"}, status_code=400)
    qty = max(1, int(data.get("qty", 1)))
    gp_value = int(data.get("gp_value", 0))

    db = get_db()
    is_dm = db.execute("SELECT 1 FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone() if _is_admin(user) else db.execute("SELECT 1 FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    is_member = db.execute("""
        SELECT 1 FROM dm_campaign_characters cc
        JOIN characters ch ON ch.id = cc.character_id
        WHERE cc.campaign_id = ? AND ch.user_id = ?
    """, (camp_id, user["id"])).fetchone()
    if not is_dm and not is_member:
        db.close()
        return JSONResponse({"error": "Not a member of this campaign"}, status_code=403)

    cur = db.execute(
        "INSERT INTO campaign_team_items (campaign_id, name, qty, gp_value, added_by_user_id) VALUES (?,?,?,?,?)",
        (camp_id, name, qty, gp_value, user["id"])
    )
    db.commit()
    item_id = cur.lastrowid
    db.close()
    return JSONResponse({"ok": True, "id": item_id})


@app.post("/api/campaign/{camp_id}/team-items/{item_id}/claim", response_class=JSONResponse)
async def campaign_claim_team_item(camp_id: int, item_id: int, request: Request):
    """Claim a team item — moves it to the claiming character's inventory."""
    user = require_user(request)
    data = await request.json()
    char_id = int(data.get("character_id", 0))
    if not char_id:
        return JSONResponse({"error": "character_id required"}, status_code=400)

    db = get_db()
    # Allow character's owner OR the campaign's DM to award items
    char = db.execute("SELECT id, inventory FROM characters WHERE id=?", (char_id,)).fetchone()
    if not char:
        db.close()
        return JSONResponse({"error": "Character not found"}, status_code=404)

    # Check: user is the character's owner, OR user is the DM of this campaign
    is_owner = db.execute("SELECT 1 FROM characters WHERE id=? AND user_id=?", (char_id, user["id"])).fetchone()
    is_dm = db.execute("SELECT 1 FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    if not is_owner and not is_dm:
        db.close()
        return JSONResponse({"error": "Not authorized to award items to this character"}, status_code=403)

    # Verify character is in the campaign (check JSON characters field + legacy table)
    camp_row = db.execute("SELECT characters FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone()
    in_json = False
    if camp_row:
        try:
            chars = json.loads(camp_row["characters"] or "[]")
            in_json = any(c.get("id") == char_id for c in chars)
        except (json.JSONDecodeError, TypeError):
            pass
    in_table = db.execute(
        "SELECT 1 FROM dm_campaign_characters WHERE campaign_id=? AND character_id=?",
        (camp_id, char_id)
    ).fetchone()
    if not in_json and not in_table:
        db.close()
        return JSONResponse({"error": "Character is not in this campaign"}, status_code=403)

    # Get the team item
    item = db.execute(
        "SELECT id, name, qty FROM campaign_team_items WHERE id=? AND campaign_id=?",
        (item_id, camp_id)
    ).fetchone()
    if not item:
        db.close()
        return JSONResponse({"error": "Item not found"}, status_code=404)

    item_name, item_qty = item[1], item[2]

    # Detect currency — absorb as GP instead of adding to inventory
    import re as _re
    currency_match = _re.search(r'(\d[\d,]*)\s*(cp|sp|ep|gp|pp)', item_name.lower().replace(',', ''))
    is_currency = bool(currency_match)

    if is_currency:
        amount = int(currency_match.group(1))
        denom = currency_match.group(2)
        gp_conv = {"cp": 0.01, "sp": 0.1, "ep": 0.5, "gp": 1, "pp": 10}
        gp_val = int(amount * gp_conv.get(denom, 1)) * item_qty
        db.execute("UPDATE characters SET gp = COALESCE(gp, 0) + ? WHERE id=?", (gp_val, char_id))
        # Delete from team pool
        db.execute("DELETE FROM campaign_team_items WHERE id=?", (item_id,))
        db.commit()
        db.close()
        return JSONResponse({"ok": True, "added": item_name, "qty": item_qty, "currency": True, "gp_added": gp_val})

    # Normal item — add to character inventory
    inv = json.loads(char[1] or "[]")
    found = False
    for inv_item in inv:
        if isinstance(inv_item, dict) and inv_item.get("name", "").lower() == item_name.lower():
            inv_item["qty"] = inv_item.get("qty", 1) + item_qty
            found = True
            break
    if not found:
        inv.append({"name": item_name, "qty": item_qty})
    db.execute("UPDATE characters SET inventory=? WHERE id=?", (json.dumps(inv), char_id))

    # Remove from team pool (or decrement qty)
    if item_qty > 1 and data.get("take_all") is not True:
        db.execute("UPDATE campaign_team_items SET qty=qty-1 WHERE id=?", (item_id,))
    else:
        db.execute("DELETE FROM campaign_team_items WHERE id=?", (item_id,))

    db.commit()
    db.close()
    return JSONResponse({"ok": True, "added": item_name, "qty": 1 if item_qty > 1 and data.get("take_all") is not True else item_qty})


@app.post("/api/character/{char_id}/share-to-team", response_class=JSONResponse)
async def character_share_to_team(char_id: int, request: Request):
    """Move an item from character inventory to the campaign team pool."""
    user = require_user(request)
    data = await request.json()
    item_name = (data.get("name") or "").strip()
    if not item_name:
        return JSONResponse({"error": "Item name required"}, status_code=400)

    db = get_db()
    char = db.execute("SELECT id, inventory, user_id FROM characters WHERE id=?",
                      (char_id,)).fetchone()
    if not char:
        db.close()
        return JSONResponse({"error": "Character not found"}, status_code=404)

    # Find the campaign this character is in (check JSON field + legacy table)
    camp = db.execute("SELECT campaign_id FROM dm_campaign_characters WHERE character_id=?",
                      (char_id,)).fetchone()
    camp_id = camp[0] if camp else None
    if not camp_id:
        # Check campaigns' JSON characters field
        all_camps = db.execute("SELECT id, characters FROM dm_campaigns").fetchall()
        for c in all_camps:
            try:
                chars = json.loads(c["characters"] or "[]")
                if any(ch.get("id") == char_id for ch in chars if isinstance(ch, dict)):
                    camp_id = c["id"]
                    break
            except (json.JSONDecodeError, TypeError):
                pass
    if not camp_id:
        db.close()
        return JSONResponse({"error": "Character is not in a campaign"}, status_code=400)

    # Find and remove item from inventory
    inv = json.loads(char[1] or "[]")
    qty_to_share = int(data.get("qty", 1))
    removed = None
    new_inv = []
    for inv_item in inv:
        if isinstance(inv_item, dict) and inv_item.get("name", "").lower() == item_name.lower():
            current_qty = inv_item.get("qty", 1)
            if qty_to_share >= current_qty:
                removed = {"name": inv_item["name"], "qty": current_qty}
                continue  # remove entirely
            else:
                inv_item["qty"] = current_qty - qty_to_share
                removed = {"name": inv_item["name"], "qty": qty_to_share}
        new_inv.append(inv_item)

    if not removed:
        db.close()
        return JSONResponse({"error": "Item not found in inventory"}, status_code=404)

    db.execute("UPDATE characters SET inventory=? WHERE id=?", (json.dumps(new_inv), char_id))

    # Check if item already exists in team pool — stack
    existing = db.execute(
        "SELECT id, qty FROM campaign_team_items WHERE campaign_id=? AND LOWER(name)=LOWER(?)",
        (camp_id, removed["name"])
    ).fetchone()
    if existing:
        db.execute("UPDATE campaign_team_items SET qty=qty+? WHERE id=?",
                   (removed["qty"], existing[0]))
    else:
        db.execute(
            "INSERT INTO campaign_team_items (campaign_id, name, qty, added_by_user_id) VALUES (?,?,?,?)",
            (camp_id, removed["name"], removed["qty"], user["id"])
        )

    db.commit()
    db.close()
    return JSONResponse({"ok": True, "shared": removed["name"], "qty": removed["qty"]})


@app.put("/api/campaign/{camp_id}/team-items/{item_id}", response_class=JSONResponse)
async def campaign_update_team_item_qty(camp_id: int, item_id: int, request: Request):
    """Update team item quantity (DM only)."""
    user = require_user(request)
    db = get_db()
    is_dm = db.execute("SELECT 1 FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone() if _is_admin(user) else db.execute("SELECT 1 FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    if not is_dm:
        db.close()
        return JSONResponse({"error": "Only the DM can update team items"}, status_code=403)
    data = await request.json()
    qty = max(1, int(data.get("qty", 1)))
    db.execute("UPDATE campaign_team_items SET qty=? WHERE id=? AND campaign_id=?",
               (qty, item_id, camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "qty": qty})


@app.delete("/api/campaign/{camp_id}/team-items/{item_id}", response_class=JSONResponse)
async def campaign_delete_team_item(camp_id: int, item_id: int, request: Request):
    """Remove a team item (DM only)."""
    user = require_user(request)
    db = get_db()
    is_dm = db.execute("SELECT 1 FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone() if _is_admin(user) else db.execute("SELECT 1 FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    if not is_dm:
        db.close()
        return JSONResponse({"error": "Only the DM can remove team items"}, status_code=403)
    db.execute("DELETE FROM campaign_team_items WHERE id=? AND campaign_id=?", (item_id, camp_id))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@app.post("/api/campaign/{camp_id}/roll-loot", response_class=JSONResponse)
async def campaign_roll_loot(camp_id: int, request: Request):
    """Roll a treasure hoard (DMG 2014 p.137-139) and return results. Items are NOT auto-added to pool — the DM picks which to keep."""
    user = require_user(request)
    db = get_db()
    is_dm = db.execute("SELECT 1 FROM dm_campaigns WHERE id=?", (camp_id,)).fetchone() if _is_admin(user) else db.execute("SELECT 1 FROM dm_campaigns WHERE id=? AND user_id=?", (camp_id, user["id"])).fetchone()
    if not is_dm:
        db.close()
        return JSONResponse({"error": "Only the DM can roll loot"}, status_code=403)

    data = await request.json()
    cr_bracket = data.get("cr_bracket", "0-4")
    if cr_bracket not in ("0-4", "5-10", "11-16", "17+"):
        db.close()
        return JSONResponse({"error": "Invalid CR bracket"}, status_code=400)

    hoard = roll_treasure_hoard(cr_bracket)
    db.close()
    return JSONResponse({"ok": True, "hoard": hoard})


# ── Routes: Character Sheet ────────────────────────────────────────────────

@app.get("/character/{char_id}", response_class=HTMLResponse)
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
              "metamagic", "invocations", "maneuvers", "magical_secrets", "infusions"):
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
    _add_cd_sub_options(char["feature_data"])
    _add_source_to_features(char["feature_data"])
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
                        _finfo = FEAT_BY_NAME.get(_fkey.lower(), {})
                        _feat["asi_feat_name"] = _finfo.get("name", _fkey.replace("_", " ").title())
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
    # Enrich with pool_kind from LIMITED_USE (so existing characters get Lay on Hands HP pool)
    for _feat in char["feature_data"]:
        if isinstance(_feat, dict) and not _feat.get("pool_kind"):
            _key = _feat.get("name", "").lower()
            for lkey, lu in LIMITED_USE.items():
                if lkey in _key and lu.get("pool_kind"):
                    _feat["pool_kind"] = lu["pool_kind"]
                    break
    # Fallback: features still without source inherit from class or subclass
    _cls_source = CLASSES.get(char.get("class_name", ""), {}).get("source", "")
    _subclass = char.get("subclass", "")
    _sub_source = ""
    _subclass_feature_names = set()
    if _subclass:
        _sub_source = CLASSES.get(char.get("class_name", ""), {}).get("_subclass_sources", {}).get(_subclass, "")
        if _subclass in SUBCLASS_FEATURES:
            for _lvl_feats in SUBCLASS_FEATURES[_subclass].values():
                _subclass_feature_names.update(_lvl_feats)
    if _cls_source:
        for _feat in char["feature_data"]:
            if not _feat.get("source") or _feat.get("source") in ("SRD 5.1", "PHB 2014"):
                _fname = _feat.get("name", "")
                if _sub_source and _fname in _subclass_feature_names:
                    _feat["source"] = _sub_source
                else:
                    _feat["source"] = _cls_source
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

    return _render("sheet.html", request=request, character=char, spells=spells,
                   mi_spells_data=mi_spells_data,
                   dm_preview=dm_preview,
                   skill_abilities=SKILL_ABILITIES, classes=CLASSES, races=RACES,
                   bg_info=BACKGROUND_INFO, saves_class=saves_class, attacks=all_attacks,
                   charged_items=charged_items,
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
                   source_map_json=json.dumps(_get_source_slug_map()))

# ── Routes: Live Session API ───────────────────────────────────────────────

@app.post("/api/character/{char_id}/update", response_class=JSONResponse)
async def update_character(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()

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

@app.post("/api/character/{char_id}/edit-asi", response_class=JSONResponse)
async def edit_asi_choice(char_id: int, request: Request):
    """Edit a past ASI/feat choice for a given level."""
    user = require_user(request)
    data = await request.json()
    level = data.get("level")
    entry = data.get("entry")  # dict: {type: "asi", ...} or {type: "feat", feat: "...", ...}

    if level is None or not entry:
        return JSONResponse({"error": "Missing level or entry"}, status_code=400)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    char = dict(row)
    asi_history = json.loads(char.get("asi_history", "[]") or "[]")

    # Ensure entry has level set before saving
    entry["level"] = level

    # Replace the entry for this level, or append if not found
    # Also match old entries that lack a level field (legacy data)
    found = False
    for i, ae in enumerate(asi_history):
        if ae.get("level") == level:
            asi_history[i] = entry
            found = True
            break
    if not found:
        asi_history.append(entry)

    db.execute(
        "UPDATE characters SET asi_history=? WHERE id=?",
        (json.dumps(asi_history), char_id)
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "asi_history": asi_history})

@app.post("/api/character/{char_id}/edit-expertise", response_class=JSONResponse)
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

@app.get("/api/character/{char_id}/attacks", response_class=JSONResponse)
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

@app.post("/api/character/{char_id}/spend-charge", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/reload-charge", response_class=JSONResponse)
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


@app.get("/api/character/{char_id}/pdf")
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
            "Content-Disposition": f'inline; filename="{char_data.get("name", "character").replace(" ", "_")}_sheet.pdf"'
        }
    )

@app.post("/api/character/{char_id}/add-spell", response_class=JSONResponse)
async def add_spell(char_id: int, request: Request):
    user = require_user(request)
    data = await request.json()
    db = get_db()
    db.execute("INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used) VALUES (?,?,?,?,?,?)",
               (char_id, data.get("name",""), data.get("level",0), data.get("prepared",1), data.get("slots_max",0), 0))
    db.commit()
    sp_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()
    return JSONResponse({"id": sp_id})


# ── Magic Initiate feat configuration ──────────────────────────────────────
MAGIC_INITIATE_CLASSES = ["Bard", "Cleric", "Druid", "Sorcerer", "Warlock", "Wizard"]

@app.get("/api/character/{char_id}/magic-initiate", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/magic-initiate", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/combat-notes", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/magic-initiate/use", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/magic-initiate/reset", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/feat-config", response_class=JSONResponse)
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


@app.get("/api/character/{char_id}/available-spells", response_class=JSONResponse)
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
                "description": " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else "",
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
                                "description": " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else "",
                                "source": f"subclass ({subclass})",
                                "book": spell.get("source", ""),
                            })

    # 3. Race spells (e.g. Tiefling: Hellish Rebuke, Darkness; Drow: Faerie Fire)
    race_innate_spells = {
        "tiefling": {1: ["hellish rebuke"], 2: ["darkness"]},
        "drow": {1: ["faerie fire"], 2: ["darkness"]},
        "high elf": {0: ["any wizard cantrip"]},
        "forest gnome": {0: ["minor illusion"]},
        "deep gnome": {1: ["disguise self"], 2: ["nondetection"]},
        "duergar": {1: ["enlarge/reduce"], 2: ["invisibility"]},
        "firbolg": {0: ["detect magic"], 1: ["disguise self"]},
        "githyanki": {0: ["mage hand"], 1: ["jump"], 2: ["misty step"]},
        "githzerai": {0: ["mage hand"], 1: ["shield"], 2: ["detect thoughts"]},
        "yuan-ti pureblood": {0: ["poison spray"], 1: ["animal friendship"], 2: ["suggestion"]},
        "aasimar": {0: ["light"], 1: ["lesser restoration"]},
    }
    race_key = race_name.lower()
    if race_key in race_innate_spells:
        for req_lvl, spell_names in race_innate_spells[race_key].items():
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


@app.post("/api/character/{char_id}/ai-spells", response_class=JSONResponse)
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
        desc = " ".join(spell.get("desc", [])) if isinstance(spell.get("desc"), list) else ""
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


@app.post("/api/character/{char_id}/toggle-prepared", response_class=JSONResponse)
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


@app.get("/api/character/{char_id}/gp", response_class=JSONResponse)
async def get_character_gp(char_id: int, request: Request):
    """Return just the gp value for a character (lightweight)."""
    user = require_user(request)
    db = get_db()
    row = db.execute("SELECT gp FROM characters WHERE id=? AND user_id=?", (char_id, user["id"])).fetchone()
    db.close()
    if not row:
        return JSONResponse({"gp": 0})
    return JSONResponse({"gp": row["gp"] or 0})


@app.post("/api/character/{char_id}/toggle-attune", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/use-feature", response_class=JSONResponse)
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

@app.post("/api/character/{char_id}/reset-features", response_class=JSONResponse)
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

@app.get("/api/character/{char_id}/relationships", response_class=JSONResponse)
async def get_relationships(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM character_relationships WHERE character_id = ? AND user_id = ? ORDER BY created_at DESC",
        (char_id, user["id"])
    ).fetchall()
    db.close()
    return JSONResponse([dict(r) for r in rows])

@app.post("/api/character/{char_id}/relationships", response_class=JSONResponse)
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

@app.post("/api/character/{char_id}/relationships/generate", response_class=JSONResponse)
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
    char_name = char.get("name", "the character")
    char_race = char.get("race", "Human")
    char_class = char.get("class_name", "Fighter")
    char_level = char.get("level", 1)
    ai_prompt = (
        f"Create a D&D NPC for a character's backstory.\n"
        f"Character: {char_name}, {char_race} {char_class} L{char_level}.\n"
        f"Relationship type: {rel_type}.\n"
        f'Player\'s description: "{prompt}"\n\n'
        "Generate a vivid NPC. Return ONLY valid JSON (no markdown):\n"
        '{"name": "NPC Name", "race": "D&D race", "class": "class or occupation", '
        '"level": 1-20, "description": "2-3 sentence description of appearance and personality", '
        f'"relationship_detail": "1-2 sentences about their history with {char_name}"' + "}"
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
            npc_data = {"race": ai_json.get("race", ""), "class": ai_json.get("class", ""), "level": ai_json.get("level", 1)}
        except (json.JSONDecodeError, AttributeError):
            description = ai_text[:500]
    # Return generated data only — frontend calls /relationships to save
    return JSONResponse({
        "name": name,
        "description": description,
        "npc_data": npc_data,
        "prompt": prompt,
        "relationship_type": rel_type,
        "ai_generated": True,
    })

@app.put("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
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

@app.delete("/api/character/{char_id}/relationships/{rel_id}", response_class=JSONResponse)
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


@app.get("/api/character/{char_id}/level-up-info", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/apply-level-up", response_class=JSONResponse)
async def apply_level_up(char_id: int, request: Request):
    """Apply all level-up choices across potentially multiple levels."""
    user = require_user(request)
    data = await request.json()
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
    ancestry = char.get("dragonborn_ancestry", "")
    if race == "Dragonborn" and ancestry:
        target_feat = f"{target_level}: Breath Weapon"
        if target_feat not in all_feature_names:
            all_feature_names.append(target_feat)
    updates["features"] = json.dumps(all_feature_names)
    
    # Enriched feature_data
    mods = {a: (cumulative.get(a, 10) - 10) // 2 for a in ABILITY_NAMES}
    eff_subclass = updates.get("subclass", char.get("subclass", ""))
    enriched = enrich_features(all_feature_names, class_name=class_to_level, level=target_level, mods=mods, class_levels=new_cl, subclass=eff_subclass)
    updates["feature_data"] = json.dumps(enriched)
    
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

@app.get("/api/character/{char_id}/de-level-info", response_class=JSONResponse)
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


@app.post("/api/character/{char_id}/de-level", response_class=JSONResponse)
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

@app.post("/api/character/{char_id}/delete", response_class=JSONResponse)
async def delete_character(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    filter_clause, filter_params = _user_filter(user)
    db.execute(f"DELETE FROM characters WHERE id = ? {filter_clause}", (char_id, *filter_params))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── Name Generators ─────────────────────────────────────────────────────────

RACE_NAMES = {
    "Dwarf": {
        "male": ["Thorin","Durin","Balin","Dwalin","Oin","Gloin","Bofur","Bombur","Nori","Dori","Fili","Kili","Gimli","Dain","Thrain","Thror","Fundin","Gror","Farin","Borin","Nain","Bifur","Floi","Loni","Frar","Onar","Brokk","Eitri","Sindri","Thekk","Vist"],
        "female": ["Dis","Hilda","Brunhild","Gerta","Helga","Inga","Sigrid","Thyra","Yrsa","Kara","Freya","Astrid","Ragna","Gudrun","Sif","Bodil","Dagmar","Frida","Ingrid","Sigrun","Thora","Aldis","Eira","Liv","Revna","Solveig","Torunn","Unn","Ylva","Brynhild"],
        "clan": ["Ironforge","Battlehammer","Bronzebeard","Darkiron","Stoutmantle","Deepdelve","Anvilmar","Stonebrow","Hammersmith","Forgefire","Thunderaxe","Runefist","Steelmantle","Goldshaper","Coppervault","Grimstone","Boulderhelm","Firebeard","Frostforge","Shieldbreaker","Oathkeeper","Rubyhelm","Stormshield","Ironfist"]
    },
    "Elf": {
        "male": ["Elrond","Thranduil","Legolas","Finrod","Celeborn","Haldir","Fingolfin","Glorfindel","Cirdan","Eol","Maedhros","Fingon","Turgon","Thingol","Beleg","Gwindor","Amroth","Oropher","Galathil","Angrod","Aegnor","Caranthir","Celegorm","Curufin","Maglor","Daeron","Saeros","Voronwe","Edrahil","Gildor","Lindir"],
        "female": ["Galadriel","Arwen","Luthien","Idril","Nimrodel","Earwen","Aredhel","Elwing","Miriel","Finduilas","Melian","Elenwe","Nienna","Varda","Yavanna","Este","Nessa","Indis","Nerdanel","Anaire","Lalwen","Amarie","Celebrian","Elanor","Morwen","Nienor","Rian","Silmarien","Tar-Miriel","Beruthiel","Ilmare"],
        "clan": ["Silverleaf","Moonshadow","Starbreeze","Dawnwhisper","Nightbreeze","Goldenoak","Swiftarrow","Brightsong","Dewdrop","Windwalker","Starlight","Moonsong","Dawnmist","Silverbrook","Summerstar","Wintershade","Crystalbrook","Amberwave","Fairbreeze","Lightfoot","Silverveil","Dreamweaver","Moonrise","Starfall"]
    },
    "Halfling": {
        "male": ["Bilbo","Frodo","Samwise","Meriadoc","Peregrin","Fredegar","Tobold","Hamfast","Drogo","Odo","Adelard","Andwise","Blanco","Bodo","Carl","Cottar","Dinodas","Dodinas","Everard","Falco","Ferdinand","Ferumbras","Folco","Fortinbras","Gormadoc","Halfred","Harding","Holman","Isembard","Largo","Lotho","Madoc","Marmadas","Mungo","Nob","Olo","Paladin","Posco","Reginard","Robin","Rorimac","Rudigar","Saradas","Saradoc","Tolman","Wilcome"],
        "female": ["Rosie","Belladonna","Primula","Lobelia","Daisy","Marigold","Pearl","Esmeralda","Pansy","Ruby","Amaranth","Angelica","Asphodel","Belba","Camellia","Celandine","Cora","Dahlia","Donnamira","Eglantine","Elanor","Estella","Gilly","Hanna","Ivy","Jasmine","Lily","Linda","Malva","Mirabella","Myrtle","Petunia","Poppy","Primrose","Rosa","Rosamunda","Rowan","Salvia","Sapphire","Tulip","Viola","Zinnia"],
        "clan": ["Baggins","Took","Brandybuck","Gamgee","Bolger","Proudfoot","Greenhand","Brockhouse","Goodbody","Chubb","Banks","Boffin","Bracegirdle","Bunce","Burrows","Cotton","Fairbairn","Goold","Grubb","Hayward","Hornblower","Longbottom","Maggot","Noakes","Pott","Roper","Sandyman","Smallburrow","Twofoot","Whitfoot"]
    },
    "Human": {
        "male": ["Aldric","Cedric","Edmund","Garret","Harold","Lothar","Merek","Oswald","Roland","Theron","Alaric","Baldwin","Beric","Caspian","Conrad","Corwin","Darius","Darian","Eddard","Eldric","Emmerich","Ewald","Florian","Gareth","Gawain","Geraint","Gregor","Hadrian","Hartwin","Jorah","Kendrick","Leofric","Manfred","Mathis","Odric","Ormund","Percival","Ragnar","Reinhardt","Roderick","Sigismund","Talbot","Thaddeus","Tobias","Tybalt","Ulric","Valerian","Victor","Wilfred","Wolfram"],
        "female": ["Alys","Brynn","Catelyn","Elara","Gwendolyn","Isolde","Liana","Morwen","Rowena","Seraphine","Adela","Anya","Beatrix","Celandra","Daria","Eleanora","Elyse","Freya","Geneva","Helena","Ilyana","Jessamy","Katrin","Lenore","Lisette","Lyanna","Magnolia","Margot","Mira","Odette","Petra","Ravenna","Renata","Sabine","Selene","Tanith","Tatiana","Thessaly","Valeria","Vesper","Yvette","Zelda"],
        "clan": ["Hawke","Blackwood","Stormwind","Ashford","Ravencroft","Thornfield","Westbrook","Northrend","Hightower","Greymane","Brightshield","Coldwater","Dawnguard","Eastwatch","Falconer","Frost","Goldcrest","Highcastle","Ironwood","Kingsley","Lockwood","Mistvale","Oakheart","Redmane","Shadowvale","Silverton","Starling","Steel","Strong","Swift","Warbringer","Weatherby","Whiteoak","Windham","Wolfhart"]
    },
    "Dragonborn": {
        "male": ["Kriv","Medrash","Nadarr","Pandjed","Patrin","Rhogar","Sora","Torrin","Ushar","Vrakas","Adrex","Bharash","Donaar","Durir","Erash","Faar","Ghesh","Harann","Heskan","Kava","Korinn","Korth","Maaz","Menereth","Mishann","Naar","Orin","Orn","Perra","Rhas","Shamash","Shedinn","Tarhun","Thava","Uadjit","Vaal","Verin","Vrondiss","Zaan","Zarosh","Zorath"],
        "female": ["Akra","Biri","Daar","Farideh","Harann","Jheri","Kava","Korinn","Nala","Sora","Arileth","Baylith","Ceras","Dira","Erris","Fenrys","Ghesha","Havilar","Iris","Kass","Kethra","Lorath","Maeris","Nalass","Ophir","Paela","Quila","Raiann","Shaena","Tazith","Uri","Vessa","Welsa","Xyra","Yrissa","Zofie"],
        "clan": ["Clethtinthiallor","Daardendrian","Delmirev","Drachedandion","Fenkenkabradon","Kepeshkmolik","Kerrhylon","Nemmonis","Verthisathurgiesh","Yarjerit","Akambherylliax","Bhergav","Cheth","Daar","Dendi","Esthanaar","Gix","Kanjentellequor","Linxakasendalor","Myastan","Norixius","Ophinshtalajiir","Prexijandilin","Shestendeliath","Turnuroth","Vayemniri","Weryon"]
    },
    "Gnome": {
        "male": ["Fizzwick","Gimble","Nackle","Orryn","Pock","Quill","Sprocket","Tinker","Wizzle","Zook","Alston","Boddynock","Coggle","Dabble","Eldon","Fiddle","Gadget","Hobble","Jingle","Kettle","Loopmottin","Mender","Nix","Oddle","Pibble","Rumble","Snibble","Thimble","Vex","Wobble","Yonkle"],
        "female": ["Bimpnottin","Caramip","Duvamil","Ellywick","Lilli","Loopmottin","Mardnab","Roywyn","Shamil","Zanna","Arinda","Breena","Carlin","Donella","Ellyjobell","Frug","Gilla","Helva","Joybell","Kithri","Lolly","Mopsa","Nyxie","Orla","Pippa","Quinby","Roslin","Tana","Ummy","Vexia","Wenna","Xelli","Yoli","Zina"],
        "clan": ["Beren","Daergel","Folkor","Garrick","Nackle","Raulnor","Scheppen","Turen","Warrick","Wiggens","Aleslosh","Ashhearth","Bafflestone","Cogglepot","Dappledew","Fapplestamp","Gimble","Higgle","Jumble","Kettlewhistle","Nimblefizz","Pockle","Quibble","Rangle","Scattercloak","Sparklegem","Thimblegear","Tosslecoat","Whizzle","Zenick"]
    },
    "Half-Elf": {
        "male": ["Aelar","Caelum","Doran","Eryndor","Fenris","Kael","Lorien","Myles","Theron","Varek","Arannis","Berrian","Coren","Darian","Elrohir","Faelan","Garel","Hadrian","Ilphas","Jorah","Keth","Lirien","Maethor","Nalion","Orin","Peren","Quillan","Raegar","Soril","Talasin","Uldred","Varis","Westin","Xandor","Yorin","Zephyr"],
        "female": ["Aeris","Caelia","Elowen","Illyria","Kyra","Lyra","Maeris","Nyx","Seren","Vaela","Arwyn","Berenice","Corinne","Delphine","Elara","Fianna","Gwyneth","Iris","Jessara","Kethra","Lunara","Miriel","Nerys","Ophelia","Phaedra","Quinna","Rowena","Sylvie","Thalia","Una","Vianne","Wisteria","Xanthe","Ysolde","Zara"],
        "clan": ["Amakiir","Ilphelkiir","Moonflower","Wintermere","Summerwind","Autumnvale","Springbrook","Truehart","Goodfellow","Whispermoon","Brightwood","Dawnfield","Evernight","Fairmeadow","Goldengrove","Highhollow","Ivywood","Mistral","Riverstone","Shadowglen","Silvermist","Starfall","Thornwood","Wildrose"]
    },
    "Half-Orc": {
        "male": ["Durgash","Goruk","Hrogath","Krusk","Lurtz","Morg","Ogruk","Thokk","Ulfgrim","Zugor","Azog","Borgakh","Durgath","Garoth","Grishnak","Huruk","Kazrak","Khargol","Magra","Mog","Nargol","Ogol","Ront","Shagrol","Taruk","Uloth","Vorgak","Wurzak","Xarg","Yagak","Zog"],
        "female": ["Borga","Druga","Grenka","Hagra","Kella","Murook","Rogga","Sutha","Urzul","Vorga","Azuk","Baggi","Durgath","Ekk","Forga","Gruna","Hurki","Kansif","Lurka","Neega","Ovak","Prug","Quagg","Rendar","Shautha","Taruk","Ugga","Wrek","Yatur","Zogga"],
        "clan": ["Bonecrusher","Doomhammer","Ironhide","Skullsplitter","Warsong","Bloodfist","Gorehowl","Dreadmaw","Stormrage","Blackrock","Bloodaxe","Bonechewer","Corpsegrinder","Dreadskull","Frostrider","Gorefiend","Hatefury","Ironskin","Killgore","Maul","Ragehowl","Sever","Skullcrusher","Spinebreaker","Thundertusk","Warhowl"]
    },
    "Tiefling": {
        "male": ["Akmenos","Damakos","Ekemon","Iados","Kairon","Leucis","Melech","Morthos","Phelan","Skamos","Amnon","Arkan","Barachiel","Carac","Caim","Dama","Ged","Hadran","Incus","Israfel","Kallik","Levistus","Malkizid","Mammon","Merodach","Moloch","Naberius","Orias","Raum","Rhyxali","Sallos","Shax","Sitri","Valafar","Vapula","Vepar","Verin","Xaphan","Zagan","Zepar"],
        "female": ["Akta","Anakis","Bryseis","Criella","Damaia","Ea","Kallista","Lerissa","Makaria","Nemeia","Akriel","Arista","Belladonna","Calista","Demeter","Eris","Fiera","Gorgona","Hecate","Jezebel","Kali","Lamia","Lilith","Medea","Naamah","Nyx","Onyx","Persephone","Raven","Sable","Tempest","Vespera","Willow","Xenia","Yama","Zara"],
        "clan": ["Art","Carrion","Chant","Creed","Despair","Fear","Glory","Hope","Ideal","Music","Reverie","Sorrow","Torment","Weary","Anguish","Beauty","Chaos","Darkness","Ecstasy","Fury","Grief","Harmony","Infinity","Justice","Knowledge","Liberty","Madness","Nightmare","Oblivion","Pain","Quest","Ruin","Silence","Twilight","Vengeance","Whimsy"]
    },
}

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

STARTING_EQUIPMENT = {
    "Barbarian": ["Greataxe", "2 Handaxes", "Explorer's Pack", "4 Javelins"],
    "Bard": ["Rapier", "Entertainer's Pack", "Lute", "Leather Armor", "Dagger"],
    "Cleric": ["Mace", "Scale Mail", "Light Crossbow + 20 Bolts", "Priest's Pack", "Shield", "Holy Symbol"],
    "Druid": ["Wooden Shield", "Scimitar", "Leather Armor", "Explorer's Pack", "Druidic Focus"],
    "Fighter": ["Chain Mail", "Longsword", "Shield", "Light Crossbow + 20 Bolts", "Dungeoneer's Pack"],
    "Monk": ["Shortsword", "Dungeoneer's Pack", "10 Darts"],
    "Paladin": ["Longsword", "Shield", "5 Javelins", "Priest's Pack", "Chain Mail", "Holy Symbol"],
    "Ranger": ["Longbow + 20 Arrows", "Shortsword", "Scale Mail", "Explorer's Pack"],
    "Rogue": ["Rapier", "Shortbow + 20 Arrows", "Burglar's Pack", "Leather Armor", "2 Daggers", "Thieves' Tools"],
    "Sorcerer": ["Light Crossbow + 20 Bolts", "Arcane Focus", "Dungeoneer's Pack", "2 Daggers"],
    "Warlock": ["Light Crossbow + 20 Bolts", "Arcane Focus", "Scholar's Pack", "Leather Armor", "Dagger"],
    "Wizard": ["Quarterstaff", "Arcane Focus", "Scholar's Pack", "Spellbook"],
}

def random_name(race: str, gender: str = "any") -> dict:
    """Generate a random name for the given race."""
    data = RACE_NAMES.get(race)
    if data:
        if gender == "any":
            gender = random.choice(["male", "female"])
        first = random.choice(data[gender])
        # 30% chance: no clan name (not every character needs a surname)
        if random.random() < 0.3:
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


# ── Subclass Feature Names (PHB 2014) ─────────────────────────────────
# Maps subclass → {level: [feature names]}.
# Used to replace generic SRD names ("Martial Archetype feature") with real names.
SUBCLASS_FEATURES: dict[str, dict[int, list[str]]] = {
    # Barbarian
    "Path of the Berserker": {3: ["Frenzy"], 6: ["Mindless Rage"], 10: ["Intimidating Presence"], 14: ["Retaliation"]},
    "Path of the Totem Warrior": {3: ["Spirit Seeker", "Totem Spirit"], 6: ["Aspect of the Beast"], 10: ["Spirit Walker"], 14: ["Totemic Attunement"]},
    # Bard
    "College of Lore": {3: ["Bonus Proficiencies", "Cutting Words"], 6: ["Additional Magical Secrets"], 14: ["Peerless Skill"]},
    "College of Valor": {3: ["Bonus Proficiencies", "Combat Inspiration"], 6: ["Extra Attack"], 14: ["Battle Magic"]},
    # Cleric
    "Knowledge Domain": {1: ["Blessings of Knowledge"], 2: ["Channel Divinity: Knowledge of the Ages"], 6: ["Channel Divinity: Read Thoughts"], 8: ["Potent Spellcasting"], 17: ["Visions of the Past"]},
    "Life Domain": {1: ["Disciple of Life"], 2: ["Channel Divinity: Preserve Life"], 6: ["Blessed Healer"], 8: ["Divine Strike"], 17: ["Supreme Healing"]},
    "Light Domain": {1: ["Warding Flare"], 2: ["Channel Divinity: Radiance of the Dawn"], 6: ["Improved Flare"], 8: ["Potent Spellcasting"], 17: ["Corona of Light"]},
    "Nature Domain": {1: ["Acolyte of Nature", "Bonus Proficiency"], 2: ["Channel Divinity: Charm Animals and Plants"], 6: ["Dampen Elements"], 8: ["Divine Strike"], 17: ["Master of Nature"]},
    "Tempest Domain": {1: ["Wrath of the Storm"], 2: ["Channel Divinity: Destructive Wrath"], 6: ["Thunderbolt Strike"], 8: ["Divine Strike"], 17: ["Stormborn"]},
    "Trickery Domain": {1: ["Blessing of the Trickster"], 2: ["Channel Divinity: Invoke Duplicity"], 6: ["Channel Divinity: Cloak of Shadows"], 8: ["Divine Strike"], 17: ["Improved Duplicity"]},
    "War Domain": {1: ["War Priest"], 2: ["Channel Divinity: Guided Strike"], 6: ["Channel Divinity: War God's Blessing"], 8: ["Divine Strike"], 17: ["Avatar of Battle"]},
    # Druid
    "Circle of the Land": {2: ["Bonus Cantrip", "Natural Recovery"], 6: ["Land's Stride"], 10: ["Nature's Ward"], 14: ["Nature's Sanctuary"]},
    "Circle of the Moon": {2: ["Combat Wild Shape", "Circle Forms"], 6: ["Primal Strike"], 10: ["Elemental Wild Shape"], 14: ["Thousand Forms"]},
    # Fighter
    "Champion": {3: ["Improved Critical"], 7: ["Remarkable Athlete"], 10: ["Additional Fighting Style"], 15: ["Superior Critical"], 18: ["Survivor"]},
    "Battle Master": {3: ["Combat Superiority"], 7: ["Know Your Enemy"], 10: ["Improved Combat Superiority"], 15: ["Relentless"]},
    "Eldritch Knight": {3: ["Spellcasting", "Weapon Bond"], 7: ["War Magic"], 10: ["Eldritch Strike"], 15: ["Arcane Charge"], 18: ["Improved War Magic"]},
    # Monk
    "Way of the Open Hand": {3: ["Open Hand Technique"], 6: ["Wholeness of Body"], 11: ["Tranquility"], 17: ["Quivering Palm"]},
    "Way of Shadow": {3: ["Shadow Arts"], 6: ["Shadow Step"], 11: ["Cloak of Shadows"], 17: ["Opportunist"]},
    "Way of the Four Elements": {3: ["Disciple of the Elements"]},
    # Paladin
    "Oath of Devotion": {3: ["Channel Divinity: Sacred Weapon", "Channel Divinity: Turn the Unholy"], 7: ["Aura of Devotion"], 15: ["Purity of Spirit"], 20: ["Holy Nimbus"]},
    "Oath of the Ancients": {3: ["Channel Divinity: Nature's Wrath", "Channel Divinity: Turn the Faithless"], 7: ["Aura of Warding"], 15: ["Undying Sentinel"], 20: ["Elder Champion"]},
    "Oath of Vengeance": {3: ["Channel Divinity: Abjure Enemy", "Channel Divinity: Vow of Enmity"], 7: ["Relentless Avenger"], 15: ["Soul of Vengeance"], 20: ["Avenging Angel"]},
    # Ranger
    "Hunter": {3: ["Hunter's Prey"], 7: ["Defensive Tactics"], 11: ["Multiattack"], 15: ["Superior Hunter's Defense"]},
    "Beast Master": {3: ["Ranger's Companion"], 7: ["Exceptional Training"], 11: ["Bestial Fury"], 15: ["Share Spells"]},
    # Rogue
    "Thief": {3: ["Fast Hands", "Second-Story Work"], 9: ["Supreme Sneak"], 13: ["Use Magic Device"], 17: ["Thief's Reflexes"]},
    "Assassin": {3: ["Assassinate", "Bonus Proficiencies"], 9: ["Infiltration Expertise"], 13: ["Impostor"], 17: ["Death Strike"]},
    "Arcane Trickster": {3: ["Spellcasting", "Mage Hand Legerdemain"], 9: ["Magical Ambush"], 13: ["Versatile Trickster"], 17: ["Spell Thief"]},
    # Sorcerer
    "Draconic Bloodline": {1: ["Dragon Ancestor", "Draconic Resilience"], 6: ["Elemental Affinity"], 14: ["Dragon Wings"], 18: ["Draconic Presence"]},
    "Wild Magic": {1: ["Wild Magic Surge", "Tides of Chaos"], 6: ["Bend Luck"], 14: ["Controlled Chaos"], 18: ["Spell Bombardment"]},
    # Warlock
    "The Archfey": {1: ["Fey Presence"], 6: ["Misty Escape"], 10: ["Beguiling Defenses"], 14: ["Dark Delirium"]},
    "The Fiend": {1: ["Dark One's Blessing"], 6: ["Dark One's Own Luck"], 10: ["Fiendish Resilience"], 14: ["Hurl Through Hell"]},
    "The Great Old One": {1: ["Awakened Mind"], 6: ["Entropic Ward"], 10: ["Thought Shield"], 14: ["Create Thrall"]},
    # Wizard
    "School of Abjuration": {2: ["Abjuration Savant", "Arcane Ward"], 6: ["Projected Ward"], 10: ["Improved Abjuration"], 14: ["Spell Resistance"]},
    "School of Conjuration": {2: ["Conjuration Savant", "Minor Conjuration"], 6: ["Benign Transposition"], 10: ["Focused Conjuration"], 14: ["Durable Summons"]},
    "School of Divination": {2: ["Divination Savant", "Portent"], 6: ["Expert Divination"], 10: ["The Third Eye"], 14: ["Greater Portent"]},
    "School of Enchantment": {2: ["Enchantment Savant", "Hypnotic Gaze"], 6: ["Instinctive Charm"], 10: ["Split Enchantment"], 14: ["Alter Memories"]},
    "School of Evocation": {2: ["Evocation Savant", "Sculpt Spells"], 6: ["Potent Cantrip"], 10: ["Empowered Evocation"], 14: ["Overchannel"]},
    "School of Illusion": {2: ["Illusion Savant", "Improved Minor Illusion"], 6: ["Malleable Illusions"], 10: ["Illusory Self"], 14: ["Illusory Reality"]},
    "School of Necromancy": {2: ["Necromancy Savant", "Grim Harvest"], 6: ["Undead Thralls"], 10: ["Inured to Undeath"], 14: ["Command Undead"]},
    "School of Transmutation": {2: ["Transmutation Savant", "Minor Alchemy"], 6: ["Transmuter's Stone"], 10: ["Shapechanger"], 14: ["Master Transmuter"]},
    # DMG
    "Death Domain": {1: ["Death Domain Spells", "Bonus Proficiency", "Reaper"], 2: ["Channel Divinity: Touch of Death"], 6: ["Inescapable Destruction"], 8: ["Divine Strike"], 17: ["Improved Reaper"]},
    "Oathbreaker": {3: ["Oathbreaker Spells", "Channel Divinity: Control Undead", "Channel Divinity: Dreadful Aspect"], 7: ["Aura of Hate"], 15: ["Supernatural Resistance"], 20: ["Dread Lord"]},
}

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

FULL_CASTERS = {"Bard", "Cleric", "Druid", "Sorcerer", "Wizard"}
HALF_CASTERS = {"Paladin", "Ranger"}
PACT_CASTERS = {"Warlock"}
PREPARED_CASTERS = {"Cleric", "Druid", "Paladin", "Wizard"}
SPELLS_KNOWN_CASTERS = {"Bard", "Ranger", "Sorcerer", "Warlock"}

# ── PHB 2014 Limited-Use Feature Definitions ─────────────────────────────
# (feature_key_lower, (min_level_uses, max_cap, recharge_type))
# recharge_type: 'short' (short or long rest), 'long' (long rest only), 'dawn' (at dawn)
# max_cap of 99 means scales with character level (capped by level-based formula)

# PHB Limited-Use Abilities (p.186+ per class)
LIMITED_USE = {
    # Barbarian (PHB p.46-50)
    "rage":                {"min": 2, "max": 99, "recharge": "long", "class": "Barbarian", "per": "level"},
    # Bard (PHB p.51-55) — Bardic Inspiration die increases at L5/10/15
    "bardic inspiration":  {"min": 3, "max": 99, "recharge": "short", "class": "Bard", "per": "level"},
    # Cleric (PHB p.56-62) / Paladin (PHB p.83-89) — single entry, class-differentiated in get_uses_for_level
    "channel divinity":    {"min": 1, "max": 3,  "recharge": "short", "class": "", "per": "level"},
    # Druid (PHB p.63-68)
    "wild shape":          {"min": 2, "max": 99, "recharge": "short", "class": "Druid", "per": "level"},
    # Fighter (PHB p.69-75)
    "action surge":        {"min": 1, "max": 2,  "recharge": "short", "class": "Fighter", "per": "fixed"},
    "second wind":         {"min": 1, "max": 1,  "recharge": "short", "class": "Fighter", "per": "fixed"},
    "indomitable":         {"min": 1, "max": 3,  "recharge": "long", "class": "Fighter", "per": "fixed"},
    # Monk (PHB p.76-82)
    "ki":                  {"min": 2, "max": 99, "recharge": "short", "class": "Monk", "per": "level", "pool_kind": "points"},
    # Paladin (PHB p.83-89)
    "divine sense":        {"min": 1, "max": 99, "recharge": "long", "class": "Paladin", "per": "level"},
    "lay on hands":        {"min": 5, "max": 99, "recharge": "long", "class": "Paladin", "per": "level", "pool_kind": "hp"},
    # (channel divinity merged above — class-differentiated in get_uses_for_level)
    # Sorcerer (PHB p.99-105)
    "sorcery points":      {"min": 2, "max": 99, "recharge": "long", "class": "Sorcerer", "per": "level", "pool_kind": "points"},
    # Warlock (PHB p.105-112)
    "mystic arcanum":      {"min": 1, "max": 1,  "recharge": "long", "class": "Warlock", "per": "fixed"},
    # Wizard (PHB p.112-120)
    "arcane recovery":     {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Dragonborn (PHB p.34) — Breath Weapon, 1/short rest
    "breath weapon":       {"min": 1, "max": 1,  "recharge": "short", "class": "", "per": "fixed"},
    # Cleric (PHB p.59) — Divine Intervention, 1/long rest (L10: roll; L20: auto)
    "divine intervention": {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    # Paladin (PHB p.85) — Cleansing Touch, CHA mod/long rest
    "cleansing touch":     {"min": 1, "max": 5,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    # Rogue (PHB p.97) — Stroke of Luck, 1/short rest
    "stroke of luck":      {"min": 1, "max": 1,  "recharge": "short", "class": "Rogue", "per": "fixed"},
    # Warlock (PHB p.107-108) — Eldritch Master (1/long), Fiend features
    "eldritch master":     {"min": 1, "max": 1,  "recharge": "long", "class": "Warlock", "per": "fixed"},
    "dark one's own luck": {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "hurl through hell":   {"min": 1, "max": 1,  "recharge": "long", "class": "Warlock", "per": "fixed"},
    # Druid Land (PHB p.68) — Natural Recovery, 1/short rest
    "natural recovery":    {"min": 1, "max": 1,  "recharge": "short", "class": "Druid", "per": "fixed"},
    # Wizard Evocation (PHB p.117-118) — Overchannel, 1/long rest
    "overchannel":         {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Wizard (PHB p.115) — Signature Spell, 2 free casts/short rest (one per chosen spell)
    "signature spell":     {"min": 2, "max": 2,  "recharge": "short", "class": "Wizard", "per": "fixed"},
    # ── Racial Traits ──
    "breath weapon":        {"min": 1, "max": 1,  "recharge": "short", "class": "", "per": "fixed"},
    "fey step":             {"min": 1, "max": 1,  "recharge": "short", "class": "", "per": "fixed"},
    "blessing of the raven queen": {"min": 1, "max": 1, "recharge": "long", "class": "", "per": "fixed"},
    "drow magic":           {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},
    "infernal legacy":      {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},
    "duergar magic":        {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},
    "mingle with the wind": {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},
    "merge with stone":     {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},
    "reach to the blaze":   {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},
    "call to the wave":     {"min": 1, "max": 1,  "recharge": "long", "class": "", "per": "fixed"},

    # ── Subclass Features ──
    # Fighter — Battle Master (PHB p.73-74)
    "combat superiority":   {"min": 4, "max": 6,  "recharge": "short", "class": "Fighter", "per": "fixed", "pool_kind": "dice"},
    # Cleric — Light Domain (PHB p.60-61): WIS mod/long (min 1)
    "warding flare":        {"min": 1, "max": 5,  "recharge": "long", "class": "Cleric", "per": "wis"},
    "improved flare":       {"min": 1, "max": 5,  "recharge": "long", "class": "Cleric", "per": "wis"},
    "corona of light":      {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    # Cleric — Nature Domain (PHB p.62): WIS mod/long (min 1)
    "dampen elements":      {"min": 1, "max": 5,  "recharge": "long", "class": "Cleric", "per": "wis"},
    # Cleric — Tempest Domain (PHB p.62): Wrath = WIS mod/long; Thunderbolt Strike = at-will (not limited)
    "wrath of the storm":   {"min": 1, "max": 5,  "recharge": "long", "class": "Cleric", "per": "wis"},
    # Paladin — capstones (PHB p.88-89): 1/long each
    "holy nimbus":          {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    "avenging angel":       {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    "elder champion":       {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    # Sorcerer — Draconic Bloodline (PHB p.103-104)
    "draconic presence":    {"min": 1, "max": 1,  "recharge": "long", "class": "Sorcerer", "per": "fixed"},
    # Sorcerer — Wild Magic (PHB p.103)
    "tides of chaos":       {"min": 1, "max": 1,  "recharge": "long", "class": "Sorcerer", "per": "fixed"},
    # Warlock — The Archfey (PHB p.108-109): 1/short each
    "fey presence":         {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "misty escape":         {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "dark delirium":        {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    # Warlock — The Great Old One (PHB p.109-110)
    "entropic ward":        {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "create thrall":        {"min": 1, "max": 1,  "recharge": "long", "class": "Warlock", "per": "fixed"},
    # Wizard — School of Divination (PHB p.115-116)
    "portent":              {"min": 2, "max": 3,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Wizard — misc (PHB p.117-119)
    "benign transposition": {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "instinctive charm":    {"min": 1, "max": 1,  "recharge": "short", "class": "Wizard", "per": "fixed"},
    "illusory self":        {"min": 1, "max": 1,  "recharge": "short", "class": "Wizard", "per": "fixed"},
    "master transmuter":    {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Additional subclass limited-use features
    "thunderbolt strike":   {"min": 1, "max": 1,  "recharge": "at will", "class": "Cleric", "per": "fixed"},
    "dragon wings":         {"min": 1, "max": 1,  "recharge": "at will", "class": "Sorcerer", "per": "fixed"},
    "bend luck":            {"min": 1, "max": 99, "recharge": "long", "class": "Sorcerer", "per": "fixed"},
    "minor conjuration":    {"min": 1, "max": 1,  "recharge": "at will", "class": "Wizard", "per": "fixed"},
    "greater portent":      {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "alter memories":       {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "command undead":       {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "minor alchemy":        {"min": 1, "max": 1,  "recharge": "at will", "class": "Wizard", "per": "fixed"},
    "transmuter's stone":   {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "improved minor illusion": {"min": 1, "max": 1, "recharge": "at will", "class": "Wizard", "per": "fixed"},
    # Cleric — capstones (PHB p.59-63)
    "master of nature":     {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    "visions of the past":  {"min": 1, "max": 1,  "recharge": "short", "class": "Cleric", "per": "fixed"},
    "improved duplicity":   {"min": 1, "max": 1,  "recharge": "short", "class": "Cleric", "per": "fixed"},
    "avatar of battle":     {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    # Cleric — Trickery Domain (PHB p.63): WIS mod/long (min 1)
    "blessing of the trickster": {"min": 1, "max": 5, "recharge": "long", "class": "Cleric", "per": "wis"},
    # Paladin — Oathbreaker (DMG p.97)
    "dread lord":           {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    # Arcane Trickster (PHB p.97-98)
    "spell thief":          {"min": 1, "max": 1,  "recharge": "long", "class": "Rogue", "per": "fixed"},
}


# ── Multiclass Support (PHB 2014 p.163-165) ──────────────────────────────

MULTICLASS_PREREQS = {
    "Barbarian": {"Strength": 13},
    "Bard": {"Charisma": 13},
    "Cleric": {"Wisdom": 13},
    "Druid": {"Wisdom": 13},
    "Fighter": {"Strength": 13, "Dexterity": 13},  # OR — either meets requirement
    "Monk": {"Dexterity": 13, "Wisdom": 13},
    "Paladin": {"Strength": 13, "Charisma": 13},
    "Ranger": {"Dexterity": 13, "Wisdom": 13},
    "Rogue": {"Dexterity": 13},
    "Sorcerer": {"Charisma": 13},
    "Warlock": {"Charisma": 13},
    "Wizard": {"Intelligence": 13},
}

# Proficiencies gained when multiclassing INTO a class (PHB p.164)
# None of these grant saving throw proficiencies
MULTICLASS_PROFICIENCIES = {
    "Barbarian": {"weapons": "simple,martial", "armor": "shields"},
    "Bard": {"armor": "light", "skills": 1},
    "Cleric": {"armor": "light,medium,shields"},
    "Druid": {"armor": "light,medium,shields"},
    "Fighter": {"weapons": "simple,martial", "armor": "light,medium,shields"},
    "Monk": {"weapons": "simple,shortswords"},
    "Paladin": {"weapons": "simple,martial", "armor": "light,medium,shields"},
    "Ranger": {"weapons": "simple,martial", "armor": "light,medium,shields", "skills": 1},
    "Rogue": {"armor": "light", "skills": 1},
    "Sorcerer": {},
    "Warlock": {"weapons": "simple", "armor": "light"},
    "Wizard": {},
}

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
ABILITY_NAMES = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]

# Which levels grant ASI per class (PHB 2014)
ASI_LEVELS: dict[str, list[int]] = {
    "Barbarian": [4,8,12,16,19],
    "Bard": [4,8,12,16,19],
    "Cleric": [4,8,12,16,19],
    "Druid": [4,8,12,16,19],
    "Fighter": [4,6,8,12,14,16,19],
    "Monk": [4,8,12,16,19],
    "Paladin": [4,8,12,16,19],
    "Ranger": [4,8,12,16,19],
    "Rogue": [4,8,10,12,16,19],
    "Sorcerer": [4,8,12,16,19],
    "Warlock": [4,8,12,16,19],
    "Wizard": [4,8,12,16,19],
}

# Subclass selection levels + options per class (PHB 2014)
SUBCLASS_LEVELS: dict[str, dict] = {
    "Barbarian": {"level": 3, "label": "Primal Path",
        "options": ["Path of the Berserker","Path of the Totem Warrior"]},
    "Bard": {"level": 3, "label": "Bard College",
        "options": ["College of Lore","College of Valor"]},
    "Cleric": {"level": 1, "label": "Divine Domain",
        "options": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"]},
    "Druid": {"level": 2, "label": "Druid Circle",
        "options": ["Circle of the Land","Circle of the Moon"]},
    "Fighter": {"level": 3, "label": "Martial Archetype",
        "options": ["Champion","Battle Master","Eldritch Knight"]},
    "Monk": {"level": 3, "label": "Monastic Tradition",
        "options": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"]},
    "Paladin": {"level": 3, "label": "Sacred Oath",
        "options": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"]},
    "Ranger": {"level": 3, "label": "Ranger Archetype",
        "options": ["Hunter","Beast Master"]},
    "Rogue": {"level": 3, "label": "Roguish Archetype",
        "options": ["Thief","Assassin","Arcane Trickster"]},
    "Sorcerer": {"level": 1, "label": "Sorcerous Origin",
        "options": ["Draconic Bloodline","Wild Magic"]},
    "Warlock": {"level": 1, "label": "Otherworldly Patron",
        "options": ["The Archfey","The Fiend","The Great Old One"]},
    "Wizard": {"level": 2, "label": "Arcane Tradition",
        "options": ["School of Abjuration","School of Conjuration","School of Divination","School of Enchantment","School of Evocation","School of Illusion","School of Necromancy","School of Transmutation"]},
}

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
METAMAGIC_OPTIONS: dict[str, dict] = {
    "careful_spell":    {"name":"Careful Spell","desc":"Allows creatures you choose to automatically succeed on saving throws against your spell"},
    "distant_spell":    {"name":"Distant Spell","desc":"Double the range of a spell (touch→30 ft, range ×2)"},
    "empowered_spell":  {"name":"Empowered Spell","desc":"Reroll up to Cha mod damage dice, must use new rolls"},
    "extended_spell":   {"name":"Extended Spell","desc":"Double the duration of a spell (max 24 hours)"},
    "heightened_spell": {"name":"Heightened Spell","desc":"One target has disadvantage on its first saving throw against your spell"},
    "quickened_spell":  {"name":"Quickened Spell","desc":"Cast a spell with casting time of 1 action as a bonus action"},
    "subtle_spell":     {"name":"Subtle Spell","desc":"Cast a spell without verbal or somatic components"},
    "twinned_spell":    {"name":"Twinned Spell","desc":"Target a second creature in range with the same spell (single-target spells only)"},
}
METAMAGIC_PICKS: dict[int, int] = {3: 2, 10: 1, 17: 1}  # level → number of choices

# ── Eldritch Invocations (Warlock L2+, ~33 SRD options) ──────────────
INVOCATION_LEVELS: dict[str, list[int]] = {"Warlock": [2, 5, 7, 9, 12, 15, 18]}
INVOCATION_OPTIONS: dict[str, dict] = {
    "agonizing_blast":       {"name":"Agonizing Blast","desc":"Add Cha modifier to Eldritch Blast damage","level":2},
    "armor_of_shadows":      {"name":"Armor of Shadows","desc":"Cast Mage Armor at will without expending a spell slot","level":2},
    "ascendant_step":        {"name":"Ascendant Step","desc":"Cast Levitate at will without expending a spell slot","level":9},
    "beast_speech":          {"name":"Beast Speech","desc":"Cast Speak with Animals at will","level":2},
    "beguiling_influence":   {"name":"Beguiling Influence","desc":"Gain proficiency in Deception and Persuasion","level":2},
    "bewitching_whispers":   {"name":"Bewitching Whispers","desc":"Cast Compulsion once per long rest using a Warlock spell slot","level":7},
    "book_of_ancient_secrets":{"name":"Book of Ancient Secrets","desc":"Learn 2 L1 rituals, can add more from scrolls (requires Pact of the Tome)","level":2,"prereq":"Pact of the Tome"},
    "chains_of_carceri":     {"name":"Chains of Carceri","desc":"Cast Hold Monster at will on celestial/fiend/elemental, 1/long rest per target","level":15,"prereq":"Pact of the Chain"},
    "devils_sight":          {"name":"Devil's Sight","desc":"See normally in darkness (magical and nonmagical) to 120 ft","level":2},
    "dreadful_word":         {"name":"Dreadful Word","desc":"Cast Confusion once per long rest using a Warlock spell slot","level":7},
    "eldritch_sight":        {"name":"Eldritch Sight","desc":"Cast Detect Magic at will","level":2},
    "eldritch_spear":        {"name":"Eldritch Spear","desc":"Eldritch Blast range becomes 300 ft","level":2},
    "eyes_of_the_rune_keeper":{"name":"Eyes of the Rune Keeper","desc":"Read all writing","level":2},
    "fiendish_vigor":        {"name":"Fiendish Vigor","desc":"Cast False Life at 1st level at will (1d4+4 temp HP)","level":2},
    "gaze_of_two_minds":     {"name":"Gaze of Two Minds","desc":"Use a willing humanoid's senses, cast targeted spells from their space","level":2},
    "lifedrinker":           {"name":"Lifedrinker","desc":"Add Cha mod as necrotic damage on Pact Weapon hit (requires Pact of the Blade)","level":12,"prereq":"Pact of the Blade"},
    "mask_of_many_faces":    {"name":"Mask of Many Faces","desc":"Cast Disguise Self at will","level":2},
    "master_of_myriad_forms":{"name":"Master of Myriad Forms","desc":"Cast Alter Self at will","level":15},
    "minions_of_chaos":      {"name":"Minions of Chaos","desc":"Cast Conjure Elemental once per long rest using a Warlock spell slot","level":9},
    "mire_the_mind":         {"name":"Mire the Mind","desc":"Cast Slow once per long rest using a Warlock spell slot","level":5},
    "misty_visions":         {"name":"Misty Visions","desc":"Cast Silent Image at will","level":2},
    "one_with_shadows":      {"name":"One with Shadows","desc":"Become invisible in dim light/darkness while not moving or acting","level":5},
    "otherworldly_leap":     {"name":"Otherworldly Leap","desc":"Cast Jump at will","level":9},
    "repelling_blast":       {"name":"Repelling Blast","desc":"Eldritch Blast pushes target 10 ft away (per hit)","level":2},
    "sculptor_of_flesh":     {"name":"Sculptor of Flesh","desc":"Cast Polymorph once per long rest using a Warlock spell slot","level":7},
    "sign_of_ill_omen":      {"name":"Sign of Ill Omen","desc":"Cast Bestow Curse once per long rest using a Warlock spell slot","level":5},
    "thief_of_five_fates":   {"name":"Thief of Five Fates","desc":"Cast Bane once per long rest using a Warlock spell slot","level":2},
    "thirsting_blade":       {"name":"Thirsting Blade","desc":"Attack twice with Pact Weapon (Extra Attack, requires Pact of the Blade)","level":5,"prereq":"Pact of the Blade"},
    "visions_of_distant_realms":{"name":"Visions of Distant Realms","desc":"Cast Arcane Eye at will","level":15},
    "voice_of_the_chain_master":{"name":"Voice of the Chain Master","desc":"Communicate telepathically with, perceive through, and speak through your familiar (requires Pact of the Chain)","level":2,"prereq":"Pact of the Chain"},
    "whispers_of_the_grave": {"name":"Whispers of the Grave","desc":"Cast Speak with Dead at will","level":9},
    "witch_sight":           {"name":"Witch Sight","desc":"See invisible creatures/illusions within 30 ft without needing to perceive them","level":15},
}
INVOCATION_PICKS: dict[int,int] = {2:2,5:1,7:1,9:1,12:1,15:1,18:1}

# ── Pact Boon (Warlock L3, pick 1 of 4) ──────────────────────────────
PACT_BOON_LEVELS: dict[str, int] = {"Warlock": 3}
PACT_BOON_OPTIONS: dict[str, dict] = {
    "pact_of_the_chain":  {"name":"Pact of the Chain","desc":"Learn Find Familiar; familiar can take the Attack action, gains special forms (imp, pseudodragon, quasit, sprite)"},
    "pact_of_the_blade":  {"name":"Pact of the Blade","desc":"Create a pact weapon as an action; you are proficient with it; it counts as magical"},
    "pact_of_the_tome":   {"name":"Pact of the Tome","desc":"Gain a Book of Shadows with 3 cantrips from any class; ritual casting if you take Book of Ancient Secrets"},
    "pact_of_the_talisman":{"name":"Pact of the Talisman","desc":"Wearer can add d4 to a failed ability check, prof bonus times per long rest"},
}

# ── Battle Master Maneuvers (Fighter L3/7/10/15, requires Battle Master) ──
MANEUVER_LEVELS: dict[str, list[int]] = {"Battle Master": [3, 7, 10, 15]}
MANEUVER_OPTIONS: dict[str, dict] = {
    "commanders_strike":   {"name":"Commander's Strike","desc":"Forgo one attack; ally uses reaction to make one weapon attack + superiority die to damage"},
    "disarming_attack":    {"name":"Disarming Attack","desc":"Add superiority die to damage; target makes Str save or drops held item"},
    "distracting_strike":  {"name":"Distracting Strike","desc":"Add superiority die to damage; next ally attack vs target has advantage"},
    "evasive_footwork":    {"name":"Evasive Footwork","desc":"Add superiority die to AC while moving (no action, on your turn)"},
    "feinting_attack":     {"name":"Feinting Attack","desc":"Bonus action: add superiority die to next attack roll + damage (if hit)"},
    "goading_attack":      {"name":"Goading Attack","desc":"Add superiority die to damage; target Wis save or disadv vs others until your next turn end"},
    "lunging_attack":      {"name":"Lunging Attack","desc":"Add superiority die to damage; melee weapon reach +5ft for this attack"},
    "maneuvering_attack":  {"name":"Maneuvering Attack","desc":"Add superiority die to damage; one ally moves half speed without provoking OA (reaction)"},
    "menacing_attack":     {"name":"Menacing Attack","desc":"Add superiority die to damage; target Wis save or frightened until your next turn end"},
    "parry":               {"name":"Parry","desc":"Reaction: reduce incoming melee damage by superiority die + Dex mod"},
    "precision_attack":    {"name":"Precision Attack","desc":"Add superiority die to attack roll after roll but before result known"},
    "pushing_attack":      {"name":"Pushing Attack","desc":"Add superiority die to damage; target Str save or pushed 15 ft"},
    "rally":               {"name":"Rally","desc":"Bonus action: ally gains temp HP = superiority die + Cha mod"},
    "riposte":             {"name":"Riposte","desc":"Reaction: when a creature misses you, make a melee attack + superiority die to damage"},
    "sweeping_attack":     {"name":"Sweeping Attack","desc":"Add superiority die to damage; deal superiority die damage to different adjacent creature"},
    "trip_attack":         {"name":"Trip Attack","desc":"Add superiority die to damage; target Str save or knocked prone (Large or smaller)"},
}
MANEUVER_PICKS: dict[int,int] = {3:3,7:2,10:2,15:2}  # level → total known

# ── Magical Secrets (Bard L10/14/18, Lore Bard gets L6 bonus) ────────
MAGICAL_SECRETS_LEVELS: dict[str, list[int]] = {"Bard": [10, 14, 18], "College of Lore": [6]}
MAGICAL_SECRETS_PICKS: dict[int,int] = {6:2,10:2,14:2,18:2}

# ── Totem Spirit (Barbarian Totem Warrior L3/6/14, pick per tier) ────
TOTEM_SPIRIT_LEVELS: dict[str, list[int]] = {"Path of the Totem Warrior": [3, 6, 14]}
TOTEM_SPIRIT_OPTIONS: dict[str, dict] = {
    "bear":   {"name":"Bear","desc":"Resistance to all damage except psychic while raging"},
    "eagle":  {"name":"Eagle","desc":"Bonus action Dash while raging; opportunity attacks against you have disadvantage"},
    "wolf":   {"name":"Wolf","desc":"Allies within 5 ft of you have advantage on melee attacks vs targets adjacent to you"},
    "elk":    {"name":"Elk (SCAG)","desc":"Speed +15 ft while raging"},
    "tiger":  {"name":"Tiger (SCAG)","desc":"Jump distance +10 ft while raging; bonus action: move up to half speed after jump attack"},
}
TOTEM_SPIRIT_TIER_LABELS: dict[int, str] = {3:"Totem Spirit", 6:"Aspect of the Beast", 14:"Totemic Attunement"}

# ── Hunter's Prey (Ranger Hunter L3, pick 1 of 3) ─────────────────────
HUNTERS_PREY_LEVELS: dict[str, int] = {"Hunter": 3}
HUNTERS_PREY_OPTIONS: dict[str, dict] = {
    "colossus_slayer": {"name":"Colossus Slayer","desc":"Once per turn, +1d8 damage to a wounded creature"},
    "giant_killer":    {"name":"Giant Killer","desc":"Reaction to attack Large+ creature that attacks you (whether it hits or misses)"},
    "horde_breaker":   {"name":"Horde Breaker","desc":"Once per turn, make an additional attack vs a different creature within 5 ft of first target"},
}

# ── Artificer Infusions (L2, pick from list) ─────────────────────────
INFUSION_LEVELS: dict[str, int] = {"Artificer": 2}
INFUSION_OPTIONS: dict[str, dict] = {
    "enhanced_defense":        {"name":"Enhanced Defense","desc":"+1 AC to armor or shield (+2 at L10)"},
    "enhanced_weapon":         {"name":"Enhanced Weapon","desc":"+1 to attack and damage rolls (+2 at L10)"},
    "repeating_shot":          {"name":"Repeating Shot","desc":"Weapon gains +1 atk/dmg, ignores loading, creates its own ammo"},
    "returning_weapon":        {"name":"Returning Weapon","desc":"Thrown weapon returns to hand immediately after attack"},
    "replicate_magic_item":    {"name":"Replicate Magic Item","desc":"Create a common/uncommon magic item from a list"},
    "homunculus_servant":      {"name":"Homunculus Servant","desc":"Create a tiny construct companion"},
    "radiant_weapon":          {"name":"Radiant Weapon","desc":"+1 atk/dmg; reaction to blind attacker for 1 round (Con save)"},
    "spell_refueling_ring":    {"name":"Spell-Refueling Ring","desc":"Recover one spell slot of L3 or lower once per day"},
    "boots_of_the_winding_path":{"name":"Boots of the Winding Path","desc":"Bonus action teleport 15 ft to unoccupied space you've been this turn"},
    "armor_of_magical_strength":{"name":"Armor of Magical Strength","desc":"+Int mod to Str checks/saves, limited uses"},
}
INFUSION_PICKS: dict[int,int] = {2:4}  # level → known infusions

# Cantrip progression
CANTRIPS_PROGRESSION: dict[str, dict[int, int]] = {
    "full": {1: 2, 4: 3, 10: 4},
    "warlock": {1: 2, 4: 3, 10: 4},
    "cleric": {1: 3, 4: 4, 10: 5},
}

# ── PHB 2014 Feats ─────────────────────────────────────────────────────
FEATS: dict[str, dict] = {
    "alert": {"name":"Alert","desc":"+5 initiative, can't be surprised, hidden creatures don't get advantage on attack rolls","prereq":None},
    "athlete": {"name":"Athlete","desc":"+1 Str/Dex, standing from prone costs 5 ft, climbing doesn't cost extra movement, running jump only needs 5 ft","prereq":None,"asi":{"choices":["Strength","Dexterity"],"amount":1}},
    "actor": {"name":"Actor","desc":"+1 Cha, adv on Deception/Performance to pass as someone else, mimic speech","prereq":None,"asi":{"choices":["Charisma"],"amount":1}},
    "charger": {"name":"Charger","desc":"When you Dash, bonus action melee attack with +5 dmg or shove 10 ft","prereq":None},
    "crossbow_expert": {"name":"Crossbow Expert","desc":"Ignore loading, no disadv on ranged attacks in melee, bonus action hand crossbow attack","prereq":None},
    "defensive_duelist": {"name":"Defensive Duelist","desc":"While wielding finesse weapon, add prof to AC as reaction vs melee attack","prereq":"Dexterity 13+"},
    "dual_wielder": {"name":"Dual Wielder","desc":"+1 AC while dual wielding, use non-light one-handed weapons, draw/stow two at once","prereq":None},
    "dungeon_delver": {"name":"Dungeon Delver","desc":"Adv on Perception/Investigation vs secret doors & traps, adv on trap saves, resist trap dmg","prereq":None},
    "durable": {"name":"Durable","desc":"+1 Con, min heal from Hit Die = 2×Con mod","prereq":None,"asi":{"choices":["Constitution"],"amount":1}},
    "elemental_adept": {"name":"Elemental Adept","desc":"Pick one damage type; spells ignore resistance, treat 1s as 2s on dmg dice","prereq":"Ability to cast at least one spell"},
    "fey_touched": {"name":"Fey Touched","desc":"+1 Int/Wis/Cha, learn Misty Step + 1 L1 div/ench spell, free 1/day each","prereq":None,"asi":{"choices":["Intelligence","Wisdom","Charisma"],"amount":1}},
    "grappler": {"name":"Grappler","desc":"Adv on attacks vs grappled targets, can pin restrained creature","prereq":"Strength 13+"},
    "great_weapon_master": {"name":"Great Weapon Master","desc":"On crit/kill with heavy melee, bonus action attack. -5 atk for +10 dmg on heavy attacks","prereq":None},
    "healer": {"name":"Healer","desc":"Stabilize → 1 HP. Use healer's kit: 1d6+4+target's HD HP per short rest","prereq":None},
    "heavily_armored": {"name":"Heavily Armored","desc":"+1 Str, gain heavy armor proficiency","prereq":"Medium armor proficiency","asi":{"choices":["Strength"],"amount":1}},
    "heavy_armor_master": {"name":"Heavy Armor Master","desc":"+1 Str, B/P/S from nonmagical weapons reduced by 3 while in heavy armor","prereq":"Heavy armor proficiency","asi":{"choices":["Strength"],"amount":1}},
    "inspiring_leader": {"name":"Inspiring Leader","desc":"10-min speech gives up to 6 allies temp HP = level + Cha mod","prereq":"Charisma 13+"},
    "keen_mind": {"name":"Keen Mind","desc":"+1 Int, always know north, time till sunrise/sunset, recall past month perfectly","prereq":None,"asi":{"choices":["Intelligence"],"amount":1}},
    "lightly_armored": {"name":"Lightly Armored","desc":"+1 Str/Dex, gain light armor proficiency","prereq":None,"asi":{"choices":["Strength","Dexterity"],"amount":1}},
    "linguist": {"name":"Linguist","desc":"+1 Int, learn 3 languages, create written ciphers","prereq":None,"asi":{"choices":["Intelligence"],"amount":1}},
    "lucky": {"name":"Lucky","desc":"3 luck points per long rest, spend to reroll any d20 or force enemy reroll","prereq":None},
    "mage_slayer": {"name":"Mage Slayer","desc":"Reaction melee attack vs adjacent caster, adv on saves vs adjacent spells","prereq":None},
    "magic_initiate": {"name":"Magic Initiate","desc":"Learn 2 cantrips + 1 L1 spell from one class's list; free 1/day casting","prereq":None},
    "martial_adept": {"name":"Martial Adept","desc":"Learn 2 Battle Master maneuvers, one d6 superiority die","prereq":None},
    "medium_armor_master": {"name":"Medium Armor Master","desc":"No disadv on Stealth in medium armor, Dex cap +3 instead of +2","prereq":"Medium armor proficiency"},
    "mobile": {"name":"Mobile","desc":"Speed +10 ft, Dash ignores difficult terrain, no OA from targets you attacked","prereq":None},
    "moderately_armored": {"name":"Moderately Armored","desc":"+1 Str/Dex, gain medium armor + shield proficiency","prereq":"Light armor proficiency","asi":{"choices":["Strength","Dexterity"],"amount":1}},
    "mounted_combatant": {"name":"Mounted Combatant","desc":"Adv on melee vs unmounted smaller than mount, redirect attacks to you, mount takes half/zero AoE","prereq":None},
    "observant": {"name":"Observant","desc":"+1 Int/Wis, +5 passive Perception and Investigation, read lips","prereq":None,"asi":{"choices":["Intelligence","Wisdom"],"amount":1}},
    "polearm_master": {"name":"Polearm Master","desc":"Bonus action 1d4 butt attack, OA when creatures enter reach with polearms","prereq":None},
    "resilient": {"name":"Resilient","desc":"+1 to one ability, gain proficiency in that ability's saving throw","prereq":None,"asi":{"choices":["Strength","Dexterity","Constitution","Intelligence","Wisdom","Charisma"],"amount":1}},
    "ritual_caster": {"name":"Ritual Caster","desc":"Gain ritual book; learn 2 L1 rituals, can add more from scrolls","prereq":"Intelligence or Wisdom 13+"},
    "savage_attacker": {"name":"Savage Attacker","desc":"Once per turn, reroll melee weapon damage dice and use either total","prereq":None},
    "sentinel": {"name":"Sentinel","desc":"OA reduces speed to 0, OA even vs Disengage, reaction attack vs attackers who target allies","prereq":None},
    "shadow_touched": {"name":"Shadow Touched","desc":"+1 Int/Wis/Cha, learn Invisibility + one L1 necro/illusion spell, free 1/day each","prereq":None,"asi":{"choices":["Intelligence","Wisdom","Charisma"],"amount":1}},
    "sharpshooter": {"name":"Sharpshooter","desc":"No disadv at long range, ignore half/three-quarters cover, -5 atk for +10 dmg","prereq":None},
    "shield_master": {"name":"Shield Master","desc":"Bonus action shove after Attack, add shield AC to Dex saves vs single-target spells, take zero dmg instead of half on successful AoE Dex save","prereq":None},
    "skilled": {"name":"Skilled","desc":"Gain proficiency in any 3 skills or tools","prereq":None},
    "skulker": {"name":"Skulker","desc":"Ranged attacks in dim light don't reveal position, hiding only needs light obscurement","prereq":"Dexterity 13+"},
    "spell_sniper": {"name":"Spell Sniper","desc":"Ranged spell attacks ignore half/three-quarters cover, range doubled, learn one attack cantrip","prereq":"Ability to cast at least one spell"},
    "tavern_brawler": {"name":"Tavern Brawler","desc":"+1 Str/Con, proficient in improvised weps (d4), bonus action grapple on unarmed hit","prereq":None,"asi":{"choices":["Strength","Constitution"],"amount":1}},
    "tough": {"name":"Tough","desc":"HP maximum increases by 2 per character level (retroactive)","prereq":None},
    "war_caster": {"name":"War Caster","desc":"Adv on Con saves for concentration, somatic components with weapon/shield, cast spell as OA","prereq":"Ability to cast at least one spell"},
    "weapon_master": {"name":"Weapon Master","desc":"+1 Str/Dex, gain proficiency with 4 weapons","prereq":None,"asi":{"choices":["Strength","Dexterity"],"amount":1}},
}

# Tag all PHB 2014 feats with source
for _feat in FEATS.values():
    if not _feat.get("source"):
        _feat["source"] = "Player's Handbook p.165-170"

# ── Feature → Combat Action mapping ──────────────────────────────────
# Maps feature name (lowercase) to (action_type, short_action_label)
# action_type: "Action", "Bonus Action", or "Reaction"
FEATURE_ACTION_TYPES = {
    # Barbarian
    "rage":                 ("Bonus Action", "Rage — advantage on STR, +2 dmg, resist B/P/S"),
    # Bard
    "bardic inspiration":   ("Bonus Action", "Bardic Inspiration — grant 1d6 to ally"),
    # Cleric / Paladin
    "channel divinity":     ("Action", "Channel Divinity — invoke divine power"),
    # Druid
    "wild shape":           ("Action", "Wild Shape — transform into a beast"),
    # Fighter
    "action surge":         ("Action", "Action Surge — take an additional action"),
    "indomitable":          ("Reaction", "Indomitable — reroll a failed saving throw"),
    "second wind":          ("Bonus Action", "Second Wind — regain 1d10 + level HP"),
    # Paladin
    "divine sense":         ("Action", "Divine Sense — detect celestials/fiends/undead"),
    "lay on hands":         ("Action", "Lay on Hands — heal 5×level HP"),
    # Rogue (features not in LIMITED_USE but present in get_class_features)
    "cunning action":       ("Bonus Action", "Cunning Action — Dash, Disengage, or Hide"),
    "uncanny dodge":        ("Reaction", "Uncanny Dodge — halve damage from one attack"),
    # Monk
    "flurry of blows":      ("Bonus Action", "Flurry of Blows — two unarmed strikes (1 ki)"),
    "patient defense":      ("Bonus Action", "Patient Defense — Dodge as bonus action (1 ki)"),
    "step of the wind":     ("Bonus Action", "Step of the Wind — Dash/Disengage + jump (1 ki)"),
    # Dragonborn
    "breath weapon":        ("Action", "Breath Weapon — 2d6 damage, DEX save (DC 8+CON+PB)"),
    # Drow
    "drow magic":           ("Action", "Drow Magic — faerie fire (L3) or darkness (L5)"),
    # Tiefling
    "infernal legacy":      ("Action", "Infernal Legacy — hellish rebuke (L3) or darkness (L5)"),
    # Duergar
    "duergar magic":        ("Action", "Duergar Magic — enlarge/reduce (L3) or invisibility (L5)"),
    # Eladrin
    "fey step":             ("Bonus Action", "Fey Step — teleport 30ft (1/short rest)"),
    # Shadar-kai
    "blessing of the raven queen": ("Bonus Action", "Blessing of the Raven Queen — teleport 30ft (1/long rest)"),
    # Genasi
    "mingle with the wind": ("Action", "Mingle with the Wind — levitate (1/long rest at L3)"),
    "merge with stone":     ("Action", "Merge with Stone — pass without trace (1/long rest at L3)"),
    "reach to the blaze":   ("Action", "Reach to the Blaze — burning hands (1/long rest at L3)"),
    "call to the wave":     ("Action", "Call to the Wave — create or destroy water (1/long rest at L3)"),
    # Resource pools (not combat actions per se, but tracked on Actions tab)
    "ki":                   ("Resource", "Ki — spend on Flurry, Patient Defense, Step of the Wind"),
    "sorcery points":       ("Resource", "Sorcery Points — spend on Metamagic options"),
    # Recovery / out-of-combat features
    "mystic arcanum":       ("Action", "Mystic Arcanum — cast a high-level Warlock spell (1/LR)"),
    "arcane_recovery":      ("Short Rest", "Arcane Recovery — regain spell slots on short rest"),
    # Barbarian — Path of the Berserker
    "intimidating presence":("Action", "Intimidating Presence — frighten one creature, WIS save"),
    "retaliation":          ("Reaction", "Retaliation — melee attack against attacker who damaged you"),
    # Cleric — Light Domain
    "warding flare":        ("Reaction", "Warding Flare — impose disadvantage on an attack against you"),
    "improved flare":       ("Reaction", "Improved Flare — impose disadvantage on an attack against ally"),
    "corona of light":      ("Action", "Corona of Light — 60ft bright light, 1 min, disadv on saves vs light/fire"),
    # Cleric — Nature Domain
    "dampen elements":      ("Reaction", "Dampen Elements — grant resistance to acid/cold/fire/lightning/thunder"),
    "master of nature":     ("Bonus Action", "Master of Nature — command beasts and plants (1/LR)"),
    # Cleric — Tempest Domain
    "wrath of the storm":   ("Reaction", "Wrath of the Storm — 2d8 lightning/thunder vs attacker"),
    "thunderbolt strike":   ("Reaction", "Thunderbolt Strike — push Large or smaller creature 10ft on lightning dmg"),
    "stormborn":            ("Action", "Stormborn — 60ft fly speed, 1 min (1/LR)"),
    # Cleric — Trickery Domain
    "blessing of the trickster": ("Action", "Blessing of the Trickster — grant adv on Stealth for 1 hour"),
    "improved duplicity":   ("Action", "Improved Duplicity — create up to 4 illusory duplicates"),
    # Cleric — Knowledge Domain
    "visions of the past":  ("Action", "Visions of the Past — object/area reading, 1/SR"),
    # Cleric — War Domain
    "war priest":           ("Bonus Action", "War Priest — make one weapon attack (WIS mod/LR)"),
    "avatar of battle":     ("Action", "Avatar of Battle — resist B/P/S from nonmagical weapons, 1 min (1/LR)"),
    # Paladin capstones
    "holy nimbus":          ("Action", "Holy Nimbus — 30ft bright light, 10 radiant dmg/turn, 1 min (1/LR)"),
    "avenging angel":       ("Action", "Avenging Angel — 60ft fly, 30ft fear aura, 1 hour (1/LR)"),
    "elder champion":       ("Action", "Elder Champion — regen 10 HP/turn, BA spells, 1 min (1/LR)"),
    # Paladin — Oathbreaker
    "dread lord":           ("Action", "Dread Lord — 30ft fear/punish aura, minions, 1 min (1/LR)"),
    # Sorcerer — Draconic
    "dragon wings":         ("Bonus Action", "Dragon Wings — fly speed = walk speed"),
    "draconic presence":    ("Action", "Draconic Presence — 60ft awe/fear aura, 1 min, 5 sorcery pts (1/LR)"),
    # Sorcerer — Wild Magic
    "wild magic surge":     ("Reaction", "Wild Magic Surge — roll on d100 table when triggered"),
    "tides of chaos":       ("Reaction", "Tides of Chaos — grant self advantage on attack/save/check (1/SR)"),
    "bend luck":            ("Reaction", "Bend Luck — add/subtract 1d4 to a creature's roll (2 sorcery pts)"),
    # Warlock — The Archfey
    "fey presence":         ("Action", "Fey Presence — 10ft cube, WIS save or charmed/frightened (1/SR)"),
    "misty escape":         ("Reaction", "Misty Escape — teleport 60ft + invisible after taking damage (1/SR)"),
    "dark delirium":        ("Action", "Dark Delirium — target in illusory realm, 1 min (1/SR)"),
    # Warlock — The Great Old One
    "awakened mind":        ("Action", "Awakened Mind — telepathy 30ft"),
    "entropic ward":        ("Reaction", "Entropic Ward — impose disadvantage on attack (1/SR)"),
    "create thrall":        ("Action", "Create Thrall — permanently charm an incapacitated humanoid (1/LR)"),
    # Warlock — The Fiend
    "dark one's blessing":  ("Reaction", "Dark One's Blessing — gain temp HP on kill"),
    "dark one's own luck":  ("Reaction", "Dark One's Own Luck — add d10 to ability check/save (1/SR)"),
    "hurl through hell":    ("Action", "Hurl Through Hell — 10d10 psychic, 1 round banish (1/LR)"),
    # Wizard — Divination
    "portent":              ("Reaction", "Portent — replace any d20 roll with a pre-rolled result (2/LR)"),
    "greater portent":      ("Reaction", "Greater Portent — replace a third d20 roll (3/LR)"),
    "the third eye":        ("Action", "The Third Eye — darkvision, ethereal sight, read any language"),
    # Wizard — Conjuration
    "minor conjuration":    ("Action", "Minor Conjuration — create a small nonmagical object"),
    "benign transposition": ("Action", "Benign Transposition — teleport 30ft or swap with ally (1/LR)"),
    # Wizard — Enchantment
    "hypnotic gaze":        ("Action", "Hypnotic Gaze — incapacitate a creature until next turn"),
    "instinctive charm":    ("Reaction", "Instinctive Charm — redirect an attack to another creature (1/LR)"),
    "alter memories":       ("Action", "Alter Memories — modify memory of charmed target"),
    # Wizard — Illusion
    "improved minor illusion": ("Action", "Improved Minor Illusion — create sound + image together"),
    "illusory self":        ("Reaction", "Illusory Self — auto-miss vs one attack (1/SR)"),
    "illusory reality":     ("Bonus Action", "Illusory Reality — make one illusion object temporarily real (1/LR)"),
    # Wizard — Necromancy
    "command undead":       ("Action", "Command Undead — charm an undead (INT save, 1/LR)"),
    # Wizard — Transmutation
    "minor alchemy":        ("Action", "Minor Alchemy — temporarily transmute material"),
    "transmuter's stone":   ("Action", "Transmuter's Stone — grant buff to holder (darkvision/speed/resist/CON)"),
    "master transmuter":    ("Action", "Master Transmuter — Panacea, Restore Life, or Restore Youth (1 use)"),
    # Wizard — Evocation
    "sculpt spells":        ("Reaction", "Sculpt Spells — protect allies from your AoE spells"),
    # Barbarian — Path of the Totem Warrior
    "totem spirit":         ("Action", "Totem Spirit — choose Bear/Eagle/Wolf spirit boon"),
    "aspect of the beast":  ("Action", "Aspect of the Beast — choose a second totem animal benefit"),
    "totemic attunement":   ("Action", "Totemic Attunement — choose a third totem animal benefit"),
    # Rogue — Arcane Trickster
    "mage hand legerdemain": ("Bonus Action", "Mage Hand Legerdemain — invisible hand, stow/retrieve, pickpocket"),
    "magical ambush":       ("Bonus Action", "Magical Ambush — impose disadvantage on spell save from hiding"),
    "versatile trickster":  ("Bonus Action", "Versatile Trickster — Mage Hand distracts for advantage"),
    "spell thief":          ("Reaction", "Spell Thief — steal a spell being cast (1/LR)"),
    # Rogue — Thief
    "fast hands":           ("Bonus Action", "Fast Hands — Sleight of Hand, thieves' tools, or Use an Object"),
    # Rogue — Assassin
    "assassinate":          ("Reaction", "Assassinate — auto-crit surprised creatures, adv vs lower initiative"),
    # Ranger — Hunter
    "multiattack":          ("Action", "Multiattack — Volley (ranged AoE) or Whirlwind Attack (melee AoE)"),
}

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
        "A The Hexblade subclass feature. Grants a thematic ability tied to the The Hexblade's specialty — check your sourcebook for full mechanical details.",
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
        "A The Hexblade subclass feature. Grants a thematic ability tied to the The Hexblade's specialty — check your sourcebook for full mechanical details.",
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
        "A The Hexblade subclass feature. Grants a thematic ability tied to the The Hexblade's specialty — check your sourcebook for full mechanical details.",
    "hexblade's curse":
        "A martial technique focused on blade mastery — enhancing weapon attacks with supernatural speed, precision, or magical effects.",
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
        "A The Hexblade subclass feature. Grants a thematic ability tied to the The Hexblade's specialty — check your sourcebook for full mechanical details.",
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
    """Return the dice display string, scaling cantrips to character level."""
    display = dice_info.get("display", "")
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
    return f"{count}d{die}"

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
RECOMMENDED_FEATS = {
    "Barbarian":    ["Great Weapon Master","Polearm Master","Sentinel","Resilient (Wisdom)","Tough"],
    "Bard":         ["Inspiring Leader","War Caster","Resilient (Constitution)","Lucky","Alert","Fey Touched"],
    "Cleric":       ["War Caster","Resilient (Constitution)","Telekinetic","Lucky","Alert","Fey Touched"],
    "Druid":        ["War Caster","Resilient (Constitution)","Observant","Lucky","Alert","Fey Touched"],
    "Fighter":      ["Great Weapon Master","Polearm Master","Sentinel","Sharpshooter","Crossbow Expert","Tough"],
    "Monk":         ["Mobile","Crusher","Sentinel","Alert","Tough"],
    "Paladin":      ["Great Weapon Master","Polearm Master","Sentinel","Inspiring Leader","Resilient (Constitution)","Fey Touched"],
    "Ranger":       ["Sharpshooter","Crossbow Expert","Fey Touched","Resilient (Constitution)","Alert","Lucky"],
    "Rogue":        ["Sharpshooter","Crossbow Expert","Skulker","Lucky","Alert","Mobile"],
    "Sorcerer":     ["War Caster","Metamagic Adept","Fey Touched","Lucky","Alert","Elemental Adept"],
    "Warlock":      ["War Caster","Resilient (Constitution)","Fey Touched","Spell Sniper","Lucky","Alert"],
    "Wizard":       ["War Caster","Resilient (Constitution)","Telekinetic","Lucky","Alert","Fey Touched"],
}

# Scaled equipment by class and level tier (PHB starting equipment + reasonable progression)
# Levels: 1, 5, 10, 15, 20
SCALED_EQUIPMENT = {
    "Barbarian": {
        1:  ["Greataxe","2 Handaxes","Explorer's Pack","4 Javelins"],
        5:  ["+1 Greataxe","2 Handaxes","Explorer's Pack","4 Javelins","Breastplate"],
        10: ["+2 Greataxe","Explorer's Pack","Half Plate","Javelin of Lightning","Cloak of Protection","Potion of Giant Strength (Hill)"],
        15: ["+2 Greatsword","Half Plate +1","Cloak of Displacement","Belt of Giant Strength (Fire)","Ring of Protection","Boots of Speed"],
        20: ["+3 Greataxe","Half Plate +2","Belt of Storm Giant Strength","Cloak of Displacement","Ring of Protection","Boots of Speed","Mantle of Spell Resistance"],
    },
    "Bard": {
        1:  ["Rapier","Entertainer's Pack","Lute","Leather Armor","Dagger"],
        5:  ["+1 Rapier","Studded Leather","Entertainer's Pack","Lute","Wand of Magic Missiles"],
        10: ["+2 Rapier","Studded Leather +1","Instrument of the Bards (Cli Lyre)","Cloak of Protection","Wand of Web","Hat of Disguise"],
        15: ["+2 Rapier","Studded Leather +2","Instrument of the Bards (Bandore)","Ring of Protection","Cloak of Displacement","Mantle of Spell Resistance"],
        20: ["+3 Rapier","Studded Leather +3","Instrument of the Bards (Ollamh Harp)","Ring of Protection","Cloak of Displacement","Robe of the Archmagi"],
    },
    "Cleric": {
        1:  ["Mace","Scale Mail","Light Crossbow + 20 Bolts","Priest's Pack","Shield","Holy Symbol"],
        5:  ["+1 Mace","Splint Mail","Shield","Holy Symbol","Priest's Pack","Necklace of Prayer Beads"],
        10: ["+2 Mace","Plate Armor","Shield +1","Necklace of Prayer Beads","Cloak of Protection","Periapt of Wound Closure"],
        15: ["+2 Mace","Plate Armor +1","Shield +2","Amulet of the Devout +2","Cloak of Displacement","Ring of Spell Storing"],
        20: ["+3 Mace","Plate Armor +2","Shield +3","Amulet of the Devout +3","Cloak of Displacement","Ring of Spell Storing","Rod of Resurrection"],
    },
    "Druid": {
        1:  ["Wooden Shield","Scimitar","Leather Armor","Explorer's Pack","Druidic Focus"],
        5:  ["+1 Scimitar","Hide Armor","Wooden Shield","Explorer's Pack","Moon Sickle +1","Cloak of Elvenkind"],
        10: ["+2 Scimitar","Studded Leather","Wooden Shield +1","Moon Sickle +2","Staff of the Woodlands","Cloak of Protection"],
        15: ["+2 Scimitar","Studded Leather +1","Wooden Shield +2","Moon Sickle +2","Staff of the Woodlands","Ring of Protection","Dragonhide Breastplate"],
        20: ["+3 Scimitar","Dragonhide Half Plate","Wooden Shield +3","Moon Sickle +3","Staff of the Woodlands","Ring of Protection","Cloak of Displacement"],
    },
    "Fighter": {
        1:  ["Chain Mail","Longsword","Shield","Light Crossbow + 20 Bolts","Dungeoneer's Pack"],
        5:  ["Plate Armor","+1 Longsword","Shield","Longbow + 20 Arrows","Dungeoneer's Pack","Cloak of Protection"],
        10: ["Plate Armor +1","+2 Longsword","Shield +1","Longbow +1","Cloak of Displacement","Ring of Protection","Gauntlets of Ogre Power"],
        15: ["Plate Armor +2","+2 Greatsword","Longbow +2","Belt of Giant Strength (Hill)","Cloak of Displacement","Ring of Protection","Winged Boots"],
        20: ["Plate Armor +3","+3 Greatsword","Longbow +3","Belt of Giant Strength (Storm)","Cloak of Displacement","Ring of Protection","Winged Boots","Mantle of Spell Resistance"],
    },
    "Monk": {
        1:  ["Shortsword","Dungeoneer's Pack","10 Darts"],
        5:  ["+1 Shortsword","Dungeoneer's Pack","10 Darts","Bracers of Defense","Cloak of Protection"],
        10: ["+2 Shortsword","Bracers of Defense","Cloak of Displacement","Ring of Protection","Insignia of Claws","Boots of Speed"],
        15: ["+2 Shortsword","Bracers of Defense +1","Cloak of Displacement","Ring of Protection","Insignia of Claws","Boots of Speed","Dragonhide Belt +2"],
        20: ["+3 Shortsword","Bracers of Defense +2","Cloak of Displacement","Ring of Protection","Boots of Speed","Dragonhide Belt +3","Tome of Understanding"],
    },
    "Paladin": {
        1:  ["Longsword","Shield","5 Javelins","Priest's Pack","Chain Mail","Holy Symbol"],
        5:  ["+1 Longsword","Shield","Plate Armor","5 Javelins","Priest's Pack","Holy Symbol"],
        10: ["+2 Longsword","Shield +1","Plate Armor +1","Cloak of Protection","Holy Avenger (base)","Necklace of Prayer Beads"],
        15: ["+2 Greatsword","Plate Armor +2","Cloak of Displacement","Holy Avenger","Ring of Protection","Belt of Giant Strength (Hill)","Necklace of Prayer Beads"],
        20: ["+3 Greatsword","Plate Armor +3","Holy Avenger","Belt of Giant Strength (Storm)","Cloak of Displacement","Ring of Protection","Mantle of Spell Resistance"],
    },
    "Ranger": {
        1:  ["Longbow + 20 Arrows","Shortsword","Scale Mail","Explorer's Pack"],
        5:  ["+1 Longbow","Breastplate","Shortsword","Explorer's Pack","Cloak of Elvenkind","Bracers of Archery"],
        10: ["+2 Longbow","Studded Leather +1","Shortsword +1","Cloak of Elvenkind","Bracers of Archery","Ring of Protection","Boots of Elvenkind"],
        15: ["+2 Longbow","Studded Leather +2","Cloak of Displacement","Bracers of Archery","Ring of Protection","Oathbow","Boots of Speed"],
        20: ["+3 Longbow","Studded Leather +3","Cloak of Displacement","Bracers of Archery","Ring of Protection","Oathbow","Boots of Speed","Mantle of Spell Resistance"],
    },
    "Rogue": {
        1:  ["Rapier","Shortbow + 20 Arrows","Burglar's Pack","Leather Armor","2 Daggers","Thieves' Tools"],
        5:  ["+1 Rapier","Studded Leather","Shortbow","Burglar's Pack","Thieves' Tools","Cloak of Elvenkind","Gloves of Thievery"],
        10: ["+2 Rapier","Studded Leather +1","Shortbow +1","Cloak of Elvenkind","Gloves of Thievery","Ring of Protection","Boots of Elvenkind"],
        15: ["+2 Rapier","Studded Leather +2","Cloak of Displacement","Ring of Protection","Boots of Speed","Dagger of Venom","Hat of Disguise"],
        20: ["+3 Rapier","Studded Leather +3","Cloak of Displacement","Ring of Protection","Boots of Speed","Dagger of Venom","Hat of Disguise","Mantle of Spell Resistance"],
    },
    "Sorcerer": {
        1:  ["Light Crossbow + 20 Bolts","Arcane Focus","Dungeoneer's Pack","2 Daggers"],
        5:  ["Arcane Focus","Dungeoneer's Pack","Cloak of Protection","Wand of Magic Missiles","Elven Chain"],
        10: ["Bloodwell Vial +2","Elven Chain","Cloak of Protection","Wand of Fireballs","Ring of Spell Storing","Broom of Flying"],
        15: ["Bloodwell Vial +2","Elven Chain +1","Cloak of Displacement","Ring of Spell Storing","Wand of Fireballs","Robe of Stars","Staff of Power"],
        20: ["Bloodwell Vial +3","Robe of the Archmagi","Cloak of Displacement","Ring of Spell Storing","Robe of Stars","Staff of Power","Tome of Leadership and Influence"],
    },
    "Warlock": {
        1:  ["Light Crossbow + 20 Bolts","Arcane Focus","Scholar's Pack","Leather Armor","Dagger"],
        5:  ["+1 Rod of the Pact Keeper","Studded Leather","Scholar's Pack","Cloak of Protection","Wand of Web"],
        10: ["+2 Rod of the Pact Keeper","Studded Leather +1","Cloak of Protection","Wand of Fireballs","Ring of Spell Storing","Broom of Flying"],
        15: ["+2 Rod of the Pact Keeper","Studded Leather +2","Cloak of Displacement","Ring of Spell Storing","Robe of Stars","Staff of Power","Illusionist's Bracers"],
        20: ["+3 Rod of the Pact Keeper","Studded Leather +3","Cloak of Displacement","Ring of Spell Storing","Robe of the Archmagi","Staff of Power","Illusionist's Bracers"],
    },
    "Wizard": {
        1:  ["Quarterstaff","Arcane Focus","Scholar's Pack","Spellbook"],
        5:  ["Arcane Focus","Scholar's Pack","Spellbook","Cloak of Protection","Wand of Magic Missiles","Elven Chain"],
        10: ["Arcane Grimoire +2","Elven Chain","Cloak of Protection","Wand of Fireballs","Ring of Spell Storing","Broom of Flying"],
        15: ["Arcane Grimoire +2","Elven Chain +1","Cloak of Displacement","Ring of Spell Storing","Wand of Fireballs","Robe of Stars","Staff of Power"],
        20: ["Arcane Grimoire +3","Robe of the Archmagi","Cloak of Displacement","Ring of Spell Storing","Robe of Stars","Staff of Power","Tome of Clear Thought"],
    },
}

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
TREASURE_HOARD_COINS = {
    "0-4":   {"gp": "4d6*10"},       # avg 140 gp (was cp+sp+gp ≈ 196 gp)
    "5-10":  {"gp": "5d6*100"},      # avg 1750 gp (was cp+sp+gp+pp ≈ 3857 gp)
    "11-16": {"gp": "4d6*1000"},     # avg 14000 gp (was gp+pp ≈ 31500 gp)
    "17+":   {"gp": "8d6*1000"},     # avg 28000 gp (was gp+pp ≈ 322000 gp)
}

# Hoard d100 table: (range_low, range_high, gems_or_art, magic_table, magic_count)
# gems_or_art: tuple of (dice_expr, gp_per) or None
# magic_table: "A"-"I" or None
TREASURE_HOARD_TABLE = {
    "0-4": [
        (1, 6, None, None, 0),
        (7, 16, ("2d6", 10), None, 0),
        (17, 26, ("2d4", 25), None, 0),
        (27, 36, ("2d6", 50), None, 0),
        (37, 44, ("2d6", 10), "A", "1d6"),
        (45, 52, ("2d4", 25), "A", "1d6"),
        (53, 60, ("2d6", 50), "A", "1d6"),
        (61, 65, ("2d6", 10), "B", "1d4"),
        (66, 70, ("2d4", 25), "B", "1d4"),
        (71, 75, ("2d6", 50), "B", "1d4"),
        (76, 78, ("2d6", 10), "C", "1d4"),
        (79, 80, ("2d4", 25), "C", "1d4"),
        (81, 85, ("2d6", 50), "C", "1d4"),
        (86, 92, ("2d4", 25), "F", "1d4"),
        (93, 97, ("2d6", 50), "F", "1d4"),
        (98, 99, ("2d4", 25), "G", "1"),
        (100, 100, ("2d6", 50), "G", "1"),
    ],
    "5-10": [
        (1, 4, None, None, 0),
        (5, 10, ("2d4", 25), None, 0),
        (11, 16, ("3d6", 50), None, 0),
        (17, 22, ("3d6", 100), None, 0),
        (23, 28, ("2d4", 250), None, 0),
        (29, 32, ("2d4", 25), "A", "1d6"),
        (33, 36, ("3d6", 50), "A", "1d6"),
        (37, 40, ("3d6", 100), "A", "1d6"),
        (41, 44, ("2d4", 250), "A", "1d6"),
        (45, 49, ("2d4", 25), "B", "1d4"),
        (50, 54, ("3d6", 50), "B", "1d4"),
        (55, 59, ("3d6", 100), "B", "1d4"),
        (60, 63, ("2d4", 250), "B", "1d4"),
        (64, 66, ("2d4", 25), "C", "1d4"),
        (67, 69, ("3d6", 50), "C", "1d4"),
        (70, 72, ("3d6", 100), "C", "1d4"),
        (73, 74, ("2d4", 250), "C", "1d4"),
        (75, 76, ("2d4", 25), "D", "1"),
        (77, 78, ("3d6", 50), "D", "1"),
        (79, 79, ("3d6", 100), "D", "1"),
        (80, 80, ("2d4", 250), "D", "1"),
        (81, 84, ("2d4", 25), "F", "1d4"),
        (85, 88, ("3d6", 50), "F", "1d4"),
        (89, 91, ("3d6", 100), "F", "1d4"),
        (92, 94, ("2d4", 250), "F", "1d4"),
        (95, 96, ("3d6", 100), "G", "1d4"),
        (97, 98, ("2d4", 250), "G", "1d4"),
        (99, 99, ("3d6", 100), "H", "1"),
        (100, 100, ("2d4", 250), "H", "1"),
    ],
    "11-16": [
        (1, 3, None, None, 0),
        (4, 6, ("2d4", 250), None, 0),
        (7, 9, ("2d4", 750), None, 0),
        (10, 12, ("3d6", 500), None, 0),
        (13, 15, ("2d4", 1000), None, 0),
        (16, 19, ("2d4", 250), "A", "1d4"),
        (20, 23, ("2d4", 750), "A", "1d4"),
        (24, 26, ("3d6", 500), "A", "1d4"),
        (27, 29, ("2d4", 1000), "A", "1d4"),
        (30, 35, ("2d4", 250), "B", "1d6"),
        (36, 40, ("2d4", 750), "B", "1d6"),
        (41, 45, ("3d6", 500), "B", "1d6"),
        (46, 50, ("2d4", 1000), "B", "1d6"),
        (51, 54, ("2d4", 250), "C", "1d6"),
        (55, 58, ("2d4", 750), "C", "1d6"),
        (59, 62, ("3d6", 500), "C", "1d6"),
        (63, 66, ("2d4", 1000), "C", "1d6"),
        (67, 69, ("2d4", 250), "D", "1d4"),
        (70, 72, ("2d4", 750), "D", "1d4"),
        (73, 74, ("3d6", 500), "D", "1d4"),
        (75, 76, ("2d4", 1000), "D", "1d4"),
        (77, 78, ("2d4", 250), "E", "1d6"),
        (79, 80, ("2d4", 750), "E", "1d6"),
        (81, 82, ("3d6", 500), "E", "1d6"),
        (83, 84, ("2d4", 1000), "E", "1d6"),
        (85, 86, ("2d4", 250), "F", "1d4"),
        (87, 88, ("2d4", 750), "F", "1d4"),
        (89, 90, ("3d6", 500), "F", "1d4"),
        (91, 92, ("2d4", 1000), "F", "1d4"),
        (93, 94, ("3d6", 500), "G", "1d4"),
        (95, 96, ("2d4", 1000), "G", "1d4"),
        (97, 97, ("3d6", 500), "H", "1d4"),
        (98, 98, ("2d4", 1000), "H", "1d4"),
        (99, 99, ("3d6", 500), "I", "1"),
        (100, 100, ("2d4", 1000), "I", "1"),
    ],
    "17+": [
        (1, 2, None, None, 0),
        (3, 5, ("3d6", 1000), None, 0),
        (6, 8, ("2d4", 2500), None, 0),
        (9, 11, ("2d4", 7500), None, 0),
        (12, 14, ("3d6", 5000), None, 0),
        (15, 22, ("3d6", 1000), "C", "1d8"),
        (23, 30, ("2d4", 2500), "C", "1d8"),
        (31, 38, ("2d4", 7500), "C", "1d8"),
        (39, 46, ("3d6", 5000), "C", "1d8"),
        (47, 52, ("3d6", 1000), "D", "1d6"),
        (53, 58, ("2d4", 2500), "D", "1d6"),
        (59, 63, ("2d4", 7500), "D", "1d6"),
        (64, 68, ("3d6", 5000), "D", "1d6"),
        (69, 72, ("3d6", 1000), "E", "1d6"),
        (73, 76, ("2d4", 2500), "E", "1d6"),
        (77, 79, ("2d4", 7500), "E", "1d6"),
        (80, 82, ("3d6", 5000), "E", "1d6"),
        (83, 85, ("3d6", 1000), "F", "1d6"),
        (86, 88, ("2d4", 2500), "F", "1d6"),
        (89, 90, ("2d4", 7500), "F", "1d6"),
        (91, 92, ("3d6", 5000), "F", "1d6"),
        (93, 94, ("3d6", 1000), "G", "1d6"),
        (95, 96, ("2d4", 2500), "G", "1d6"),
        (97, 97, ("2d4", 7500), "G", "1d6"),
        (98, 98, ("3d6", 5000), "G", "1d6"),
        (99, 99, ("3d6", 1000), "H", "1d6"),
        (100, 100, ("2d4", 2500), "I", "1d6"),
    ],
}

# Magic item table → rarity/category filter for SRD pool
MAGIC_TABLE_POOLS = {
    "A": {"rarity": ["common", "uncommon", "varies"], "category": ["potion", "scroll", "wand", "wondrous item"]},
    "B": {"rarity": ["uncommon", "rare", "varies"], "category": ["armor", "weapon", "wondrous item", "ring", "rod", "staff"]},
    "C": {"rarity": ["rare", "very rare"], "category": ["armor", "weapon", "wondrous item", "ring", "rod", "staff"]},
    "D": {"rarity": ["very rare"], "category": ["armor", "weapon", "wondrous item", "ring", "rod", "staff"]},
    "E": {"rarity": ["uncommon", "rare"], "category": ["weapon", "armor", "rod", "staff", "wand"]},
    "F": {"rarity": ["rare", "very rare"], "category": ["weapon", "armor", "wondrous item"]},
    "G": {"rarity": ["very rare"], "category": ["weapon", "armor", "wondrous item", "ring", "rod", "staff"]},
    "H": {"rarity": ["legendary"], "category": None},
    "I": {"rarity": ["legendary", "artifact"], "category": None},
}


def _roll_dice(expr: str) -> int:
    """Roll a dice expression like '3d6*100' or '2d4' or '1'."""
    import random, re
    expr = expr.strip()
    if expr.isdigit():
        return int(expr)
    # Handle multiplier suffix: 3d6*100
    mult = 1
    m = re.match(r"(.+?)\*(\d+)", expr)
    if m:
        expr = m.group(1)
        mult = int(m.group(2))
    # Parse NdX
    m = re.match(r"(\d+)d(\d+)", expr)
    if m:
        count, sides = int(m.group(1)), int(m.group(2))
        return sum(random.randint(1, sides) for _ in range(count)) * mult
    return 0


def _pick_magic_item(table: str) -> dict | None:
    """Pick one random magic item from the SRD pool matching the table.
    
    Uses weighted selection: lower rarities are favored to match DMG table distributions.
    Table A: ~70% common, ~25% uncommon, ~5% varies
    Table B: ~60% uncommon, ~35% rare, ~5% varies
    Other tables: uniform random from matching pool.
    """
    import random
    pool_cfg = MAGIC_TABLE_POOLS.get(table, {})
    rarities = pool_cfg.get("rarity", [])
    categories = pool_cfg.get("category")
    
    # Filter SRD_MAGIC_ITEMS (includes merged manual items)
    candidates = []
    for item in SRD_MAGIC_ITEMS:
        item_rarity = (item.get("rarity", {}) or {}).get("name", "").lower()
        if rarities and item_rarity not in rarities:
            continue
        if categories:
            cat = (item.get("equipment_category", {}) or {}).get("name", "").lower()
            if cat not in categories:
                continue
        candidates.append(item)
    if not candidates:
        return None
    
    # Weighted selection: favor lower rarities for common/uncommon tables
    rarity_order = ["common", "uncommon", "rare", "very rare", "legendary", "artifact"]
    if len(rarities) > 1 and "common" in rarities:
        # Table A: pick from common-biased distribution
        weights = []
        for c in candidates:
            r = (c.get("rarity", {}) or {}).get("name", "").lower()
            if r == "common":
                weights.append(8)
            elif r == "uncommon":
                weights.append(3)
            else:
                weights.append(1)
        item = random.choices(candidates, weights=weights, k=1)[0]
    elif len(rarities) > 1 and "uncommon" in rarities and "rare" in rarities:
        # Table B/E: favor uncommon over rare
        weights = []
        for c in candidates:
            r = (c.get("rarity", {}) or {}).get("name", "").lower()
            if r == "uncommon":
                weights.append(3)
            elif r == "rare":
                weights.append(1)
            else:
                weights.append(1)
        item = random.choices(candidates, weights=weights, k=1)[0]
    else:
        item = random.choice(candidates)
    
    rarity = (item.get("rarity", {}) or {}).get("name", "")
    desc = " ".join(item.get("desc", [])[:3])  # first 3 sentences
    return {
        "name": item.get("name", "Unknown"),
        "rarity": rarity,
        "description": desc,
        "source": item.get("source", "") or "DMG 2014",
    }


def roll_treasure_hoard(cr_bracket: str) -> dict:
    """Roll a full treasure hoard per DMG 2014 p.137-139.

    Args:
        cr_bracket: one of '0-4', '5-10', '11-16', '17+'

    Returns dict with: coins (list of {type, amount, gp_value}), gems (list of {count, value}),
        magic_items (list of {name, rarity, description}), total_gp_value
    """
    import random

    coins = TREASURE_HOARD_COINS.get(cr_bracket, {})
    table = TREASURE_HOARD_TABLE.get(cr_bracket, [])
    if not table:
        return {"coins": [], "gems": [], "magic_items": [], "total_gp_value": 0}

    result = {"coins": [], "gems": [], "magic_items": [], "total_gp_value": 0}

    # Roll coins
    for coin_type, expr in coins.items():
        if expr:
            amount = _roll_dice(expr)
            if amount > 0:
                gp_conv = {"cp": 0.01, "sp": 0.1, "ep": 0.5, "gp": 1, "pp": 10}
                gp_value = int(amount * gp_conv.get(coin_type, 0))
                result["coins"].append({
                    "type": coin_type.upper(),
                    "amount": amount,
                    "gp_value": gp_value,
                    "label": f"{amount:,} {coin_type.upper()}",
                })
                result["total_gp_value"] += gp_value

    # Roll d100 for gems/art + magic items
    d100 = random.randint(1, 100)
    for lo, hi, gems_expr, magic_table, magic_count_expr in table:
        if lo <= d100 <= hi:
            # Gems / art objects
            if gems_expr:
                dice, gp_per = gems_expr
                count = _roll_dice(dice)
                value = count * gp_per
                result["gems"].append({
                    "count": count,
                    "value_per": gp_per,
                    "total_value": value,
                    "label": f"{count} × {gp_per}gp {'gems' if gp_per < 100 else 'art objects'}",
                })
                result["total_gp_value"] += value

            # Magic items
            if magic_table:
                item_count = _roll_dice(magic_count_expr)
                for _ in range(item_count):
                    mi = _pick_magic_item(magic_table)
                    if mi:
                        result["magic_items"].append(mi)
            break

    return result

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

    for item_name in equipped:
        key = item_name.lower()
        props = ITEM_PROPERTIES.get(key, {})
        if not props:
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
        # Strip use-count suffix for matching (e.g. "Action Surge (2 uses)" -> "action surge")
        import re
        _clean_key = re.sub(r'\s*\(\d+\s+uses?(?:\s+per\s+rest)?\s*\)\s*$', '', key, flags=re.IGNORECASE).strip()
        action_info = FEATURE_ACTION_TYPES.get(_clean_key) or FEATURE_ACTION_TYPES.get(key)
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


async def _call_gemini(prompt: str) -> str | None:
    """Tier 1: Google Gemini. Requires GOOGLE_API_KEY."""
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            result = resp.json()
            return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None

async def _call_openrouter(prompt: str) -> str | None:
    """Tier 2: OpenRouter free router (never charges). Requires OPENROUTER_API_KEY."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://characters.jamlarnet.stream",
                    "X-OpenRouter-Title": "D&D Character Manager",
                },
                json={
                    "model": "openrouter/free",  # auto-load-balances free models, can't charge
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                },
            )
            result = resp.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OR] error: {e}")
        return None

async def _call_ollama(prompt: str) -> str | None:
    """Tier 3: Local Ollama hermes3:8b. No API key needed."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "http://192.168.1.31:11434/api/generate",
                json={"model": "hermes3:8b-llama3.1-q8_0", "prompt": prompt, "stream": False},
            )
            result = resp.json()
            return result.get("response", "")
    except Exception:
        return None

async def _fetch_stable_horde_image(prompt: str, max_wait: int = 120) -> str | None:
    """Generate image via Stable Horde (free, no API key, crowdsourced).
    Returns base64 data URL or None.
    Uses urllib in a thread to avoid httpx IPv6 issues."""
    import base64, asyncio, json, urllib.request, urllib.error
    payload = json.dumps({
        "prompt": prompt[:1000],
        "params": {
            "width": 384, "height": 512,
            "steps": 15, "n": 1,
            "sampler_name": "k_euler_a",
        }
    }).encode()

    def _sync_request(url, data=None, headers=None, timeout=30):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        # Submit job
        headers = {"apikey": "0000000000", "Content-Type": "application/json"}
        resp = await asyncio.to_thread(_sync_request,
            "https://stablehorde.net/api/v2/generate/async", payload, headers, 30)
        if resp.status != 202:
            print(f"[IMG] Stable Horde submit failed: {resp.status}")
            return None
        req_id = json.loads(resp.read()).get("id", "")
        if not req_id:
            return None
        print(f"[IMG] Stable Horde job {req_id} submitted")

        # Poll for result
        for i in range(max_wait // 2):
            await asyncio.sleep(2)
            try:
                sr = await asyncio.to_thread(_sync_request,
                    f"https://stablehorde.net/api/v2/generate/status/{req_id}",
                    None, {}, 15)
                status = json.loads(sr.read())
                if status.get("done"):
                    gen = status.get("generations", [{}])[0]
                    img_url = gen.get("img", "")
                    if img_url:
                        ir = await asyncio.to_thread(_sync_request, img_url, None, {}, 30)
                        ct = ir.headers.get("Content-Type", "image/webp")
                        b64 = base64.b64encode(ir.read()).decode()
                        print(f"[IMG] Stable Horde done after {(i+1)*2}s")
                        return f"data:{ct};base64,{b64}"
                    break
                if i % 5 == 0:
                    print(f"[IMG] waiting... {i*2}s")
            except Exception:
                pass
        print(f"[IMG] Stable Horde timed out after {max_wait}s")
    except Exception as e:
        print(f"[IMG] Stable Horde error: {e}")
    return None

def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response, stripping markdown wrappers."""
    if not text:
        return None
    if "```" in text:
        block = text.split("```")[1]
        if block.startswith("json"):
            block = block[4:]
        text = block
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None

def _validate_and_fix(ai: dict, race: str = "", class_name: str = "", name: str = "") -> dict:
    """Ground AI output in PHB data. Fix hallucinations silently."""
    # Validate background against PHB list (p.125-141)
    if ai.get("background") not in PHB_BACKGROUNDS:
        ai["background"] = random.choice(PHB_BACKGROUNDS)
    # Validate alignment against PHB list (p.122)
    if ai.get("alignment") not in PHB_ALIGNMENTS:
        ai["alignment"] = random.choice(PHB_ALIGNMENTS)
    # Ensure name exists
    if not ai.get("name"):
        ai["name"] = name or random_name(race)["name"]
    # Ensure personality + backstory exist
    if not ai.get("personality"):
        ai["personality"] = f"Brave but reckless. Loyal to friends. Distrusts authority."
    if not ai.get("backstory"):
        bg = ai.get("background", "adventurer").lower()
        ai["backstory"] = f"A {race} {class_name} who grew up as a {bg}. They seek adventure and glory."
    return ai

@app.post("/api/ai/generate", response_class=JSONResponse)
async def ai_generate(request: Request):
    """Generate character flavor (name/bg/alignment/personality/backstory).
    All mechanical data is hardcoded from PHB. Only creative text is generated.
    Model chain: Gemini → OpenRouter → Ollama → deterministic fallback."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    name = data.get("name", "")

    # Prompt is constrained to PHB-approved options only
    bg_list = ", ".join(PHB_BACKGROUNDS)
    al_list = ", ".join(PHB_ALIGNMENTS)
    prompt = f"""Generate a D&D 5e character concept using ONLY these official Player's Handbook options.

Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
PHB Backgrounds: {bg_list}
PHB Alignments: {al_list}
{'Player name suggestion: ' + name if name else 'Generate a race-appropriate name.'}

Return ONLY valid JSON (no markdown, no explanation):
{{"name": "Firstname Lastname", "background": "one from PHB list above", "alignment": "one from PHB list above", "personality": "2-3 personality traits", "backstory": "2-3 sentence backstory connecting race, class, and background"}}"""

    # Tiered model chain
    TIER_NAMES = ["gemini", "openrouter", "ollama"]
    text = None
    used_tier = None
    for tier, caller in enumerate([_call_gemini, _call_openrouter, _call_ollama]):
        text = await caller(prompt)
        if text:
            used_tier = TIER_NAMES[tier]
            break
    print(f"[AI] tier={used_tier or 'fallback'} race={race} class={class_name}")

    ai = _extract_json(text) if text else None
    if ai:
        ai = _validate_and_fix(ai, race, class_name, name)
    else:
        ai = _fallback_generate(race, class_name, subclass, name)
    return JSONResponse(ai)

def _fallback_generate(race: str, class_name: str, subclass: str, name: str) -> dict:
    """Deterministic fallback when AI is unavailable."""
    if not name:
        name = random_name(race)["name"]
    bg = random.choice(BACKGROUNDS)
    al = random.choice(ALIGNMENTS)
    return {
        "name": name,
        "background": bg,
        "alignment": al,
        "personality": "Brave but reckless. Loyal to friends. Distrusts authority.",
        "backstory": f"A {race} {class_name}{' of the ' + subclass if subclass else ''} who grew up as a {bg.lower()}. They seek adventure and glory, driven by a desire to prove themselves to the world."
    }

# ── Custom Background Generation ──────────────────────────────────────────

# Background-appropriate items (common magic + mundane equipment from SRD/PHB)
# Format: "Name (SRD reference)" for magic items, plain name for mundane
BACKGROUND_ITEM_POOL = [
    # Potions (common)
    "Potion of Healing (SRD: Potion of Healing)",
    "Potion of Climbing (SRD: Potion of Climbing)",
    # Scrolls (common)
    "Spell Scroll — Cantrip (SRD: Spell Scroll Cantrip)",
    "Spell Scroll — 1st level (SRD: Spell Scroll 1st)",
    # Minor magic items (uncommon, not overpowered)
    "Hat of Disguise (SRD: Hat of Disguise)",
    "Goggles of Night (SRD: Goggles of Night)",
    "Rope of Climbing (SRD: Rope of Climbing)",
    "Helm of Comprehending Languages (SRD: Helm of Comprehending Languages)",
    "Lantern of Revealing (SRD: Lantern of Revealing)",
    "Potion of Animal Friendship (SRD: Potion of Animal Friendship)",
    "Potion of Water Breathing (SRD: Potion of Water Breathing)",
    "Potion of Growth (SRD: Potion of Growth)",
    "Oil of Slipperiness (SRD: Oil of Slipperiness)",
    "Dust of Disappearance (SRD: Dust of Disappearance)",
    "Restorative Ointment (SRD: Restorative Ointment)",
    # Mundane equipment / tools (PHB)
    "Healer's Kit",
    "Climber's Kit",
    "Disguise Kit",
    "Herbalism Kit",
    "Navigator's Tools",
    "Thieves' Tools",
    "Hunting Trap",
    "Magnifying Glass",
    "Spyglass",
    "Signal Whistle",
    "Grappling Hook",
    "Crowbar",
    "Block and Tackle",
    "Abacus",
    "Sealing Wax",
    "Merchant's Scale",
    "Fishing Tackle",
    "Hourglass",
    "Steel Mirror",
    "Vial of Perfume",
    "Vial of Acid",
    "Vial of Alchemist's Fire",
    "Vial of Antitoxin",
    "Flask of Holy Water",
    "Blanket",
    "Tinderbox",
    "Waterskin",
    "Rations (2 days)",
    "Chalk (5 pieces)",
    "String (10 feet)",
    "Bell",
    "Candle",
    "Soap",
    "Iron Pot",
    "Shovel",
    "Whetstone",
    "Two-Person Tent",
    # Trinkets and flavor
    "Silver locket with a portrait inside",
    "Small mechanical bird that chirps",
    "Bag of polished river stones",
    "Bone dice set with a worn leather cup",
    "Lucky rabbit's foot",
    "Old brass compass that doesn't point north",
    "Glass orb filled with swirling smoke",
    "Carved wooden whistle shaped like a dragon",
    "Sealed letter with a wax sigil",
    "Worn journal with cryptic entries",
]

@app.post("/api/ai/generate-background", response_class=JSONResponse)
async def ai_generate_background(request: Request):
    """Generate a unique custom background based on character choices.
    Model chain: Gemini → OpenRouter → Ollama → deterministic fallback."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    abilities = data.get("abilities", {})
    skills = data.get("skills", [])
    alignment = data.get("alignment", "")

    prompt = f"""Create a UNIQUE D&D 5e custom background for this character. Do NOT use any of the {len(BACKGROUNDS)} standard backgrounds. Invent something new and specific to this character.

Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
Ability scores: {abilities}
Skills: {skills}
Alignment: {alignment or 'Any'}

Choose exactly 3 items from this approved list. You may add flavor/rename them (e.g., "Granny's Healer's Kit"), but ALWAYS include the SRD reference in parentheses — e.g., "Granny's Kit (SRD: Healer's Kit)". Mundane items don't need an SRD reference. Do NOT invent items not on this list. Do NOT include armor, weapons, or powerful magic items.

Available items:
{chr(10).join('- ' + it for it in BACKGROUND_ITEM_POOL)}

Return ONLY valid JSON (no markdown, no explanation):
{{"name": "Background Name (2-4 words, creative and unique)", "description": "2-3 sentence description of this character's background and how it shaped them", "items": ["Flavored Name (SRD: Reference)", "Item 2", "Item 3"], "gp": 15}}"""

    TIER_NAMES = ["gemini", "openrouter", "ollama"]
    text = None
    used_tier = None
    for tier, caller in enumerate([_call_gemini, _call_openrouter, _call_ollama]):
        text = await caller(prompt)
        if text:
            used_tier = TIER_NAMES[tier]
            break
    print(f"[AI bg] tier={used_tier or 'fallback'} race={race} class={class_name}")

    ai = _extract_json(text) if text else None
    if ai and ai.get("name") and ai.get("description"):
        # Ensure items and gp exist
        if "items" not in ai or not isinstance(ai.get("items"), list):
            ai["items"] = _random_items(class_name)
        if "gp" not in ai:
            ai["gp"] = random.randint(10, 25)
        # Validate uniqueness — reject if it matches a PHB background name
        if ai["name"] in BACKGROUNDS:
            ai = _fallback_background(race, class_name, subclass)
    else:
        ai = _fallback_background(race, class_name, subclass)
    return JSONResponse(ai)

def _random_items(class_name: str) -> list:
    """Pick 3 random background-appropriate items from the pool."""
    return random.sample(BACKGROUND_ITEM_POOL, min(3, len(BACKGROUND_ITEM_POOL)))

def _fallback_background(race: str, class_name: str, subclass: str = "") -> dict:
    """Deterministic fallback — pool of unique backgrounds by class."""
    bg_pool = {
        "Barbarian": [
            {"name":"Tribal Outcast","desc":"Exiled from their clan for refusing to follow a corrupt chieftain. They wander the wilds, honing their rage into a weapon of justice.","items":["Clan Hunter's Trap (SRD: Hunting Trap)","Bone Dice Set (SRD: Bone dice set with a worn leather cup)","Whetstone"],"gp":12},
            {"name":"Spirit-Blessed Wanderer","desc":"Touched by ancestral spirits during a near-death experience. They follow visions across the land, guided by voices only they can hear.","items":["Spirit Ward Pouch (SRD: Restorative Ointment)","Blanket","Tinderbox"],"gp":10},
            {"name":"Arena Champion","desc":"Fought for survival in underground fighting pits. They earned their freedom through blood and now seek a greater purpose.","items":["Victor's Brew (SRD: Potion of Healing)","Iron Pot","Soap"],"gp":18},
        ],
        "Bard": [
            {"name":"Traveling Minstrel","desc":"Performed in taverns and courts across the realm. Their songs carry secrets overheard from nobles and commoners alike.","items":["Disguise Kit","Vial of Perfume","Signal Whistle"],"gp":14},
            {"name":"Disgraced Courtier","desc":"Once a trusted advisor who uncovered a conspiracy. Framed for treason, they now travel under a new identity, seeking the truth.","items":["Courtier's Veil (SRD: Hat of Disguise)","Sealing Wax","Sealed letter with a wax sigil"],"gp":20},
        ],
        "Cleric": [
            {"name":"Pilgrim of the Lost","desc":"Received a divine vision during a plague that wiped out their village. They now travel, healing the sick and seeking answers.","items":["Sacred Salve (SRD: Restorative Ointment)","Healer's Kit","Candle"],"gp":11},
            {"name":"Heretic Reformer","desc":"Cast out from their temple for questioning doctrine. Their faith remains unshaken, but they now serve their god outside the clergy's walls.","items":["Forbidden Scroll (SRD: Spell Scroll — 1st level)","Flask of Holy Water","Worn journal with cryptic entries"],"gp":15},
        ],
        "Druid": [
            {"name":"Grove Warden","desc":"Sworn protector of an ancient forest grove. The trees whisper warnings to them, and animals treat them as kin.","items":["Herbalism Kit","Fishing Tackle","Carved wooden whistle shaped like a dragon"],"gp":10},
        ],
        "Fighter": [
            {"name":"Broken Legionnaire","desc":"The sole survivor of a massacre that destroyed their company. They carry the unit's standard, seeking to restore its honor.","items":["Whetstone","Crowbar","Blanket"],"gp":13},
            {"name":"Village Guardian","desc":"A small-town militia captain who stood against a monster incursion. They now seek proper training to protect others.","items":["Healer's Kit","Signal Whistle","Iron Pot"],"gp":16},
        ],
        "Monk": [
            {"name":"Mountain Acolyte","desc":"Trained in a remote monastery hidden among the peaks. They descend to the world below on a mission of enlightenment.","items":["Meditation Scroll (SRD: Spell Scroll — Cantrip)","Hourglass","Bell"],"gp":10},
        ],
        "Paladin": [
            {"name":"Oathsworn Avenger","desc":"Witnessed their mentor fall to corruption. They swore an oath over the mentor's grave to root out evil wherever it hides.","items":["Blessed Vial (SRD: Flask of Holy Water)","Steel Mirror","Candle"],"gp":14},
        ],
        "Ranger": [
            {"name":"Frontier Scout","desc":"Mapped uncharted wilderness for a frontier settlement. They know every trail, every danger, and every safe haven for 50 miles.","items":["Climber's Kit","Old brass compass that doesn't point north","Tinderbox"],"gp":12},
        ],
        "Rogue": [
            {"name":"Reformed Smuggler","desc":"Ran contraband through city sewers before a close call with the law. They now use their skills for more honest — but equally thrilling — work.","items":["Thieves' Tools","Grappling Hook","Chalk (5 pieces)"],"gp":15},
        ],
        "Sorcerer": [
            {"name":"Wild Magic Prodigy","desc":"Their powers erupted during a childhood accident, destroying their home. They've spent years learning to control the chaos within.","items":["Arcanic Spark (SRD: Spell Scroll — Cantrip)","Glass orb filled with swirling smoke","Chalk (5 pieces)"],"gp":12},
        ],
        "Warlock": [
            {"name":"Reluctant Pact-Bearer","desc":"Made a desperate bargain to save a loved one. Now bound to a mysterious patron, they seek a way to break free — or at least understand the terms.","items":["Patron's Token (SRD: Sealed letter with a wax sigil)","Candle","Silver locket with a portrait inside"],"gp":10},
        ],
        "Wizard": [
            {"name":"Arcane Archivist","desc":"Apprenticed to an eccentric librarian who guarded forbidden knowledge. They inherited the library's secrets when the old mage vanished.","items":["Scholar's Scroll (SRD: Spell Scroll — 1st level)","Magnifying Glass","Worn journal with cryptic entries"],"gp":14},
            {"name":"Failed Academy Student","desc":"Expelled from a prestigious magical academy for an experiment gone wrong. They continue their studies independently, determined to prove the academy wrong.","items":["Practitioner's Lens (SRD: Magnifying Glass)","Vial of Acid","Abacus"],"gp":11},
        ],
    }
    pool = bg_pool.get(class_name, [
        {"name":"Wandering Adventurer","desc":"Grew up hearing tales of heroes and monsters. When tragedy struck their hometown, they took up arms and never looked back.","items":["Healer's Kit","Tinderbox","Waterskin"],"gp":10}
    ])
    bg = random.choice(pool)
    return {"name": bg["name"], "description": bg["desc"], "items": list(bg["items"]), "gp": bg["gp"]}

# ── Character History Generation ─────────────────────────────────────────

@app.post("/api/character/{char_id}/generate-history", response_class=JSONResponse)
async def generate_character_history(char_id: int, request: Request):
    """Generate a comprehensive character backstory from childhood to present.
    Weaves race, class, subclass, background, skills, feats, ASI choices,
    and any user keywords into a rich narrative history.
    Model chain: Gemini → OpenRouter → Ollama → deterministic fallback."""
    user = require_user(request)
    data = await request.json()
    keywords = (data.get("keywords") or "").strip()

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    char = dict(row)
    db.close()

    # Parse JSON fields
    for f in ("skills","features","expertise_skills","asi_history","metamagic_history",
              "metamagic","invocations","maneuvers","magical_secrets","infusions",
              "feature_data"):
        try:
            char[f] = json.loads(char[f])
        except (json.JSONDecodeError, TypeError):
            char[f] = [] if f != "feature_data" else []

    race = char.get("race","")
    subrace = char.get("subrace","")
    class_name = char.get("class_name","")
    subclass = char.get("subclass","")
    bg = char.get("background","")
    alignment = char.get("alignment","")
    name = char.get("name","")
    level = char.get("level",1)
    fighting_style = char.get("fighting_style","")

    # Race/class data
    race_data = RACES.get(race, {})
    class_data = CLASSES.get(class_name, {})
    race_desc = race_data.get("desc","")
    class_desc = class_data.get("desc","")
    subclass_desc = (class_data.get("subclass_descs",{}) or {}).get(subclass,"")

    # Ability scores
    abilities = {a: char.get(a,10) for a in ("strength","dexterity","constitution","intelligence","wisdom","charisma")}

    # Skills
    skills = char.get("skills",[])
    expertise = char.get("expertise_skills",[])

    # Feats from feature_data
    feat_names = []
    for f in char.get("feature_data",[]):
        fn = f.get("name","") if isinstance(f,dict) else ""
        if "Ability Score Improvement" in fn and char.get("asi_history"):
            # Look up what was chosen
            lvl = int(f.get("level","L1").replace("L",""))
            for ae in char["asi_history"]:
                if ae.get("level") == lvl:
                    if ae.get("type") == "feat":
                        feat_names.append(ae.get("feat","").replace("_"," "))
                    else:
                        choices = ae.get("choices",{})
                        parts = [f"+{v} {k[:3].title()}" for k,v in choices.items()]
                        feat_names.append(", ".join(parts))
                    break

    # Choice systems
    metamagic = char.get("metamagic",[])
    invocations = char.get("invocations",[])
    maneuvers = char.get("maneuvers",[])
    magical_secrets = char.get("magical_secrets",[])
    infusions = char.get("infusions",[])

    prompt_parts = [f"""Write a rich, compelling backstory for this D&D 5e character. Write in third-person past tense, covering childhood origins through to the present day (level {level}). Weave in details about their race, class, background, skills, and key abilities. Make it feel like a lived life — include formative events, mentors, failures, and triumphs. Write 3-5 paragraphs.

CHARACTER PROFILE
Name: {name}
Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
Level: {level}
Background: {bg}
Alignment: {alignment}
Fighting Style: {fighting_style.replace('_',' ').title() if fighting_style else 'None'}"""]

    if race_desc:
        prompt_parts.append(f"\nRACE DESCRIPTION\n{race_desc[:400]}")
    if class_desc:
        prompt_parts.append(f"\nCLASS DESCRIPTION\n{class_desc[:400]}")
    if subclass_desc:
        prompt_parts.append(f"\nSUBCLASS DESCRIPTION\n{subclass_desc[:400]}")

    prompt_parts.append(f"\nABILITY SCORES\n" + ", ".join(f"{k.title()}: {v}" for k,v in abilities.items()))

    if skills:
        prompt_parts.append(f"\nSkill Proficiencies: {', '.join(skills)}")
    if expertise:
        prompt_parts.append(f"\nExpertise: {', '.join(expertise)}")

    if feat_names:
        prompt_parts.append(f"\nASI/Feat Choices: {', '.join(feat_names)}")
    if metamagic:
        prompt_parts.append(f"\nMetamagic: {', '.join(metamagic)}")
    if invocations:
        prompt_parts.append(f"\nEldritch Invocations: {', '.join(invocations)}")
    if maneuvers:
        prompt_parts.append(f"\nManeuvers: {', '.join(maneuvers)}")
    if magical_secrets:
        prompt_parts.append(f"\nMagical Secrets: {', '.join(magical_secrets)}")
    if infusions:
        prompt_parts.append(f"\nInfusions: {', '.join(infusions)}")

    if keywords:
        prompt_parts.append(f"\nPLAYER NOTES (incorporate these): {keywords}")

    prompt_parts.append("\nReturn ONLY the backstory text — no JSON, no labels, no preamble. Just the narrative paragraphs.")

    prompt = "\n".join(prompt_parts)

    # Tiered model chain
    TIER_NAMES = ["gemini","openrouter","ollama"]
    text = None
    used_tier = None
    for tier, caller in enumerate([_call_gemini, _call_openrouter, _call_ollama]):
        text = await caller(prompt)
        if text:
            used_tier = TIER_NAMES[tier]
            break

    if not text:
        # Fallback
        text = _fallback_history(char, race_desc, class_desc, subclass_desc)

    print(f"[AI] history tier={used_tier or 'fallback'} char_id={char_id} len={len(text)}")
    return JSONResponse({"backstory": text.strip(), "tier": used_tier or "fallback"})


def _fallback_history(char: dict, race_desc: str, class_desc: str, subclass_desc: str) -> str:
    """Deterministic fallback when all AI tiers are unavailable."""
    name = char.get("name","the adventurer")
    race = char.get("race","")
    subrace = char.get("subrace","")
    class_name = char.get("class_name","")
    subclass = char.get("subclass","")
    bg = char.get("background","").lower()
    skills = char.get("skills",[])
    fighting_style = char.get("fighting_style","")
    level = char.get("level",1)

    race_str = f"{subrace} {race}" if subrace else race
    class_str = f"{subclass} {class_name}" if subclass else class_name
    skill_str = f"skilled in {', '.join(skills[:4])}" if skills else "eager to learn"

    paras = [
        f"{name} was born into a {race_str} community, raised from childhood as a {bg}. Early life taught them resilience and shaped their identity as a {skill_str}.",
    ]

    if fighting_style:
        fs = fighting_style.replace("_"," ").title()
        paras.append(f"As a young adult, they trained relentlessly, mastering the {fs} fighting style. Their reputation grew among allies and rivals alike.")

    paras.append(f"Now a level {level} {class_str}, {name} carries the weight of every battle fought and every lesson learned. Driven by purpose and hardened by experience, they stand ready for whatever adventure comes next.")

    return "\n\n".join(paras)


# ── Character Portrait Generation ────────────────────────────────────────

@app.post("/api/ai/portrait", response_class=JSONResponse)
async def ai_portrait(request: Request):
    """Generate a high-fantasy character portrait prompt, and attempt image generation.
    Returns prompt immediately; image may be empty if generation is slow/unavailable.
    If custom_prompt provided, uses it directly.
    If character_id provided, persists the result to the DB."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    abilities = data.get("abilities", {})
    background = data.get("background", "")
    alignment = data.get("alignment", "")
    skills = data.get("skills", [])
    gender = data.get("gender", "")
    custom_prompt = (data.get("custom_prompt") or "").strip()
    character_id = data.get("character_id")

    # Build prompt — always use fast deterministic fallback (covers all 38 races)
    if custom_prompt:
        image_prompt = f"Bust portrait, upper body only, 3:4 aspect ratio, close-up composition. High fantasy oil painting, dramatic lighting. {custom_prompt}"
        print(f"[AI portrait] custom_prompt race={race} class={class_name}")
    else:
        image_prompt = _fallback_portrait_prompt(race, class_name, subclass)
        # Kick off background tasks: AI enrichment + image generation (don't block response)
        asyncio.create_task(_try_generate_image(image_prompt, character_id, user["id"] if character_id else None,
                                                  race, class_name))
        print(f"[AI portrait] fallback race={race} class={class_name}")

    # Persist prompt to DB immediately if character_id provided
    if character_id:
        db = get_db()
        db.execute("UPDATE characters SET portrait_url=?, portrait_prompt=? WHERE id=? AND user_id=?",
                   ("", image_prompt, character_id, user["id"]))
        db.commit()
        db.close()

    return JSONResponse({"prompt": image_prompt, "image_url": ""})


async def _try_ai_enrich_prompt(race: str, subrace: str, class_name: str, subclass: str,
                                 abilities: dict, background: str, alignment: str, skills: list):
    """Background task: try AI chain to produce richer prompt. Updates nothing — informational only."""
    try:
        CLASS_ATTIRE = {
            "Barbarian": "bare-chested or animal furs, NO heavy armor, tribal style, muscular and primal",
            "Bard": "flamboyant colorful performer's clothing, stylish but light, musical instrument visible",
            "Cleric": "robes with holy symbols, chainmail possible, divine motifs",
            "Druid": "natural materials, hides and furs, NO metal armor, nature motifs, wooden staff",
            "Fighter": "heavy armor, chainmail or plate, practical and battle-worn",
            "Monk": "simple monastic robes, bare hands and feet, unarmored, disciplined posture",
            "Paladin": "gleaming plate armor with holy symbols and divine motifs",
            "Ranger": "leather armor in forest tones, hooded cloak, practical and rugged",
            "Rogue": "dark leather armor, hooded, shadowy, stealthy, daggers visible",
            "Sorcerer": "flowing robes, NO armor, innate magical energy visible",
            "Warlock": "dark occult robes, eldritch symbols, NO heavy armor, arcane pact motifs",
            "Wizard": "scholarly robes, NO armor, spellbook or arcane focus, studious appearance",
        }
        attire = CLASS_ATTIRE.get(class_name, "appropriate adventuring attire")
        prompt = f"""Describe a D&D 5e character portrait in High Fantasy art style. Oil painting, dramatic lighting.

CRITICAL: This is a BUST portrait — head and upper chest only, no full body. 3:4 portrait aspect ratio, close-up composition, character fills the frame from the top of their head to mid-chest.

Character: {race}{' (' + subrace + ')' if subrace else ''} {class_name}{' — ' + subclass if subclass else ''}
Background: {background}
Alignment: {alignment or 'Unknown'}
Skills: {', '.join(skills) if skills else 'various'}
Key abilities: {', '.join(f'{k}:{v}' for k,v in sorted(abilities.items(), key=lambda x:-x[1])[:3]) if abilities else 'balanced'}
Class-appropriate attire: {attire}

Write a DETAILED image prompt (150-200 words) describing this character for an AI image generator. Include: face, build, hair, distinctive features, clothing/armor visible on upper body, weapon or focus if it fits in frame, pose, expression, lighting, background setting. Remember: BUST ONLY — head to mid-chest, 3:4 ratio, no legs, no full body. High fantasy oil painting style. Do NOT include the character name — just describe what they look like."""
        text = None
        for caller in [_call_gemini, _call_openrouter, _call_ollama]:
            try:
                text = await caller(prompt)
                if text:
                    break
            except Exception:
                continue
        if text:
            print(f"[AI portrait] AI enrichment succeeded for {race} {class_name}")
    except Exception as e:
        print(f"[AI portrait] AI enrichment failed: {e}")


async def _try_generate_image(prompt: str, character_id, user_id,
                               race: str, class_name: str):
    """Background task: try to generate image via Stable Horde. Updates DB on success."""
    try:
        image_data = await _fetch_stable_horde_image(prompt, max_wait=90)
        if image_data and character_id and user_id:
            db = get_db()
            db.execute("UPDATE characters SET portrait_url=? WHERE id=? AND user_id=?",
                       (image_data, character_id, user_id))
            db.commit()
            db.close()
            print(f"[AI portrait] background image saved for char {character_id}")
    except Exception as e:
        print(f"[AI portrait] background image generation failed: {e}")


def _fallback_portrait_prompt(race: str, class_name: str, subclass: str = "") -> str:
    """Deterministic portrait prompts by class/race — all bust/upper-body framed."""
    prompts = {
        ("Dwarf","Barbarian"): "Bust portrait, 3:4 aspect ratio. A stout mountain dwarf barbarian with thick braided auburn hair and a scarred face. Bare-chested with a heavy fur mantle across broad shoulders, tribal tattoos visible on muscular upper arms. Gripping a massive greataxe, battle-ready expression, primal fury in eyes. Snow-capped peaks and storm clouds behind. Oil painting, dramatic lighting, high fantasy.",
        ("Dwarf","Cleric"): "Bust portrait, 3:4 aspect ratio. A venerable dwarf cleric with a silver-streaked beard and kind eyes. Robes of deep blue with gold embroidery across the chest, a holy symbol of Moradin hanging from a chain. One hand raised in blessing near the face, warm candlelight glow. Stone temple interior. Oil painting, high fantasy.",
        ("Dwarf","Fighter"): "Bust portrait, 3:4 aspect ratio. A battle-hardened dwarf fighter in chainmail armor visible across shoulders and chest, warhammer resting on one shoulder. Braided copper beard, stern expression, scar across one eyebrow. Mountain fortress stonework behind. Oil painting, dramatic lighting.",
        ("Elf","Wizard"): "Bust portrait, 3:4 aspect ratio. A slender high elf wizard with silver-white hair flowing past pointed ears. Deep purple robes with arcane sigils at the collar, a crystal-topped staff held diagonally across the frame. Faint magical aura, ancient library backdrop. Oil painting, ethereal lighting.",
        ("Elf","Ranger"): "Bust portrait, 3:4 aspect ratio. A wood elf ranger with amber eyes and copper hair pulled back. Leather armor in forest greens across shoulders, longbow visible over one shoulder. Hood partially up, alert expression, dappled sunlight. Ancient forest bokeh behind. Oil painting, high fantasy.",
        ("Elf","Rogue"): "Bust portrait, 3:4 aspect ratio. A sleek elven rogue with dark cropped hair and a knowing smirk. Black leather armor, dagger hilts visible at the collar, cloak half-drawn across shoulders. Moonlit shadows dancing across the face. Oil painting, chiaroscuro lighting.",
        ("Human","Paladin"): "Bust portrait, 3:4 aspect ratio. A noble human paladin with short-cropped blonde hair and a strong jaw. Gleaming plate armor with a sunburst emblem on the chestplate, longsword held near the face reflecting divine light. Radiant glow from behind. Oil painting, cinematic lighting.",
        ("Human","Fighter"): "Bust portrait, 3:4 aspect ratio. A weathered human fighter with close-cropped dark hair and a faint scar across the cheek. Well-worn scale mail across shoulders, longsword hilt visible at hip-level frame edge. Castle wall stonework behind. Oil painting, late afternoon light.",
        ("Half-Orc","Barbarian"): "Bust portrait, 3:4 aspect ratio. A towering half-orc barbarian with gray-green skin and tribal tattoos across the face and shoulders. Bald head, tusked jaw, fur mantle over bare chest. Massive axe head visible, primal snarl. Stormy sky background. Oil painting, dramatic lighting.",
        ("Tiefling","Warlock"): "Bust portrait, 3:4 aspect ratio. A tiefling warlock with deep purple skin and curved horns sweeping back from the forehead. Eyes glowing with eldritch fire, dark robes with infernal patterns at the collar. A crackling tome held near the chest. Shadowy ruins at midnight. Oil painting, occult atmosphere.",
        ("Dragonborn","Paladin"): "Bust portrait, 3:4 aspect ratio. A bronze dragonborn paladin with gleaming metallic scales and a prominent draconic snout. Plate armor with a dragon emblem across the chest, sparks of lightning crackling between teeth. Holy symbol grasped near the chest, righteous intensity. Stormlit cathedral behind. Oil painting, dramatic lighting.",
        ("Dragonborn","Sorcerer"): "Bust portrait, 3:4 aspect ratio. A red dragonborn sorcerer with crimson scales and draconic frills framing the face. Robes shimmering with arcane heat, eyes glowing with inner fire. Hands wreathed in flame near the chest. Volcanic glow behind. Oil painting, high fantasy.",
    }
    key = (race, class_name)
    if key in prompts:
        return prompts[key]
    # Generic fallback with comprehensive race features
    race_features = {
        "Dwarf": "stout build, braided hair or beard, rugged dwarven features",
        "Elf": "slender build, pointed ears, graceful elven features, sharp cheekbones",
        "Human": "determined expression, practical demeanor, varied appearance",
        "Half-Orc": "tusked jaw, muscular build, gray-green skin, prominent lower canines",
        "Halfling": "small stature, curly hair, cheerful round face, pointed ears",
        "Gnome": "small and bright-eyed, clever expression, prominent nose, delicate features",
        "Tiefling": "curved horns, pointed tail, violet or red skin tones, solid-color eyes, otherworldly presence",
        "Dragonborn": "draconic snout and frills, metallic or chromatic scales, reptilian eyes, clawed hands",
        "Half-Elf": "slightly pointed ears, mixed heritage beauty, human versatility with elven grace",
        "Aarakocra": "avian features, beak-like nose, feathery crest, large expressive bird-like eyes, taloned hands",
        "Aasimar": "angelic radiance, luminous eyes, metallic-flecked skin, faint glowing halo effect, celestial beauty",
        "Bugbear": "shaggy dark fur, long goblinoid ears, heavy brow, powerful long-limbed build, bestial features",
        "Centaur": "equine lower body suggested, human torso with strong build, wild flowing hair, nature-worn features",
        "Changeling": "pale skin, colorless white hair, large colorless eyes, subtly shifting features, androgynous",
        "Firbolg": "towering broad build, gray-blue skin, pointed ears, wide bovine nose, gentle giant features",
        "Genasi": "elemental features — skin and hair tinted with elemental colors, faint elemental energy, striking eyes",
        "Gith": "gaunt elongated features, yellow-green skin, sharp angular face, deep-set piercing eyes, thin build",
        "Goblin": "small and wiry, green skin, large pointed ears, sharp jagged teeth, cunning wide eyes",
        "Goliath": "towering muscular build, gray stone-like skin, lithoderms (bony growths) visible, bald or short dark hair, intense gaze",
        "Grung": "small frog-like build, bright colorful skin (orange/green/blue), large bulbous eyes, webbed hands",
        "Hobgoblin": "orange-red skin, broad muscular build, sharp goblinoid features, military bearing, dark swept-back hair",
        "Kalashtar": "human-like with subtle ethereal quality, slightly luminous eyes, serene composed expression, psychic presence",
        "Kenku": "raven-like features, glossy black feathers, beak-like face, dark avian eyes, slight hunched posture",
        "Kobold": "small reptilian build, scaly skin in earthy tones, draconic snout, large expressive eyes, small horns",
        "Lizardfolk": "reptilian scales in green-brown tones, elongated snout, slit-pupil eyes, crest or spines, muscular jaw",
        "Loxodon": "elephantine features, gray wrinkled skin, prominent tusks, large floppy ears, trunk, wise deep-set eyes",
        "Minotaur": "bull-like features, large curved horns, bovine snout, thick neck, muscular bullish build, dark fur",
        "Orc": "powerful muscular build, gray-green skin, prominent tusks, heavy brow, pig-like snout, battle scars",
        "Shifter": "humanoid with bestial features — feline eyes, pointed ears, subtle fur patches, predatory grace",
        "Simic Hybrid": "humanoid with grafted animal features — gills, tentacles, carapace plates, or fin-crests",
        "Tabaxi": "feline features, cat-like vertical-pupil eyes, fur in leopard/jaguar/tiger patterns, whiskers, pointed ears, tail",
        "Tlincalli": "scorpion-like features, chitinous plates, mandibles, multiple eyes, segmented frame, striking silhouette",
        "Tortle": "turtle-like features, domed shell visible behind shoulders, beaked mouth, leathery green-brown skin, wise ancient eyes",
        "Triton": "aquatic features, blue-green skin, fin-like ears, webbed hands, iridescent scales, flowing sea-colored hair",
        "Vedalken": "tall and slender, blue-gray skin, completely hairless, large analytical eyes, elongated smooth head",
        "Warforged": "constructed living armor of wood and metal, hinged jaw, glowing eyes, rune-etched plating, golem-like features",
        "Xvart": "small, bright blue skin, large bat-like ears, bulging eyes, hunched posture, sharp teeth",
        "Yuan-ti Pureblood": "human-like with serpentine features — slit-pupil eyes, small scales, forked tongue, cold calculating gaze",
    }
    rf = race_features.get(race, "distinctive features, adventurer's bearing")
    return f"Bust portrait, 3:4 aspect ratio. A {race.lower()} {class_name.lower()} with {rf}. Wearing appropriate {class_name.lower()} attire, upper body visible. Confident expression, high fantasy oil painting style with dramatic lighting. Rich colors, detailed background bokeh."

@app.post("/api/character/portrait", response_class=JSONResponse)
async def save_portrait_image(request: Request):
    """Save a client-generated portrait image (base64 data URL) to the DB."""
    user = require_user(request)
    data = await request.json()
    character_id = data.get("character_id")
    image_data = data.get("image_data", "")  # base64 data URL from Puter.js

    if not character_id or not image_data:
        return JSONResponse({"error": "character_id and image_data required"}, status_code=400)

    db = get_db()
    db.execute("UPDATE characters SET portrait_url=? WHERE id=? AND user_id=?",
               (image_data, character_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── Build Generation (PHB-grounded, level-aware) ────────────────────────────

@app.post("/api/ai/build", response_class=JSONResponse)
async def ai_build(request: Request):
    """Generate a complete optimized build for the given class/race/subclass/level."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    level = min(max(int(data.get("level", 1)), 1), 20)

    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    abilities = allocate_ability_scores(class_name, race, subrace)
    mods = {ability: modifier(score) for ability, score in abilities.items()}
    pb = PROFICIENCY_BONUS.get(level, 2)

    con_mod = mods["constitution"]
    hp = calc_hp(class_name, level, con_mod)
    racial_eff = get_racial_trait_effects(race, subrace)
    ac = _calculate_ac(class_name, level, mods, racial_eff.get("natural_armor"))

    saves = {ability: mod + (pb if ability in class_data["saves"] else 0)
             for ability, mod in mods.items()}

    skills = _pick_skills(class_name, mods)
    equipment = get_equipment_for_level(class_name, level)
    magic_items = pick_magic_items(class_name, level)
    feats = get_feats_for_level(class_name, level)

    spell_data = get_spells_for_level(class_name, level)
    spell_slots = get_spell_slots(class_name, level)

    attacks = _calculate_attacks(class_name, level, mods, pb, equipment)

    raw_features = get_class_features(class_name, level, subclass)
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods, subclass=subclass)

    race_data = RACES.get(race, RACES["Human"])
    build = {
        "class": class_name,
        "subclass": subclass or None,
        "race": race,
        "subrace": subrace or None,
        "level": level,
        "proficiency_bonus": pb,
        "hit_points": hp,
        "hit_dice": f"{level}d{class_data['hd']}",
        "armor_class": ac,
        "initiative": mods["dexterity"],
        "speed": race_data["speed"],
        "ability_scores": abilities,
        "modifiers": mods,
        "saving_throws": saves,
        "skills": skills,
        "equipment": equipment,
        "magic_items": magic_items,
        "feats": feats,
        "cantrips": spell_data.get("cantrips", []),
        "spells": spell_data.get("spells", {}),
        "spell_slots": spell_slots,
        "attacks": attacks,
        "features": enriched_features,
    }
    return JSONResponse(build)


def _calculate_ac(class_name: str, level: int, mods: dict, natural_armor: dict | None = None) -> int:
    # Natural armor (Tortle, Lizardfolk, Loxodon, etc.) overrides class-based AC
    if natural_armor:
        ac = natural_armor.get("base_ac", 17)
        # Determine which ability modifier to use
        stat = natural_armor.get("stat", "dexterity")
        ability_mod = mods[stat]
        # Uncapped: add full ability modifier (Lizardfolk: 13 + DEX)
        if natural_armor.get("uncapped"):
            ac += ability_mod
            return ac
        # Capped DEX bonus (e.g., medium armor)
        max_bonus = natural_armor.get("max_bonus")
        if max_bonus is not None:
            ac += min(ability_mod, max_bonus)
            return ac
        # Flat AC with no ability modifier (Tortle: 17 flat, Loxodon: 12 + CON via stat key)
        return ac

    dex = mods["dexterity"]
    if class_name == "Barbarian":
        con = mods["constitution"]
        base = 15 if level >= 20 else 14 if level >= 10 else 13 if level >= 5 else 12
        return base + dex + con
    if class_name == "Monk":
        wis = mods["wisdom"]
        return 10 + dex + wis
    if class_name in ("Wizard", "Sorcerer"):
        if level >= 5: return 13 + min(dex, 2)
        return 10 + dex
    if class_name == "Warlock":
        if level >= 20: return 12 + dex + 3
        if level >= 15: return 12 + min(dex, 5) + 2
        if level >= 10: return 12 + min(dex, 5) + 1
        if level >= 5: return 12 + min(dex, 5)
        return 11 + min(dex, 5)
    if class_name in ("Bard", "Rogue", "Ranger"):
        if level >= 20: return 12 + min(dex, 5) + 3
        if level >= 15: return 12 + min(dex, 5) + 2
        if level >= 10: return 12 + min(dex, 5) + 1
        if level >= 5: return 12 + min(dex, 5)
        return 11 + min(dex, 5)
    if class_name == "Druid":
        if level >= 20: return 15 + min(dex, 2) + 3
        if level >= 10: return 12 + min(dex, 5) + 1
        return 11 + min(dex, 5)
    if class_name == "Cleric":
        if level >= 20: return 18 + 3 + 2
        if level >= 10: return 18 + 1 + 2
        return 14 + min(dex, 2) + 2
    if level >= 20: return 18 + 3
    if level >= 15: return 18 + 2
    if level >= 10: return 18 + 1
    if level >= 5: return 18
    return 16 + 2


def _calculate_attacks(class_name: str, level: int, mods: dict, pb: int, equipment: list) -> list:
    str_mod = mods["strength"]
    dex_mod = mods["dexterity"]
    magic = 3 if level >= 20 else 2 if level >= 10 else 1 if level >= 5 else 0

    if class_name == "Warlock":
        beams = 4 if level >= 17 else 3 if level >= 11 else 2 if level >= 5 else 1
        cha = mods["charisma"]
        return [{"name": f"Eldritch Blast ({beams} beam{'s' if beams > 1 else ''})",
                 "attack_bonus": cha + pb, "damage": f"1d10+{cha} per beam",
                 "type": "force", "range": "120 ft"}]

    if class_name == "Ranger":
        return [{"name": f"Longbow +{magic}" if magic else "Longbow",
                 "attack_bonus": dex_mod + pb + magic, "damage": f"1d8+{dex_mod + magic}",
                 "type": "piercing", "range": "150/600"}]

    if class_name in ("Rogue", "Monk"):
        atk = dex_mod
        weapon = "Shortsword" if class_name == "Monk" else "Rapier"
        die = "1d6" if class_name == "Monk" else "1d8"
        return [{"name": f"{weapon} +{magic}" if magic else weapon,
                 "attack_bonus": atk + pb + magic, "damage": f"{die}+{atk + magic}",
                 "type": "piercing"}]

    atk = str_mod
    if class_name in ("Barbarian", "Fighter", "Paladin"):
        weapon = "Greatsword" if level >= 15 else "Longsword"
        die = "2d6" if level >= 15 else "1d8"
        if class_name == "Fighter" and dex_mod > str_mod:
            atk = dex_mod
    elif class_name == "Bard":
        atk = max(dex_mod, str_mod)
        weapon = "Rapier"
        die = "1d8"
    else:
        atk = max(str_mod, dex_mod)
        weapon = "Mace" if class_name == "Cleric" else "Quarterstaff"
        die = "1d6"

    return [{"name": f"{weapon} +{magic}" if magic else weapon,
             "attack_bonus": atk + pb + magic, "damage": f"{die}+{atk + magic}",
             "type": "slashing" if weapon in ("Greatsword", "Longsword") else "bludgeoning"}]


def _pick_skills(class_name: str, mods: dict) -> list[str]:
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    available = class_data["skills"]
    if available == "all":
        available = ALL_SKILLS
    count = class_data["skill_count"]
    scored = [(skill, mods.get(SKILL_ABILITIES.get(skill, "intelligence"), 0))
              for skill in available]
    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored[:count]]


# ── Custom trap CRUD ──────────────────────────────────────────────────────────

@app.get("/api/dm/traps", response_class=JSONResponse)
async def dm_list_traps(request: Request):
    """List custom traps for the current user."""
    user = require_user(request)
    db = get_db()
    traps = [dict(r) for r in db.execute(
        "SELECT * FROM dm_custom_traps WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]
    db.close()
    return JSONResponse({"traps": traps})


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
async def search_items(q: str = "", limit: int = 200):
    """Search equipment + magic items by name (fuzzy substring match).
    When q is empty, returns all items alphabetically (up to limit)."""
    query = q.strip().lower()
    results = []
    if not query:
        # Return all items alphabetically
        for key in sorted(ITEM_INDEX.keys()):
            item = ITEM_INDEX[key]
            results.append({
                "name": item["name"],
                "type": item["type"],
                "rarity": item.get("rarity", ""),
                "source": item.get("source", ""),
                "cost": item.get("cost", ""),
                "weight": item.get("weight"),
            })
            if len(results) >= limit:
                break
    else:
        for key, item in ITEM_INDEX.items():
            if query in key:
                results.append({
                    "name": item["name"],
                    "type": item["type"],
                    "rarity": item.get("rarity", ""),
                    "source": item.get("source", ""),
                    "cost": item.get("cost", ""),
                    "weight": item.get("weight"),
                })
                if len(results) >= limit:
                    break
        results.sort(key=lambda r: (0 if r["name"].lower().startswith(query) else 1, r["name"]))

    return JSONResponse({"results": results[:limit], "total": len(ITEM_INDEX)})


@app.get("/api/items/describe", response_class=JSONResponse)
async def describe_item(name: str = ""):
    """Get full description and metadata for a single item."""
    if not name or not name.strip():
        return JSONResponse({"error": "No item name provided"}, status_code=400)
    key = name.strip().lower()
    item = _resolve_item_key(name)
    if not item:
        return JSONResponse({"name": name, "description": "No description available.", "type": "Unknown"})
    return JSONResponse(item)


# ── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()

# ── Reference Manual Lookup ─────────────────────────────────────────────────
# 18 D&D 5e PDFs on SlowDisk. Query by filename or indexed metadata.

MANUALS_BASE = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals")

@app.get("/api/reference/manuals", response_class=JSONResponse)
async def list_manuals(request: Request):
    """List available reference manuals — PDFs on disk + all ingested manuals. No auth."""
    import glob
    result = {"count": 0, "manuals": [], "ingested": [], "path": str(MANUALS_BASE)}

    # 1. PDFs in the manuals directory
    if MANUALS_BASE.exists():
        pdfs = sorted(glob.glob(str(MANUALS_BASE / "*.pdf")) + glob.glob(str(MANUALS_BASE / "*/*.pdf")))
        result["manuals"] = [Path(p).name for p in pdfs]
        result["count"] = len(pdfs)

    # 2. Full ingested manual metadata from _meta.json
    meta = _load_manual_json("_meta.json")
    if isinstance(meta, dict):
        pdf_map = meta.get("pdf_map", {})
        result["ingested"] = [
            {"slug": slug, "title": info.get("title", ""), "filename": info.get("filename", ""),
             "path": info.get("path", "")}
            for slug, info in sorted(pdf_map.items(), key=lambda x: x[1].get("title", "").lower())
        ]
        result["ingested_count"] = len(result["ingested"])

    return JSONResponse(result)

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
        }
        for slug, info in pdf_map.items():
            title = info.get("title", slug)
            display = _slug_displays.get(slug) or re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
            _source_slug_cache[slug] = {
                "title": title,
                "display": display,
                "path": info.get("path", ""),
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
    info = slug_map.get(slug.upper())
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
    uvicorn.run("main:app", host="0.0.0.0", port=8300)
