"""Character sheet / combat / session routes — character detail page,
update, ASI/expertise edits, attacks, summons, conditions, charge
tracking, delete, share.

Extracted from routes/characters/all.py (2026-07-31). Imports helpers
from main / data / services.leveling / schemas only — never from
all.py (avoids circulars).
"""

import json
import random
import re
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from main import (
    get_db, require_user, _render, _user_filter, _is_admin, _require_owned,
    _normalize_equipped, _equipped_names, _build_racial_traits,
    _build_character_attacks,
    _build_charged_item_attacks, _build_item_description,
    _resolve_item_key, _resolve_armor_item, _resolve_source, _parse_enhancement,
    _item_rarity_for_level, _entry, _load_manual_json, _get_named_item_types,
    _get_source_slug_map, _manual_races_raw as _MANUAL_RACES_RAW,
    BACKGROUND_INFO, CLASSES, DRACONIC_ANCESTRIES, EXPERTISE_LEVELS, FEATS,
    FEATURE_ACTION_TYPES, FEATURE_DESCRIPTIONS, INVOCATION_LEVELS,
    INVOCATION_OPTIONS, INVOCATION_PICKS, ITEMS_BY_RARITY, ITEM_ARMOR,
    ITEM_INDEX, ITEM_RODS_STAVES_WANDS, ITEM_WEAPONS, ITEM_WONDROUS,
    LIMITED_USE, MANEUVER_OPTIONS, METAMAGIC_OPTIONS, PACT_BOON_OPTIONS,
    RACES, RACIAL_TRAIT_DESCS, SCALED_EQUIPMENT, SKILL_ABILITIES,
    SRD_FEATURES, SRD_LEVELS, SRD_MAGIC_ITEMS, SUBASIS, SUBCLASS_FEATURES,
    check_armor_proficiency_from_set, get_character_armor_profs,
    get_racial_trait_effects, enrich_features, get_caster_type,
)
from data import (
    ABILITY_NAMES, ALL_SKILLS, LANGUAGES,
    PREPARED_CASTERS, METAMAGIC_OPTIONS, INVOCATION_OPTIONS,
    PACT_BOON_OPTIONS, MANEUVER_OPTIONS, TOTEM_SPIRIT_OPTIONS,
    HUNTERS_PREY_OPTIONS, FAVORED_ENEMY_OPTIONS, FAVORED_TERRAIN_OPTIONS,
    INFUSION_OPTIONS, SUBCLASS_LEVELS, STARTING_EQUIPMENT,
    RECOMMENDED_FEATS, MULTICLASS_PREREQS, MULTICLASS_PROFICIENCIES,
    ASI_LEVELS, FULL_CASTERS, HALF_CASTERS, PACT_CASTERS,
    SPELLS_KNOWN_CASTERS,
)
from services.leveling import (
    ABILITY_PRIORITY, EXPERTISE_LEVELS, FEATURE_ACTION_TYPES,
    FEATURE_DESCRIPTIONS, INVOCATION_LEVELS, INVOCATION_PICKS, LIMITED_USE,
    RACIAL_TRAIT_DESCS, SRD_FEATURES, SRD_LEVELS, SUBCLASS_FEATURES,
    _build_racial_limited_features, enrich_features, enrich_spells,
    get_cantrips_known_max, get_caster_type, get_expertise_count,
    get_expertise_options, get_multiclass_caster_types, get_prepared_max,
    get_spells_known_max, get_srd_spells_for_class, get_uses_for_level,
    parse_class_levels, modifier,
)
from routes.schemas import EditASI, UpdateCharacter
from summon_templates import SUMMON_TEMPLATES

router = APIRouter()
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

_BA_GRANT_RE = None  # lazy compile (module import is hot)

def _grants_bonus_action(desc_lower: str) -> bool:
    """True only when an item description grants a bonus-action ability.

    Naive 'bonus action' substring matching misfires on weapon text that merely
    mentions the term (e.g. Loading: 'fire only one piece of ammunition per
    action, bonus action, or reaction'), which made firearms appear in the
    Bonus Actions section. Require an actual grant phrase instead.
    """
    if not desc_lower:
        return False
    global _BA_GRANT_RE
    if _BA_GRANT_RE is None:
        import re
        # "as a bonus action", "as your bonus action", "using a bonus action",
        # "use a bonus action to X", "... on a bonus action"
        _BA_GRANT_RE = re.compile(
            r'(?:as|using|use|uses|used|with|on|by|spend|expend)\s+'
            r'(?:your\s+)?(?:a\s+)?bonus action',
            re.IGNORECASE,
        )
    return bool(_BA_GRANT_RE.search(desc_lower))

