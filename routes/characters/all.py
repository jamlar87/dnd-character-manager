"""Character routes — composite router + the shared PHB registries.

Every route handler now lives in a sibling module (creation, sheet, leveling,
spells, campaign, relationships, pdf, transfer, ai_routes); this module only
assembles their routers into one `router`, and keeps the data the package
re-exports: SUBCLASS_FEATURE_DESCRIPTIONS and CHANNEL_DIVINITY_DESCRIPTIONS
(merged into FEATURE_DESCRIPTIONS below) plus the name-cache extension.
Importing this module also triggers load_manual_data().
"""

import json

from fastapi import APIRouter

from core.config import DATA_DIR
from core.data import ALIGNMENTS, BACKGROUNDS, RACE_NAMES, load_manual_data
from data import (
    EXPERTISE_LEVELS,
    FEATS,
    FEATURE_DESCRIPTIONS,
)

# Leveling/spell-slot helper core (extracted to services/leveling.py — re-exported
# here so existing callers of these names keep working).
from services.leveling import (
    # Level-up data constants (moved from all.py 2026-07-31)
    WARLOCK_EXPANDED_SPELLS_BY_LEVEL,
    # Choice-system constants (shadow data.py versions)
    # Ability modifier
    get_cantrips_known_max,
    get_caster_type,
    # Batch 2 (progression core)
    get_character_spell_slots,
    get_prepared_max,
    get_spell_slots,
    get_spells_known_max,
    parse_class_levels,
    primary_class,
    total_level,
)
from summon_templates import SUMMON_TEMPLATES

router = APIRouter()


# ── Routes: Character Creation Wizard ──────────────────────────────────────


# ── NPC ↦ Character conversion helpers ──────────────────────────────────

# Maps NPC race strings → canonical RACES keys (case-insensitive lookup)

# Strip parenthetical context and case-fold for race matching


# ═══════════════════════════════════════════════════════════════════════
# Build a character from an NPC stat block
# ═══════════════════════════════════════════════════════════════════════


# ── Starting spells lookup (no character needed — creation wizard) ──────────


# ── DM Tools: Monster helpers ──────────────────────────────────────────────


# DM routes moved to routes/dm.py — see startup event
# ── Campaign Team Items (extracted to campaign.py) ───────────────
from routes.characters.campaign import router as _campaign_router
from routes.characters.helpers import (
    MANUAL_MONSTERS,
    MANUAL_TRAPS,
    _assign_encounter_counts,
    _load_monster_cache,
    _monster_cr_sort_key,
    _search_manuals,
    _xp_for_cr,
    parse_source_filter,
    slug_for_source,
    source_matches,
)

router.include_router(_campaign_router)
# ── Character Creation API (extracted to creation.py) ───────────────
from routes.characters.creation import router as _creation_router

router.include_router(_creation_router)


# ── Advantage Map: compute which stats/saves/skills have advantage ──


# ── Routes: Character Sheet ────────────────────────────────────────────────


# ── Routes: Live Session API ───────────────────────────────────────────────


# ── Conditions (CRUD) ─────────────────────────────────────────────


# ── PDF generation (extracted to pdf.py) ───────────────────────────────────
from routes.characters.pdf import router as _pdf_router

router.include_router(_pdf_router)

# ── Export/Import JSON (transfer.py) ────────────────────────────────────────
from routes.characters.transfer import router as _transfer_router

router.include_router(_transfer_router)

# ── Spell/combat management (extracted to spells.py) ────────────────────
from routes.characters.spells import router as _spells_router

router.include_router(_spells_router)


# ── History & Relationships API (extracted to relationships.py) ─────────────
from routes.characters.relationships import router as _relationships_router

router.include_router(_relationships_router)


# ── Level-Up API ────────────────────────────────────────────────────────────

# ── Level-Up / De-Level API (extracted to leveling.py) ──────────────────
# ── Audit trail (extracted to audit.py) ───────────────────────────────
from routes.characters.audit import router as _audit_router  # noqa: E402

router.include_router(_audit_router)


from routes.characters.leveling import router as _leveling_router

