"""Encounters: AI building, copy, membership, initiative and combat state."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
import json
import random
from core.security import _require_owned, require_user
from core.db import get_db
from routes.characters import (
    _assign_encounter_counts, _extract_json, _load_monster_cache, _xp_for_cr,
)
from core.features import get_caster_type, get_spell_slots
from services.rules import ability_mod
from services.schema import hydrate_json_columns
from ._shared import call_ai, user_where

router = APIRouter()


# Why the explicit list: portrait_url holds base64 JPEG/PNG for every character (up to ~3MB per
# row here). _build_party_profile feeds an AI encounter builder, and the profile it returns never
# includes the portrait — so selecting `c.*` shipped megabytes of discarded text on every call.
# Keep this list in sync with the keys the profile builder reads below.
PARTY_PROFILE_COLS = """
    id, name, race, class_name, subclass, level, ac, hp_current, hp_max,
    strength, dexterity, constitution, intelligence, wisdom, charisma,
    save_proficiencies, proficiency_bonus, damage_resistances, damage_immunities,
    damage_vulnerabilities, condition_immunities, skills, features, equipped,
    attuned_items, asi_history
"""

# Same list qualified with the `c` alias: `_build_party_profile` joins dm_campaign_characters, and
# an unqualified `id` there is ambiguous (both tables have one) — sqlite answers with an error.
PARTY_PROFILE_COLS_C = ", ".join("c." + col.strip() for col in PARTY_PROFILE_COLS.split(","))

def _build_party_profile(db, campaign_id: int) -> dict | None:
    """Build a combat profile of all characters in a campaign for AI counter-play.
    Returns {characters: [...], summary: {...}} or None if no characters."""
    rows = db.execute(f"""
        SELECT {PARTY_PROFILE_COLS_C} FROM characters c
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
                        f"SELECT {PARTY_PROFILE_COLS_C} FROM characters c "
                        f"WHERE c.id IN ({placeholders})", char_ids
                    ).fetchall()
            except (json.JSONDecodeError, KeyError):
                pass

    if not rows:
        return None

    # One lookup for every member's prepared spells, keyed by character id —
    # this used to run one SELECT inside the per-member loop (N+1).
    spells_by_char: dict[int, list[str]] = {}
    _member_ids = [r["id"] for r in rows]
    if _member_ids:
        for sr in db.execute(
            "SELECT character_id, spell_name FROM character_spells "
            "WHERE prepared=1 AND character_id IN ({}) ORDER BY rowid".format(
                ",".join("?" * len(_member_ids))),
            _member_ids,
        ).fetchall():
            spells_by_char.setdefault(sr["character_id"], []).append(sr["spell_name"])

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
        hydrate_json_columns(c)

        ac = c.get("ac", 10)
        hp = c.get("hp_current", c.get("hp_max", 10)) or c.get("hp_max", 10)
        lvl = c.get("level", 1)
        total_level += lvl

        scores = {
            "STR": c.get("strength", 10), "DEX": c.get("dexterity", 10),
            "CON": c.get("constitution", 10), "INT": c.get("intelligence", 10),
            "WIS": c.get("wisdom", 10), "CHA": c.get("charisma", 10),
        }
        mods = {k: ability_mod(v) for k, v in scores.items()}

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
        spell_names = spells_by_char.get(c["id"], [])

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

@router.get("/api/dm/ai/party-profile", response_class=JSONResponse)
def dm_ai_party_profile(request: Request):
    """Return a combat profile summary of all characters in a campaign.
    Used by the AI Encounter Builder frontend to show party preview."""
    user = require_user(request)
    cid = request.query_params.get("campaign_id", "")
    if not cid:
        return JSONResponse({"profile": None})
    db = get_db()
    try:
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            return JSONResponse({"profile": None, "error": "campaign_id must be a number"},
                                status_code=400)
        # Ownership, not just authentication. require_user() only proves SOMEONE is logged in, and
        # this read any campaign_id handed to it: any user could walk ids and pull another DM's
        # whole party — names, AC/HP, saves, spells and features, serialised in the response.
        # Every other campaign route scopes by user (_user_where / _require_owned).
        camp = _require_owned(db, user, "dm_campaigns", cid_int, id_col="id")
        if not camp:
            return JSONResponse({"profile": None, "error": "campaign not found"}, status_code=404)
        profile = _build_party_profile(db, cid_int)
        fallback = {"party_level": camp["party_level"] or 1, "party_size": camp["party_size"] or 4}
        return JSONResponse({"profile": profile, "campaign": fallback})
    finally:
        db.close()

