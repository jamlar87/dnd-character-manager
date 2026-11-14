"""Read-only monster and spell reference endpoints for the DM Tools palette."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from core.security import require_user
from routes.characters import (
    _load_monster_cache, _monster_cr_sort_key, parse_source_filter, source_matches,
)
from core.data import SRD_SPELLS
from services.rules import ability_mod

router = APIRouter()


@router.get("/api/dm/monster/{index}", response_class=JSONResponse)
def dm_monster_detail(index: str, request: Request):
    """Full monster detail from SRD cache."""
    require_user(request)
    all_monsters = _load_monster_cache()
    for m in all_monsters:
        if m.get("index", "").lower() == index.lower():
            return JSONResponse(_enrich_monster(m))
    raise HTTPException(status_code=404, detail="Monster not found")

@router.get("/api/dm/spell/{index}", response_class=JSONResponse)
def dm_spell_detail(index: str, request: Request):
    """Full spell detail from the merged SRD + manual spell registry."""
    require_user(request)
    for s in SRD_SPELLS:
        if s.get("index", "").lower() == index.lower():
            return JSONResponse(_enrich_spell(s))
    raise HTTPException(status_code=404, detail="Spell not found")

def _enrich_spell(s: dict) -> dict:
    """Enrich spell data for the detail modal: readable school/classes/level label."""
    import copy
    s = copy.deepcopy(s)

    # Normalize school to a readable name
    school = s.get("school") or {}
    if isinstance(school, dict):
        s["school_name"] = school.get("name") or school.get("index", "").title() or "Unknown"
    elif isinstance(school, str):
        s["school_name"] = school.title()
    else:
        s["school_name"] = "Unknown"

    # Normalize classes to readable names
    cls = s.get("classes") or []
    s["class_names"] = []
    for c in cls:
        if isinstance(c, dict):
            s["class_names"].append(c.get("name") or c.get("index", "").title())
        elif isinstance(c, str):
            s["class_names"].append(c.title())
    s["class_names"] = sorted(set(s["class_names"]))

    # Normalize subclasses
    sub = s.get("subclasses") or []
    s["subclass_names"] = []
    for c in sub:
        if isinstance(c, dict):
            s["subclass_names"].append(c.get("name") or c.get("index", "").title())
        elif isinstance(c, str):
            s["subclass_names"].append(c.title())
    s["subclass_names"] = sorted(set(s["subclass_names"]))

    # Level label (Cantrip vs Nth level)
    lvl = s.get("level", 0)
    s["level_label"] = "Cantrip" if lvl == 0 else f"{lvl}"

    # Components — strip material parens for the badge, keep raw
    s["components_raw"] = s.get("components", [])

    # Damage summary for the header strip
    dmg = s.get("damage") or {}
    if isinstance(dmg, dict):
        dt = dmg.get("damage_type")
        s["damage_summary"] = dt.get("name", "") if isinstance(dt, dict) else (dt if isinstance(dt, str) else "")
    else:
        s["damage_summary"] = ""

    # DC info
    dc = s.get("dc") or {}
    if isinstance(dc, dict):
        dct = dc.get("dc_type")
        s["dc_summary"] = dct.get("name", "") if isinstance(dct, dict) else (dct if isinstance(dct, str) else "")
    else:
        s["dc_summary"] = ""

    # Area of effect
    aoe = s.get("area_of_effect") or {}
    if isinstance(aoe, dict):
        at = aoe.get("type", "")
        s["aoe_summary"] = f"{aoe.get('size', '')} ft. {at}" if aoe.get("size") else at
    else:
        s["aoe_summary"] = ""

    # Attack type
    s["attack_summary"] = (s.get("attack_type") or "").replace("_", " ").title()

    return s

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
                save_bonus = ability_mod(stat_val)
                m["proficiencies"].append({
                    "proficiency": {"name": f"Saving Throw: {save_name}"},
                    "value": save_bonus
                })
    
    # Compute skills from manual data
    raw_skills = m.get("skills", {})
    if isinstance(raw_skills, dict) and raw_skills:
        m.setdefault("proficiencies", [])
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

@router.get("/api/dm/monsters", response_class=JSONResponse)
def dm_monster_list(request: Request, source: str = ""):
    """List monsters, optionally scoped to books (`source=SLUG[,SLUG]`)."""
    require_user(request)
    all_monsters = _load_monster_cache()
    src_slugs = parse_source_filter(source)
    if src_slugs:
        all_monsters = [m for m in all_monsters if source_matches(m.get("source", ""), src_slugs)]
    return JSONResponse({"count": len(all_monsters), "monsters": all_monsters})

@router.get("/api/dm/monsters/search", response_class=JSONResponse)
def dm_monster_search(request: Request, q: str = "", type: str = "", cr_min: float = 0, cr_max: float = 30, cr: str = "", source: str = ""):
    """Search/filter monsters by name, type, CR range and book source."""
    require_user(request)
    all_monsters = _load_monster_cache()
    src_slugs = parse_source_filter(source)
    if src_slugs:
        all_monsters = [m for m in all_monsters if source_matches(m.get("source", ""), src_slugs)]
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

@router.get("/api/dm/monsters/by-cr", response_class=JSONResponse)
def dm_monsters_by_cr(request: Request, source: str = ""):
    """Grouped monsters by CR tier for encounter building (`source=` scopes books)."""
    require_user(request)
    all_monsters = _load_monster_cache()
    src_slugs = parse_source_filter(source)
    if src_slugs:
        all_monsters = [m for m in all_monsters if source_matches(m.get("source", ""), src_slugs)]
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