router.include_router(_leveling_router)

# ── Character Sheet / Combat API (extracted to sheet.py) ───────────────
from routes.characters.sheet import router as _sheet_router

router.include_router(_sheet_router)


# ── Name Generators ─────────────────────────────────────────────────────────

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

# ── From data.py: STARTING_EQUIPMENT


# ── PHB-Grounded AI Generation ──────────────────────────────────────────────
# All mechanical data (races, classes, spells, equipment) is from the PHB 2014
# hardcoded above. AI only handles creative flavor: name, personality, backstory.
# Backgrounds and alignments are validated against PHB-approved lists.
# Model chain: Gemini → OpenRouter (free) → Ollama (local) → deterministic


# ── Character Build Optimization (PHB 2014) ─────────────────────────────────

# Ability Score Priority (PHB Quick Build sections + optimal play)

# Standard array: 15,14,13,12,10,8 (PHB p.13)

# Proficiency bonus by level (PHB p.15)

# ── SRD-Backed Functions (replace hand-coded PHB tables) ─────────────────────


# ── From data.py: SUBCLASS_FEATURES (includes DMG subclasses: Death Domain, Oathbreaker)


# PHB-granted proficiencies that come from subclass choice (not base class)


# ── Caster type detection & prepared spell computation ────────────────────

# ── Racial Limited-Use Feature Builder ────────────────────────────────────


# ── PHB 2014 Limited-Use Feature Definitions ─────────────────────────────
# (feature_key_lower, (min_level_uses, max_cap, recharge_type))
# recharge_type: 'short' (short or long rest), 'long' (long rest only), 'dawn' (at dawn)
# max_cap of 99 means scales with character level (capped by level-based formula)

# PHB Limited-Use Abilities (p.186+ per class)
# ── Multiclass Support (PHB 2014 p.163-165) ──────────────────────────────

# Proficiencies gained when multiclassing INTO a class (PHB p.164)
# None of these grant saving throw proficiencies


# PHB 2014 p.165 — Multiclass Spellcaster: Spell Slots per Spell Level
# Key = combined caster level. Value = [1st, 2nd, 3rd, 4th, 5th, 6th, 7th, 8th, 9th]


# ── Level-Up Data ──────────────────────────────────────────────────────
# ── From data.py: ABILITY_NAMES, ASI_LEVELS

# Subclass selection levels + options per class (PHB 2014)
# (EXPERTISE_LEVELS lives in data.py and is imported above — the old
#  local shadow here drifted into a second, contradicting definition.)


# ── Fighting Styles ────────────────────────────────────────────────────


# 8 roleplay/combat choice systems: level maps, options, and descriptions
# ── Metamagic (Sorcerer L3/10/17, pick from list each time) ──────────

# ── Eldritch Invocations (Warlock L2+, ~33 SRD options) ──────────────

# ── Pact Boon (Warlock L3, pick 1 of 4) ──────────────────────────────
# ── Summon Templates (imported from summon_templates.py) ─────────────────

# ── Battle Master Maneuvers (Fighter L3/7/10/15, requires Battle Master) ──

# ── Magical Secrets (Bard L10/14/18, Lore Bard gets L6 bonus) ────────

# ── Totem Spirit (Barbarian Totem Warrior L3/6/14, pick per tier) ────

# ── Hunter's Prey (Ranger Hunter L3, pick 1 of 3) ─────────────────────
# ── Ranger Favored Enemy (PHB p.91, L1/6/14, pick 1 per tier) ────────
# ── Ranger Favored Terrain / Natural Explorer (PHB p.91, L1/6/10) ────
# ── Artificer Infusions (L2, pick from list) ─────────────────────────

# Cantrip progression

# ── PHB 2014 Feats ─────────────────────────────────────────────────────
# Tag all PHB 2014 feats with source
for _feat in FEATS.values():
    if not _feat.get("source"):
        _feat["source"] = "Player's Handbook p.165-170"