# ── Where a monster belongs, derived from its own stat block ──────────────────────────────
# The environment parameter did nothing: the filter's main condition short-circuited to True (see
# the call site) and no monster carries a habitat field — measured, 0 of 2201. But every monster
# carries a type, a speed block and damage immunities, and those say a great deal: a `swim` speed
# means water (Aboleth, black dragons — 64 monsters), `burrow` means earth and sand (blue dragons,
# 22), cold immunity means somewhere cold (yetis, white dragons). Derived from the whole library,
# so a monster added tomorrow is placed without anyone editing a list.
_HABITAT_TYPE = {
    "aberration": {"underdark", "dungeon", "swamp"},
    "beast": {"forest", "grassland", "mountain", "arctic", "desert", "coastal", "swamp"},
    "celestial": {"planar", "mountain"},
    "construct": {"dungeon", "urban"},
    "dragon": {"mountain", "coastal", "forest", "swamp", "arctic", "desert", "underdark"},
    "elemental": {"planar", "mountain", "coastal", "desert"},
    "fey": {"forest", "grassland", "swamp"},
    "fiend": {"planar", "underdark", "urban"},
    "giant": {"mountain", "grassland", "arctic"},
    "humanoid": {"urban", "dungeon", "grassland", "mountain", "forest", "arctic", "desert",
                 "swamp", "coastal"},
    "monstrosity": {"underdark", "dungeon", "forest", "mountain", "swamp", "desert", "coastal",
                    "grassland"},
    "ooze": {"underdark", "dungeon", "swamp"},
    "plant": {"forest", "swamp", "grassland"},
    "undead": {"underdark", "dungeon", "urban", "swamp", "forest", "arctic", "desert", "grassland"},
    "swarm": {"forest", "swamp", "dungeon", "grassland", "urban"},
}

_HABITAT_SUBTYPE = {
    "demon": {"planar", "underdark"},
    "devil": {"planar", "underdark", "urban"},
    "fiend": {"planar", "underdark"},
    "yugoloth": {"planar", "underdark"},
    "goblinoid": {"forest", "mountain", "grassland", "underdark"},
    "gnoll": {"grassland", "desert"},
    "kobold": {"dungeon", "mountain", "forest"},
    "shapechanger": {"urban", "forest"},
    "tit": {"mountain", "planar"},
}

_HABITAT_MARKERS = {
    "arctic": ("frost", "ice", "snow", "winter", "glacial", "yeti", "rime"),
    "coastal": ("sea", "ocean", "coral", "reef", "tide", "sahuagin", "kraken", "merrow"),
    "desert": ("dune", "sand", "desert", "scorch", "dust"),
    "forest": ("forest", "grove", "wood", "bark", "thorn", "treant", "dryad", "vine", "moss"),
    "swamp": ("swamp", "marsh", "bog", "mire", "fen", "sludge"),
    "mountain": ("mountain", "peak", "crag", "summit", "avalanche"),
    "underdark": ("underdark", "cavern", "drow", "duergar", "myconid", "deep"),
    "urban": ("city", "town", "street", "guild", "urchin"),
}

# A monster that NAMES its home or is immune to cold cannot be re-homed. The type lists above are
# deliberately broad (dragons live "everywhere"), so they can only ever INCLUDE — they cannot rule a
# creature out, and a bare type check put a Young White Dragon in a desert encounter. These are the
# environments that rule each other out, and only hard evidence (a name marker, an immunity, a
# speed) triggers the exclusion.
_HABITAT_CONFLICTS = {
    "desert": {"arctic", "swamp"},
    "arctic": {"desert", "swamp"},
    "swamp": {"desert", "arctic"},
    "coastal": {"desert"},
    "forest": {"desert"},
}