def compute_advantage_map(char: dict) -> dict:
    """Compute advantage_map from character race, class, subclass, features, and items.
    Returns {saves:[], ability_checks:[], skills:[], attack_rolls:bool, notes:[]}
    """
    adv = {"saves": [], "ability_checks": [], "skills": [],
           "attack_rolls": False, "initiative": False, "notes": []}
    race = (char.get("race") or "").lower()
    subrace = (char.get("subrace") or "").lower()
    race_names = f"{race} {subrace}"
    cls = (char.get("class_name") or "").lower()
    subclass = (char.get("subclass") or "").lower()
    level = char.get("level", 0)
    class_levels = char.get("class_levels", {})
    if isinstance(class_levels, str):
        try: class_levels = json.loads(class_levels)
        except: class_levels = {}

    def _class_level(cls_name: str) -> int:
        try:
            return int(class_levels.get(cls_name, 0)) if isinstance(class_levels, dict) else level
        except (TypeError, ValueError):
            return level

    rc = _class_level

    def _note(text: str):
        if text not in adv["notes"]:
            adv["notes"].append(text)

    # ================================================================
    # RACE-BASED ADVANTAGE
    # ================================================================

    # Gnome — Gnome Cunning: Int/Wis/Cha saves vs magic
    if 'gnome' in race or 'gnome' in subrace:
        adv["saves"].extend(["intelligence", "wisdom", "charisma"])
        _note("Gnome Cunning — Advantage on Int/Wis/Cha saves vs magic")

    # Deep Gnome (Svirfneblin) — Stone Camouflage: Stealth checks underground
    if 'deep' in race_names or 'svirfneblin' in race_names:
        adv["skills"].append("stealth")
        _note("Deep Gnome — Advantage on Stealth checks while underground")

    # Dwarf — Dwarven Resilience: Con saves vs poison
    if 'dwarf' in race or ('halfling' in race and 'stout' in subrace):
        adv["saves"].append("constitution")
        _note("Dwarven Resilience — Advantage on Con saves vs poison")

    # Duergar — Duergar Resilience: saves vs illusions, paralysis, poison
    if 'duergar' in race_names:
        adv["saves"].append("constitution")
        _note("Duergar Resilience — Advantage on saves vs illusions, paralysis, and poison")

    # Halfling — Brave: saves vs frightened
    if 'halfling' in race:
        _note("Halfling Brave — Advantage on saves vs frightened")

    # Elf / Half-Elf — Fey Ancestry: saves vs charmed, cannot be put to sleep
    if 'elf' in race or 'half-elf' in race:
        _note("Fey Ancestry — Advantage on saves vs charmed; magic can't put you to sleep")

    # Yuan-ti Pureblood / Satyr — Magic Resistance: saves vs spells
    if 'yuan-ti' in race_names or 'satyr' in race_names:
        adv["saves"].extend(["strength", "dexterity", "constitution",
                             "intelligence", "wisdom", "charisma"])
        _note("Magic Resistance — Advantage on all saves vs spells and magic effects")

    # Vedalken — Vedalken Dispassion: Int/Wis/Cha saves vs spells
    if 'vedalken' in race_names:
        adv["saves"].extend(["intelligence", "wisdom", "charisma"])
        _note("Vedalken Dispassion — Advantage on Int/Wis/Cha saves vs spells")

    # Kalashtar — Dual Mind: advantage on all Int saves
    if 'kalashtar' in race_names:
        adv["saves"].append("intelligence")
        _note("Kalashtar Dual Mind — Advantage on Intelligence saves")

    # Kobold — Pack Tactics: advantage on attacks when ally is adjacent
    if 'kobold' in race_names:
        adv["attack_rolls"] = True
        _note("Pack Tactics — Advantage on attack rolls when an ally is within 5 ft")

    # Reborn (VRGR) — Deathless Nature: saves vs disease and poisoned
    if 'reborn' in race_names:
        adv["saves"].append("constitution")
        _note("Deathless Nature — Advantage on saves vs disease and poisoned condition")

    # ================================================================
    # CLASS-BASED ADVANTAGE
    # ================================================================

    # Barbarian
    barb_lvl = rc("Barbarian") or rc("barbarian") or (level if cls == "barbarian" else 0)
    if barb_lvl >= 1:
        adv["ability_checks"].append("strength")
        _note("Rage — Advantage on Strength checks (while raging)")
    if barb_lvl >= 2:
        adv["saves"].append("dexterity")
        _note("Danger Sense — Advantage on Dex saves vs visible effects")
    if barb_lvl >= 2:
        adv["attack_rolls"] = True
        _note("Reckless Attack — Melee STR attacks can be made with advantage (attackers also have advantage on you)")
    if barb_lvl >= 7:
        adv["initiative"] = True
        _note("Feral Instinct — Advantage on initiative rolls")

    # ================================================================
    # SUBCLASS-BASED ADVANTAGE
    # ================================================================

    # Paladin — Oath of Vengeance: Vow of Enmity (L3)
    if 'vengeance' in subclass:
        _note("Vow of Enmity — Bonus action to gain advantage on attacks vs one target for 1 min (short rest)")

    # Paladin — Oath of the Watchers: Watcher's Will (L3)
    if 'watcher' in subclass:
        _note("Watcher's Will — Bonus action to grant yourself advantage on Int/Wis/Cha saves for 1 min")

    # Fighter — Samurai: Fighting Spirit (L3)
    if 'samurai' in subclass:
        _note("Fighting Spirit — Bonus action to gain advantage on all attacks for 1 turn (3×/long rest)")

    # Fighter — Cavalier: Unwavering Mark (L3)
    if 'cavalier' in subclass:
        _note("Unwavering Mark — Advantage on attack rolls vs creatures you marked (within 5 ft)")

    # Ranger — Gloom Stalker: Umbral Sight (L3)
    if 'gloom' in subclass:
        _note("Umbral Sight — Invisible to creatures relying on darkvision while in darkness")

    # Rogue — Assassin: Assassinate (L3)
    if 'assassin' in subclass:
        adv["attack_rolls"] = True
        _note("Assassinate — Advantage on attack rolls against creatures that haven't taken a turn in combat")
        _note("Assassinate — Any hit on a surprised creature is a critical hit")

    # Rogue — Steady Aim (Tasha's optional class feature)
    if cls == "rogue":
        rogue_lvl = rc("Rogue") or rc("rogue") or (level if cls == "rogue" else 0)
        if rogue_lvl >= 3:
            _note("Steady Aim — Bonus action to gain advantage on next attack (your speed is 0 until end of turn)")

    # Wizard — War Magic: Arcane Deflection (L2)
    if 'war' in subclass and 'mage' in subclass:
        _note("Arcane Deflection — Reaction to gain +4 to a saving throw (can't cast spells other than cantrips until end of next turn)")

    # Wizard — Chronurgy: Chronal Shift (L2)
    if 'chronurgy' in subclass:
        _note("Chronal Shift — Reaction to force a creature (or yourself) to reroll an attack, check, or save")

    # Druid — Circle of Stars: Dragon Starry Form (L2)
    if 'stars' in subclass or 'star' in subclass:
        _note("Dragon Constellation — While in Starry Form: minimum roll of 10 on Int/Wis checks and Con saves to maintain concentration")

    # Sorcerer — Wild Magic: Tides of Chaos (L1)
    if 'wild' in subclass or 'wild magic' in subclass:
        _note("Tides of Chaos — Gain advantage on one attack roll, ability check, or saving throw (recharges after spell of 1st+ level or long rest)")

    # Sorcerer — Divine Soul: Favored by the Gods (L1)
    if 'divine' in subclass:
        _note("Favored by the Gods — Add 2d4 to a failed save or missed attack (1×/short rest)")

    # Monk — Shadow: Shadow Step (L3)
    if 'shadow' in subclass:
        _note("Shadow Step — Teleport 60 ft to dim light/darkness; next melee attack has advantage")

    # Monk — Drunken Master: Drunken Technique (L3)
    if 'drunken' in subclass:
        _note("Drunken Technique — Redirect attack; Disengage after Flurry of Blows")

    # Bard — College of Valor: Combat Inspiration (L3)
    # (grants 1d6 to hit/save for allies, not self-advantage)

    # Bard — College of Lore: Cutting Words (L3)
    # (reduces enemy attacks/saves, not self-advantage)

    # Barbarian — Wolf Totem: allies get advantage vs enemies near you (L3)
    if 'wolf' in subclass and 'totem' in subclass:
        _note("Wolf Totem — Allies have advantage on melee attack rolls against enemies within 5 ft of you")

    # Artificer — Armorer: Guardian Thunder Gauntlets (L3)
    if 'armorer' in subclass or 'armourer' in subclass:
        _note("Guardian Mode — Creatures you hit with Thunder Gauntlets have disadvantage on attacks against targets other than you")

    # Artificer — Battle Smith: Steel Defender (L3)
    if 'battle' in subclass:
        _note("Battle Ready — Use Intelligence for attack/damage with magic weapons")

    # ================================================================
    # FEAT-BASED ADVANTAGE
    # ================================================================

    asi_history = char.get("asi_history", [])
    if isinstance(asi_history, str):
        try: asi_history = json.loads(asi_history)
        except: asi_history = []
    feats = []
    for entry in asi_history:
        if isinstance(entry, dict) and entry.get("type") == "feat":
            fn = entry.get("feat", "").lower().replace("_", " ").replace("-", " ")
            if fn:
                feats.append(fn)

    for feat_name in feats:
        if 'war caster' in feat_name:
            adv["saves"].append("constitution")
            _note("War Caster — Advantage on Constitution saves to maintain concentration on spells")
        if 'mage slayer' in feat_name:
            adv["saves"].extend(["intelligence", "wisdom", "charisma"])
            _note("Mage Slayer — Advantage on saves vs spells from a creature within 5 ft of you")
        if 'mounted combatant' in feat_name:
            adv["attack_rolls"] = True
            _note("Mounted Combatant — Advantage on melee attacks against creatures smaller than your mount")
        if 'grappler' in feat_name:
            adv["attack_rolls"] = True
            _note("Grappler — Advantage on attack rolls against creatures you are grappling")
        if 'dungeon delver' in feat_name:
            adv["saves"].append("dexterity")  # vs traps
            adv["skills"].append("perception")  # for secret doors
            _note("Dungeon Delver — Advantage on Perception checks to detect secret doors and on saves vs traps")
        if 'elven accuracy' in feat_name:
            _note("Elven Accuracy — When you have advantage on an attack roll using Dex/Int/Wis/Cha, you can reroll one of the dice")
        if 'sentinel' in feat_name:
            _note("Sentinel — Opportunity attacks reduce target speed to 0; creatures provoke OA even with Disengage")
        if 'shield master' in feat_name:
            _note("Shield Master — Bonus action to shove; adds shield AC to Dex saves vs single-target spells")
        if 'observant' in feat_name:
            _note("Observant — +1 to Intelligence or Wisdom; can read lips; +5 passive Perception/Investigation")

    # ================================================================
    # ITEM-BASED ADVANTAGE
    # ================================================================

    equipped = char.get("equipped", [])
    if isinstance(equipped, str):
        try: equipped = json.loads(equipped)
        except: equipped = []
    for item in equipped:
        iname = (item.get("name") or item if isinstance(item, str) else "").lower()
        if 'cloak of elvenkind' in iname:
            adv["skills"].append("stealth")
            _note("Cloak of Elvenkind — Advantage on Stealth checks")
        if 'sentinel shield' in iname:
            adv["initiative"] = True
            adv["skills"].append("perception")
            _note("Sentinel Shield — Advantage on initiative rolls and Perception checks")
        if 'weapon of warning' in iname:
            adv["initiative"] = True
            _note("Weapon of Warning — Advantage on initiative rolls")
        if 'periapt of proof against poison' in iname:
            adv["saves"].append("constitution")
            _note("Periapt of Proof Against Poison — Advantage on Con saves vs poison")
        if 'scarab of protection' in iname:
            adv["saves"].extend(["intelligence", "wisdom", "charisma"])
            _note("Scarab of Protection — Advantage on saves vs necrotic effects and curses")
        if 'robe of the archmagi' in iname:
            adv["saves"].extend(["strength","dexterity","constitution",
                                 "intelligence","wisdom","charisma"])
            _note("Robe of the Archmagi — Advantage on all saves vs spells")
        if 'gem of seeing' in iname:
            adv["skills"].append("perception")
            _note("Gem of Seeing — Advantage on Perception checks relying on sight (for 10 min)")
        if 'ioun stone' in iname and 'mastery' in iname:
            _note("Ioun Stone of Mastery — +1 proficiency bonus")
        if 'luck blade' in iname:
            _note("Luck Blade — +1 to saving throws; contains the Wish spell")
        if 'helm of telepathy' in iname:
            adv["skills"].append("insight")
            _note("Helm of Telepathy — While wearing, can cast Detect Thoughts at will (advantage on Insight checks)")

    # ================================================================
    # HIGH-LEVEL PERSISTENT SPELL EFFECTS
    # ================================================================

    # Foresight (9th level) — advantage on all attacks, checks, saves
    # Check feature_data for known Foresight effects or subclass granting it
    # For now, check if caster level >= 17 and has Foresight prepared
    # (reliable detection requires spell data, so skip for now)

    # ================================================================
    # DEDUPLICATE & FINALIZE
    # ================================================================

    adv["saves"] = list(dict.fromkeys(adv["saves"]))
    adv["ability_checks"] = list(dict.fromkeys(adv["ability_checks"]))
    adv["skills"] = list(dict.fromkeys(adv["skills"]))
    adv["notes"] = list(dict.fromkeys(adv["notes"]))
    char["advantage_map"] = adv
    return adv

