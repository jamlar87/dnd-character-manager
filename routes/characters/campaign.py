"""Campaign team items routes."""

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


router = APIRouter()

# ── Treasure hoard data (DMG 2014 p.137-139) ──────────────────────────────────────

TREASURE_HOARD_COINS = {
    "0-4":   {"gp": "4d6*10"},       # avg 140 gp
    "5-10":  {"gp": "5d6*100"},      # avg 1750 gp
    "11-16": {"gp": "4d6*1000"},     # avg 14000 gp
    "17+":   {"gp": "8d6*1000"},     # avg 28000 gp
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
    mult = 1
    m = re.match(r"(.+?)\*(\d+)", expr)
    if m:
        expr = m.group(1)
        mult = int(m.group(2))
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
    rarity_order = ["common", "uncommon", "rare", "very rare", "legendary", "artifact"]
    if len(rarities) > 1 and "common" in rarities:
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
    desc = " ".join(item.get("desc", [])[:3])
    return {
        "name": item.get("name", "Unknown"),
        "rarity": rarity,
        "description": desc,
        "source": item.get("source", "") or "DMG 2014",
    }


def roll_treasure_hoard(cr_bracket: str) -> dict:
    """Roll a full treasure hoard per DMG 2014 p.137-139."""
    import random
    coins = TREASURE_HOARD_COINS.get(cr_bracket, {})
    table = TREASURE_HOARD_TABLE.get(cr_bracket, [])
    if not table:
        return {"coins": [], "gems": [], "magic_items": [], "total_gp_value": 0}
    result = {"coins": [], "gems": [], "magic_items": [], "total_gp_value": 0}
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
    d100 = random.randint(1, 100)
    for lo, hi, gems_expr, magic_table, magic_count_expr in table:
        if lo <= d100 <= hi:
            if gems_expr:
                dice, gp_per = gems_expr
                count = _roll_dice(dice)
                value = count * gp_per
                result["gems"].append({
                    "count": count,
                    "value_per": gp_per,
                    "total_value": value,
                    "label": f"{count} x {gp_per}gp {'gems' if gp_per < 100 else 'art objects'}",
                })
                result["total_gp_value"] += value
            if magic_table:
                item_count = _roll_dice(magic_count_expr)
                for _ in range(item_count):
                    mi = _pick_magic_item(magic_table)
                    if mi:
                        result["magic_items"].append(mi)
            break
    return result

# ── Routes: Campaign Team Items ─────────────────────────────────────────────

@router.get("/api/character/{char_id}/campaign", response_class=JSONResponse)
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


@router.get("/api/campaign/{camp_id}/team-items", response_class=JSONResponse)
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


@router.post("/api/campaign/{camp_id}/team-items", response_class=JSONResponse)
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


@router.post("/api/campaign/{camp_id}/team-items/{item_id}/claim", response_class=JSONResponse)
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
    currency_match = _re.match(r'(\d[\d,]*)\s*(cp|sp|ep|gp|pp)', item_name.lower().replace(',', ''))
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


@router.post("/api/character/{char_id}/share-to-team", response_class=JSONResponse)
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


@router.put("/api/campaign/{camp_id}/team-items/{item_id}", response_class=JSONResponse)
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


@router.delete("/api/campaign/{camp_id}/team-items/{item_id}", response_class=JSONResponse)
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


@router.post("/api/campaign/{camp_id}/roll-loot", response_class=JSONResponse)
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

