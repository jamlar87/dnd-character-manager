"""Pure sheet domain helpers: the invocation-name set, the
bonus-action detector, the advantage map and the race merge used by the page.
No routes here.

Split out of the single-module sheet.py; behaviour and URLs are unchanged.
"""

import json

from core.data import RACES, _manual_races_raw

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

def _merged_races_for_popup() -> dict:
    """RACES + manual races + alias keys — the lookup table the detail popup uses.

    Shared by the sheet route and the cached reference asset so both serve the
    same table (a private copy would drift).
    """
    merged_races = dict(RACES)
    _aliases = {
        "elves of mirkwood": "Mirkwood Elf",
        "hobbits of the shire": "Hobbit of the Shire",
        "hobbit of the shire": "Hobbit of the Shire",
        "high elves of rivendell": "High Elf of Rivendell",
    }
    for _mr in _manual_races_raw:
        merged_races[_mr["name"]] = {
            "desc": _mr.get("description", ""),
            "source": _mr.get("source", ""),
            "asi": _mr.get("asi", {}),
        }
    for _alias, _target in _aliases.items():
        if _alias in merged_races:
            continue
        if _target in merged_races:
            merged_races[_alias] = merged_races[_target]
        else:
            for _parent_name, _parent_data in merged_races.items():
                if _target in _parent_data.get("subraces", []):
                    _entry = {
                        "desc": _parent_data.get("subrace_descs", {}).get(_target, ""),
                        "source": _parent_data.get("_subrace_sources", {}).get(_target, ""),
                        "subrace": _target,
                    }
                    merged_races[_alias] = _entry
                    merged_races[_alias.title()] = _entry
                    break
    return merged_races