@router.get("/character/{char_id}", response_class=HTMLResponse)
async def character_sheet(char_id: int, request: Request):
    user = require_user(request)
    dm_preview = request.query_params.get("dm_preview", "0") == "1"
    db = get_db()
    if dm_preview or _is_admin(user):
        # DM preview or admin: allow viewing any character
        row = db.execute("SELECT * FROM characters WHERE id = ?",
                         (char_id,)).fetchone()
    else:
        row = db.execute("SELECT * FROM characters WHERE id = ? AND (user_id = ? OR shared = 1)",
                         (char_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    for f in ("skills","features","inventory","equipped","languages","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
              "save_proficiencies","damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities",
              "expertise_skills", "asi_history", "metamagic_history",
              "metamagic", "invocations", "maneuvers", "magical_secrets", "infusions", "summons", "conditions", "favored_enemies", "favored_terrains"):
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
        # Enrich with damage dice from ITEM_INDEX
        if not inv_item.get("dice"):
            idx_entry = _resolve_item_key(inv_item.get("name", ""))
            if idx_entry and idx_entry.get("dice"):
                inv_item["dice"] = idx_entry["dice"]
        # Flag items that grant a bonus action ability.
        # Match only actual action-grant phrasings ("as a bonus action",
        # "using your bonus action", "...bonus action to..."), NOT mere
        # mentions like a Loading rule's "per action, bonus action, or
        # reaction" (that text made firearms appear as bonus-action items).
        _desc_ba = (inv_item.get("description") or "").lower()
        inv_item["bonus_action"] = _grants_bonus_action(_desc_ba)
        # Flag items that require concentration
        inv_item["concentration"] = "concentration" in _desc_ba
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
        # Enrich with damage dice
        if not eq_item.get("dice"):
            idx_entry = _resolve_item_key(eq_item.get("name", ""))
            if idx_entry and idx_entry.get("dice"):
                eq_item["dice"] = idx_entry["dice"]
        # Flag items that grant a bonus action ability
        _desc_ba_eq = (eq_item.get("description") or "").lower()
        eq_item["bonus_action"] = _grants_bonus_action(_desc_ba_eq)
        # Flag items that require concentration
        eq_item["concentration"] = "concentration" in _desc_ba_eq
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
    # First: normalize string-format features to dicts (legacy characters)
    if char["feature_data"] and isinstance(char["feature_data"][0], str):
        char["feature_data"] = enrich_features(
            char["feature_data"],
            class_name=char.get("class_name", ""),
            level=char.get("level", 1),
            mods={s: (char.get(s, 10) - 10) // 2 for s in ("strength","dexterity","constitution","intelligence","wisdom","charisma")},
            subclass=char.get("subclass", ""),
        )
    else:
        # Recompute limited-use data for already-enriched dict features
        # (uses_max, uses, recharge) so multiclass features like Action Surge
        # get their tracking even when stored as pre-enriched dicts.
        _cls_raw = char.get("class_levels", "{}") or "{}"
        try: _cls_data = json.loads(_cls_raw) if isinstance(_cls_raw, str) else (_cls_raw or {})
        except: _cls_data = {}
        _recalc_limited_uses(char.get("feature_data", []),
                             class_name=char.get("class_name", ""),
                             level=char.get("level", 1),
                             mods={s: (char.get(s, 10) - 10) // 2 for s in ("strength","dexterity","constitution","intelligence","wisdom","charisma")},
                             class_levels=_cls_data if len(_cls_data) > 1 else None,
                             subclass=char.get("subclass", ""))
    _add_cd_sub_options(char["feature_data"])
    _add_source_to_features(char["feature_data"])
    # Normalize feature names (base_name) and collapse duplicate level-variant
    # entries (e.g. "Brutal Critical (1 die)" @L9 + "(2 dice)" @L13) so template
    # dice badges match and stale variants don't double-render.
    char["feature_data"] = collapse_variant_features(char["feature_data"])
    # Level-scaled dice for limited-use (tracked) features the passive-loop
    # badges never reach (Rage damage bonus, Bardic Inspiration die).
    _cl = {}
    try:
        _cl = json.loads(char.get("class_levels", "{}") or "{}") if isinstance(char.get("class_levels"), str) else (char.get("class_levels") or {})
    except Exception:
        pass
    for _feat in char["feature_data"]:
        if not isinstance(_feat, dict) or _feat.get("dice"):
            continue
        _bn = (_feat.get("base_name") or "").lower()
        if _bn == "rage" and _cl.get("Barbarian"):
            _bl = int(_cl["Barbarian"])
            _feat["dice"] = "+4 dmg" if _bl >= 16 else ("+3 dmg" if _bl >= 9 else "+2 dmg")
        elif _bn == "bardic inspiration" and _cl.get("Bard"):
            _s = int(_cl["Bard"])
            _feat["dice"] = "d12" if _s >= 15 else ("d10" if _s >= 10 else ("d8" if _s >= 5 else "d6"))
    # Enrich features with dice badge data from FEATS/RACES lookup
    _add_dice_to_features(char["feature_data"])
    # Auto-detect reaction features and set action_type accordingly
    _add_reaction_type_to_features(char["feature_data"])
    # Inject Eldritch Invocation level cards for Warlocks (SRD only has L2)
    _add_invocation_levels(char["feature_data"], char.get("class_name", ""), char.get("level", 0))
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
                        # FEAT_BY_NAME uses space-separated keys, asi_history uses underscores
                        _finfo = FEAT_BY_NAME.get(_fkey.lower(), None)
                        if _finfo is None:
                            _finfo = FEAT_BY_NAME.get(_fkey.lower().replace("_", " "), {})
                        _feat["asi_feat_name"] = _finfo.get("name") or _fkey.replace("_", " ").title()
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
    # Inject orphaned feats: asi_history feats with no matching ASI feature_data entry, or enrich existing
    for _ae in (char.get("asi_history") or []):
        if _ae.get("type") == "feat":
            _lvl = _ae.get("level", 0)
            _fkey = _ae.get("feat", "")
            _finfo = FEAT_BY_NAME.get(_fkey.lower(), None)
            if _finfo is None:
                _finfo = FEAT_BY_NAME.get(_fkey.lower().replace("_", " "), {})
            _feat_name = _finfo.get("name") or _fkey.replace("_", " ").title()
            _feat_desc = _finfo.get("description", "") or _finfo.get("desc", "")
            # Try to enrich existing ASI entry first
            _existing = None
            for _ef in char["feature_data"]:
                if isinstance(_ef, dict) and "Ability Score Improvement" in _ef.get("name", ""):
                    try:
                        if int(str(_ef.get("level", "0")).replace("L", "")) == _lvl:
                            _existing = _ef
                            break
                    except (ValueError, TypeError):
                        pass
            if _existing is not None:
                _existing["asi_feat_name"] = _feat_name
                _existing["asi_feat_desc"] = _feat_desc
                _existing["source"] = _existing.get("source") or _finfo.get("source", "")
            else:
                # Orphaned feat — create new entry
                _new_asi = {
                    "name": "Ability Score Improvement",
                    "level": f"L{_lvl}",
                    "description": FEATURE_DESCRIPTIONS.get("ability score improvement", ""),
                    "source": _finfo.get("source", ""),
                    "asi_feat_name": _feat_name,
                    "asi_feat_desc": _feat_desc,
                }
                if _fkey == "magic_initiate":
                    _mi = _ae.get("magic_initiate", {})
                    if _mi:
                        _new_asi["magic_initiate"] = _mi
                _fc = _ae.get("feat_config")
                if _fc:
                    _new_asi["feat_config"] = _fc
                char["feature_data"].append(_new_asi)
    # Enrich with pool_kind from LIMITED_USE (so existing characters get Lay on Hands HP pool)
    for _feat in char["feature_data"]:
        if isinstance(_feat, dict) and not _feat.get("pool_kind"):
            _key = _feat.get("name", "").lower()
            for lkey, lu in LIMITED_USE.items():
                if lkey in _key and lu.get("pool_kind"):
                    _feat["pool_kind"] = lu["pool_kind"]
                    break
        # Enrich missing uses_max/recharge from LIMITED_USE for all features
        if isinstance(_feat, dict) and not _feat.get("uses_max"):
            _fname_lower = _feat.get("name", "").lower()
            if _fname_lower in LIMITED_USE:
                _lu = LIMITED_USE[_fname_lower]
                # Use get_uses_for_level for proper scaling (instead of raw max=99)
                import json as _json
                _cls_levels_raw = char.get("class_levels", "{}") or "{}"
                try:
                    _cls_levels = _json.loads(_cls_levels_raw) if isinstance(_cls_levels_raw, str) else (_cls_levels_raw or {})
                except (_json.JSONDecodeError, TypeError):
                    _cls_levels = {}
                _lu_class = _lu.get("class", "")
                _source_level = 0
                if _lu_class and _cls_levels:
                    _source_level = _cls_levels.get(_lu_class, 0)
                if not _source_level:
                    _source_level = char.get("level", 0)
                if _source_level > 0:
                    _uses = get_uses_for_level(_fname_lower, _lu_class or "", _source_level)
                    if _uses > 0:
                        _feat["uses_max"] = _uses
                        _feat["uses"] = _uses
                # Fallback: use max from LIMITED_USE if get_uses_for_level returned 0
                if not _feat.get("uses_max"):
                    _feat["uses_max"] = _lu.get("max", 1)
                    _feat["uses"] = _lu.get("max", 1)
                if _lu.get("recharge"):
                    _feat["recharge"] = _lu["recharge"]
                # Special case: Divine Sense adds CHA mod
                if _fname_lower == "divine sense":
                    _cha = char.get("charisma", 10)
                    _cha_mod = (_cha - 10) // 2
                    _feat["uses_max"] = max(1, _feat["uses_max"] + _cha_mod)
                    _feat["uses"] = max(1, _feat.get("uses", 0) + _cha_mod)
        # Enrich racial traits missing uses_max (set from uses if present)
        if isinstance(_feat, dict) and _feat.get("uses") and not _feat.get("uses_max"):
            _feat["uses_max"] = _feat["uses"]
        # Enrich action_type from FEATURE_ACTION_TYPES for features missing it
        if isinstance(_feat, dict):
            if not _feat.get("action_type"):
                _key = _feat.get("name", "").lower()
                import re as _re
                _clean_key = _re.sub(r'\s*\([^)]*\)\s*$', '', _key).strip()
                _action_info = FEATURE_ACTION_TYPES.get(_clean_key) or FEATURE_ACTION_TYPES.get(_key)
                # Fallback: composite Channel Divinity names (e.g. "channel divinity: abjure enemy | l3: channel divinity: vow of enmity")
                if not _action_info and "channel divinity" in _clean_key:
                    _action_info = FEATURE_ACTION_TYPES.get("channel divinity")
                if _action_info:
                    _feat["action_type"] = _action_info[0]
                    _feat["action_desc"] = _action_info[1]
            else:
                # Normalize legacy action_type formats to canonical title-case
                _at = _feat["action_type"].strip()
                if _at.lower() in ("bonus action", "bonus_action"):
                    _feat["action_type"] = "Bonus Action"
                elif _at.lower() in ("action", "use an action"):
                    _feat["action_type"] = "Action"
                elif _at.lower() in ("reaction",):
                    _feat["action_type"] = "Reaction"
    # Inject missing racial limited-use features (they may not be in feature_data)
    _existing_names = {f.get("name", "").lower() for f in char["feature_data"] if isinstance(f, dict)}
    _race = char.get("race", "")
    _subrace = char.get("subrace", "")
    _char_level = char.get("level", 1)
    for _rf in _build_racial_limited_features(_race, _subrace, _char_level):
        _rf_name = _rf.split(": ", 1)[1] if ": " in _rf else _rf
        if _rf_name.lower() not in _existing_names:
            _new_feat = {
                "name": _rf_name,
                "level": str(_char_level),
                "description": RACIAL_TRAIT_DESCS.get(_rf_name, "") or "",
                "source": f"Race: {_race or 'Unknown'}",
                "uses": 1,
                "uses_max": 1,
            }
            # Enrich with LIMITED_USE data
            _rf_key = _rf_name.lower()
            if _rf_key in LIMITED_USE:
                _lu = LIMITED_USE[_rf_key]
                _new_feat["uses_max"] = _lu.get("max", 1)
                _new_feat["uses"] = _lu.get("max", 1)
                _new_feat["recharge"] = _lu.get("recharge", "long")
            # Enrich action_type
            _action_info = FEATURE_ACTION_TYPES.get(_rf_key, None)
            if _action_info:
                _new_feat["action_type"] = _action_info[0]
                _new_feat["action_desc"] = _action_info[1]
            char["feature_data"].append(_new_feat)
    # Fallback: features still without source inherit from class or subclass
    # For multiclass, assign per-class sources using SRD class level data
    # Build per-class feature→source map from SRD class_levels data
    _cl_data = parse_class_levels(char)
    _cls_sources = {}
    _feature_to_class = {}
    _subclass = char.get("subclass", "")
    _sub_source = ""
    _subclass_feature_names = set()
    if _subclass:
        for _cls in (_cl_data or {char.get("class_name","Fighter"): char.get("level",1)}):
            _src = CLASSES.get(_cls, {}).get("_subclass_sources", {}).get(_subclass, "")
            if _src:
                _sub_source = _src
                break
        if _subclass in SUBCLASS_FEATURES:
            for _lvl_feats in SUBCLASS_FEATURES[_subclass].values():
                _subclass_feature_names.update(_lvl_feats)
    for _cls in (_cl_data or {char.get("class_name","Fighter"): char.get("level",1)}):
        _cls_lower = _cls.lower()
        _cls_sources[_cls] = CLASSES.get(_cls, {}).get("source", "")
        for _entry in SRD_LEVELS.get(_cls_lower, []):
            for _f in _entry.get("features", []):
                _fname = _f.get("name", "")
                if _fname and _fname not in _feature_to_class:
                    _feature_to_class[_fname] = _cls
    # Primary class source as fallback
    _primary_source = CLASSES.get(char.get("class_name", "Fighter"), {}).get("source", "")
    # Assign sources
    for _feat in char["feature_data"]:
        if not _feat.get("source") or _feat.get("source") in ("SRD 5.1", "PHB 2014"):
            _fname = _feat.get("name", "")
            # Check subclass features first
            if _sub_source and _fname in _subclass_feature_names:
                _feat["source"] = _sub_source
            else:
                # Look up which class owns this feature via SRD class levels
                _owning_cls = _feature_to_class.get(_fname)
                if _owning_cls and _cls_sources.get(_owning_cls):
                    _feat["source"] = _cls_sources[_owning_cls]
                elif _primary_source:
                    _feat["source"] = _primary_source
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
    try:
        char["personality_data"] = json.loads(char.get("personality_data") or "{}")
    except (json.JSONDecodeError, TypeError):
        char["personality_data"] = {}

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

    # Compute advantage map
    compute_advantage_map(char)
    advantage_map = char.get("advantage_map", {})

    # Build attacks from inventory weapons + natural weapons (race traits)
    all_attacks = _build_character_attacks(char)
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

    # Darkvision: racial + item-granted (Goggles of Night etc.). Item effect
    # says +60 when you already have darkvision; otherwise grants 60.
    race_dv = racial_effects.get("darkvision") or 0
    item_dv = item_effects.get("darkvision") or 0
    if item_dv:
        darkvision_ft = race_dv + item_dv if race_dv else item_dv
    else:
        darkvision_ft = race_dv
    # Fallback for races whose core data lists darkvision but trait-effects
    # map doesn't capture it (e.g. some manual races) — trust the race data.
    if not darkvision_ft:
        _rd = RACES.get(char.get("race", ""), {})
        darkvision_ft = _rd.get("darkvision") or 0
    darkvision_source = []
    if race_dv:
        darkvision_source.append(f"{char.get('race', '')} (racial)")
    if item_dv:
        darkvision_source.append("Equipped item")
    darkvision_source = ", ".join(darkvision_source)

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

    # Compute invocations per level from the flat list + level/picks data
    invocations_by_level: dict[int, list[str]] = {}
    inv_flat = char.get("invocations", [])
    if inv_flat:
        inv_levels = INVOCATION_LEVELS.get(char.get("class_name", ""), [])
        if inv_levels:
            offset = 0
            for lvl in inv_levels:
                picks = INVOCATION_PICKS.get(lvl, 0)
                if picks > 0 and offset < len(inv_flat):
                    invocations_by_level[lvl] = inv_flat[offset:offset + picks]
                    offset += picks

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

    # ── Merge manual race data into races dict for description popup ──
    merged_races = dict(RACES)
    # Also add common race name aliases (plurals, alternate names)
    _aliases = {
        "elves of mirkwood": "Mirkwood Elf",
        "hobbits of the shire": "Hobbit of the Shire",
        "hobbit of the shire": "Hobbit of the Shire",
        "high elves of rivendell": "High Elf of Rivendell",
    }
    for _mr in _MANUAL_RACES_RAW:
        merged_races[_mr["name"]] = {
            "desc": _mr.get("description", ""),
            "source": _mr.get("source", ""),
            "asi": _mr.get("asi", {}),
        }
    # Alias entries so "Elves of Mirkwood" → "Mirkwood Elf" data
    # Also store title-cased fallback key for case-insensitive JS lookup
    for _alias, _target in _aliases.items():
        if _alias in merged_races:
            continue
        if _target in merged_races:
            merged_races[_alias] = merged_races[_target]
        else:
            # Target may be a migrated subrace (e.g. Mirkwood Elf subrace of Elf)
            # Find which parent race owns it and pull the subrace description
            for _parent_name, _parent_data in merged_races.items():
                if _target in _parent_data.get("subraces", []):
                    _sr_desc = _parent_data.get("subrace_descs", {}).get(_target, "")
                    _sr_src = _parent_data.get("_subrace_sources", {}).get(_target, "")
                    _entry = {
                        "desc": _sr_desc,
                        "source": _sr_src,
                        "subrace": _target,
                    }
                    merged_races[_alias] = _entry
                    # Also store title-cased version for matching character.race
                    merged_races[_alias.title()] = _entry
                    break

    return _render("sheet.html", request=request, character=char, spells=spells,
                   mi_spells_data=mi_spells_data,
                   dm_preview=dm_preview,
                   skill_abilities=SKILL_ABILITIES, classes=CLASSES, races=merged_races,
                   bg_info=BACKGROUND_INFO, saves_class=saves_class, attacks=all_attacks,
                   charged_items=charged_items,
                   named_item_types=_get_named_item_types(),
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
                   darkvision_ft=darkvision_ft, darkvision_source=darkvision_source,
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
                   source_map_json=json.dumps(_get_source_slug_map()),
                   invocation_levels=INVOCATION_LEVELS,
                   invocation_picks=INVOCATION_PICKS,
                   invocations_by_level=invocations_by_level,
                   invocation_options=INVOCATION_OPTIONS,
                   pact_boon_options=PACT_BOON_OPTIONS,
                   summon_templates=SUMMON_TEMPLATES,
                   advantage_map=advantage_map,
                   current_user_id=user["id"],
                   is_owner=char.get("user_id") == user["id"])

# ── Routes: Live Session API ───────────────────────────────────────────────

@router.post("/api/character/{char_id}/update", response_class=JSONResponse)
async def update_character(char_id: int, request: Request, body: UpdateCharacter):
    user = require_user(request)
    # Build data dict from model (extra fields pass through)
    data = body.model_dump(exclude_none=True) | (body.model_extra or {})

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
                "personality", "backstory", "journal",
        "skills","save_proficiencies","tool_proficiencies","weapon_proficiencies","armor_proficiencies",
        "languages","features","inventory","spell_slots_used","equipped","feature_data","attacks_data",
        "damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities",
        "attuned_items", "expertise_skills", "fighting_style",
        "cp",
        "gp",
        "dragonborn_ancestry",
        "summons",
        "background_data",
        "personality_data",
    }
    updates = {}
    for k, v in data.items():
        if k in allowed:
            updates[k] = v

    if "inventory" in updates:
        import logging as _invlog
        _invlog.getLogger(__name__).info(
            f"INVENTORY SAVE char={char_id} user={user['id']} "
            f"value_len={len(str(updates['inventory']))} "
            f"sample={str(updates['inventory'])[:200]}"
        )

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

@router.post("/api/character/{char_id}/edit-asi", response_class=JSONResponse)
async def edit_asi_choice(char_id: int, request: Request, body: EditASI):
    """Edit a past ASI/feat choice for a given level."""
    user = require_user(request)
    if body.entry is None:
        return JSONResponse({"error": "Missing entry"}, status_code=400)

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    char = dict(row)
    asi_history = json.loads(char.get("asi_history", "[]") or "[]")

    # Ensure entry has level set before saving
    body.entry["level"] = body.level

    # Replace the entry for this level, or append if not found
    # Also match old entries that lack a level field (legacy data)
    found = False
    for i, ae in enumerate(asi_history):
        if ae.get("level") == body.level:
            asi_history[i] = body.entry
            found = True
            break
    if not found:
        asi_history.append(body.entry)

    db.execute(
        "UPDATE characters SET asi_history=? WHERE id=?",
        (json.dumps(asi_history), char_id)
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "asi_history": asi_history})

@router.post("/api/character/{char_id}/edit-expertise", response_class=JSONResponse)
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

@router.get("/api/character/{char_id}/attacks", response_class=JSONResponse)
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
    attacks = _build_character_attacks(char)
    return JSONResponse({"attacks": attacks})

@router.get("/api/character/{char_id}/summons", response_class=JSONResponse)
async def get_summons(char_id: int, request: Request):
    """Return character's active summons for combat tab integration."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    db.close()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        summons = json.loads(row["summons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        summons = []
    return JSONResponse({"summons": summons, "char_name": row["name"]})

@router.post("/api/character/{char_id}/summons", response_class=JSONResponse)
async def create_summon(char_id: int, request: Request):
    """Create a new summon for a character. Returns the created summon object."""
    user = require_user(request)
    data = await request.json()
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        summons = json.loads(row["summons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        summons = []

    summon = {
        "id": "summon_" + str(int(time.time() * 1000)),
        "name": data.get("name", "Unnamed"),
        "form": data.get("form", ""),
        "category": data.get("category", "custom"),
        "source": data.get("source", "custom"),
        "ac": data.get("ac", 10),
        "hp_max": data.get("hp_max", 1),
        "hp_current": data.get("hp_current", data.get("hp_max", 1)),
        "size": data.get("size", "Medium"),
        "speed": data.get("speed", "30 ft."),
        "stats": data.get("stats", {}),
        "features": data.get("features", []),
        "attacks": data.get("attacks", []),
        "skills": data.get("skills", ""),
        "senses": data.get("senses", ""),
        "hp_note": data.get("hp_note", ""),
    }
    summons.append(summon)
    db.execute("UPDATE characters SET summons=? WHERE id=? AND user_id=?",
               (json.dumps(summons), char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"summon": summon, "total": len(summons)})

@router.post("/api/character/{char_id}/update-summon-hp", response_class=JSONResponse)
async def update_summon_hp(char_id: int, request: Request):
    """Update a single summon's HP by index. Used by combat page to sync back."""
    user = require_user(request)
    data = await request.json()
    idx = data.get("summon_idx")
    hp = data.get("hp_current")
    if idx is None or hp is None:
        return JSONResponse({"error": "Missing summon_idx or hp_current"}, status_code=400)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        summons = json.loads(row["summons"] or "[]")
    except (json.JSONDecodeError, TypeError):
        summons = []
    if idx < 0 or idx >= len(summons):
        db.close()
        return JSONResponse({"error": "Invalid summon index"}, status_code=400)
    summons[idx]["hp_current"] = max(0, hp)
    db.execute("UPDATE characters SET summons=? WHERE id=? AND user_id=?",
               (json.dumps(summons), char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "hp_current": summons[idx]["hp_current"]})

# ── Conditions (CRUD) ─────────────────────────────────────────────
@router.get("/api/character/{char_id}/conditions", response_class=JSONResponse)
async def get_conditions(char_id: int, request: Request):
    """Get active conditions for a character."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    db.close()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        conditions = json.loads(row["conditions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        conditions = []
    return JSONResponse({"conditions": conditions, "char_name": row["name"]})

@router.post("/api/character/{char_id}/conditions", response_class=JSONResponse)
async def add_condition(char_id: int, request: Request):
    """Add a condition to a character."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Condition name required"}, status_code=400)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        conditions = json.loads(row["conditions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        conditions = []
    # Don't add duplicate
    existing = [c for c in conditions if c.get("name","").lower() == name.lower()]
    if existing:
        db.close()
        return JSONResponse({"conditions": conditions, "duplicate": True})
    condition = {
        "name": name,
        "description": data.get("description", ""),
        "source": data.get("source", ""),
        "applied_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    conditions.append(condition)
    db.execute("UPDATE characters SET conditions=? WHERE id=? AND user_id=?",
               (json.dumps(conditions), char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"conditions": conditions, "added": condition})

@router.delete("/api/character/{char_id}/conditions", response_class=JSONResponse)
async def remove_condition(char_id: int, request: Request):
    """Remove a condition by name."""
    user = require_user(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Condition name required"}, status_code=400)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    try:
        conditions = json.loads(row["conditions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        conditions = []
    conditions = [c for c in conditions if c.get("name","").lower() != name.lower()]
    db.execute("UPDATE characters SET conditions=? WHERE id=? AND user_id=?",
               (json.dumps(conditions), char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"conditions": conditions, "removed": name})


@router.post("/api/sync-combat-hp", response_class=JSONResponse)
async def sync_combat_hp(request: Request):
    """Batch fetch HP + summons for characters in combat. Takes {char_ids: [...]}."""
    user = require_user(request)
    data = await request.json()
    char_ids = data.get("char_ids", [])
    if not char_ids:
        return JSONResponse({"characters": {}})
    db = get_db()
    result = {}
    for cid in char_ids:
        row = db.execute(
            "SELECT name, hp_current, hp_max, summons, conditions FROM characters WHERE id=? AND user_id=?",
            (cid, user["id"])
        ).fetchone()
        if not row:
            continue
        try:
            summons = json.loads(row["summons"] or "[]")
        except (json.JSONDecodeError, TypeError):
            summons = []
        try:
            conditions = json.loads(row["conditions"] or "[]")
        except (json.JSONDecodeError, TypeError):
            conditions = []
        result[str(cid)] = {
            "name": row["name"],
            "hp_current": row["hp_current"],
            "hp_max": row["hp_max"],
            "summons": summons,
            "conditions": conditions,
        }
    db.close()
    return JSONResponse({"characters": result})

@router.post("/api/character/{char_id}/spend-charge", response_class=JSONResponse)
async def spend_charge(char_id: int, request: Request):
    """Spend one or more charges from an equipped charged item."""
    user = require_user(request)
    data = await request.json()
    item_name = (data.get("name") or "").strip()
    amount = int(data.get("amount", 1))
    if amount < 1:
        amount = 1
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
            # Cap amount: don't go below 0 available
            info = _resolve_item_key(item_name.lower())
            max_charges = info.get("charges", 0) if info else 0
            available = max(0, max_charges - used)
            spend = min(amount, available)
            if spend < 1:
                item["charges_used"] = max_charges  # fully deplete
            else:
                item["charges_used"] = used + spend
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


@router.post("/api/character/{char_id}/reload-charge", response_class=JSONResponse)
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

@router.post("/api/character/{char_id}/delete", response_class=JSONResponse)
async def delete_character(char_id: int, request: Request):
    user = require_user(request)
    db = get_db()
    filter_clause, filter_params = _user_filter(user)
    db.execute(f"DELETE FROM characters WHERE id = ? {filter_clause}", (char_id, *filter_params))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


@router.post("/api/character/{char_id}/toggle-share", response_class=JSONResponse)
async def character_toggle_share(char_id: int, request: Request):
    """Toggle public sharing on a character (owner only)."""
    user = require_user(request)
    data = await request.json()
    shared = 1 if data.get("shared", False) else 0
    db = get_db()
    db.execute("UPDATE characters SET shared=? WHERE id=? AND user_id=?",
               (shared, char_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "shared": bool(shared)})

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

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
    if _item.get("dice"):
        ITEM_INDEX[_key]["dice"] = _item["dice"]
    _equipment_added += 1
if _equipment_added:
    print(f"  Manual equipment added to index: {_equipment_added}")

# ── PHB scale functions per feature ──


def get_spellcasting_mod(class_name: str, mods: dict) -> int:
    """Return the spellcasting ability modifier for this class (PHB p.xxx)."""
    if class_name in ("Bard", "Paladin", "Sorcerer", "Warlock"):
        return mods.get("charisma", 0)
    if class_name in ("Cleric", "Druid", "Ranger"):
        return mods.get("wisdom", 0)
    if class_name in ("Wizard",):
        return mods.get("intelligence", 0)
    return 0


# ── Spell enrichment (SRD descriptions) ───────────────────────────────────


# ── Spells also available as tiered recommendations (from SRD cache) ──────────

def get_spells_for_level(class_name: str, level: int) -> dict:
    """Get recommended spells for this class from SRD data."""
    return get_srd_spells_for_class(class_name, level)

# Feats by class tier (PHB-optimal picks at ASI levels 4,8,12,16,19)
# Scaled equipment by class and level tier (PHB starting equipment + reasonable progression)
# Levels: 1, 5, 10, 15, 20
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
# ── Treasure hoard data & helpers (moved to campaign.py to avoid circular import) ──
from routes.characters.campaign import roll_treasure_hoard, TREASURE_HOARD_COINS, TREASURE_HOARD_TABLE, MAGIC_TABLE_POOLS, _roll_dice, _pick_magic_item

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
        "goggles of night": {"darkvision": 60, "note": "Darkvision 60 ft (if you already have darkvision, its range increases by 60 ft)."},
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
        "darkvision": 0,
    }
    attuned_set = set(a.lower() for a in attuned)

    # ── Armor/shield keywords for enhancement detection ──
    _ARMOR_KEYWORDS = [
        "padded", "leather", "studded", "hide", "chain shirt", "scale mail",
        "breastplate", "half plate", "ring mail", "chain mail", "splint",
        "plate", "shield", "armor",
    ]

    for item in equipped:
        # Normalize: item may be string or dict
        if isinstance(item, dict):
            item_name = item.get("name", "")
            enhancement = item.get("enhancement", 0)
        else:
            item_name = str(item)
            enhancement = _parse_enhancement(item_name)

        key = item_name.lower()
        props = ITEM_PROPERTIES.get(key, {})
        if not props:
            # Check for armor/shield with enhancement (e.g. "Studded Leather +1")
            if enhancement:
                is_armor_item = any(kw in key for kw in _ARMOR_KEYWORDS)
                if is_armor_item:
                    result["ac_bonus"] += enhancement
                    result["notes"].append(f"{item_name}: +{enhancement} enhancement AC")
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

        # Darkvision (item grants/extends darkvision)
        dv = props.get("darkvision", 0)
        if dv:
            result["darkvision"] = max(result["darkvision"], int(dv))

        # Notes
        if props.get("note"):
            result["notes"].append(f"{item_name}: {props['note']}")

    return result

# Initialize item properties at module load
_build_item_properties()


def _recalc_limited_uses(feature_data: list, class_name: str = "", level: int = 0,
                         mods: dict = None, class_levels: dict = None,
                         subclass: str = "") -> None:
    """Recompute uses_max/uses/recharge on already-enriched dict features.
    Needed for multiclass characters where stored feature_data was enriched
    before the class_levels fix — features like Action Surge end up with
    the wrong source class and no limited-use tracking."""
    from main import LIMITED_USE, FEATURE_ACTION_TYPES, FEATURE_DESCRIPTIONS
    if not feature_data:
        return
    import re
    for feat in feature_data:
        if not isinstance(feat, dict):
            continue
        name = feat.get("name", "")
        key = name.lower()
        _strip_key = re.sub(r'\s*\([^)]*\)\s*$', '', key).strip()

        # Infer source class + level (same logic as enrich_features)
        source_class = None
        source_level = 0
        if class_levels and len(class_levels) > 1:
            for cls_name in class_levels:
                if cls_name.lower() in _strip_key or _strip_key in cls_name.lower():
                    source_class = cls_name
                    source_level = class_levels[cls_name]
                    break
            if not source_class:
                for lkey, lu in LIMITED_USE.items():
                    if lkey == _strip_key or lkey in _strip_key or _strip_key.startswith(lkey) or lkey.startswith(_strip_key):
                        lu_class = lu.get("class", "")
                        if lu_class and lu_class in class_levels:
                            source_class = lu_class
                            source_level = class_levels[lu_class]
                            break
        if not source_class:
            source_class = class_name
            source_level = class_levels.get(class_name, level) if class_levels else level

        # Compute limited-use values
        if source_class and source_level > 0:
            _NON_LIMITED = {"wild magic surge", "bend luck", "controlled chaos",
                "spell bombardment", "totem spirit", "aspect of the beast",
                "totemic attunement", "thunderbolt strike", "stormborn",
                "dragon wings", "awakened mind", "the third eye", "greater portent",
                "minor conjuration", "hypnotic gaze", "alter memories",
                "improved minor illusion", "illusory reality",
                "minor alchemy", "transmuter's stone",
                "mage hand legerdemain", "magical ambush", "versatile trickster"}
            if _strip_key not in _NON_LIMITED:
                _FEAT_ALIASES = {"font of magic": "sorcery points"}
                for lkey, lu in LIMITED_USE.items():
                    _match_key = _FEAT_ALIASES.get(_strip_key, _strip_key)
                    if lkey in _match_key or _match_key.startswith(lkey) or lkey.startswith(_match_key):
                        uses_max = get_uses_for_level(lkey, source_class, source_level)
                        if uses_max > 0:
                            if lkey == "divine sense":
                                cha_mod = (mods or {}).get("charisma", 0)
                                uses_max = max(1, uses_max + cha_mod)
                            if lkey == "cleansing touch":
                                cha_mod = (mods or {}).get("charisma", 0)
                                uses_max = max(1, uses_max + cha_mod - 1)
                            if lu.get("per") == "wis":
                                wis_mod = (mods or {}).get("wisdom", 0)
                                uses_max = max(1, wis_mod)
                            feat["uses_max"] = uses_max
                            feat["uses"] = uses_max
                            feat["recharge"] = lu["recharge"]
                            if lu.get("pool_kind"):
                                feat["pool_kind"] = lu["pool_kind"]
                        break


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


def _add_reaction_type_to_features(feature_data: list[dict]) -> None:
    """Auto-detect reaction features and set action_type='Reaction' on them.
    Scans feature descriptions for reaction keywords so unlimited-use
    reactions (Uncanny Dodge, Deflect Missiles, Slow Fall, etc.) appear
    in the Actions in Combat section alongside limited-use features."""
    import re
    _pattern = re.compile(
        r'(use|using|as|with|spend|expend)\s+(your\s+)?(a\s+)?reaction',
        re.IGNORECASE
    )
    for feat in feature_data:
        if feat.get("action_type"):
            continue  # already has a type
        desc = feat.get("description", "") or ""
        name = feat.get("name", "") or ""
        # Only flag if the description explicitly says it uses a reaction
        # (avoid false positives from passing mentions)
        if _pattern.search(desc):
            feat["action_type"] = "Reaction"


def collapse_variant_features(feature_data: list[dict]) -> list[dict]:
    """Drop duplicate level-variant entries (e.g. leveling stored both
    "Brutal Critical (1 die)" @L9 and "(2 dice)" @L13): keep the highest-level
    variant of any feature whose name differs only by a parenthetical suffix.
    Repeatable picks (Expertise, ASIs, invocations, etc.) have no suffix so
    they are untouched by this rule. Also sets feat["base_name"] to the
    suffix-stripped name so template dice badges and level checks match even
    when a suffixed variant of a known feature was stored."""
    for _feat in feature_data:
        if isinstance(_feat, dict) and _feat.get("name"):
            _feat["base_name"] = _feat["name"].split(" (")[0]
    _variant_groups: dict[str, list[dict]] = {}
    for _feat in feature_data:
        if isinstance(_feat, dict) and _feat.get("base_name"):
            _variant_groups.setdefault(_feat["base_name"], []).append(_feat)
    _level_num = lambda _f: int(str(_f.get("level", "L0")).replace("L", "") or 0)
    _drop_ids: set[int] = set()
    for _base, _members in _variant_groups.items():
        if len(_members) < 2:
            continue
        _suffixed = [m for m in _members if " (" in (m.get("name") or "")]
        _plain = [m for m in _members if " (" not in (m.get("name") or "")]
        if not _suffixed or len(_plain) >= 1:
            # no variant group, or a plain base entry exists (distinct feature) — skip
            continue
        _best = max(_members, key=lambda m: (_level_num(m), len(m.get("name") or "")))
        for _m in _members:
            if _m is _best:
                continue
            if _level_num(_m) < _level_num(_best):
                _drop_ids.add(id(_m))
    if _drop_ids:
        return [f for f in feature_data if id(f) not in _drop_ids]
    return feature_data


def _add_dice_to_features(feature_data: list[dict]) -> None:
    """Mutate feature_data in-place: add 'dice' field from FEATS lookup.
    Only adds if feat doesn't already have a dice field (preserves hardcoded values)."""
    for feat in feature_data:
        if feat.get("dice"):
            continue  # Already has dice (hardcoded or from previous enrichment)
        name = feat.get("name", "")
        key = name.lower()
        # Look up in FEATS dict
        _feat = FEATS.get(key)
        if _feat and _feat.get("dice"):
            feat["dice"] = _feat["dice"]

def _add_invocation_levels(feature_data: list[dict], class_name: str, char_level: int) -> None:
    """Inject Eldritch Invocation level cards for Warlocks.

    The SRD only lists 'Eldritch Invocations' at level 2, but warlocks gain
    additional invocations at 5, 7, 9, 12, 15, and 18. This adds a feature
    card at each level where invocations are gained, so per-level choices
    can be displayed separately."""
    if class_name != "Warlock":
        return
    inv_levels = INVOCATION_LEVELS.get("Warlock", [])
    existing_levels = {
        int(f["level"].replace("L", ""))
        for f in feature_data
        if f.get("name") == "Eldritch Invocations"
    }
    for inv_lvl in inv_levels:
        if inv_lvl <= char_level and inv_lvl not in existing_levels:
            # Insert at correct position
            insert_at = 0
            for i, f in enumerate(feature_data):
                fl = int(f.get("level", "L0").replace("L", ""))
                if fl > inv_lvl:
                    insert_at = i
                    break
                insert_at = i + 1
            feature_data.insert(insert_at, {
                "name": "Eldritch Invocations",
                "level": f"L{inv_lvl}",
                "description": f"Gain additional Eldritch Invocations at level {inv_lvl}.",
            })