# ── Feature → Combat Action mapping ──────────────────────────────────
# Maps feature name (lowercase) to (action_type, short_action_label)
# action_type: "Action", "Bonus Action", or "Reaction"
# ── Channel Divinity sub-option descriptions (PHB 2014, not in SRD) ──────
from routes.characters.feature_text import (  # noqa: E402
    CHANNEL_DIVINITY_DESCRIPTIONS,
    SUBCLASS_FEATURE_DESCRIPTIONS,
)

# ── Domain / Oath spells — always prepared, don't count against limit (PHB p.58, p.85) ──

# ── Warlock expanded spell lists — always known (PHB p.108-109, XGtE p.56-57, TCE) ──
# Warlocks are known casters, not prepared. These spells are auto-inserted with
# source='Expanded Spell List' and don't count against spells known max.
# Format: subclass_name → {patron_spell_level: [spell_names]}

# Flat list of all expanded spell names per subclass (for prepared/known checks)

# ── Subclass feature descriptions (PHB 2014, not in SRD) ──────
# ── Subclass feature descriptions (PHB 2014, not in SRD) ──────

# Merge subclass descriptions into FEATURE_DESCRIPTIONS
for sub_key, sub_desc in SUBCLASS_FEATURE_DESCRIPTIONS.items():
    if sub_key not in FEATURE_DESCRIPTIONS:
        FEATURE_DESCRIPTIONS[sub_key] = sub_desc

# Merge CD descriptions into FEATURE_DESCRIPTIONS so enrich_features finds them
for cd_key, cd_desc in CHANNEL_DIVINITY_DESCRIPTIONS.items():
    if cd_key not in FEATURE_DESCRIPTIONS:
        FEATURE_DESCRIPTIONS[cd_key] = cd_desc

# Call manual data loader after all data structures are defined
# (Guard against double-load when imported from routes/dm.py during startup)
if not MANUAL_TRAPS:
    load_manual_data()

# ── Known feats list for ASI picker dropdown ──────────────────────────
# Auto-generated from FEATS dict after manual data merge — filters out
# Eldritch Invocations and ALL-CAPS duplicates. No manual maintenance.

# Feat name → {desc, prereq, source} for the ASI picker preview
# Case-insensitive name → FEATS value for enrichment lookups


# ── PHB scale functions per feature ──


# ── Spell enrichment (SRD descriptions) ───────────────────────────────────


# ── Spells also available as tiered recommendations (from SRD cache) ──────────


# Feats by class tier (PHB-optimal picks at ASI levels 4,8,12,16,19)
# Scaled equipment by class and level tier (PHB starting equipment + reasonable progression)
# Levels: 1, 5, 10, 15, 20
# HP calculation: max at level 1, average (rounded up) thereafter


# ── Ability score modifier (moved to services/leveling.py) ──


# ── Treasure Hoard Engine (DMG 2014 p.137-139) ──

# Coin formulas per CR bracket — all standardized to GP.
# Original DMG 2014 values (cp/sp/ep/pp) converted at standard rates:
#   100 cp = 1 gp, 10 sp = 1 gp, 2 ep = 1 gp, 1 pp = 10 gp
# ── Treasure hoard data & helpers (moved to campaign.py to avoid circular import) ──

# ── Feature → Defense Mappings (PHB 2014) ──
# Maps feature names to resistances/immunities they grant.
# Format: {"resist": [...], "immune": [...], "note": "while raging"}

# ── Item Attunement & Properties (PHB 2014 p.136-138) ──
# Auto-built at startup from SRD magic items desc text.
# Items that say "requires attunement" in their description.

# Item → mechanical effects when equipped AND attuned (if required).
# Keys are lowercase item names. Properties merged into character sheet.
# Supported keys: resist, immune, ac_bonus, save_bonus,
#   str_override, dex_override, con_override, int_override, wis_override, cha_override,
#   str_bonus, con_bonus, adv_skill, note

# (ITEM_PROPERTIES init moved to sheet.py — owns the data now)


# ── AI Generation Routes (extracted to ai_routes.py) ─────
from routes.characters.ai_routes import (
    _call_ai,
    _call_ollama,
    _extract_json,
)
from routes.characters.ai_routes import router as _ai_router

router.include_router(_ai_router)