def _monster_habitat_evidence(m: dict) -> set[str]:
    """What the monster's own mechanics and NAME say, as opposed to its broad type. Evidence, so it
    can exclude: a cold-immune creature is not a desert encounter whatever its type allows."""
    envs: set[str] = set()
    speed = m.get("speed")
    if isinstance(speed, str):
        try:
            speed = json.loads(speed)
        except (ValueError, TypeError):
            speed = {}
    if isinstance(speed, dict):
        keys = {str(k).lower() for k in speed}
        if "swim" in keys:
            envs |= {"coastal", "swamp", "underdark"}
        if "burrow" in keys:
            envs |= {"desert", "underdark", "mountain", "grassland"}
        if "climb" in keys:
            envs |= {"forest", "urban", "underdark"}
        if "fly" in keys and "walk" not in keys:
            envs |= {"mountain", "forest", "coastal"}

    # The values are Python-repr strings ("['cold']"), so read them as text rather than parsing.
    damage = f"{m.get('damage_immunities', '')} {m.get('damage_resistances', '')}".lower()
    if "cold" in damage:
        envs.add("arctic")
    if "fire" in damage:
        envs |= {"desert", "planar"}
    if "acid" in damage or "poison" in damage or "necrotic" in damage:
        envs |= {"swamp", "underdark", "forest", "dungeon"}

    name = str(m.get("name", "")).lower()
    for env, markers in _HABITAT_MARKERS.items():
        if any(mark in name for mark in markers):
            envs.add(env)
    # The colour in a dragon's name is its habitat ("White" has cold immunity, which the damage
    # line above already catches; these two catch the ones it does not).
    if "white dragon" in name or "white wyrm" in name:
        envs.add("arctic")
    return envs

def _monster_habitats(m: dict) -> set[str]:
    """Everything a monster plausibly belongs in: the broad type/subtype plus its own evidence."""
    envs = set(_HABITAT_TYPE.get(str(m.get("type", "")).lower(), set()))
    envs |= _HABITAT_SUBTYPE.get(str(m.get("subtype", "")).lower(), set())
    return envs | _monster_habitat_evidence(m)

