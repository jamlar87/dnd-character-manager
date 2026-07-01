"""Shared helper functions — monster cache, OCR search, encounter building."""

"""Character routes — create, sheet, level-up, spells, combat, relationships."""

from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import sqlite3, json, math, random, re, urllib.parse, os, httpx
from pathlib import Path
from datetime import datetime

from main import (
    get_db, get_current_user, require_user, _render,
    _user_where, _require_owned, _resolve_item_key, _resolve_armor_item,
    _resolve_source, _build_item_description, _build_charged_item_attacks,
    _build_inventory_attacks, _build_racial_traits, _normalize_equipped,
    _equipped_names, _load_json_cache, _parse_enhancement, _is_admin,
    _item_rarity_for_level, _user_filter,
)
from main import RACES, CLASSES, RACE_NAMES, SUBCLASS_FEATURES, LIMITED_USE
from main import BACKGROUNDS, BACKGROUND_SOURCES, ALIGNMENTS
from main import (
    FLEXIBLE_ASI_RACES, SUBASIS, SRD_FEATURES, SRD_MAGIC_ITEMS,
    ITEM_INDEX, ITEM_WEAPONS, ITEM_ARMOR, ITEM_WONDROUS, ITEM_RODS_STAVES_WANDS,
    ITEMS_BY_RARITY, SPELL_DICE, DATA_DIR,
)
from main import _load_manual_json
from main import get_racial_trait_effects, check_armor_proficiency_from_set, get_character_armor_profs
from main import load_manual_data
from main import SRD_LEVELS, SRD_SPELLS, _get_named_item_types, _get_source_slug_map
from main import _manual_races_raw, _manual_races_raw as _MANUAL_RACES_RAW
from data import (
    SPELLS_KNOWN_CASTERS, RACIAL_TRAIT_EFFECTS, FEATURE_ACTION_TYPES,
    ABILITY_NAMES, ALL_SKILLS, LANGUAGES, SKILL_ABILITIES, FEATS, FEAT_BY_NAME,
    FEATURE_DESCRIPTIONS, DRACONIC_ANCESTRIES, PREPARED_CASTERS,
    METAMAGIC_OPTIONS, METAMAGIC_LEVELS, METAMAGIC_PICKS,
    INVOCATION_OPTIONS, INVOCATION_LEVELS, INVOCATION_PICKS,
    PACT_BOON_OPTIONS, PACT_BOON_LEVELS,
    MANEUVER_OPTIONS, MANEUVER_LEVELS,
    TOTEM_SPIRIT_OPTIONS,
    HUNTERS_PREY_OPTIONS,
    FAVORED_ENEMY_OPTIONS,
    FAVORED_TERRAIN_OPTIONS,
    INFUSION_OPTIONS,
    SUBCLASS_LEVELS, EXPERTISE_LEVELS, STARTING_EQUIPMENT,
    SCALED_EQUIPMENT, RECOMMENDED_FEATS, MULTICLASS_PREREQS,
    MULTICLASS_PROFICIENCIES, BACKGROUND_INFO,
    ASI_LEVELS, FULL_CASTERS, HALF_CASTERS, PACT_CASTERS,
    RACIAL_TRAIT_DESCS,
)
from routes.schemas import CreateCharacter, AddSpell, EditASI, ApplyLevelUp, UpdateCharacter
from summon_templates import SUMMON_TEMPLATES

MANUAL_MONSTERS: list[dict] = []
MANUAL_TRAPS: list[dict] = []

def _load_monster_cache() -> list[dict]:
    global MANUAL_MONSTERS
    base = _load_json_cache("monsters.json")
    # Load monster→page map for source badges
    _monster_page_map: dict[str, int] = {}
    try:
        _mpm_path = DATA_DIR / "page_maps" / "monster_page_map.json"
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
    # Append summon-template-derived monsters (vehicles, siege, class summons, Tasha)
    return base + MANUAL_MONSTERS + _template_monster_entries()


