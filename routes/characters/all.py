"""Character routes — create, sheet, level-up, spells, combat, relationships."""

from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import sqlite3, json, math, random, re, urllib.parse, os, httpx, functools
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
from main import RACES, CLASSES, RACE_NAMES, SUBCLASS_FEATURES, LIMITED_USE, SUBCLASS_FEATURE_REPLACEMENTS
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
# Leveling/spell-slot helper core (extracted to services/leveling.py — re-exported
# here so existing callers of these names keep working).
from services.leveling import (
    MAGIC_INITIATE_CLASSES,
    _ordinal,
    get_spell_slots,
    get_srd_spells_for_class,
    _scaled_dice_display,
    # Batch 2 (progression core)
    ABILITY_PRIORITY,
    PROFICIENCY_BONUS,
    get_character_spell_slots,
    has_casters,
    SUBCLASS_PROFICIENCIES,
    _deduplicate_multiclass_features,
    get_class_features,
    _GENERIC_SUBCLASS_NAMES,
    _replace_subclass_name,
    _build_racial_limited_features,
    parse_class_levels,
    total_level,
    primary_class,
    meets_multiclass_prereq,
    get_multiclass_proficiencies,
    MULTICLASS_SPELL_SLOTS,
    compute_multiclass_caster_level,
    get_multiclass_spell_slots,
    get_expertise_count,
    get_expertise_options,
    get_uses_for_level,
    get_caster_type,
    get_multiclass_caster_types,
    is_multiclass_caster,
    get_prepared_max,
    get_spells_known_max,
    get_cantrips_known_max,
    enrich_spells,
    get_asi_levels,
    get_feats_for_level,
    enrich_features,
    # Level-up data constants (moved from all.py 2026-07-31)
    DOMAIN_SPELLS, FAVORED_ENEMY_LEVELS, FAVORED_TERRAIN_LEVELS,
    FIGHTING_STYLES, FIGHTING_STYLE_LEVELS, FIGHTING_STYLE_OPTIONS,
    HUNTERS_PREY_LEVELS, INFUSION_LEVELS, INFUSION_PICKS,
    MAGICAL_SECRETS_LEVELS, MAGICAL_SECRETS_PICKS, MANEUVER_PICKS,
    TOTEM_SPIRIT_LEVELS, TOTEM_SPIRIT_TIER_LABELS,
    WARLOCK_EXPANDED_SPELLS_BY_LEVEL,
    # Choice-system constants (shadow data.py versions)
    METAMAGIC_LEVELS, METAMAGIC_PICKS,
    INVOCATION_LEVELS, INVOCATION_PICKS,
    PACT_BOON_LEVELS, MANEUVER_LEVELS, CANTRIPS_PROGRESSION,
    # Ability modifier
    modifier,
)
from data import (
    SPELLS_KNOWN_CASTERS, RACIAL_TRAIT_EFFECTS, FEATURE_ACTION_TYPES,
    ABILITY_NAMES, ALL_SKILLS, LANGUAGES, SKILL_ABILITIES, FEATS, FEAT_BY_NAME,
    FEATURE_DESCRIPTIONS, DRACONIC_ANCESTRIES, PREPARED_CASTERS,
    METAMAGIC_OPTIONS,
    INVOCATION_OPTIONS,
    PACT_BOON_OPTIONS,
    MANEUVER_OPTIONS,
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


# ── Routes: Character Creation Wizard ──────────────────────────────────────


# ── NPC ↦ Character conversion helpers ──────────────────────────────────

# Maps NPC race strings → canonical RACES keys (case-insensitive lookup)

# Strip parenthetical context and case-fold for race matching


# ═══════════════════════════════════════════════════════════════════════
# Build a character from an NPC stat block
# ═══════════════════════════════════════════════════════════════════════


# ── Starting spells lookup (no character needed — creation wizard) ──────────


# ── DM Tools: Monster helpers ──────────────────────────────────────────────


from routes.characters.helpers import (
    MANUAL_MONSTERS, MANUAL_TRAPS,
    _load_monster_cache, _template_monster_entries,
    _normalize_manual_monster, _monster_cr_sort_key,
    _xp_for_cr, _encounter_mult, _assign_encounter_counts,
    _format_monster_action, _ensure_manual_cache,
    _extract_pdf, _fuzzy_variants, _search_manuals,
)


# DM routes moved to routes/dm.py — see startup event


# ── Campaign Team Items (extracted to campaign.py) ───────────────
from routes.characters.campaign import router as _campaign_router
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

# ── Spell/combat management (extracted to spells.py) ────────────────────
from routes.characters.spells import router as _spells_router
router.include_router(_spells_router)


# ── History & Relationships API (extracted to relationships.py) ─────────────
from routes.characters.relationships import router as _relationships_router
router.include_router(_relationships_router)


# ── Level-Up API ────────────────────────────────────────────────────────────

# ── Level-Up / De-Level API (extracted to leveling.py) ──────────────────
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

PHB_BACKGROUNDS = BACKGROUNDS  # PHB p.125-141
PHB_ALIGNMENTS = ALIGNMENTS    # PHB p.122

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
# Expertise progression — class/subclass → {levels: [...], options: "skills" | "skills_and_thieves_tools" | [...]}
EXPERTISE_LEVELS: dict = {
    "Rogue":         {"levels": [1, 6], "options": "skills_and_thieves_tools"},
    "Bard":          {"levels": [3, 10], "options": "skills"},
    "Knowledge Domain": {"levels": [1], "options": ["Arcana", "History", "Nature", "Religion"]},
}


# ── Fighting Styles ────────────────────────────────────────────────────


# 8 roleplay/combat choice systems: level maps, options, and descriptions
# ── Metamagic (Sorcerer L3/10/17, pick from list each time) ──────────

# ── Eldritch Invocations (Warlock L2+, ~33 SRD options) ──────────────

# ── Pact Boon (Warlock L3, pick 1 of 4) ──────────────────────────────
# ── Summon Templates (imported from summon_templates.py) ─────────────────
from summon_templates import SUMMON_TEMPLATES

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

# ── Warlock expanded spell lists — always known (PHB p.108-109, XGtE p.56-57, TCE) ──
# Warlocks are known casters, not prepared. These spells are auto-inserted with
# source='Expanded Spell List' and don't count against spells known max.
# Format: subclass_name → {patron_spell_level: [spell_names]}

# Flat list of all expanded spell names per subclass (for prepared/known checks)
WARLOCK_EXPANDED_SPELLS: dict[str, list[str]] = {
    sub: [s for spells in levels.values() for s in spells]
    for sub, levels in WARLOCK_EXPANDED_SPELLS_BY_LEVEL.items()
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
        "Starting at 6th level, you can curse the soul of a person you slay, temporarily binding it to your service. When you slay a humanoid, you can cause its spirit to rise as a specter with temp HP equal to half your warlock level. It obeys your verbal commands and gains a bonus to attack rolls equal to your Charisma modifier. The specter vanishes after your next long rest. 1/long rest.",
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
        "At 10th level, your hex grows more powerful. If the target cursed by your Hexblade's Curse hits you with an attack roll, you can use your reaction to roll a d6. On a 4 or higher, the attack instead misses you, regardless of its roll.",
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
        "A Warlock subclass feature (PHB p.108-109, XGtE). Your patron grants you bonus spells that are always known and don't count against your spells known limit. You gain access to these spells at warlock levels 1, 3, 5, 7, and 9.",
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
        "At 1st level, you gain proficiency with medium armor, shields, and martial weapons. When you finish a long rest, touch one proficient weapon lacking the two-handed property — use Charisma for attack/damage rolls with it instead of Str/Dex. If you later gain Pact of the Blade, this extends to every pact weapon you conjure.",
    "hexblade's curse":
        "Starting at 1st level, as a bonus action, curse a creature you can see within 30 ft for 1 minute. You gain +proficiency bonus to damage rolls against it. Any attack roll against it is a critical hit on 19-20. If the cursed target dies, you regain HP equal to your warlock level + Cha modifier. 1/short or long rest.",
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
        "Starting at 14th level, you can spread your Hexblade's Curse from a slain creature to another. When the cursed target dies, as a bonus action apply the curse to a different creature you can see within 30 ft. You don't regain HP from the previous target's death.",
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
from routes.characters.campaign import roll_treasure_hoard, TREASURE_HOARD_COINS, TREASURE_HOARD_TABLE, MAGIC_TABLE_POOLS, _roll_dice, _pick_magic_item

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
from routes.characters.ai_routes import router as _ai_router
from routes.characters.ai_routes import (
    _call_ollama,
    _call_ai,
    _fetch_openrouter_image, _extract_json, _validate_and_fix,
    _fallback_generate, _random_items, _fallback_background,
    _fallback_history, _try_ai_enrich_prompt, _try_generate_image,
    _fallback_portrait_prompt, _calculate_ac, _calculate_attacks,
    _pick_skills,
)
router.include_router(_ai_router)