@router.post("/api/dm/ai/build-encounter", response_class=JSONResponse)
async def dm_ai_build_encounter(request: Request):
    """AI-suggested encounter composition based on party size/level, environment, and difficulty.
    When campaign_id is provided, loads all party characters and tailors the encounter
    to their stats — exploiting weak saves, bypassing resistances, and countering strengths."""
    user = require_user(request)
    db = get_db()
    data = await request.json()

    def _as_int(value, default):
        """A malformed number is a 400-shaped problem, not a 500: 'five' used to raise out of here,
        and the client showed 'Failed to generate encounter. Try again.'"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    party_level = _as_int(data.get('party_level', 5), 5)
    party_size = _as_int(data.get('party_size', 4), 4)
    environment = data.get('environment', 'dungeon')
    difficulty = data.get('difficulty', 'medium')
    theme = data.get('theme', '')
    tone = data.get('tone', '')
    target_cr_raw = data.get('target_cr', '')
    campaign_id = data.get('campaign_id', '')
    # The campaign shapes the encounter (party stats, boss rotation), so it has to be the
    # caller's own: this came straight from the body into _build_party_profile and the
    # boss-rotation query, which leaked another DM's party the same way party-profile did.
    # Unknown or unowned is treated as "no campaign", not as an error.
    if campaign_id:
        try:
            if not _require_owned(db, user, "dm_campaigns", int(campaign_id), id_col="id"):
                print(f"[AI encounter] ignoring campaign_id={campaign_id}: not owned by user {user['id']}")
                campaign_id = ""
        except (TypeError, ValueError):
            campaign_id = ""

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
        min_cr = max(0, (party_level // 2) - 1)  # L1→0, L5→1, L7→2, L11→4, L15→6, L20→8

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
                # The condition here used to read `any(e in env_words or e in suitable or e in
                # extra for e in env_words)` — and `e in env_words` is true for every element of a
                # non-empty list, so it short-circuited to True and filtered NOTHING (measured:
                # 2201 of 2201 monsters accepted for forest, desert and underdark alike).
                type_fits = any(w in suitable or w in extra for w in env_words)
                name_fits = any(kw in m_name for kw in env_words)
                if not (type_fits or name_fits or environment.lower() in _monster_habitats(m)):
                    env_match = False
                # …but a monster that carries HARD evidence of a contradicting home is out,
                # whatever its type allows: a cold-immune creature is not a desert encounter.
                elif _monster_habitat_evidence(m) & _HABITAT_CONFLICTS.get(environment.lower(), set()):
                    env_match = False

        # ── Negative environment filter: exclude clearly wrong creatures ──
        if env_match and environment.lower() not in ("any", "", "coastal", "swamp", "underdark"):
            # The list used to hold frogs, toads, turtles, crocodiles, otters, beavers and even a
            # rhinoceros, so a Giant Frog was excluded from a FOREST — the ban was eating correct
            # picks. These are the truly aquatic ones; amphibians and land animals are not.
            aquatic_keywords = ["dolphin", "whale", "shark", "octopus", "eel", "jellyfish",
                               "squid", "seal", "sea ", "coral", "manta", "merfolk", "merrow",
                               "koi", "piranha", "reef", "sahuagin", "kraken", "water "]
            if any(kw in m_name for kw in aquatic_keywords):
                env_match = False

        # ── Also filter by tags ──
        m_tags = m.get("tags", [])
        if isinstance(m_tags, list):
            if env_match:
                # If monster is tagged "aquatic" and environment isn't aquatic, exclude
                if "aquatic" in m_tags and environment.lower() not in ("coastal", "swamp", "underdark", "any"):
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
        party_section = "\n--- PARTY PROFILE (tailor encounter to counter these characters) ---\n"
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
        target_raw = xp_budget / expected_mult if xp_budget > 0 else 500
        minion_budget = int(target_raw / 10)  # per-creature budget
        boss_budget = 0
        elite_budget = 0
    elif encounter_type == "solo_lair":
        expected_mult = 1.0
        boss_budget = int(xp_budget / expected_mult * 0.85) if xp_budget > 0 else 500
        elite_budget = 0
        minion_budget = int(xp_budget / expected_mult * 0.15) if xp_budget > 0 else 50
    else:
        expected_mult = 2.0  # skirmish, ambush, social: target 3-6
        boss_budget = int(xp_budget / expected_mult * 0.45) if xp_budget > 0 else 500
        elite_budget = int(xp_budget / expected_mult * 0.25) if xp_budget > 0 else 200
        minion_budget = int(xp_budget / expected_mult * 0.12) if xp_budget > 0 else 50

    target_raw = xp_budget / expected_mult if xp_budget > 0 else 500

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
            if boss_budget > 0 and xp >= boss_budget * 0.3 and xp <= boss_budget * 3.0:
                boss_pool.append(c)
            if elite_budget > 0 and xp >= elite_budget * 0.2 and xp <= elite_budget * 2.5:
                elite_pool.append(c)
            if minion_budget > 0 and not is_trivial and xp <= minion_budget * 3:
                minion_pool.append(c)

    boss_pool.sort(key=lambda c: abs(c["xp"] - boss_budget) if boss_budget > 0 else c["xp"])
    elite_pool.sort(key=lambda c: abs(c["xp"] - elite_budget) if elite_budget > 0 else c["xp"])
    minion_pool.sort(key=lambda c: c["xp"])

    # ── Tier-based scaling guidance ──
    tier_guides = {
        "low": "Tier 1 (L1-4): Keep it simple. 1-2 monster types max. Clear threat roles. No legendary actions. Focus on basic tactics — flanking, cover, simple terrain. Party has few resources; don't grind them down with 6+ creatures.",
        "mid": "Tier 2 (L5-10): Medium complexity. 2-4 monster types. Add synergy between types. Start using multi-attack monsters, spellcasters. Terrain and positioning matter. Party has extra attack and L3 spells; they can handle tactical challenges.",
        "high": "Tier 3 (L11-16): High complexity. 3-5 monster types. Include legendary/lair actions. Use resistances, immunities, and legendary saves. Multi-phase fight structure. Party has powerful magic and features; the encounter must challenge resource management.",
        "epic": "Tier 4 (L17-20): Maximum complexity. Multi-phase boss encounters. Legendary + lair actions. Environmental hazards that shift each round. Minion waves. Punish saves, bypass resistances. Party is demigod-level; anything less is a speed bump.",
    }
    tier_key = "low" if party_level <= 4 else "mid" if party_level <= 10 else "high" if party_level <= 16 else "epic"
    tier_guide = tier_guides[tier_key]

    # ── Archetype-specific design guides ──
    archetype_guides = {
        "skirmish": (
            "DESIGN CONCEPT: Think about monster synergy. Why do these specific creatures fight together? "
            "What's their combined tactical approach? (e.g., brute front line + ranged support + flanker). "
            "Pick 2-4 monster types that complement each other — a primary threat and creatures that enable it."
        ),
        "swarm": (
            "DESIGN CONCEPT: A horde encounter where numbers are the threat. Pick 1-2 creature types "
            "appropriate for the environment. Each creature should be weak individually (CR 0.125 to CR 2). "
            "Describe how the swarm attacks — does it surround, ambush from above, surge in waves? "
            "No boss. Role is always 'minion'."
        ),
        "ambush": (
            "DESIGN CONCEPT: The environment is the first enemy. The ambushers use terrain, surprise, "
            "and first-round alpha strike. Pick 2-4 creatures, at least one with Stealth proficiency "
            "or surprise ability. Tactics must describe: ambush setup, trigger, first-round plan, "
            "and fallback plan if surprise fails."
        ),
        "solo_lair": (
            "DESIGN CONCEPT: One legendary monster with lair actions. CR should be 2-5 above party level "
            "(solo monsters need the action economy advantage). Pick exactly 1 boss + 0-2 minions "
            "as lair guards. Tactics: lair action rotation, terrain hazards, escape route. "
            "Description: set up the lair — size, hazards, environment features."
        ),
        "rival_faction": (
            "DESIGN CONCEPT: Three-way fight. Two rival groups (2-3 creatures each) that hate each other "
            "more than the party — at least initially. E.g., goblins vs hobgoblins, cultists vs guards. "
            "In JSON use roles 'faction_a' and 'faction_b'. Tactics: How each faction fights AND when they "
            "might switch targets or flee."
        ),
        "social_combat": (
            "DESIGN CONCEPT: Negotiation first, violence second. Pick 1-3 intelligent creatures "
            "that have something the party wants (information, passage, treasure). "
            "Description: the social tension. Tactics: what they want + what happens if combat starts."
        ),
    }
    guide = archetype_guides.get(encounter_type, archetype_guides["skirmish"])

    # ── Phase 1: Algorithm picks monsters (AI can't do this reliably) ──
    # Pick boss from boss pool, or CR-appropriate candidates if pool empty
    fb_pool = boss_pool if boss_pool else (
        [c for c in candidates if abs(c["cr"] - party_level) <= 1 and c["cr"] >= 1]
        or candidates[:20])

    boss = random.choice(fb_pool) if fb_pool else None
    # Safety: validate boss is budget-appropriate (should be from pool, but guard)
    if boss and boss_budget > 0 and boss["xp"] > boss_budget * 3.5:
        print(f"[AI Encounter] Safety: boss {boss['name']} ({boss['xp']} XP > {boss_budget*3.5}) over budget — re-picking from filtered pool")
        budget_ok = [c for c in fb_pool if c["xp"] <= boss_budget * 3.0]
        boss = random.choice(budget_ok) if budget_ok else None
    picks = []
    if boss:
        picks.append({**boss, "role": "boss", "_suggested_count": 1})
        # Minions: prefer same-type from minion_pool
        if minion_pool:
            same_type = [c for c in minion_pool if c["type"] == boss["type"]]
            other = [c for c in minion_pool if c["index"] != boss["index"]]
            random.shuffle(same_type)
            random.shuffle(other)
            minion_picks = (same_type + other)[:3]
            for m in minion_picks:
                picks.append({**m, "role": "minion", "_suggested_count": 2 if m["cr"] <= 0.5 else 1})
    composition, xp_total = _assign_encounter_counts(picks, xp_budget, encounter_type) if picks else ([], 0)

    # Build composition summary for AI
    comp_name_list = []
    seen_names = set()
    for c in composition:
        n = c.get("name", "?")
        if n not in seen_names:
            seen_names.add(n)
            comp_name_list.append(f"{c.get('count', 1)}× {n}")
    monster_str = ", ".join(comp_name_list) or "various creatures"

    # ── Phase 2: AI writes narrative for the actual composition ──
    # The DM's own inputs have to reach the model: the form collects a Tone and an
    # Encounter Type, and both used to be read and then dropped (the archetype brief was
    # computed into a local that nothing interpolated), so the flavor text ignored them.
    tone_line = f"Tone: {tone}\n" if tone else ""
    design_brief = (
        f"--- ENCOUNTER-TYPE BRIEF ({encounter_type}) ---\n{guide}\n"
        "The creature list above is final — do NOT add, swap or drop creatures; use the "
        "brief for why they are together and how they fight.\n"
    )
    ai_prompt = f"""Write flavor text for a D&D 5e encounter:
