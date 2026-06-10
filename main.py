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

# ── SRD Magic Items & Features (from dnd5eapi.co 2014, cached locally) ──────

def _load_json_cache(filename: str) -> list[dict]:
    try:
        with open(SRD_CACHE / filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

SRD_MAGIC_ITEMS: list[dict] = _load_json_cache("magic-items.json")
SRD_FEATURES: list[dict] = _load_json_cache("features.json")

# Tag SRD data with source
for _item in SRD_SPELLS:
    if "source" not in _item:
        _item["source"] = "SRD 5.1"
for _item in SRD_FEATURES:
    if "source" not in _item:
        _item["source"] = "SRD 5.1"
for _item in SRD_MAGIC_ITEMS:
    if "source" not in _item:
        _item["source"] = "SRD 5.1"

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
    }
    
    manual_races = _load_manual_json("races.json")
    for race in manual_races:
        name = race.get("name", "")
        if not name or name in RACES:
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
                   "wisdom": "wisdom", "charisma": "charisma"}
        asi = {}
        for k, v in race.get("asi", {}).items():
            if v and k in asi_map:
                asi[asi_map[k]] = v
        traits = [t.get("name", "") for t in race.get("traits", [])]
        # Process subraces
        subrace_names = []
        subrace_descs = {}
        for sr in race.get("subraces", []):
            sr_name = sr.get("name", "")
            if sr_name:
                subrace_names.append(sr_name)
                subrace_descs[sr_name] = sr.get("description", "")
                # Merge subrace ASI into SUBASIS
                sr_asi = {k: v for k, v in sr.get("asi", {}).items() if v}
                if sr_asi and sr_name not in SUBASIS:
                    SUBASIS[sr_name] = sr_asi
                # Add subrace trait names to SUBRACE_TRAITS
                sr_trait_names = [st.get("name", "") for st in sr.get("traits", [])]
                if sr_trait_names and sr_name not in SUBRACE_TRAITS:
                    SUBRACE_TRAITS[sr_name] = sr_trait_names
                # Add subrace trait descriptions to RACIAL_TRAIT_DESCS (quality-aware)
                for st in sr.get("traits", []):
                    stname = st.get("name", "")
                    stdesc = st.get("description", "")
                    if stname and stdesc:
                        existing = RACIAL_TRAIT_DESCS.get(stname, "")
                        if not existing or _should_replace_description(existing, stdesc):
                            RACIAL_TRAIT_DESCS[stname] = stdesc
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
            "desc": race.get("description", ""),
            "subrace_descs": subrace_descs,
            "source": race.get("source", ""),
        }
        # Clean up bad sources: bare page numbers, Unknown markers
        src = RACES[name].get("source", "")
        ref_manual = race.get("_source_manual", "")
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
        # Add trait descriptions (quality-aware)
        for t in race.get("traits", []):
            tname = t.get("name", "")
            tdesc = t.get("description", "")
            if tname and tdesc:
                existing = RACIAL_TRAIT_DESCS.get(tname, "")
                if not existing or _should_replace_description(existing, tdesc):
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

    # ── Spells ── append to SRD_SPELLS (normalize classes/school to dict format)
    manual_spells = _load_manual_json("spells.json")
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
            # Map to SRD format
            mapped = {
                "name": item.get("name", ""),
                "desc": [item.get("description", "")],
                "rarity": {"name": item.get("rarity", "varies")},
                "equipment_category": {"name": item.get("type", "Wondrous item")},
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
        if key not in FEATS:
            FEATS[key] = {
                "name": name,
                "prerequisite": feat.get("prerequisite", ""),
                "description": feat.get("description", ""),
                "source": feat.get("source", ""),
            }
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
        if sc_name not in subs:
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
    "Dwarf": {"subraces": ["Hill Dwarf", "Mountain Dwarf", "Duergar", "Gold Dwarf"], "asi": {"constitution": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Dwarvish"], "traits": ["Dwarven Resilience", "Stonecunning"], "desc": "Bold and hardy, dwarves are known as skilled warriors, miners, and workers of stone and metal. Standing 4 to 5 feet tall, they are broad and compact, weighing around 150 pounds. Their skin ranges from deep tan to light brown, and their hair—worn long—ranges from black to red, graying to white with age. Dwarves can live to be over 400 years old.\n\nDwarven culture is built on three pillars: clan, craft, and honor. They inhabit great stone halls carved deep into mountains, forging legendary weapons and armor. Slow to trust but fiercely loyal once earned, dwarves hold grudges for centuries and friendships for millennia. Their kingdoms are ordered by ancient tradition, with kings and queens descended from the first dwarves.\n\nMechanically, dwarves gain +2 Constitution, Darkvision 60 ft, Dwarven Resilience (advantage on poison saves + poison resistance), and Stonecunning (expertise on History checks related to stonework). Even heavily armored dwarves maintain full speed.", "subrace_descs": {"Hill Dwarf": "+1 Wisdom. Dwarven Toughness grants +1 HP per level. Hill dwarves are the heartiest of their kind, with keen senses and a deeper connection to the living earth. They are the most common dwarven merchants, farmers, and diplomats.", "Mountain Dwarf": "+2 Strength. Dwarven Armor Training grants proficiency with light and medium armor. Mountain dwarves are the martial backbone of dwarven society— soldiers, smiths, and sentinels who guard the deep roads. Hardy and strong, they thrive in the most forbidding peaks.", "Duergar": "+1 Strength. Superior Darkvision (120 ft), Duergar Resilience (advantage on saves vs illusions, charms, and paralysis), Duergar Magic (enlarge/reduce and invisibility at levels 3 and 5), Sunlight Sensitivity. The gray dwarves of the Underdark—grim, psionic, and hardened by eons beneath the earth.", "Gold Dwarf": "+1 Wisdom. Dwarven Toughness (+1 HP per level). The southern dwarves of the Great Rift in Faerûn—confident, shrewd merchants with golden-brown skin and a proud, unbroken lineage. They are resistant to the scheming of other races but generous to those who earn their trust."}},
    "Elf": {"subraces": ["High Elf", "Wood Elf", "Dark Elf (Drow)", "Sea Elf", "Eladrin", "Shadar-kai"], "asi": {"dexterity": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Keen Senses", "Fey Ancestry", "Trance"], "desc": "Elves are a magical people of otherworldly grace, living in the world but not entirely of it. They are slender, standing 5 to 6 feet tall, with delicate features and pointed ears. Their skin ranges from pale alabaster to deep brown, and their eyes shine with colors not seen in humans—gold, silver, violet, deep green. Elves live up to 750 years, and their perspective is shaped by this long view of history.\n\nElven culture values freedom, beauty, and artistic expression above all. They build elegant spires that blend with the forest canopy or rise from ancient woods. Music, poetry, and bladecraft are all refined to an art form. Elves love nature and magic, and their trance—a four-hour meditative state that replaces sleep—allows them to relive memories and reflect on their long lives.\n\nMechanically, elves gain +2 Dexterity, Darkvision 60 ft, Keen Senses (proficiency in Perception), Fey Ancestry (advantage on saves vs charms, immune to magical sleep), and Trance (4-hour meditation replaces 8-hour sleep). They are fluent in Common and Elvish.", "subrace_descs": {"High Elf": "+1 Intelligence. Gain a wizard cantrip and one extra language. High elves are the most magically gifted— scholars, wizards, and keepers of elven high culture. Their kingdoms are bastions of arcane learning.", "Wood Elf": "+1 Wisdom. Fleet of Foot (+5 ft speed) and Mask of the Wild (hide in light natural obscurement). Wood elves are reclusive guardians of deep forests—swift, perceptive, and deadly with a bow.", "Dark Elf (Drow)": "+1 Charisma. Superior Darkvision (120 ft), Sunlight Sensitivity, and Drow Magic (dancing lights at will; faerie fire and darkness at levels 3 and 5). The drow are a dark-skinned, white-haired subrace of the Underdark, living in a matriarchal society devoted to the spider goddess Lolth.", "Sea Elf": "+1 Constitution. Swim speed 30 ft, amphibious (breathe air and water), Sea Elf Training (proficiency with spear, trident, light crossbow, and net). Sea elves fell in love with the ocean in the earliest days and now live in hidden shallows and the Elemental Plane of Water.", "Eladrin": "+1 Intelligence. Fey Step (misty step 1/short rest). Eladrin are elves of the Feywild, their appearance and personality shifting with the seasons—spring (joyful, green), summer (fierce, golden), autumn (generous, russet), winter (contemplative, pale blue).", "Shadar-kai": "+1 Constitution. Necrotic resistance, Blessing of the Raven Queen (teleport 30 ft 1/long rest; at level 3+, gain resistance to all damage for 1 round after teleporting). Shadar-kai serve the Raven Queen in the Shadowfell, their souls bound to her eternal duty."}},
    "Halfling": {"subraces": ["Lightfoot Halfling", "Stout Halfling", "Ghostwise Halfling"], "asi": {"dexterity": 2}, "speed": 25, "darkvision": 0, "languages": ["Common", "Halfling"], "traits": ["Lucky", "Brave", "Halfling Nimbleness"], "desc": "Halflings are small, cheerful folk who stand about 3 feet tall and weigh around 40 pounds. With round faces, rosy cheeks, and curly hair, they project an aura of comfort and contentment. They favor simple, colorful clothing and go barefoot whenever possible. Halflings live about 150 years, and their outlook is practical and grounded—they value home, hearth, and a well-told story over grand ambitions.\n\nHalfling communities are pastoral and peaceful, built around farms, mills, and cozy burrows. They dislike pomp and ceremony, preferring to govern by family consensus and the quiet wisdom of elders. Despite their peaceful nature, halflings are surprisingly brave when their homes or friends are threatened—a bravery born of loyalty, not recklessness.\n\nMechanically, halflings gain +2 Dexterity, Lucky (reroll 1s on attack rolls, ability checks, and saves), Brave (advantage on saves vs frightened), and Halfling Nimbleness (move through spaces of larger creatures). They speak Common and Halfling.", "subrace_descs": {"Lightfoot Halfling": "+1 Charisma. Naturally Stealthy lets you hide behind creatures larger than you. Lightfoot halflings are charming, gregarious travelers who love meeting new people and can slip away from trouble unnoticed.", "Stout Halfling": "+1 Constitution. Stout Resilience grants advantage on poison saves and resistance to poison damage. Known as Strongheart halflings in the Forgotten Realms, they have dwarven blood in their lineage— hardy, durable, and fond of good ale.", "Ghostwise Halfling": "+1 Wisdom. Silent Speech grants telepathy to any creature within 30 ft that shares a language. Ghostwise halflings are fiercely reclusive, living deep in the Chondalwood and speaking mind-to-mind rather than aloud. They have a deep, spiritual bond with the natural world."}},
    "Human": {"subraces": ["Variant Human"], "asi": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common"], "traits": [], "desc": "Humans are the youngest of the common races and the most ambitious. Standing 5 to 6 feet tall with skin tones ranging from nearly black to very pale, hair from black to blond, and facial hair from sparse to thick, humans are the most physically diverse race in the multiverse. They live less than a century, yet their drive and adaptability have spread them to every corner of every world.\n\nHuman culture is as varied as their appearance—no single god, philosophy, or way of life defines them. They build empires and topple them within the span of an elf's youth. This brevity of life fuels an intensity that other races find both admirable and alarming: humans achieve in decades what dwarves take centuries to accomplish.\n\nMechanically, humans gain +1 to all six ability scores—an unparalleled breadth of talent. They start with one extra language. The standard human is the ultimate generalist, capable of excelling in any class.", "subrace_descs": {"Variant Human": "+1 to two different abilities of your choice, one feat, and one extra skill proficiency. The variant human trades the jack-of-all-trades approach for focused specialization, making them the most customizable race in the game—particularly powerful for builds that need an early feat."}},
    "Dragonborn": {"subraces": [], "asi": {"strength": 2, "charisma": 1}, "speed": 30, "darkvision": 0, "languages": ["Common", "Draconic"], "traits": ["Draconic Ancestry", "Breath Weapon", "Damage Resistance"], "desc": "Dragonborn are tall, muscular humanoids with the blood of dragons running through their veins. Standing well over 6 feet tall and weighing 250 pounds or more, they have scaly hide, a draconic snout, sharp claws, and a powerful tail. Their scales mirror the color of their draconic ancestry—brass, bronze, copper, gold, silver, black, blue, green, red, or white. Dragonborn live about 80 years.\n\nDragonborn society revolves around clan and honor above all else. To a dragonborn, one's word is one's bond, and failure to uphold it brings dishonor not just to the individual but to their entire clan. They are proud warriors who approach life with the gravity of a sacred duty. Dragonborn are rare outside their own insular communities, and those who adventure do so to prove their worth or to seek a new destiny for their clan.\n\nMechanically, dragonborn gain +2 Strength, +1 Charisma, a Breath Weapon (2d6 damage in a 15-ft cone or 30-ft line based on ancestry, DC 8 + CON + prof, recharge on short rest), and Damage Resistance matching their draconic ancestry. They speak Common and Draconic."},
    "Gnome": {"subraces": ["Forest Gnome", "Rock Gnome", "Deep Gnome"], "asi": {"intelligence": 2}, "speed": 25, "darkvision": 60, "languages": ["Common", "Gnomish"], "traits": ["Gnome Cunning"], "desc": "Gnomes are small, energetic humanoids standing 3 to 4 feet tall and weighing 40 to 45 pounds. Their skin ranges from tan to woody brown, their hair is fair, and their eyes are bright, often blue or violet. Male gnomes favor short, well-trimmed beards. Gnomes live 350 to 500 years—their boundless enthusiasm for life never dims with age.\n\nGnomish culture is defined by curiosity and creativity. They are natural inventors, alchemists, and illusionists, always tinkering with some device or perfecting a new trick. Their communities are hidden burrows in wooded hills, connected by winding tunnels and lit by cleverly engineered mirrors. Gnomes laugh easily, love puzzles, and treat knowledge as the greatest treasure.\n\nMechanically, gnomes gain +2 Intelligence, Darkvision 60 ft, and Gnome Cunning (advantage on all Intelligence, Wisdom, and Charisma saves against magic). They speak Common and Gnomish. This makes gnomes exceptional wizards, artificers, and arcane tricksters.", "subrace_descs": {"Forest Gnome": "+1 Dexterity. Natural Illusionist grants the minor illusion cantrip. Speak with Small Beasts allows simple communication with Tiny and Small animals. Forest gnomes are shy, reclusive tricksters of the deep woods—masters of stealth and woodland magic.", "Rock Gnome": "+1 Constitution. Artificer's Lore doubles proficiency bonus on History checks related to magic items, alchemical objects, or technological devices. Tinker lets you build a tiny clockwork toy, fire starter, or music box. Rock gnomes are the engineers of gnomish society—gadgeteers and jewelers who craft wonders from clockwork and gemstone.", "Deep Gnome": "+1 Dexterity. Superior Darkvision (120 ft), Stone Camouflage (advantage on Stealth in rocky terrain). The svirfneblin—secretive Underdark gnomes with gray skin and a talent for survival in the deepest darkness. They are more serious than their surface cousins, hardened by life among the horrors of the deep earth."}},
    "Half-Elf": {"subraces": [], "asi": {"charisma": 2}, "speed": 30, "darkvision": 60, "languages": ["Common", "Elvish"], "traits": ["Fey Ancestry", "Skill Versatility"], "desc": "Half-elves are born of two worlds—human passion and elven grace. They stand 5 to 6 feet tall, with features that blend the best of both parents: the pointed ears and delicate features of elves with the sturdy build and varied coloration of humans. Their eyes are particularly striking, often green or gold. Half-elves live about 180 years.\n\nHalf-elves are natural diplomats, bridging the gap between cultures. They inherit the elven love of art and the human drive for achievement. Many half-elves feel torn between two heritages, never fully belonging to either world—a loneliness that often drives them to the adventuring life, where skill matters more than bloodline. They are easygoing and charismatic, with a gift for making friends wherever they go.\n\nMechanically, half-elves gain +2 Charisma, +1 to two other abilities of their choice, Fey Ancestry (advantage on saves vs charms, immune to magical sleep), Darkvision 60 ft, and Skill Versatility (two extra skill proficiencies). With their unmatched flexibility, they excel as bards, sorcerers, paladins, and warlocks."},
    "Half-Orc": {"subraces": [], "asi": {"strength": 2, "constitution": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Orc"], "traits": ["Relentless Endurance", "Savage Attacks"], "desc": "Half-orcs are towering figures of strength and endurance, standing 5 to 7 feet tall with powerful builds, gray-green skin, pronounced lower canines, and jutting jaws. They typically weigh 180 to 250 pounds of muscle and bone. Their orcish blood gives them a fearsome appearance, but half-orcs raised among humans often develop remarkable self-control and a deep loyalty to those who accept them.\n\nHalf-orc life is one of constant challenge. Whether in orc tribes where they must prove their strength daily, or in human societies where they must overcome fear and prejudice, half-orcs learn early that respect is earned through deeds, not words. Those who take up the adventuring life do so to find a place where their strength is valued and their loyalty rewarded.\n\nMechanically, half-orcs gain +2 Strength, +1 Constitution, Darkvision 60 ft, Relentless Endurance (when reduced to 0 HP but not killed, drop to 1 HP instead—1/long rest), and Savage Attacks (roll one extra weapon damage die on critical hits). They speak Common and Orc. They make devastating barbarians, fighters, and paladins."},
    "Tiefling": {"subraces": [], "asi": {"charisma": 2, "intelligence": 1}, "speed": 30, "darkvision": 60, "languages": ["Common", "Infernal"], "traits": ["Hellish Resistance", "Infernal Legacy"], "desc": "Tieflings bear the mark of an ancient infernal pact—a sin of their ancestors that manifests in their bloodline. They have large horns, thick tails, sharply pointed teeth, and solid-colored eyes (black, red, white, silver, or gold). Their skin ranges from human tones through deep reds and purples. They stand 5 to 6 feet tall and live slightly longer than humans. No two tieflings look exactly alike.\n\nTieflings are met with suspicion and prejudice in most societies. Their fiendish appearance triggers instinctive fear in common folk, and they grow up knowing they are outsiders. This breeds either bitter resentment or a fierce independence—tieflings who rise above prejudice often become self-reliant adventurers, proving their worth through heroic deeds. Their natural charisma can be unsettling or magnetic, depending on how they choose to wield it.\n\nMechanically, tieflings gain +2 Charisma, +1 Intelligence, Hellish Resistance (resistance to fire damage), Darkvision 60 ft, and Infernal Legacy—the thaumaturgy cantrip, hellish rebuke (1/day at level 3 as 2nd-level), and darkness (1/day at level 5). They speak Common and Infernal. Tieflings make natural warlocks, sorcerers, and bards."},
    "Genasi": {"subraces": ["Air Genasi", "Earth Genasi", "Fire Genasi", "Water Genasi"], "asi": {"constitution": 2}, "speed": 30, "darkvision": 0, "languages": ["Common", "Primordial"], "traits": [], "desc": "Genasi are the children of mortals and genies—elemental spirits of air, earth, fire, and water. They carry the power of the Elemental Planes in their blood. Standing 5 to 6 feet tall, they are built like humans but marked by their elemental heritage: skin that glitters with moisture, hair that ripples like flame, a voice that echoes like shifting stone. Genasi live about 120 years.\n\nGenasi are rare and often solitary. Their elemental nature sets them apart from both their mortal and genie parents. They are self-reliant, independent, and tend toward neutrality—reflecting the primal forces within them. A genasi's elemental subrace defines not just their abilities but their entire outlook: air genasi are swift and detached, earth genasi are stoic and patient, fire genasi are passionate and impulsive, water genasi are adaptable and deep.\n\nMechanically, all genasi gain +2 Constitution, and their subrace grants additional traits including innate spellcasting, damage resistances, and movement abilities tied to their element. They speak Common and Primordial—the language of elemental beings.", "subrace_descs": {"Air Genasi": "+1 Dexterity. Unending Breath (hold breath indefinitely), Mingle with the Wind (levitate 1/long rest at level 3+). Air genasi are light of frame and quick of wit, with pale blue skin and hair that perpetually stirs in an unfelt breeze. Children of the djinn.", "Earth Genasi": "+1 Strength. Earth Walk (ignore difficult terrain of earth or stone), Merge with Stone (pass without trace 1/long rest at level 3+). Earth genasi are solid, deliberate, and patient, with skin in shades of gray and brown, sometimes marked with crystalline growths. Children of the dao.", "Fire Genasi": "+1 Intelligence. Darkvision 60 ft, Fire Resistance, Reach to the Blaze (produce flame cantrip; burning hands 1/long rest at level 3+). Fire genasi burn with inner heat—their skin smolders in shades of coal and ash, their hair a corona of flame. Children of the efreet.", "Water Genasi": "+1 Wisdom. Amphibious (breathe water and air), Swim speed 30 ft, Acid Resistance, Call to the Wave (shape water cantrip; create or destroy water 1/long rest at level 3+). Water genasi appear perpetually fresh from a swim, with blue-green skin and hair that floats as if underwater. Children of the marid."}},
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
        srcs[_s] = SUBRACE_SOURCES.get(_s, parent_src)
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
        # Natural armor: first trait wins (typically highest base AC)
        na = effects.get("natural_armor")
        if na and result["natural_armor"] is None:
            result["natural_armor"] = na

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
            desc = RACIAL_TRAIT_DESCS.get(t)
            if desc:
                # Look up page-accurate source
                src = _trait_page_map.get(t, "")
                if not src:
                    src = race_name
                result.append({"name": t, "desc": desc, "source": src})

    if subrace:
        sub_traits = SUBRACE_TRAITS.get(subrace, [])
        for t in sub_traits:
            desc = RACIAL_TRAIT_DESCS.get(t)
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

CLASSES = {
    "Barbarian": {"hd": 12, "skills": ["Animal Handling","Athletics","Intimidation","Nature","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Path of the Berserker","Path of the Totem Warrior"], "desc": "Barbarians are warriors defined by their rage — a primal fury that transforms them into seemingly unstoppable forces of destruction. Standing at the front of any battle, their muscular frames bear the scars of countless fights. Where other warriors rely on technique and discipline, the barbarian trusts in raw power, instinct, and an almost supernatural resilience that lets them shrug off wounds that would fell lesser combatants.\n\nIn combat, a barbarian enters a Rage as a bonus action, gaining advantage on Strength checks and saves, bonus melee damage, and resistance to bludgeoning, piercing, and slashing damage. They fight recklessly, trading defense for devastating offense with Reckless Attack. Their Danger Sense gives them advantage on Dexterity saves against effects they can see, and their unarmored defense lets them calculate AC from Constitution and Dexterity while eschewing heavy armor.\n\nMechanically, barbarians are d12 hit die melee strikers and damage sponges. At higher levels, they gain Brutal Critical (extra dice on critical hits), Relentless Rage (drop to 1 HP instead of 0), and eventually Primal Champion (+4 to Strength and Constitution, breaking the normal ability score cap). They are unmatched at absorbing punishment while dishing out consistent, heavy damage.", "subclass_descs": {"Path of the Berserker": "At 3rd level, you can go into a Frenzy during your rage, allowing a bonus action melee weapon attack each turn at the cost of a level of exhaustion when the rage ends. Mindless Rage at 6th prevents being charmed or frightened while raging. Intimidating Presence at 10th lets you frighten foes with a display of raw menace. At 14th, Retaliation lets you strike back at anyone who damages you — no action required.", "Path of the Totem Warrior": "At 3rd level, choose a spirit totem: Bear (resistance to all damage except psychic while raging), Eagle (bonus action Dash and enemies have disadvantage on opportunity attacks), or Wolf (allies within 5 feet gain advantage on melee attacks). At 6th, gain an animal aspect: Bear (double carrying capacity), Eagle (see a mile), or Wolf (track at fast pace). At 14th, gain a totemic attunement: Bear (enemies within 5 feet have disadvantage on attacks against others), Eagle (limited flight), or Wolf (bonus action knock prone on hit)."}, "weapons": "Simple weapons, Martial weapons", "armor": "Light armor, Medium armor, Shields", "tools": ""},
    "Bard": {"hd": 8, "skills": "all", "skill_count": 3, "saves": ["dexterity","charisma"], "subclasses": ["College of Lore","College of Valor"], "desc": "Bards are the ultimate storytellers, weaving magic through words, music, and performance. Whether a skald chanting sagas of ancient heroes, a cunning jester who mocks enemies into submission, or a loremaster collecting lost knowledge from forgotten libraries, bards understand that the right word at the right moment can change the course of history. They are charming, quick-witted, and impossibly versatile — a bard can fill nearly any role in an adventuring party.\n\nIn combat, bards wield Bardic Inspiration — a pool of d6s (growing to d12s) that they grant to allies, who can add them to ability checks, attack rolls, or saving throws. Their spellcasting is Charisma-based and draws from a broad list that includes healing, control, enchantment, and utility magic. Jack of All Trades adds half their proficiency bonus to every ability check they're not proficient in, making bards remarkably competent at everything.\n\nMechanically, bards are d8 hit die full spellcasters who know a fixed number of spells rather than preparing them daily. Song of Rest improves short-rest healing for the party. Expertise doubles proficiency for chosen skills. At 10th level, Magical Secrets lets them steal spells from any class list — a bard can learn fireball, find steed, or counterspell. At 20th, Superior Inspiration guarantees they start every combat with at least one use of Bardic Inspiration. They naturally excel as faces, supports, and skill monkeys.", "subclass_descs": {"College of Lore": "At 3rd level, gain three bonus skill proficiencies and Cutting Words: spend a Bardic Inspiration die as a reaction to subtract from an enemy's attack roll, ability check, or damage roll. At 6th, Additional Magical Secrets lets you learn two spells from any class — the Lore bard becomes a magical Swiss Army knife. At 14th, Peerless Skill lets you add Bardic Inspiration to your own ability checks, turning failure into success on demand.", "College of Valor": "At 3rd level, gain proficiency with medium armor, shields, and martial weapons. Combat Inspiration allows allies to add Bardic Inspiration to weapon damage rolls or use it as a reaction to boost AC against an attack. At 6th, Extra Attack grants a second attack when you take the Attack action. At 14th, Battle Magic lets you make a weapon attack as a bonus action after casting a bard spell — the spellblade who sings and swings."}, "weapons": "Simple weapons, Hand crossbows, Longswords, Rapiers, Shortswords", "armor": "Light armor", "tools": "Three musical instruments of your choice"},
    "Cleric": {"hd": 8, "skills": ["History","Insight","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Knowledge Domain","Life Domain","Light Domain","Nature Domain","Tempest Domain","Trickery Domain","War Domain"], "desc": "Clerics are mortal agents of the gods, chosen to wield divine power in the world. A cleric might be a war priest blessing soldiers before battle, a cloistered scholar uncovering forbidden knowledge, or a healer tending to plague victims in the slums. Their power flows from faith and devotion — not study or bloodline — and a cleric's choice of deity and domain shapes their entire identity and playstyle.\n\nAll clerics share core divine abilities. Channel Divinity grants powerful effects (often Turning Undead) that recharge on short rests. They prepare spells daily from the full cleric list, and their Divine Domain at 1st level grants bonus spells and features that define their role — a Life cleric heals more, a Light cleric blasts with radiant fire, a War cleric wades into melee with heavy armor and martial weapons.\n\nMechanically, clerics are d8 hit die full spellcasters with medium armor and shield proficiency. Their spell list includes the best healing magic in the game, powerful buffs (bless, shield of faith), control (spirit guardians, banishment), and offensive staples (guiding bolt, spiritual weapon, flame strike). At 10th level, Divine Intervention gives a percentage chance to call on their deity directly for aid. At 20th, it succeeds automatically. Clerics are the most flexible full casters — a single subclass choice can turn them into a blaster, tank, healer, or controller.", "subclass_descs": {"Knowledge Domain": "Blessings of Knowledge grants expertise in two knowledge skills. Channel Divinity: Read Thoughts lets you read surface thoughts and cast suggestion. Visions of the Past at 17th lets you psychically experience an object's or location's history. The ultimate lore-seeker.", "Life Domain": "Disciple of Life adds 2 + spell level bonus healing to every healing spell. Preserve Life (Channel Divinity) restores HP to allies up to half their max. Blessed Healer heals you when you heal others. Supreme Healing at 17th maximizes all healing dice.", "Light Domain": "Warding Flare imposes disadvantage on attackers as a reaction. Radiance of the Dawn (Channel Divinity) deals radiant damage in a 30-ft radius and dispels magical darkness. Corona of Light at 17th gives enemies disadvantage on saves against your fire and radiant spells. The burning light of truth.", "Nature Domain": "Acolyte of Nature grants a druid cantrip and skill. Charm Animals and Plants (Channel Divinity) pacifies beasts and plants. Dampen Elements at 6th grants resistance to elemental damage as a reaction. Master of Nature at 17th lets you command animals and plants.", "Tempest Domain": "Wrath of the Storm deals thunder or lightning damage as a reaction. Destructive Wrath (Channel Divinity) maximizes thunder/lightning damage instead of rolling. Thunderbolt Strike at 6th pushes foes 10 ft on lightning damage. Stormborn at 17th grants a flying speed.", "Trickery Domain": "Invoke Duplicity (Channel Divinity) creates a perfect illusory double that you can cast spells through. Cloak of Shadows at 6th lets you turn invisible for a round. Divine Strike at 8th adds poison damage. The god of shadows and mischief smiles.", "War Domain": "War Priest grants bonus action weapon attacks (limited WIS mod times per long rest). Guided Strike (Channel Divinity) adds +10 to attack rolls. War God's Blessing at 6th lets allies add +10 to their attacks. Avatar of Battle at 17th grants resistance to nonmagical weapon damage."}, "weapons": "Simple weapons", "armor": "Light armor, Medium armor, Shields", "tools": ""},
    "Druid": {"hd": 8, "skills": ["Arcana","Animal Handling","Insight","Medicine","Nature","Perception","Religion","Survival"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["Circle of the Land","Circle of the Moon"], "desc": "Druids are priests of the old faith — guardians of the natural world who draw their magic from the divine essence of nature itself. They are shapeshifters, storm-callers, and beast-speakers who stand between civilization and the untamed wilds. A druid might be a wizened hermit protecting an ancient grove, a feral wanderer running with wolf packs, or a coastal sage commanding the tides and winds.\n\nA druid's signature ability is Wild Shape, which at 2nd level lets them transform into beasts they've seen. This grants extraordinary utility — a druid can become a spider to infiltrate, a horse to carry allies, or a bear to fight. At higher levels, they can become elementals. Their spellcasting is Wisdom-based, drawn from a nature-themed list heavy on control, summoning, healing, and elemental damage. They prepare spells daily from the full druid list.\n\nMechanically, druids are d8 hit die full spellcasters with medium armor and shield proficiency (though they refuse to wear metal). They gain Timeless Body at 18th (age at 1/10th the normal rate) and Beast Spells at 18th (cast spells while in Wild Shape). Archdruid at 20th grants unlimited Wild Shape uses. They are the most adaptable full casters — capable of tanking as a bear, blasting as a storm, or healing as a forest guardian, all in the same day.", "subclass_descs": {"Circle of the Land": "At 2nd level, gain a bonus druid cantrip and Natural Recovery (recover spell slots on a short rest, like a wizard's Arcane Recovery). At 3rd and higher, Circle Spells grant terrain-based bonus spells: Arctic, Coast, Desert, Forest, Grassland, Mountain, Swamp, or Underdark. Land's Stride at 6th ignores nonmagical difficult terrain. Nature's Ward at 10th grants immunity to poison, disease, and charm/frighten from fey and elementals. Nature's Sanctuary at 14th makes beasts and plants hesitate to attack you.", "Circle of the Moon": "At 2nd level, Combat Wild Shape lets you transform as a bonus action and expend spell slots to heal 1d8 per slot level while transformed. Your Wild Shape CR caps at 1 (instead of 1/4), scaling to CR 6 at 18th. Primal Strike at 6th makes your beast form attacks magical. Elemental Wild Shape at 10th lets you expend both uses to become an air, earth, fire, or water elemental. Thousand Forms at 14th grants alter self at will."}, "weapons": "Clubs, Daggers, Darts, Javelins, Maces, Quarterstaffs, Scimitars, Sickles, Slings, Spears", "armor": "Light armor, Medium armor, Shields (druids will not wear metal armor or shields)", "tools": "Herbalism kit"},
    "Fighter": {"hd": 10, "skills": ["Acrobatics","Animal Handling","Athletics","History","Insight","Intimidation","Perception","Survival"], "skill_count": 2, "saves": ["strength","constitution"], "subclasses": ["Champion","Battle Master","Eldritch Knight"], "desc": "Fighters are the undisputed masters of combat — warriors who have trained their bodies and minds for one purpose: victory in battle. They come from every walk of life: knights in shining plate, grizzled mercenaries, elven archers who never miss, dwarven defenders who hold the line against impossible odds. What unites them is absolute mastery of weapons, armor, and tactics.\n\nFighters gain more Ability Score Improvements than any other class, and their Fighting Style at 1st level defines their combat identity — Archery, Defense, Dueling, Great Weapon Fighting, Protection, or Two-Weapon Fighting. Action Surge at 2nd level grants a second full action once per short rest. Second Wind provides a bonus-action self-heal. But the fighter's true claim to greatness is Extra Attack — at 5th, 11th, and 20th level, they gain additional attacks, swinging four times for every one swing of other warriors.\n\nMechanically, fighters are d10 hit die martial characters who can use every weapon and wear every armor. They are the most reliable damage-dealers and the most durable front-liners. Their subclass choice dramatically expands their toolkit: the Champion is simplicity and lethality, the Battle Master is tactical control, and the Eldritch Knight blends swordplay with wizardry. At 20th, they attack four times per Attack action — eight times with Action Surge.", "subclass_descs": {"Champion": "At 3rd level, Improved Critical scores a critical hit on 19–20 (later 18–20 at 15th). Remarkable Athlete at 7th adds half your proficiency bonus to any Strength, Dexterity, or Constitution check you aren't proficient in. Additional Fighting Style at 10th broadens your combat options. Survivor at 18th regenerates 5 + CON mod HP each turn while below half HP.", "Battle Master": "At 3rd level, Combat Superiority grants four d8 superiority dice and three maneuvers chosen from 16 options: Precision Attack (+die to hit), Trip Attack (+die to damage + knock prone), Riposte (counterattack as reaction), Menacing Attack (frighten), Disarming Attack, and more. Know Your Enemy at 7th lets you size up foes' stats relative to yours. Superiority dice grow to d10 at 10th and d12 at 18th. Relentless at 15th ensures you start every combat with at least one die.", "Eldritch Knight": "At 3rd level, gain wizard spellcasting (abjuration/evocation mostly) with 1/3 caster progression — slots up to 4th level. Weapon Bond prevents you from being disarmed and lets you summon bonded weapons across planes as a bonus action. War Magic at 7th lets you make a weapon attack as a bonus action after casting a cantrip. Eldritch Strike at 10th imposes disadvantage on saves against your next spell. Arcane Charge at 15th lets you teleport before Action Surge."}, "weapons": "Simple weapons, Martial weapons", "armor": "All armor, Shields", "tools": ""},
    "Monk": {"hd": 8, "skills": ["Acrobatics","Athletics","History","Insight","Religion","Stealth"], "skill_count": 2, "saves": ["strength","dexterity"], "subclasses": ["Way of the Open Hand","Way of Shadow","Way of the Four Elements"], "desc": "Monks are living weapons — martial artists who have honed their bodies into instruments of supernatural precision through rigorous discipline and meditation. They need no sword or shield; their fists, feet, and ki — a mystical life energy that flows through all living things — are all the tools they require. A monk might be a serene master atop a mountain peak, a shadowy infiltrator moving without sound, or a wandering ascetic who can catch arrows and run across water.\n\nAll monks share core abilities powered by Ki points, which recharge on short rests. Flurry of Blows lets them attack twice as a bonus action. Patient Defense dodges as a bonus action. Step of the Wind dashes or disengages as a bonus action and doubles jump distance. Deflect Missiles catches and throws back arrows. Stunning Strike at 5th lets them stun enemies with a well-placed blow. Their Unarmored Defense calculates AC from Wisdom and Dexterity, and Unarmored Movement grants increasing speed bonuses — eventually letting them run up walls and across water.\n\nMechanically, monks are d8 hit die skirmishers who excel at mobility, single-target control, and sustained damage through multiple attacks. They gain Evasion at 7th, Purity of Body (immunity to disease and poison) at 10th, Diamond Soul (proficiency in all saves + reroll) at 14th, and Empty Body (invisibility + astral projection resistance) at 18th. At 20th, Perfect Self ensures they start every combat with 4 ki points.", "subclass_descs": {"Way of the Open Hand": "At 3rd level, Open Hand Technique modifies Flurry of Blows: each hit can knock prone, push 15 ft, or prevent reactions. Wholeness of Body at 6th heals 3 × monk level HP per long rest. Tranquility at 11th grants a permanent sanctuary effect. Quivering Palm at 17th delivers a death touch — on a failed CON save, the target dies; on success, it takes 10d10 necrotic damage.", "Way of Shadow": "At 3rd level, Shadow Arts lets you cast darkness, darkvision, pass without trace, or silence for 2 ki each. Shadow Step at 6th allows bonus action teleport between dim light/darkness up to 60 ft, granting advantage on your next attack. Cloak of Shadows at 11th grants invisibility in dim light/darkness. Opportunist at 17th lets you make an opportunity attack against anyone hit by an ally." , "Way of the Four Elements": "At 3rd level, Elemental Attunement grants a minor elemental cantrip and access to Elemental Disciplines — spell-like effects powered by ki: Fangs of the Fire Snake, Water Whip, Fist of Unbroken Air, Shape the Flowing River. At 6th, 11th, and 17th, learn additional disciplines including fireball, fly, stoneskin, and wall of fire. The monk who commands the elements."}, "weapons": "Simple weapons, Shortswords", "armor": "", "tools": "One type of artisan's tools or one musical instrument"},
    "Paladin": {"hd": 10, "skills": ["Athletics","Insight","Intimidation","Medicine","Persuasion","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["Oath of Devotion","Oath of the Ancients","Oath of Vengeance"], "desc": "Paladins are holy warriors, sworn to a sacred oath that is the source of their divine power. More than just fighters with divine magic, paladins are living embodiments of their ideals — a Devotion paladin is a shining beacon of hope, an Ancients paladin is a guardian of joy and life, a Vengeance paladin is an unstoppable force of righteous fury. Their power comes not from a god, but from the sheer force of their own conviction.\n\nIn combat, paladins are devastating melee combatants who can channel divine energy through Divine Smite — expending spell slots to add radiant damage to weapon attacks, with extra damage against fiends and undead. Lay on Hands provides a pool of healing they can distribute as they choose. Their Aura of Protection at 6th adds Charisma to all saving throws for themselves and nearby allies. At higher levels, their auras expand with subclass-specific effects that can turn the tide of battle.\n\nMechanically, paladins are d10 hit die half-casters who prepare spells daily. Fighting Style at 2nd, Extra Attack at 5th, and Aura of Courage (immunity to frightened) at 10th. Cleansing Touch at 14th ends spells on allies. At 20th, their capstone transformation is subclass-specific: Devotion becomes an avatar of divine light, Ancients becomes a force of primeval nature, and Vengeance becomes an avenging angel with flight and frightful presence.", "subclass_descs": {"Oath of Devotion": "Tenets: honesty, courage, compassion, honor, duty. At 3rd, Sacred Weapon (Channel Divinity) adds CHA to attack rolls and makes the weapon magical and glowing. Turn the Unholy frightens fiends and undead. Aura of Devotion at 7th prevents charm within 10 ft. Purity of Spirit at 15th grants permanent protection from evil and good. Holy Nimbus at 20th deals 10 radiant damage per round to enemies within 30 ft — the ultimate holy avatar.", "Oath of the Ancients": "Tenets: kindle light, shelter joy, preserve life, be the light. At 3rd, Nature's Wrath (Channel Divinity) restrains a foe with spectral vines. Turn the Faithless frightens fey and fiends. Aura of Warding at 7th grants resistance to all spell damage within 10 ft — one of the strongest defensive features in the game. Undying Sentinel at 15th drops you to 1 HP instead of 0 once per long rest. Elder Champion at 20th grants fast healing, quickened smite spells, and disadvantage on enemy saves.", "Oath of Vengeance": "Tenets: fight evil, no mercy, by any means, restitution. At 3rd, Vow of Enmity (Channel Divinity) grants advantage on all attacks against one foe for 1 minute. Abjure Enemy frightens and immobilizes. Relentless Avenger at 7th lets you move half speed after opportunity attacks without provoking. Soul of Vengeance at 15th lets you make an attack as a reaction against the target of your Vow. Avenging Angel at 20th grants 60-ft flight, a frightful presence aura, and advantage on Vow of Enmity attacks."}, "weapons": "Simple weapons, Martial weapons", "armor": "All armor, Shields", "tools": ""},
    "Ranger": {"hd": 10, "skills": ["Animal Handling","Athletics","Insight","Investigation","Nature","Perception","Stealth","Survival"], "skill_count": 3, "saves": ["strength","dexterity"], "subclasses": ["Hunter","Beast Master"], "desc": "Rangers are the scouts, trackers, and wilderness warriors who thrive at the boundary between civilization and the wild. They are expert hunters who know their prey's every habit, master archers who can loose a volley into a crowd, and lonely wanderers who follow ancient trails through trackless forests. A ranger's connection to nature grants them primal magic — not the full power of a druid, but enough to heal, ensnare, and strike from the shadows.\n\nAt their core, rangers combine martial skill with nature magic. They gain Fighting Style at 2nd, Spellcasting (Wisdom-based, from the ranger list) at 2nd, and Extra Attack at 5th. Their signature abilities focus on exploration and terrain mastery: Natural Explorer grants doubled proficiency and benefits in favored terrain, Favored Enemy grants advantage on tracking and knowledge about specific creature types. Primeval Awareness at 3rd lets them sense the presence of favored enemies within miles.\n\nMechanically, rangers are d10 hit die half-casters who excel at ranged combat, stealth, and exploration. Land's Stride at 8th ignores nonmagical difficult terrain, Hide in Plain Sight at 10th lets them camouflage themselves with natural materials, and Vanish at 14th lets them Hide as a bonus action. Foe Slayer at 20th adds Wisdom to one attack or damage roll against a favored enemy per turn. Rangers are the ultimate wilderness specialists.", "subclass_descs": {"Hunter": "At 3rd level, choose a Hunter's Prey: Colossus Slayer (extra 1d8 damage to wounded foes once per turn), Giant Killer (reaction attack when Large+ creatures miss you), or Horde Breaker (free extra attack against a nearby creature once per turn). Defensive Tactics at 7th: Escape the Horde (opportunity attacks have disadvantage), Multiattack Defense (+4 AC against subsequent attacks), or Steel Will (advantage on saves vs frightened). Multiattack at 11th: Volley (attack everything in a 10-ft radius) or Whirlwind Attack (melee attack everything within 5 ft). Superior Hunter's Defense at 15th: Evasion, Stand Against the Tide (force attacker to hit someone else), or Uncanny Dodge.", "Beast Master": "At 3rd level, gain an animal companion — a beast of CR 1/4 or lower that acts on your initiative and obeys your commands. At 7th, Exceptional Training lets your companion Dash, Disengage, Dodge, or Help as a bonus action. At 11th, Bestial Fury grants your companion two attacks. At 15th, Share Spells lets spells you cast on yourself also affect your companion. The bond between ranger and beast is unbreakable — if it dies, you can spend 8 hours magically bonding with a new one."}, "weapons": "Simple weapons, Martial weapons", "armor": "Light armor, Medium armor, Shields", "tools": ""},
    "Rogue": {"hd": 8, "skills": ["Acrobatics","Athletics","Deception","Insight","Intimidation","Investigation","Perception","Performance","Persuasion","Sleight of Hand","Stealth"], "skill_count": 4, "saves": ["dexterity","intelligence"], "subclasses": ["Thief","Assassin","Arcane Trickster"], "desc": "Rogues are masters of stealth, precision, and misdirection. They live by their wits — slipping through shadows, disarming traps, picking pockets, and striking when their enemies least expect it. A rogue might be a charming con artist, a silent assassin, a cat burglar who can scale any wall, or an investigator who notices what everyone else misses. What defines them is not their weapons or armor, but their cunning.\n\nA rogue's defining combat feature is Sneak Attack — once per turn, when they attack with advantage or when an ally is adjacent to the target, they deal massive extra damage (1d6 at 1st, scaling to 10d6 at 20th). Cunning Action at 2nd lets them Dash, Disengage, or Hide as a bonus action — unmatched tactical mobility. Uncanny Dodge at 5th halves damage from one attack per round. Evasion at 7th negates damage entirely on successful Dexterity saves. They gain more skill proficiencies and Expertise than any other class.\n\nMechanically, rogues are d8 hit die martial strikers who avoid direct confrontation in favor of hit-and-run tactics. Reliable Talent at 11th makes any proficient skill check they roll treat a 9 or lower as a 10 — rogues almost never fail at what they're good at. Blindsense at 14th detects hidden and invisible creatures. Slippery Mind at 15th grants Wisdom save proficiency. Stroke of Luck at 20th turns a missed attack into a hit or a failed ability check into a natural 20 once per short rest.", "subclass_descs": {"Thief": "At 3rd level, Fast Hands lets you use objects, pick locks, and disarm traps as a bonus action — plus make Sleight of Hand checks. Second-Story Work adds climbing speed equal to your walking speed and increased running jump distance. Supreme Sneak at 9th grants advantage on Stealth checks when moving slowly. Use Magic Device at 13th ignores all class, race, and level restrictions on magic items. Thief's Reflexes at 17th grants an extra turn in the first round of combat.", "Assassin": "At 3rd level, Assassinate gives advantage against creatures that haven't acted yet and auto-crits surprised creatures. Bonus proficiencies with disguise kit and poisoner's kit. Infiltration Expertise at 9th lets you create false identities over 7 days of preparation. Impostor at 13th lets you perfectly mimic a studied person's speech, writing, and behavior. Death Strike at 17th forces a CON save on surprised targets you hit — on failure, double the damage.", "Arcane Trickster": "At 3rd level, gain wizard spellcasting (illusion/enchantment mostly) with 1/3 caster progression. Mage Hand Legerdemain makes your mage hand invisible and capable of pickpocketing, lockpicking, and stowing objects. Magical Ambush at 9th imposes disadvantage on spell saves when you're hidden. Versatile Trickster at 13th lets you use your bonus action to grant advantage via mage hand. Spell Thief at 17th lets you steal a spell from an enemy and cast it yourself."}, "weapons": "Simple weapons, Hand crossbows, Longswords, Rapiers, Shortswords", "armor": "Light armor", "tools": "Thieves' tools"},
    "Sorcerer": {"hd": 6, "skills": ["Arcana","Deception","Insight","Intimidation","Persuasion","Religion"], "skill_count": 2, "saves": ["constitution","charisma"], "subclasses": ["Draconic Bloodline","Wild Magic"], "desc": "Sorcerers are born with magic in their blood — not learned, not granted, but innate. The source of their power might be a draconic ancestor, an encounter with a being of raw chaos, or some cosmic event that awakened latent potential. Unlike wizards who study dusty tomes, sorcerers wield magic by instinct and force of personality. They don't prepare spells; they know a smaller, carefully chosen repertoire and can bend those spells in ways no other caster can.\n\nA sorcerer's defining feature is Metamagic — the ability to reshape spells on the fly using Sorcery Points. They can Twin a spell to hit two targets, Quicken a spell to cast as a bonus action, Subtle a spell to cast without components, Heighten a spell to impose disadvantage on saves, and more. Sorcery Points can also be converted into spell slots and vice versa — sorcerers have more flexibility with their spell slots than any other full caster.\n\nMechanically, sorcerers are d6 hit die full casters who use Charisma as their spellcasting ability. They know a limited number of spells but can cast them flexibly. Font of Magic at 2nd creates their Sorcery Point pool. At 20th, Sorcerous Restoration recovers 4 Sorcery Points on short rests. Sorcerers are the most specialized full casters — a Draconic sorcerer is a durable elemental blaster, while a Wild Magic sorcerer is an unpredictable chaos engine that can grant advantage at will.", "subclass_descs": {"Draconic Bloodline": "At 1st level, choose a dragon color (determining your damage affinity). Draconic Resilience grants +1 HP per sorcerer level and natural AC of 13 + DEX. At 6th, Elemental Affinity adds CHA to spell damage of your chosen element and grants resistance to that element for 1 hour per Sorcery Point. Dragon Wings at 14th grant a flying speed of your walking speed. Draconic Presence at 18th frightens or charms creatures within 60 ft.", "Wild Magic": "At 1st level, Wild Magic Surge: after casting a non-cantrip spell, the DM can have you roll a d20 — on a 1, roll on the Wild Magic table (50 random effects from self-fireball to feather beard to flumph summoning). Tides of Chaos grants advantage on any d20 roll, recharging after your next surge. Bend Luck at 6th spends 2 Sorcery Points to add or subtract 1d4 from any creature's roll as a reaction. Controlled Chaos at 14th lets you roll twice on the Wild Magic table and choose. Spell Bombardment at 18th lets you reroll damage dice on spells and take the higher."}, "weapons": "Daggers, Darts, Slings, Quarterstaffs, Light crossbows", "armor": "", "tools": ""},
    "Warlock": {"hd": 8, "skills": ["Arcana","Deception","History","Intimidation","Investigation","Nature","Religion"], "skill_count": 2, "saves": ["wisdom","charisma"], "subclasses": ["The Archfey","The Fiend","The Great Old One"], "desc": "Warlocks are seekers of forbidden knowledge who have struck a bargain with an otherworldly patron — a being of immense power, neither wholly benevolent nor entirely malevolent. The patron might be an ancient fey lord, a demon prince of the Abyss, or a slumbering entity from beyond the stars. In exchange for power, the warlock serves their patron's interests — or at least, they pay lip service while pursuing their own goals.\n\nWarlocks use Pact Magic — a unique spellcasting system with very few spell slots (starting at 1, maxing at 4) that all cast at the same level (scaling up to 5th) and recharge on short rests. This means warlocks can cast their most powerful spells every fight but must be judicious about when to use them. To compensate for limited slots, warlocks rely on Eldritch Blast — the best damaging cantrip in the game, which they can enhance with Eldritch Invocations to add Charisma to damage, push enemies, or pull them closer.\n\nMechanically, warlocks are d8 hit die full casters who use Charisma. At 3rd level they choose a Pact Boon: Pact of the Chain (improved familiar), Pact of the Blade (summonable magic weapon), or Pact of the Tome (Book of Shadows with extra cantrips). Eldritch Invocations at 2nd and beyond grant permanent abilities — agonizing blast, devil's sight, mask of many faces, and more. Mystic Arcanum at 11th+ grants one 6th, 7th, 8th, and 9th level spell per long rest. Warlocks are the most modular class — no two are built the same.", "subclass_descs": {"The Archfey": "At 1st level, Fey Presence (once per short rest) charms or frightens creatures in a 10-ft cube. Misty Escape at 6th lets you teleport 60 ft and turn invisible until your next turn when you take damage. Beguiling Defenses at 10th grants immunity to charm and reflects charm attempts back at the source. Dark Delirium at 14th sends a creature into an illusory nightmare realm where it is charmed or frightened of you.", "The Fiend": "At 1st level, Dark One's Blessing grants temporary HP equal to CHA + warlock level when you reduce a hostile creature to 0 HP. Dark One's Own Luck at 6th adds 1d10 to an ability check or saving throw once per short rest. Fiendish Resilience at 10th grants resistance to one damage type (changeable on short rest). Hurl Through Hell at 14th sends a creature on a psychic journey through the lower planes — they return at the end of your next turn, taking 10d10 psychic damage.", "The Great Old One": "At 1st level, Awakened Mind grants two-way telepathy with any creature within 30 ft that understands a language. Entropic Ward at 6th imposes disadvantage on an attacker's roll as a reaction — if they miss, you gain advantage on your next attack against them. Thought Shield at 10th grants resistance to psychic damage and prevents your thoughts from being read. Create Thrall at 14th lets you permanently charm a humanoid touched while incapacitated."}, "weapons": "Simple weapons", "armor": "Light armor", "tools": ""},
    "Wizard": {"hd": 6, "skills": ["Arcana","History","Insight","Investigation","Medicine","Religion"], "skill_count": 2, "saves": ["intelligence","wisdom"], "subclasses": ["School of Abjuration","School of Conjuration","School of Divination","School of Enchantment","School of Evocation","School of Illusion","School of Necromancy","School of Transmutation"], "desc": "Wizards are the ultimate students of magic — scholars who have spent years, decades, or centuries poring over ancient tomes, deciphering arcane formulae, and mastering the fundamental laws of reality. Their power comes from intellect and discipline, not bloodline or pact. A wizard's spellbook is their most prized possession, a growing encyclopedia of magical knowledge that represents years of research and discovery.\n\nWizards have the largest spell list in the game and the unique ability to learn new spells by copying scrolls or spellbooks into their own — for a cost in gold and time, a wizard can theoretically learn every wizard spell in existence. They prepare spells daily from their spellbook, choosing a flexible loadout tailored to the challenges ahead. Arcane Recovery at 1st level lets them recover spell slots on short rests. Ritual Casting allows them to cast any ritual spell in their spellbook without preparing it or expending a slot.\n\nMechanically, wizards are d6 hit die full casters who use Intelligence — the ultimate utility and control casters. They gain no armor proficiency and have the smallest hit die, so positioning and defensive spells are critical. At higher levels, they gain Spell Mastery (at-will 1st and 2nd level spells at 18th) and Signature Spells (two 3rd-level spells always prepared at 20th). Their Arcane Tradition (school specialization) at 2nd level dramatically shapes their role: blaster (Evocation), controller (Enchantment), defender (Abjuration), or summoner (Conjuration).", "subclass_descs": {"School of Abjuration": "At 2nd level, Arcane Ward creates a magical HP buffer equal to wizard level × 2 + INT mod, recharged by casting abjuration spells. Projected Ward at 6th lets you extend your ward to protect allies. Improved Abjuration at 10th adds proficiency to ability checks when casting abjuration spells (like counterspell and dispel magic). Spell Resistance at 14th grants advantage on all saving throws against spells and resistance to spell damage.", "School of Conjuration": "At 2nd level, Minor Conjuration creates a nonmagical object (max 10 lbs, 3 ft per side) that glows faintly and lasts 1 hour. Benign Transposition at 6th teleports you 30 ft or swaps places with a willing ally — recharges when you cast a conjuration spell. Focused Conjuration at 10th prevents concentration from being broken by damage on conjuration spells. Durable Summons at 14th grants 30 temporary HP to any creature you summon.", "School of Divination": "At 2nd level, Portent: after each long rest, roll two d20s and record the results. Before any creature you can see makes an attack roll, saving throw, or ability check, you can replace their roll with one of your portent dice. Expert Divination at 6th recovers a lower-level spell slot when you cast a divination spell. The Third Eye at 10th grants darkvision, ethereal sight, greater comprehension, or see invisibility. Greater Portent at 14th adds a third portent die.", "School of Enchantment": "At 2nd level, Hypnotic Gaze incapacitates a creature within 5 ft until your next turn. Instinctive Charm at 6th redirects an attack against you to the nearest creature as a reaction. Split Enchantment at 10th lets you target two creatures with any enchantment spell that normally targets one. Alter Memories at 14th makes a charmed creature unaware of being charmed and forgets some of the time spent charmed.", "School of Evocation": "At 2nd level, Sculpt Spells lets you designate 1 + spell level creatures to automatically succeed on saves and take no damage from your evocation spells. Potent Cantrip at 6th makes your cantrips deal half damage on successful saves. Empowered Evocation at 10th adds INT mod to evocation spell damage. Overchannel at 14th maximizes the damage of a 5th-level-or-lower evocation spell — at the cost of necrotic damage to yourself on repeat uses.", "School of Illusion": "At 2nd level, Improved Minor Illusion creates both sound and image with one casting. Malleable Illusions at 6th lets you reshape ongoing illusion spells as an action. Illusory Self at 10th creates an illusory double that intercepts an attack, making it miss — recharges on short rest. Illusory Reality at 14th makes one inanimate object in your illusion temporarily real — a bridge that can be crossed, a wall that blocks attacks, a cage that holds prisoners.", "School of Necromancy": "At 2nd level, Grim Harvest heals you for 2 × spell level (or 3 × for necromancy spells) when you kill a creature with a spell. Undead Thralls at 6th lets you animate more undead and gives them extra HP and your proficiency bonus to damage. Inured to Undeath at 10th grants resistance to necrotic damage and prevents your HP maximum from being reduced. Command Undead at 14th lets you permanently control any undead that fails an INT save — even a mummy lord or a lich, if you're lucky.", "School of Transmutation": "At 2nd level, Minor Alchemy temporarily transforms wood, stone, iron, copper, or silver into another of those materials for 1 hour per 10 minutes spent. Transmuter's Stone at 6th creates a stone that grants you or a holder a buff: darkvision, speed +10 ft, proficiency in CON saves, or resistance to one element. Shapechanger at 10th lets you cast polymorph on yourself once per short rest (CR 1 or lower). Master Transmuter at 14th lets you destroy your stone to raise dead, de-age, restore youth, or cure all diseases."}, "weapons": "Daggers, Darts, Slings, Quarterstaffs, Light crossbows", "armor": "", "tools": ""},
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
        print(f"  Class sources enriched: {sum(1 for c in CLASSES.values() if 'p.' in c.get('source',''))}/{len(CLASSES)}")
except Exception as _e:
    print(f"  (class page map unavailable: {_e})")

# ── Subclass source enrichment ──
# Apply to subclass sources stored in CLASSES._subclass_sources
_subclass_enriched = 0
for _cname, _cdata in CLASSES.items():
    _ss_map = _cdata.get("_subclass_sources", {})
    for _sname in list(_ss_map.keys()):
        _mapped = _class_page_map.get(_sname.lower())
        if _mapped:
            _ss_map[_sname] = _mapped
            _subclass_enriched += 1
if _subclass_enriched:
    print(f"  Subclass sources enriched: {_subclass_enriched}")

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
    idx_entry = ITEM_INDEX.get(name) or ITEM_INDEX.get(name_singular)
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
        "description": ITEM_INDEX.get(item_name.lower(), {}).get("description", ""),
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
                item_info = ITEM_INDEX.get(key, {})
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
        info = ITEM_INDEX.get(key)
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
    """Extract just the names from a [{name, qty}] equipped list."""
    return [e["name"] for e in equipped if isinstance(e, dict) and e.get("name")]

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
        race_names=RACE_NAMES)

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
    enriched = enrich_features(build_features, class_name=class_name, level=level, mods={a: (stats[a] - 10) // 2 for a in stats})
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
        feature_data, attacks_data, spell_slot_data, passive_perception, dragonborn_ancestry, portrait_url, portrait_prompt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        data.get("portrait_url", ""), data.get("portrait_prompt", "")
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
    if slots and slots.get("by_level"):
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
        })
    results.sort(key=lambda s: (s["level"], s["name"]))
    return JSONResponse(results)

# ── DM Tools: Monster helpers ──────────────────────────────────────────────

MANUAL_MONSTERS: list[dict] = []
MANUAL_TRAPS: list[dict] = []

def _load_monster_cache() -> list[dict]:
    global MANUAL_MONSTERS
    base = _load_json_cache("monsters.json")
    # Tag SRD monsters with source
    for m in base:
        if "source" not in m:
            m["source"] = "SRD 5.1"
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
        try:
            m["challenge_rating"] = float(cr)
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
    """
    MANUAL_CACHE.mkdir(parents=True, exist_ok=True)
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
    for pdf_name, label in book_labels.items():
        pdf_path = MANUALS_DIR / pdf_name
        txt_path = MANUAL_CACHE / f"{label}.txt"
        if pdf_path.exists():
            if not txt_path.exists() or pdf_path.stat().st_mtime > txt_path.stat().st_mtime:
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
    }

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
                raw = (prox * 0.35 + density * 0.20 + source_w * 0.25 + exact * 0.20)
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
                book_results.append({
                    "book": label,
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
    book_order = list(cached.keys())
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
    return _render("dm_tools.html", request=request,
                   monsters=all_monsters, monster_types=monster_types,
                   cr_ranges=cr_ranges, npcs=npcs,
                   encounters=encounters, campaigns=campaigns,
                   traps=MANUAL_TRAPS)


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


@app.post("/api/dm/ai/build-encounter", response_class=JSONResponse)
async def dm_ai_build_encounter(request: Request):
    """AI-suggested encounter composition based on party size/level, environment, and difficulty."""
    user = require_user(request)
    data = await request.json()
    party_level = int(data.get('party_level', 5))
    party_size = int(data.get('party_size', 4))
    environment = data.get('environment', 'dungeon')
    difficulty = data.get('difficulty', 'medium')
    theme = data.get('theme', '')
    tone = data.get('tone', '')
    target_cr_raw = data.get('target_cr', '')

    # Determine effective CR range for filtering
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
            if not any(e in env_words or e in suitable for e in env_words) and not any(kw in m_name for kw in env_words):
                env_match = False

        if not env_match:
            continue

        # CR within reasonable range (target CR ±2, or party level ±2 if no target)
        try:
            m_cr = float(m.get("challenge_rating", 0))
        except (TypeError, ValueError):
            continue

        if target_cr is not None:
            max_cr = target_cr + 3
            min_cr = max(0, target_cr - 2)
        else:
            max_cr = party_level + 2
            min_cr = max(0, party_level - 3)
        if m_cr > max_cr or (m_cr < min_cr and m_cr > 0.125):
            continue
        if m_cr < 0.125:
            continue

        m_xp = _xp_for_cr(m_cr)
        if m_xp == 0:
            continue

        candidates.append({
            "index": m["index"], "name": m["name"], "cr": m_cr, "xp": m_xp,
            "type": m_type, "size": m.get("size", ""),
            "ac": m["armor_class"][0]["value"] if m.get("armor_class") else 10,
            "hp": m.get("hit_points", 0),
        })

    # AI composition suggestion
    cr_info = f"Target CR: {target_cr_raw}" if target_cr_raw else f"Party: {party_size} level {party_level}"
    ai_prompt = f"""Suggest a D&D 5e encounter for {cr_info} characters.
Environment: {environment}{f' Theme: {theme}' if theme else ''}{f' Tone: {tone}' if tone else ''}
Difficulty target: {difficulty}

Available monsters (pick 2-4 types, vary roles — one boss-type, some support, some minions):
{candidates[:50]}

Return ONLY valid JSON (no markdown):
{{"name": "encounter name (atmospheric, location-based)", "description": "1-2 sentence setup vignette", 
"composition": [{{"index": "monster index from list", "count": 2}}],
"tactics": "1-2 sentence tactics for this encounter"}}"""

    text = await _call_gemini(ai_prompt) or await _call_openrouter(ai_prompt) or await _call_ollama(ai_prompt)
    ai = _extract_json(text) if text else None

    composition = []
    xp_total = 0
    if ai and ai.get("composition"):
        for entry in ai.get("composition", []):
            count = int(entry.get("count", 1))
            idx = entry.get("index", "").lower()
            # Find matching monster
            m = next((c for c in candidates if c["index"].lower() == idx), None)
            if m:
                xp_total += m["xp"] * count
                composition.append({
                    "index": m["index"], "name": m["name"], "cr": m["cr"],
                    "xp": m["xp"], "count": count,
                    "ac": m["ac"], "hp": m["hp"], "type": m["type"], "size": m["size"],
                })

    # Fallback: algorithmic composition if AI fails
    if not composition:
        import random
        # Pick a boss-appropriate monster (CR ≈ party level ± 1)
        boss_candidates = [c for c in candidates if abs(c["cr"] - party_level) <= 1 and c["cr"] >= 1]
        if not boss_candidates:
            boss_candidates = candidates[:20]
        boss = random.choice(boss_candidates) if boss_candidates else None
        if boss:
            boss_count = 1
            composition.append({**boss, "count": boss_count})
            xp_total += boss["xp"] * boss_count

        # Add minions (lower CR)
        remaining = xp_budget - xp_total
        minion_candidates = [c for c in candidates if c["cr"] < (party_level - 1 if party_level > 1 else 0.5)]
        minion_count = 0
        while remaining > 0 and minion_candidates and minion_count < 6:
            minion = random.choice(minion_candidates)
            if minion["xp"] <= remaining:
                c = min(3, max(1, remaining // minion["xp"]))
                composition.append({**minion, "count": c})
                xp_total += minion["xp"] * c
                remaining -= minion["xp"] * c
                minion_count += c
            else:
                minion_candidates.remove(minion)

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

    # Difficulty label (DMG p.82)
    if budget_pct < 50: diff_label = "Easy"
    elif budget_pct < 100: diff_label = "Medium"
    elif budget_pct < 150: diff_label = "Hard"
    else: diff_label = "Deadly"

    return JSONResponse({
        "name": ai.get("name", f"Random {environment.title()} Encounter") if ai else f"{environment.title()} Encounter",
        "description": ai.get("description", f"A {difficulty} encounter in {environment}.") if ai else "",
        "tactics": ai.get("tactics", "") if ai else "",
        "composition": composition,
        "xp": {"raw_total": xp_total, "adjusted": adjusted_xp, "budget": xp_budget, "budget_pct": budget_pct},
        "difficulty": diff_label,
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
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods)
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
              "save_proficiencies","damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities"):
        try:
            char[f] = json.loads(char[f])
        except (json.JSONDecodeError, TypeError):
            char[f] = []
    # Normalize equipped to [{name, qty}] format (backward compat with old string-list format)
    char["equipped"] = _normalize_equipped(char["equipped"])
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
    # Enrich with pool_kind from LIMITED_USE (so existing characters get Lay on Hands HP pool)
    for _feat in char["feature_data"]:
        if isinstance(_feat, dict) and not _feat.get("pool_kind"):
            _key = _feat.get("name", "").lower()
            for lkey, lu in LIMITED_USE.items():
                if lkey in _key and lu.get("pool_kind"):
                    _feat["pool_kind"] = lu["pool_kind"]
                    break
    # Fallback: features still without source inherit from character's class
    _cls_source = CLASSES.get(char.get("class_name", ""), {}).get("source", "")
    if _cls_source:
        for _feat in char["feature_data"]:
            if not _feat.get("source"):
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
    enrich_spells(spells)
    db.close()

    # Compute modifiers
    for stat in ["strength","dexterity","constitution","intelligence","wisdom","charisma"]:
        char[f"{stat}_mod"] = (char[stat] - 10) // 2

    # Recalculate AC for natural armor races (Tortle, Lizardfolk, etc.)
    racial_effects = get_racial_trait_effects(
        char.get("race", ""), char.get("subrace", ""),
        char.get("dragonborn_ancestry", ""))
    natural_armor = racial_effects.get("natural_armor")
    if natural_armor:
        char["ac"] = natural_armor.get("base_ac", 17)
        max_dex = natural_armor.get("max_dex")
        if max_dex is not None:
            char["ac"] += min(char["dexterity_mod"], max_dex)

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
    class_levels_data = parse_class_levels(char)
    
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

    return _render("sheet.html", request=request, character=char, spells=spells,
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
                   draconic_ancestries=DRACONIC_ANCESTRIES)

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
        "attuned_items",
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
    char_data = build_char_data(row, db)
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
    cursor = db.execute(
        "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, npc_data, ai_generated) VALUES (?,?,?,?,?,?,0)",
        (char_id, user["id"], name, rel_type, description, json.dumps(npc_data))
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
    db = get_db()
    cursor = db.execute(
        "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description, prompt, npc_data, ai_generated) VALUES (?,?,?,?,?,?,?,1)",
        (char_id, user["id"], name, rel_type, description, prompt, json.dumps(npc_data))
    )
    rel_id = cursor.lastrowid
    db.commit()
    rel_row = dict(db.execute("SELECT * FROM character_relationships WHERE id = ?", (rel_id,)).fetchone())
    db.close()
    return JSONResponse(rel_row)

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
    for lvl in asi_levels:
        asi_infos[str(lvl)] = {
            "level": lvl,
            "abilities": dict(abilities),  # snapshot
            "max_20": [a for a in ABILITY_NAMES if abilities[a] >= 20],
        }
    
    # Feats — return ALL, JS filters by running abilities per ASI step
    feats_available = []
    for key, feat in FEATS.items():
        feats_available.append({
            "key": key, "name": feat["name"],
            "desc": feat.get("desc") or feat.get("description", ""),
            "asi": feat.get("asi"), "prereq": feat.get("prereq") or feat.get("prerequisite"),
            "source": feat.get("source", ""),
        })
    
    # Subclass
    subclass_info = None
    sc = SUBCLASS_LEVELS.get(cls)
    if sc and current_level < sc["level"] <= target_level and not char.get("subclass"):
        descs = CLASSES.get(cls, {}).get("subclass_descs", {})
        subclass_info = {
            "level": sc["level"],
            "label": sc["label"],
            "options": sc["options"],
            "descriptions": {opt: descs.get(opt, "") for opt in sc["options"]},
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
        # Cantrips
        cantrip_key = "cleric" if cls == "Cleric" else ("warlock" if cls == "Warlock" else "full")
        if caster_type in ("full", "pact") or cls == "Cleric":
            ct = CANTRIPS_PROGRESSION.get(cantrip_key, {})
            old_cantrips = sum(v for k, v in ct.items() if k <= current_level)
            new_cantrips = sum(v for k, v in ct.items() if k <= target_level)
            spell_info["cantrips_change"] = max(0, new_cantrips - old_cantrips)
    
    return JSONResponse({
        "class_name": cls,
        "current_level": current_level,
        "class_level": class_level,  # level IN this class (0 for new multiclass)
        "target_level": target_level,
        "levels": levels_gained,
        "all_features": all_features,
        "hp": hp_options,
        "asi_levels": asi_levels,
        "asi_info": asi_infos,
        "feats": feats_available,
        "subclass": subclass_info,
        "subclass_bonus_map": subclass_bonus_map,
        "proficiency_bonus": {"old": old_pb, "new": new_pb, "changed": old_pb != new_pb},
        "spells": spell_info,
        "has_subclass": bool(char.get("subclass")),
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
    hp_choices = data.get("hp_choices", {})
    # Also support flat hp choice from frontend (legacy format)
    hp_flat = data.get("hp")
    hp_custom = data.get("hp_custom")
    if not hp_choices and hp_flat:
        for offset in range(levels_gained):
            lvl_str = str(old_total + offset + 1)
            hp_choices[lvl_str] = hp_flat
    asi_choices = data.get("asi_choices", {})
    feat_asi_choices = data.get("feat_asi_choices", {})
    
    total_hp_gain = 0
    hd = CLASSES.get(class_to_level, {}).get("hd", 8)
    levels_gained = target_level - old_total
    
    for offset in range(levels_gained):
        lvl_num = old_total + offset + 1
        lvl_str = str(lvl_num)
        if lvl_str in asi_choices:
            choice = asi_choices[lvl_str]
            if isinstance(choice, dict):
                for ability, increase in choice.items():
                    cumulative[ability] = cumulative.get(ability, 10) + increase
                    updates[ability.lower()] = cumulative[ability]
                    changes.append(f"L{lvl_str}: {ability} +{increase}")
            elif isinstance(choice, str) and choice.startswith("feat:"):
                feat_key = choice[5:]
                feat = FEATS.get(feat_key, {})
                changes.append(f"L{lvl_str}: Feat — {feat.get('name', feat_key)}")
                feat_asi = feat.get("asi")
                if feat_asi:
                    chosen_abi = feat_asi_choices.get(lvl_str)
                    if chosen_abi and chosen_abi in ABILITY_NAMES:
                        cumulative[chosen_abi] = cumulative.get(chosen_abi, 10) + feat_asi["amount"]
                        updates[chosen_abi.lower()] = cumulative[chosen_abi]
        
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
    enriched = enrich_features(all_feature_names, class_name=class_to_level, level=target_level, mods=mods, class_levels=new_cl)
    updates["feature_data"] = json.dumps(enriched)
    
    # Spell slots — multiclass-aware
    char_copy = dict(char)
    char_copy["class_levels"] = json.dumps(new_cl)
    char_copy["level"] = target_level
    spell_slots = get_character_spell_slots(char_copy)
    updates["spell_slot_data"] = json.dumps(spell_slots)
    updates["spell_slots_used"] = json.dumps({})  # Fresh slots after level up
    
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
    target_level = int(request.query_params.get("target", current_level - 1))
    target_level = max(1, min(target_level, current_level - 1))
    
    if current_level <= 1:
        return JSONResponse({"error": "Already at level 1"}, status_code=400)
    
    hd = CLASSES.get(cls, {}).get("hd", 8)
    con_mod = (char.get("constitution", 10) - 10) // 2
    avg_hp = (hd // 2) + 1 + con_mod
    
    # Features lost (at current but not at target)
    old_features = get_class_features(cls, target_level, char.get("subclass", ""))
    new_features = get_class_features(cls, current_level, char.get("subclass", ""))
    features_lost = [f for f in new_features if f not in old_features]
    
    # Get what target-level features look like
    target_features = get_class_features(cls, target_level, char.get("subclass", ""))
    
    # ASI levels being rolled back
    lost_asi_levels = [lvl for lvl in range(target_level + 1, current_level + 1) if lvl in ASI_LEVELS.get(cls, [])]
    
    # Current ability scores
    abilities = {a: char.get(a.lower(), 10) for a in ABILITY_NAMES}
    
    # Subclass note
    subclass_note = None
    sc = SUBCLASS_LEVELS.get(cls)
    current_subclass = char.get("subclass", "")
    if sc and current_subclass and sc["level"] > target_level:
        subclass_note = f"{sc['label']}: {current_subclass} (chosen at L{sc['level']} — will be cleared since target < L{sc['level']})"
    
    # Proficiency
    old_pb = PROFICIENCY_BONUS.get(current_level, 2)
    new_pb = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Spell changes
    spell_info = None
    caster_type = get_caster_type(cls)
    if caster_type != "none":
        try:
            old_slots = get_spell_slots(cls, current_level)
            new_slots = get_spell_slots(cls, target_level)
        except:
            old_slots = {}; new_slots = {}
        spell_info = {
            "caster_type": caster_type,
            "old_slots": old_slots, "new_slots": new_slots,
        }
    
    # HP estimate: subtract average per level rolled back
    levels_lost = current_level - target_level
    estimated_hp = max(1, char.get("hp_max", 10) - levels_lost * avg_hp)
    
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
    
    # Ability scores: user-specified or keep current (user should adjust manually)
    ability_updates = data.get("abilities", {})
    for a in ABILITY_NAMES:
        key = a.lower()
        if a in ability_updates:
            updates[key] = int(ability_updates[a])
            if updates[key] != char.get(key, 10):
                changes.append(f"{a}: {char.get(key, 10)} → {updates[key]}")
    
    # Subclass: keep if target >= subclass level, clear otherwise
    sc = SUBCLASS_LEVELS.get(cls)
    if sc and sc["level"] > target_level:
        updates["subclass"] = ""
        if char.get("subclass"):
            changes.append(f"Subclass cleared ({char.get('subclass')})")
    
    # Proficiency
    updates["proficiency_bonus"] = PROFICIENCY_BONUS.get(target_level, 2)
    
    # Features rebuild
    features_list = get_class_features(cls, target_level, updates.get("subclass", char.get("subclass", "")))
    updates["features"] = json.dumps(features_list)
    
    # Feature data rebuild
    final_mods = {}
    for a in ABILITY_NAMES:
        key = a.lower()
        val = updates.get(key, char.get(key, 10))
        final_mods[a] = (val - 10) // 2
    enriched = enrich_features(features_list, class_name=cls, level=target_level, mods=final_mods)
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
    # Barbarian — Path of the Totem Warrior
    "totem spirit":         {"min": 1, "max": 1,  "recharge": "long", "class": "Barbarian", "per": "fixed"},
    "aspect of the beast":  {"min": 1, "max": 1,  "recharge": "long", "class": "Barbarian", "per": "fixed"},
    "totemic attunement":   {"min": 1, "max": 1,  "recharge": "long", "class": "Barbarian", "per": "fixed"},
    # Fighter — Battle Master (PHB p.73-74)
    "combat superiority":   {"min": 4, "max": 6,  "recharge": "short", "class": "Fighter", "per": "fixed", "pool_kind": "dice"},
    # Cleric — Light Domain
    "warding flare":        {"min": 1, "max": 99,  "recharge": "long", "class": "Cleric", "per": "level"},
    "improved flare":        {"min": 1, "max": 99,  "recharge": "long", "class": "Cleric", "per": "level"},
    "corona of light":      {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    # Cleric — Nature Domain
    "dampen elements":      {"min": 1, "max": 1,  "recharge": "short", "class": "Cleric", "per": "fixed"},
    # Cleric — Tempest Domain
    "wrath of the storm":   {"min": 1, "max": 99,  "recharge": "long", "class": "Cleric", "per": "level"},
    "thunderbolt strike":   {"min": 1, "max": 99,  "recharge": "long", "class": "Cleric", "per": "level"},
    "stormborn":            {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    # Paladin — capstones
    "holy nimbus":          {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    "avenging angel":       {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    "elder champion":       {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    # Sorcerer — Draconic Bloodline
    "dragon wings":         {"min": 1, "max": 1,  "recharge": "long", "class": "Sorcerer", "per": "fixed"},
    "draconic presence":    {"min": 1, "max": 1,  "recharge": "long", "class": "Sorcerer", "per": "fixed"},
    # Sorcerer — Wild Magic
    "tides of chaos":       {"min": 1, "max": 1,  "recharge": "short", "class": "Sorcerer", "per": "fixed"},
    "bend luck":            {"min": 1, "max": 99,  "recharge": "long", "class": "Sorcerer", "per": "level"},
    "wild magic surge":     {"min": 1, "max": 99,  "recharge": "short", "class": "Sorcerer", "per": "fixed"},
    # Warlock — The Archfey
    "fey presence":         {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "misty escape":         {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "dark delirium":        {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    # Warlock — The Great Old One
    "entropic ward":        {"min": 1, "max": 1,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    "create thrall":        {"min": 1, "max": 1,  "recharge": "long", "class": "Warlock", "per": "fixed"},
    "awakened mind":        {"min": 1, "max": 99,  "recharge": "short", "class": "Warlock", "per": "fixed"},
    # Wizard — School of Divination
    "portent":              {"min": 2, "max": 3,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "greater portent":      {"min": 2, "max": 3,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "the third eye":        {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Wizard — misc
    "minor conjuration":    {"min": 1, "max": 99, "recharge": "long", "class": "Wizard", "per": "fixed"},
    "benign transposition": {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "hypnotic gaze":        {"min": 1, "max": 99, "recharge": "long", "class": "Wizard", "per": "fixed"},
    "instinctive charm":    {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "alter memories":       {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "improved minor illusion": {"min": 1, "max": 99, "recharge": "short", "class": "Wizard", "per": "fixed"},
    "illusory self":        {"min": 1, "max": 1,  "recharge": "short", "class": "Wizard", "per": "fixed"},
    "illusory reality":     {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "minor alchemy":        {"min": 1, "max": 99, "recharge": "long", "class": "Wizard", "per": "fixed"},
    "transmuter's stone":   {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    "master transmuter":    {"min": 1, "max": 1,  "recharge": "long", "class": "Wizard", "per": "fixed"},
    # Cleric — capstones
    "master of nature":     {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    "visions of the past":  {"min": 1, "max": 1,  "recharge": "short", "class": "Cleric", "per": "fixed"},
    "improved duplicity":   {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    "avatar of battle":     {"min": 1, "max": 1,  "recharge": "long", "class": "Cleric", "per": "fixed"},
    "blessing of the trickster": {"min": 1, "max": 99, "recharge": "long", "class": "Cleric", "per": "fixed"},
    # Paladin — Oathbreaker
    "dread lord":           {"min": 1, "max": 1,  "recharge": "long", "class": "Paladin", "per": "fixed"},
    # Arcane Trickster
    "spell thief":          {"min": 1, "max": 1,  "recharge": "long", "class": "Rogue", "per": "fixed"},
    "mage hand legerdemain": {"min": 1, "max": 99, "recharge": "short", "class": "Rogue", "per": "fixed"},
    "magical ambush":       {"min": 1, "max": 99, "recharge": "short", "class": "Rogue", "per": "fixed"},
    "versatile trickster":  {"min": 1, "max": 99, "recharge": "short", "class": "Rogue", "per": "fixed"},
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

def enrich_spells(spells: list[dict]) -> None:
    """Add full SRD spell data to each spell dict in-place."""
    if not SRD_SPELLS:
        return
    # Build lookup by lowercase name
    srd_lookup = {s.get("name", "").lower(): s for s in SRD_SPELLS}
    for sp in spells:
        name = sp.get("spell_name", "")
        srd = srd_lookup.get(name.lower())
        if srd:
            sp["srd"] = {
                "desc": srd.get("desc", []),
                "higher_level": srd.get("higher_level", []),
                "range": srd.get("range", ""),
                "components": srd.get("components", []),
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
    "A": {"rarity": ["common", "uncommon"], "category": ["potion", "scroll", "wand", "wondrous item"]},
    "B": {"rarity": ["uncommon", "rare"], "category": ["armor", "weapon", "wondrous item", "ring", "rod", "staff"]},
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
    """Pick one random magic item from the SRD pool matching the table."""
    import random
    pool_cfg = MAGIC_TABLE_POOLS.get(table, {})
    rarities = pool_cfg.get("rarity", [])
    categories = pool_cfg.get("category")
    # Filter SRD_MAGIC_ITEMS
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
    item = random.choice(candidates)
    rarity = (item.get("rarity", {}) or {}).get("name", "")
    desc = " ".join(item.get("desc", [])[:3])  # first 3 sentences
    return {
        "name": item.get("name", "Unknown"),
        "rarity": rarity,
        "description": desc[:200],
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


def enrich_features(feature_list: list[str], class_name: str = "", level: int = 0, mods: dict = None, class_levels: dict = None) -> list[dict]:
    """Add SRD descriptions to feature names, and track limited-use abilities.
    When class_levels dict provided, uses per-class levels for multiclass limited uses."""
    enriched = []
    for feat_str in feature_list:
        if ": " in feat_str:
            level_part, name = feat_str.split(": ", 1)
        else:
            level_part, name = feat_str, feat_str
        key = name.lower()
        desc = FEATURE_DESCRIPTIONS.get(key, "")
        # If composite name from multiclass dedup, try first segment
        if not desc and " | " in key:
            first_seg = key.split(" | ")[0].strip()
            desc = FEATURE_DESCRIPTIONS.get(first_seg, "")
        entry = {"name": name, "level": level_part, "description": desc}
        # Look up source from SRD feature data
        _src = next((f.get("source", "") for f in SRD_FEATURES if f.get("name", "").lower() == key), "")
        if _src:
            entry["source"] = _src
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
    Safe to call on already-enriched data — no-ops if source already present."""
    for feat in feature_data:
        if feat.get("source"):
            continue  # Already has source
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

    prompt = f"""Create a UNIQUE D&D 5e custom background for this character. Do NOT use any of the 13 standard PHB backgrounds (Acolyte, Charlatan, Criminal, Entertainer, Folk Hero, Guild Artisan, Hermit, Noble, Outlander, Sage, Sailor, Soldier, Urchin). Invent something new and specific to this character.

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
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods)

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
    # Natural armor (Tortle, Lizardfolk, etc.) overrides class-based AC
    if natural_armor:
        ac = natural_armor.get("base_ac", 17)
        # If armor allows DEX bonus
        max_dex = natural_armor.get("max_dex")
        if max_dex is not None:
            dex = mods["dexterity"]
            ac += min(dex, max_dex)
        # Shield bonus is handled separately by equipment system
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
    item = ITEM_INDEX.get(key)
    if not item:
        # Try partial match
        for k, v in ITEM_INDEX.items():
            if key in k:
                item = v
                break
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
    """List available reference manuals. No auth required."""
    if not MANUALS_BASE.exists():
        return JSONResponse({"manuals": [], "warning": "Manual directory not found"})
    import glob
    pdfs = sorted(glob.glob(str(MANUALS_BASE / "*.pdf")) + glob.glob(str(MANUALS_BASE / "*/*.pdf")))
    return JSONResponse({
        "count": len(pdfs),
        "manuals": [Path(p).name for p in pdfs],
        "path": str(MANUALS_BASE),
    })

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
        for slug, info in pdf_map.items():
            title = info.get("title", slug)
            display = re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
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