def _template_monster_entries() -> list[dict]:
    """Convert summon templates (no monster_index) into monster-list entries."""
    from summon_templates import SUMMON_TEMPLATES
    entries = []
    for key, t in SUMMON_TEMPLATES.items():
        if t.get("monster_index"):
            continue  # already in SRD/manual cache
        name = t["name"]
        idx = "summon-" + key
        ac_val = t.get("ac_base", t.get("ac", 10))
        hp_val = t.get("hp_base", t.get("hp_max", t.get("hp_base", 20)))
        stats = t.get("stats", {})
        features = t.get("features", [])
        descs = t.get("feature_descs", {})
        attacks = t.get("attacks", [])
        speed_val = t.get("speed", "30 ft.")

        # Build speed dict from string
        speed_dict = {}
        for part in speed_val.split(","):
            part = part.strip()
            if "fly" in part:
                speed_dict["fly"] = part
            elif "swim" in part:
                speed_dict["swim"] = part
            elif "climb" in part:
                speed_dict["climb"] = part
            elif "burrow" in part:
                speed_dict["burrow"] = part
            else:
                speed_dict["walk"] = part

        # Determine type
        cat = t.get("category", "")
        if cat == "vehicle":
            mtype = "construct"
        elif cat == "siege":
            mtype = "construct"
        elif cat == "tashas_summon":
            mtype = "construct"  # summoned spirits
        elif cat == "class_feature":
            mtype = "construct"
        else:
            mtype = "beast"

        # CR estimate: ~1 per 20 HP for class features, 0 for vehicles/siege
        if cat in ("vehicle", "siege"):
            cr = 0
        elif hp_val >= 300:
            cr = 10
        elif hp_val >= 150:
            cr = 7
        elif hp_val >= 80:
            cr = 4
        elif hp_val >= 40:
            cr = 2
        else:
            cr = max(0.5, hp_val / 20)

        entry = {
            "index": idx,
            "name": name,
            "size": t.get("size", "Medium"),
            "type": mtype,
            "alignment": "unaligned",
            "armor_class": [{"type": "natural" if cat in ("vehicle","siege") else "dex", "value": ac_val}],
            "hit_points": hp_val,
            "hit_dice": "",
            "speed": speed_dict,
            "strength": stats.get("str", 10),
            "dexterity": stats.get("dex", 10),
            "constitution": stats.get("con", 10),
            "intelligence": stats.get("int", 0),
            "wisdom": stats.get("wis", 0),
            "charisma": stats.get("cha", 0),
            "challenge_rating": cr,
            "xp": {0: 0, 0.5: 100, 1: 200, 2: 450, 4: 1100, 7: 2900, 10: 5900}.get(cr, int(cr * 200)),
            "proficiency_bonus": max(2, 2 + int((cr - 1) / 4)),
            "special_abilities": [
                {"name": f, "desc": descs.get(f, "")}
                for f in features
            ],
            "actions": [
                {
                    "name": a.get("name", "Strike"),
                    "desc": f"{a.get('damage_base','?')} damage.",
                    "attack_bonus": a.get("atk_bonus_base", 4),
                    "damage": [{"damage_type": {"name": "bludgeoning"}, "damage_dice": a.get("damage_base", "1d6")}]
                }
                for a in attacks
            ] if attacks else [],
            "source": t.get("source", "Summon"),
            "tags": [cat],
        }
        entries.append(entry)
    return entries


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
    AI picks which monsters and suggests counts; this function fine-tunes.
    First pass: apply AI's suggested counts. Second pass: pad/trim to hit ≥85% budget.
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

    # ── First pass: apply AI's suggested counts ──
    for m in sorted_picks:
        count = m.get("_suggested_count", 0) or 1
        # Cap suggested count to avoid blowing the budget or exceeding MAX
        count = min(count, MAX_CREATURES - _total())
        if count < 1:
            count = 1
        existing = next((c for c in composition if c["index"] == m["index"]), None)
        if existing:
            existing["count"] += count
        else:
            composition.append({**m, "count": count})
        raw_xp += m["xp"] * count
        if _total() >= MAX_CREATURES:
            break

    # Solo lair: boss + guards — suggested counts work as-is
    if encounter_type == "solo_lair":
        return composition, raw_xp

    total_count = _total()
    mult = _encounter_mult(total_count) if total_count > 0 else 1.0
    adjusted = int(raw_xp * mult) if total_count > 0 else 0

    # ── Second pass: pad if under budget ──
    # Build pool of eligible minion/elite picks for padding
    pad_pool = [m for m in sorted_picks if m.get("role") not in ("boss",) or len(sorted_picks) == 1]
    for _pass in range(5):
        total_count = _total()
        if total_count >= MAX_CREATURES:
            break
        if xp_budget > 0 and adjusted / xp_budget >= 0.85:
            break
        if not pad_pool:
            break
        # Pick cheapest creature to pad with
        cheapest = min(pad_pool, key=lambda m: m["xp"])
        extra = 1
        existing = next((c for c in composition if c["index"] == cheapest["index"]), None)
        if existing:
            existing["count"] += extra
        else:
            composition.append({**cheapest, "count": extra})
        raw_xp += cheapest["xp"] * extra
        total_count = _total()
        mult = _encounter_mult(total_count)
        adjusted = int(raw_xp * mult)

    # ── Third pass: trim if extremely over budget (>200%) — keep all AI-chosen types ──
    if xp_budget > 0 and adjusted > xp_budget * 2.0:
        for _ in range(50):
            if not composition:
                break
            total_count = _total()
            total_types = len(composition)
            if total_count <= total_types:
                # Keep at least 1 of each type (don't remove unique types)
                break
            # Reduce count of the cheapest non-boss with duplicates
            non_boss = [c for c in composition if c.get("role") != "boss" and c["count"] > 1]
            if not non_boss:
                break
            cheapest = min(non_boss, key=lambda c: c["xp"])
            cheapest["count"] -= 1
            raw_xp -= cheapest["xp"]
            total_count = _total()
            mult = _encounter_mult(total_count) if total_count > 0 else 1
            adjusted = int(raw_xp * mult) if total_count > 0 else 0
            if adjusted <= xp_budget * 1.50:
                break

    print(f"[Counts] final: {[(c['name'], c['count']) for c in composition]} raw={raw_xp} adj={adjusted} budget={xp_budget}")
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

MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals").resolve()
MANUAL_CACHE = DATA_DIR / "manual_cache"


def _ensure_manual_cache() -> dict[str, Path]:
    """Ensure all manual PDFs have been extracted to text cache.
    Returns {book_label: path_to_txt}.
    Uses _meta.json pdf_map to discover all ingested manuals.
    If a PDF is missing but a cached .txt extract exists, that
    cached extract is used — enabling rebuilds without source PDFs.
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
        elif txt_path.exists():
            # PDF not available but cached extract is — use it
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
    Common confusions: l↔t (lhe/the), I↔l (aI/at), i↔l, rn↔m, cl↔d.
    Also normalizes apostrophe types (curly ↔ straight) for PDF extracts.
    """
    variants = {word}
    # Normalize any apostrophe type to straight apostrophe variant
    if "'" in word or "\u2018" in word or "\u2019" in word:
        straight = word.replace("\u2018", "'").replace("\u2019", "'")
        if straight != word:
            variants.add(straight)
        # Also add curly variants if the word uses straight
        if "'" in word:
            variants.add(word.replace("'", "\u2019"))
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
                # Escape regex special chars so OCR variants are literal in rg
                esc_rep = rep.replace("|", "\\|").replace(".", "\\.")
                variants.add(word.replace(orig, esc_rep, 1))
    # Also try common multi-char: "the"<>"lhe", "th"<>"lh"
    if "th" in word:
        variants.add(word.replace("th", "lh"))
        variants.add(word.replace("th", "tn"))
    return list(variants)