Difficulty: {difficulty.upper()} | Setting: {environment} | Type: {encounter_type}
Party: {cr_info}
Monsters: {monster_str}
{tier_guide}{party_section}{boss_rotation_context}
{tone_line}{design_brief}

Write a vivid scene description that sets up WHY these specific monsters are here and
how they work together. Then describe their tactics and any dynamic element.

Return ONLY valid JSON:
{{"name": "short evocative encounter name",
  "description": "vignette setting the scene and why these creatures are together",
  "tactics": "2-3 sentences: terrain use, opening combo, how monsters adapt when hurt",
  "dynamic": "1 sentence about what changes mid-fight (reinforcements, enrage, terrain shift, morale)"}}"""
    
    text = await call_ai(prompt=ai_prompt, label="encounter-phase1")
    ai = _extract_json(text) if text else None
    if ai:
        print(f"[AI Encounter] Phase 2: name={bool(ai.get('name'))} desc={bool(ai.get('description'))} tactics={bool(ai.get('tactics'))}")
    else:
        print("[AI Encounter] Phase 2 FAILED, using generic flavor")

    name = (ai.get("name") or f"{environment.title()} Encounter") if ai else f"{environment.title()} Encounter"
    desc = ((ai.get("description") or "") if ai else "") or f"A {difficulty} encounter in a {environment} setting with {monster_str}."
    tactics = (ai.get("tactics") or "") if ai else ""
    dynamic = (ai.get("dynamic") or "") if ai else ""

    print(f"[AI Encounter] Final composition: {monster_str}")

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
        "name": name or (ai.get("name") or f"{environment.title()} Encounter") if ai else f"{environment.title()} Encounter",
        "description": desc or f"A {difficulty} encounter in a {environment} setting.",
        "tactics": tactics or "",
        "dynamic": dynamic,
        "composition": composition,
        "xp": {"raw_total": xp_total, "adjusted": adjusted_xp, "budget": xp_budget, "budget_pct": budget_pct},
        "difficulty": difficulty.capitalize(),
        "party": {"level": party_level, "size": party_size},
    })

@router.post("/api/dm/encounter/create", response_class=JSONResponse)
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

@router.post("/api/dm/encounter/{enc_id}/copy", response_class=JSONResponse)
def dm_encounter_copy(enc_id: int, request: Request):
    """Deep copy an encounter (shared or own) — creates a new encounter with unique name."""
    user = require_user(request)
    db = get_db()
    # Load source encounter (allow shared)
    src = db.execute("""
        SELECT * FROM dm_encounters WHERE id = ? AND (user_id = ? OR shared = 1)
    """, (enc_id, user["id"])).fetchone()
    if not src:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    src = dict(src)

    # Generate unique name
    base_name = src["name"]
    # Strip existing copy suffix for cleaner base
    import re
    base = re.sub(r"\s*\(Copy \d+\)\s*$", "", base_name).strip()
    # Find next available copy number
    existing = [r[0] for r in db.execute(
        "SELECT name FROM dm_encounters WHERE user_id = ? AND name LIKE ?",
        (user["id"], f"{base}%")
    ).fetchall()]
    copy_num = 1
    while f"{base} (Copy {copy_num})" in existing:
        copy_num += 1
    new_name = f"{base} (Copy {copy_num})"

    # Create new encounter
    cur = db.execute("""
        INSERT INTO dm_encounters (user_id, name, description, location, environment, difficulty, status, notes, xp_total)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        user["id"], new_name,
        src.get("description", ""),
        src.get("location", ""),
        src.get("environment", ""),
        src.get("difficulty", "medium"),
        src.get("status", "planned"),
        src.get("notes", ""),
        src.get("xp_total", 0)
    ))
    new_id = cur.lastrowid

    # Copy NPC participants
    participants = [dict(r) for r in db.execute(
        "SELECT * FROM dm_encounter_npcs WHERE encounter_id = ?", (enc_id,)
    ).fetchall()]
    for p in participants:
        db.execute("""
            INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, hp_max, ac, notes, defeated, spell_slots_used, creature_data)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            new_id, p["npc_id"], p["initiative"], p["hp_current"],
            p["hp_max"], p["ac"], p.get("notes", ""), p.get("defeated", 0),
            p.get("spell_slots_used", "{}"), p.get("creature_data", "")
        ))
    db.commit()
    db.close()
    return JSONResponse({"id": new_id, "name": new_name, "ok": True})

@router.get("/api/dm/encounters", response_class=JSONResponse)
def dm_encounters_list(request: Request):
    """List all encounters — own encounters + shared encounters from other users."""
    user = require_user(request)
    db = get_db()
    # Own encounters
    where, params = user_where(user)
    rows = [dict(r) for r in db.execute(
        f"SELECT e.*, u.email as owner_email FROM dm_encounters e JOIN users u ON u.id = e.user_id {where} ORDER BY e.created_at DESC", params
    ).fetchall()]
    # Shared encounters from other users
    shared = [dict(r) for r in db.execute("""
        SELECT e.*, u.email as owner_email FROM dm_encounters e
        JOIN users u ON u.id = e.user_id
        WHERE e.shared = 1 AND e.user_id != ?
        ORDER BY e.created_at DESC
    """, (user["id"],)).fetchall()]
    # Count participants
    all_rows = rows + shared
    for r in all_rows:
        npcs = db.execute(
            "SELECT COUNT(*) as cnt FROM dm_encounter_npcs WHERE encounter_id = ?",
            (r["id"],)
        ).fetchone()
        r["npc_count"] = npcs["cnt"] if npcs else 0
        r["is_owner"] = r["user_id"] == user["id"]
    db.close()
    return JSONResponse({"encounters": all_rows})

@router.get("/api/dm/encounter/{enc_id}", response_class=JSONResponse)
def dm_encounter_detail(enc_id: int, request: Request):
    """Full encounter detail with NPC participants."""
    user = require_user(request)
    db = get_db()
    row = db.execute("""
        SELECT e.*, u.email as owner_email FROM dm_encounters e
        JOIN users u ON u.id = e.user_id
        WHERE e.id = ? AND (e.user_id = ? OR e.shared = 1)
    """, (enc_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404)
    enc = dict(row)
    # Get NPC participants
    participants = [dict(r) for r in db.execute("""
        SELECT en.*, n.name as npc_name, n.race, n.class_name, n.level, n.is_enemy,
               n.hp_max as npc_hp_max, n.ac as npc_ac, n.role, n.xp_reward,
               CASE WHEN n.portrait_url IS NOT NULL AND n.portrait_url != '' THEN 1 ELSE 0 END as npc_has_portrait
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
            p["char_id"] = cd.get("char_id")
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

@router.post("/api/dm/encounter/{enc_id}/share", response_class=JSONResponse)
async def dm_encounter_share(enc_id: int, request: Request):
    """Toggle sharing on an encounter (owner only)."""
    user = require_user(request)
    data = await request.json()
    shared = 1 if data.get("shared", False) else 0
    db = get_db()
    db.execute("UPDATE dm_encounters SET shared=? WHERE id=? AND user_id=?",
               (shared, enc_id, user["id"]))
    db.commit()
    rows = db.execute("SELECT COUNT(*) as c FROM dm_encounters WHERE shared=1").fetchone()
    db.close()
    return JSONResponse({"ok": True, "shared": bool(shared), "total_shared": rows["c"] if rows else 0})

@router.post("/api/dm/encounter/{enc_id}/add-npc", response_class=JSONResponse)
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

@router.post("/api/dm/encounter/{enc_id}/add-creature", response_class=JSONResponse)
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
    # A narrative NPC (no stat block, no hit points) is not a combatant. int(None) here used to be
    # unreachable because every manual NPC was given a fabricated 10 HP; now that the corpus value
    # is honoured, refuse the add instead of inventing hit points.
    raw_hp = data.get("hp", data.get("hp_current"))
    if raw_hp in (None, ""):
        db.close()
        raise HTTPException(status_code=400,
                            detail="This NPC has no hit points and cannot be added as a combatant")
    try:
        hp = int(raw_hp)
    except (TypeError, ValueError):
        db.close()
        raise HTTPException(status_code=400, detail="Invalid hit points for this creature")
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
        "char_id": data.get("char_id"),
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

@router.post("/api/dm/encounter/{enc_id}/remove-npc", response_class=JSONResponse)
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

@router.post("/api/dm/encounter/{enc_id}/update-initiative", response_class=JSONResponse)
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

@router.post("/api/dm/encounter/{enc_id}/roll-initiative", response_class=JSONResponse)
def dm_encounter_roll_initiative(enc_id: int, request: Request):
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
        dex_mod = ability_mod(p.get("dexterity", 10))
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

@router.post("/api/dm/encounter/{enc_id}/combat-state", response_class=JSONResponse)
async def dm_encounter_combat_state(enc_id: int, request: Request):
    """Save or load combat state. Non-owners can load shared encounters but not save."""
    user = require_user(request)
    db = get_db()
    enc = db.execute("""
        SELECT e.id, e.combat_state FROM dm_encounters e
        WHERE e.id = ? AND (e.user_id = ? OR e.shared = 1)
    """, (enc_id, user["id"])).fetchone()
    if not enc:
        db.close()
        raise HTTPException(status_code=404)

    data = await request.json()
    action = data.get("action", "load")

    if action == "save":
        # Non-owners can't save combat state
        enc_owner = db.execute("SELECT user_id FROM dm_encounters WHERE id=?", (enc_id,)).fetchone()
        if enc_owner and enc_owner[0] != user["id"]:
            db.close()
            raise HTTPException(status_code=403, detail="Only the owner can modify combat state")
        state = json.dumps({
            "round": data.get("round", 1),
            "turn_index": data.get("turn_index", 0),
            "initiative_order": data.get("initiative_order", []),
            "benched_en_ids": data.get("benched_en_ids", []),
            "player_participants": data.get("player_participants", []),
            "campaign_id": data.get("campaign_id"),
            "participant_conditions": data.get("participant_conditions", {})
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
        "participant_conditions": state.get("participant_conditions", {}),
        "ok": True
    })

@router.post("/api/dm/encounter/{enc_id}/update", response_class=JSONResponse)
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
        if db.total_changes == 0:
            db.close()
            raise HTTPException(status_code=404, detail="Encounter not found")
        db.commit()
        db.close()
    return JSONResponse({"ok": True})

@router.post("/api/dm/encounter/{enc_id}/delete", response_class=JSONResponse)
def dm_encounter_delete(enc_id: int, request: Request):
    """Delete an encounter."""
    user = require_user(request)
    db = get_db()
    db.execute("DELETE FROM dm_encounter_npcs WHERE encounter_id IN (SELECT id FROM dm_encounters WHERE id=? AND user_id=?)",
               (enc_id, user["id"]))
    db.execute("DELETE FROM dm_encounters WHERE id=? AND user_id=?", (enc_id, user["id"]))
    if db.total_changes == 0:
        db.close()
        raise HTTPException(status_code=404, detail="Encounter not found")
    db.commit()
    db.close()
    return JSONResponse({"ok": True})