def _search_json_data(query: str, words: list[str], max_results: int = 20) -> list[dict]:
    """Search structured JSON data in manual_data/ for query words.

    Returns same format as _search_manuals: [{book, snippet, line, page, score}].
    Searches feats.json, spells.json, magic_items.json, equipment.json,
    races.json, backgrounds.json, subclasses.json, monsters.json.
    """
    import re

    results = []

    # Map JSON file → display label
    json_sources = [
        ("feats.json", "Manual Feats"),
        ("spells.json", "Manual Spells"),
        ("magic_items.json", "Magic Items"),
        ("equipment.json", "Equipment"),
        ("races.json", "Races"),
        ("backgrounds.json", "Backgrounds"),
        ("subclasses.json", "Subclasses"),
        ("monsters.json", "Monsters"),
    ]

    for filename, label in json_sources:
        path = DATA_DIR / "manual_data" / filename
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, list):
            continue

        for item in data:
            name = str(item.get("name", "") or "")
            desc = str(item.get("description", "") or item.get("desc", "") or "")
            source = str(item.get("source", "") or "")
            text = f"{name} {desc}"
            text_norm = text.lower()
            text_norm = text_norm.replace("\u2018", "'").replace("\u2019", "'")
            name_lower = name.lower()

            # All query words must appear somewhere
            if not all(w in text_norm for w in words):
                continue

            # Score: name hits worth more, text hits worth less
            score = 0.0
            if query.lower() in text_norm:
                score += 5.0
            for w in words:
                if w in name_lower:
                    score += 3.0
                elif w in text_norm:
                    score += 1.0

            # Book label: use source field if available, else filename stem
            src_label = filename.replace(".json", "")
            if source:
                # Extract short book code if source looks like "Chapter X: ..."
                m = re.match(r"^(.+?)\s", source)
                if m:
                    src_label = m.group(1)[:8]

            snippet = desc[:400] if desc else name
            results.append({
                "book": src_label,
                "book_name": label,
                "snippet": f"[{name}] {snippet}",
                "line": 0,
                "page": 0,
                "score": score,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def _search_data_py_feats(query: str, words: list[str], max_results: int = 20) -> list[dict]:
    """Search the FEATS dict from data.py — covers non-SRD feats like
    Gunner that were added manually and aren't in manual_data/feats.json."""
    results = []
    from data import FEATS, FEAT_BY_NAME

    text_norm = query.lower().replace("\u2018", "'").replace("\u2019", "'")

    # Search by name (FEAT_BY_NAME key is lowercase)
    for feat_key, feat_info in FEAT_BY_NAME.items():
        name = feat_key.title()
        desc = feat_info.get("description") or feat_info.get("desc") or ""
        text = f"{name} {desc}".lower()
        text = text.replace("\u2018", "'").replace("\u2019", "'")

        if not all(w in text for w in words):
            continue

        score = 0.0
        if text_norm in text:
            score += 5.0
        for w in words:
            if w in feat_key:
                score += 3.0
            elif w in text:
                score += 1.0

        results.append({
            "book": "ManualFeats",
            "book_name": "Manual Feats (data.py)",
            "snippet": f"[{name}] {desc[:400]}",
            "line": 0,
            "page": 0,
            "score": score,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def _search_manuals(query: str, max_results: int = 20) -> list[dict]:
    """Search all cached manual text files AND structured JSON data
    (manual_data/*.json) with multi-word AND, relevance scoring, source
    priority, paragraph context, and OCR-tolerant fuzzy matching.

    Returns [{book, snippet, line, page, score}].
    Uses a single batch rg call per word across all files instead of
    72 separate subprocess calls — ~40× faster on cold cache.
    """
    import subprocess, re, collections

    cached = _ensure_manual_cache()
    words = [w.strip().lower() for w in query.split() if w.strip()]
    if not words:
        return []

    # ── Step 0: Search structured JSON data (manual_data/) ────────────────
    json_results = _search_json_data(query, words, max_results)
    # Also search data.py FEATS dict (covers non-SRD feats like Gunner)
    json_results += _search_data_py_feats(query, words, max_results)
    # Re-sort merged JSON results
    json_results.sort(key=lambda r: r["score"], reverse=True)
    json_results = json_results[:max_results]

    if not cached:
        return json_results[:max_results]

    SOURCE_WEIGHT = {
        "PHB": 1.00, "DMG": 0.95, "MM": 0.90, "XGE": 0.85,
        "VGM": 0.75, "MTF": 0.75, "SCAG": 0.70, "EEPC": 0.65,
        "GGR": 0.60, "WGE": 0.60, "TTP": 0.55, "EBT": 0.55,
        "CC": 0.50, "AIPG": 0.50, "LMG": 0.50, "KW": 0.45,
        "BLRG": 0.45, "RRG": 0.45, "RVR": 0.45, "LMRG": 0.45,
        "EREA": 0.45, "ERIA": 0.45, "MWC": 0.45, "WLA": 0.45,
        "RGEO": 0.45,
    }
    _book_names = _get_source_slug_map()

    PROXIMITY_WINDOW = 5
    CONTEXT_MARGIN = 3
    FRONT_MATTER_SKIP = 100
    all_scored = []
    total_raw_hits = 0

    # ── Build reverse path→label map ──────────────────────────────────────
    path_to_label = {str(p): l for l, p in cached.items()}
    all_txt_paths = sorted(path_to_label.keys())

    # ── Step 1: batch rg — one call per word across ALL files ─────────────
    # rg --no-heading output format: /path/file.txt:123:content
    word_line_map: dict[str, dict[str, set[int]]] = {}
    # word_line_map[word][label] = {line_numbers}

    for word in words:
        patterns = _fuzzy_variants(word)
        cmd = ["rg", "-i", "-n", "--no-heading", "--color", "never"]
        for p in patterns:
            cmd.extend(["-e", p])
        cmd.extend(all_txt_paths)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            proc = subprocess.CompletedProcess(cmd, -1, "", "")

        per_label: dict[str, set[int]] = {l: set() for l in cached}
        if proc.returncode == 0 and proc.stdout.strip():
            for line in proc.stdout.strip().split("\n"):
                # Format: /path/file.txt:123:content
                m = re.match(r"^(.+?):(\d+):", line)
                if m:
                    fpath, ln_str = m.group(1), m.group(2)
                    ln = int(ln_str)
                    if ln <= FRONT_MATTER_SKIP:
                        continue
                    label = path_to_label.get(fpath)
                    if label:
                        per_label[label].add(ln)
                        total_raw_hits += 1
        word_line_map[word] = per_label

    # ── Step 2: per-book AND scoring ─────────────────────────────────────
    # Pre-read each book into memory for fast snippet extraction
    book_text: dict[str, list[str]] = {}
    for label, p in cached.items():
        try:
            with open(p) as f:
                book_text[label] = f.readlines()
        except Exception:
            book_text[label] = []

    for label in cached:
        source_w = SOURCE_WEIGHT.get(label, 0.50)
        lines = book_text.get(label, [])

        # Gather each word's line numbers for this book
        word_lines: dict[str, set[int]] = {}
        for word in words:
            s = word_line_map[word].get(label, set())
            if not s:
                break
            word_lines[word] = s
        else:
            pass  # all words have matches — proceed
        if not word_lines or any(len(ls) == 0 for ls in word_lines.values()):
            continue

        all_line_nums = sorted(set().union(*word_lines.values()))
        # ── cluster by proximity ──
        clusters: list[list[int]] = []
        cur = [all_line_nums[0]]
        for ln in all_line_nums[1:]:
            if ln - cur[-1] <= PROXIMITY_WINDOW:
                cur.append(ln)
            else:
                clusters.append(cur)
                cur = [ln]
        clusters.append(cur)

        book_results: list[dict] = []
        for cluster in clusters:
            presence = {}
            for w, wlines in word_lines.items():
                in_cluster = [l for l in cluster if l in wlines]
                if not in_cluster:
                    break
                presence[w] = in_cluster
            else:
                match_lines = sorted(set().union(*presence.values()))
                cluster_span = max(match_lines) - min(match_lines)
                prox = 1.0 if len(match_lines) <= 1 else max(0.1, 1.0 - (cluster_span / (PROXIMITY_WINDOW * 3)))
                density = min(1.0, len(match_lines) / max(1, len(words) * 2))
                exact = 0.0
                try:
                    start = max(0, match_lines[0] - 3)
                    end = match_lines[-1] + 3
                    region_text = " ".join(lines[start - 1:end]).lower()
                    # Normalize apostrophes so curly quotes from PDF extraction
                    # don't prevent exact-match bonus against user's straight apostrophe
                    region_text = region_text.replace("\u2018", "'").replace("\u2019", "'")
                    if query.lower() in region_text:
                        exact = 0.35
                except Exception:
                    pass
                raw = (prox * 0.35 + density * 0.25 + exact * 0.30 + source_w * 0.10)
                score = round(raw * 10, 2)

                snippet_start = max(1, match_lines[0] - CONTEXT_MARGIN)
                snippet_end = match_lines[-1] + CONTEXT_MARGIN
                try:
                    snippet = " ".join(l.strip() for l in lines[snippet_start - 1:snippet_end])[:400]
                except Exception:
                    snippet = ""

                est_page = max(1, match_lines[0] // 45)
                book_name = _book_names.get(label, {}).get("display", label) if _book_names else label
                book_results.append({
                    "book": label, "book_name": book_name,
                    "snippet": snippet, "line": match_lines[0],
                    "page": est_page, "score": score,
                    "_prox": prox, "_density": density, "_exact": exact,
                })

        # deduplicate within book
        seen_snippets = set()
        for r in sorted(book_results, key=lambda x: x["score"], reverse=True):
            key = r["snippet"][:80].strip().lower()
            if key and key not in seen_snippets:
                seen_snippets.add(key)
                all_scored.append(r)

    # ── Fallback: OR-only when AND yields < 3 results ─────────────────────
    if len(all_scored) < 3 and len(words) > 1:
        # Merge all per-word line sets (any word = match)
        merged_per_label: dict[str, set[int]] = {l: set() for l in cached}
        for word in words:
            for label, lines_set in word_line_map[word].items():
                merged_per_label[label].update(lines_set)

        for label, all_lines in merged_per_label.items():
            if not all_lines:
                continue
            source_w = SOURCE_WEIGHT.get(label, 0.50)
            lines = book_text.get(label, [])
            sorted_lines = sorted(all_lines)
            clusters = []
            cur = [sorted_lines[0]]
            for ln in sorted_lines[1:]:
                if ln - cur[-1] <= PROXIMITY_WINDOW * 3:
                    cur.append(ln)
                else:
                    clusters.append(cur)
                    cur = [ln]
            clusters.append(cur)

            for cluster in clusters[:3]:
                match_lines = sorted(cluster)
                span = match_lines[-1] - match_lines[0]
                prox = 1.0 if len(match_lines) <= 1 else max(0.1, 1.0 - (span / (PROXIMITY_WINDOW * 5)))
                density = min(1.0, len(match_lines) / max(1, len(words) * 2))
                raw = (prox * 0.35 + density * 0.25 + 0.0 * 0.30 + source_w * 0.10)
                score = round(raw * 10, 2)
                try:
                    start = max(1, match_lines[0] - CONTEXT_MARGIN)
                    end = match_lines[-1] + CONTEXT_MARGIN
                    snippet = " ".join(l.strip() for l in lines[start - 1:end])[:400]
                except Exception:
                    snippet = ""
                book_name = _book_names.get(label, {}).get("display", label) if _book_names else label
                all_scored.append({
                    "book": label, "book_name": book_name,
                    "snippet": snippet, "line": match_lines[0],
                    "page": max(1, match_lines[0] // 45), "score": score,
                })

    # ── Merge JSON data results ────────────────────────────────────────
    if json_results:
        # Filter out duplicates by snippet key
        existing_snippets = {r["snippet"][:80].strip().lower() for r in all_scored}
        for jr in json_results:
            key = jr["snippet"][:80].strip().lower()
            if key not in existing_snippets:
                existing_snippets.add(key)
                all_scored.append(jr)

    # ── Final sort, dedup, cap ─────────────────────────────────────────

    all_scored.sort(key=lambda r: r["score"], reverse=True)

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

