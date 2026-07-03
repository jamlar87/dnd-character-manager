"""D&D 5e data constants — pure data, no logic.

Extracted from main.py to keep concerns separate.
All dicts/list definitions only — no functions, no imports from project code.
"""

# ── PHB Core Constants ────────────────────────────────────────────────

ABILITY_NAMES = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]

SKILL_ABILITIES = {
    "Acrobatics": "dexterity", "Animal Handling": "wisdom", "Arcana": "intelligence",
    "Athletics": "strength", "Deception": "charisma", "History": "intelligence",
    "Insight": "wisdom", "Intimidation": "charisma", "Investigation": "intelligence",
    "Medicine": "wisdom", "Nature": "intelligence", "Perception": "wisdom",
    "Performance": "charisma", "Persuasion": "charisma", "Religion": "intelligence",
    "Sleight of Hand": "dexterity", "Stealth": "dexterity", "Survival": "wisdom",
}

ALL_SKILLS = sorted(SKILL_ABILITIES.keys())

LANGUAGES = [
    "Common", "Dwarvish", "Elvish", "Giant", "Gnomish", "Goblin", "Halfling", "Orc",
    "Abyssal", "Celestial", "Draconic", "Deep Speech", "Infernal", "Primordial",
    "Sylvan", "Undercommon",
]

BACKGROUNDS = [
    "Acolyte", "Charlatan", "Criminal", "Entertainer", "Folk Hero", "Guild Artisan",
    "Hermit", "Noble", "Outlander", "Sage", "Sailor", "Soldier", "Urchin", "Custom",
]

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

ALIGNMENTS = [
    "Lawful Good", "Neutral Good", "Chaotic Good",
    "Lawful Neutral", "True Neutral", "Chaotic Neutral",
    "Lawful Evil", "Neutral Evil", "Chaotic Evil",
    "Unaligned",
]

# ── Draconic Ancestry (PHB p.34) ──────────────────────────────────────

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

# ── Starting Equipment by Class ────────────────────────────────────────

STARTING_EQUIPMENT = {
    "Barbarian": ["Greataxe", "2 Handaxes", "Explorer's Pack", "4 Javelins"],
    "Bard":      ["Rapier", "Entertainer's Pack", "Lute", "Leather Armor", "Dagger"],
    "Cleric":    ["Mace", "Scale Mail", "Light Crossbow + 20 Bolts", "Priest's Pack", "Shield", "Holy Symbol"],
    "Druid":     ["Wooden Shield", "Scimitar", "Leather Armor", "Explorer's Pack", "Druidic Focus"],
    "Fighter":   ["Chain Mail", "Longsword", "Shield", "Light Crossbow + 20 Bolts", "Dungeoneer's Pack"],
    "Monk":      ["Shortsword", "Dungeoneer's Pack", "10 Darts"],
    "Paladin":   ["Longsword", "Shield", "5 Javelins", "Priest's Pack", "Chain Mail", "Holy Symbol"],
    "Ranger":    ["Longbow + 20 Arrows", "Shortsword", "Scale Mail", "Explorer's Pack"],
    "Rogue":     ["Rapier", "Shortbow + 20 Arrows", "Burglar's Pack", "Leather Armor", "2 Daggers", "Thieves' Tools"],
    "Sorcerer":  ["Light Crossbow + 20 Bolts", "Arcane Focus", "Dungeoneer's Pack", "2 Daggers"],
    "Warlock":   ["Light Crossbow + 20 Bolts", "Arcane Focus", "Scholar's Pack", "Leather Armor", "Dagger"],
    "Wizard":    ["Quarterstaff", "Arcane Focus", "Scholar's Pack", "Spellbook"],
}

# ── ASI Levels by Class (PHB 2014) ────────────────────────────────────

ASI_LEVELS = {
    "Barbarian": [4, 8, 12, 16, 19],
    "Bard":      [4, 8, 12, 16, 19],
    "Cleric":    [4, 8, 12, 16, 19],
    "Druid":     [4, 8, 12, 16, 19],
    "Fighter":   [4, 6, 8, 12, 14, 16, 19],
    "Monk":      [4, 8, 12, 16, 19],
    "Paladin":   [4, 8, 12, 16, 19],
    "Ranger":    [4, 8, 12, 16, 19],
    "Rogue":     [4, 8, 10, 12, 16, 19],
    "Sorcerer":  [4, 8, 12, 16, 19],
    "Warlock":   [4, 8, 12, 16, 19],
    "Wizard":    [4, 8, 12, 16, 19],
}

# ── Caster Type Classification (PHB 2014) ─────────────────────────────

FULL_CASTERS = {"Bard", "Cleric", "Druid", "Sorcerer", "Wizard"}
HALF_CASTERS = {"Paladin", "Ranger"}
PACT_CASTERS = {"Warlock"}
PREPARED_CASTERS = {"Cleric", "Druid", "Paladin", "Wizard"}
SPELLS_KNOWN_CASTERS = {"Bard", "Ranger", "Sorcerer", "Warlock"}

# ── Feat Descriptions ─────────────────────────────────────────────────

FEATS = {
    "alert":                {"name":"Alert",                "desc":"+5 initiative, can't be surprised, creatures don't get advantage from being hidden"},
    "athlete":              {"name":"Athlete",              "desc":"+1 Str or Dex, stand up with 5 ft of movement, climb at full speed, long/jump with 1 ft of running start"},
    "actor":                {"name":"Actor",                "desc":"+1 Cha, advantage on Deception/Performance to pass as someone else, mimic speech"},
    "charger":              {"name":"Charger",              "desc":"Dash + bonus action melee attack: +5 dmg or shove 10 ft"},
    "crossbow_expert":      {"name":"Crossbow Expert",     "desc":"Ignore loading property, no disadvantage in melee range, bonus hand crossbow attack"},
    "defensive_duelist":    {"name":"Defensive Duelist",   "desc":"+Prof bonus to AC vs melee with finesse weapon, reaction"},
    "dual_wielder":         {"name":"Dual Wielder",        "desc":"+1 AC when two-weapon fighting, use non-light one-handed, draw two at once"},
    "dungeon_delver":       {"name":"Dungeon Delver",      "desc":"Advantage on Perception/Trap finding, resistance to trap damage, faster trap searching"},
    "durable":              {"name":"Durable",             "desc":"+1 Con, HD minimum heal = 2× Con mod"},
    "elemental_adept":      {"name":"Elemental Adept",     "desc":"Ignore resistance to chosen element, treat 1s as 2s on damage dice"},
    "fey_touched":          {"name":"Fey Touched",         "desc":"+1 Int/Wis/Cha, misty step + one 1st-level divination/enchantment spell 1/day each"},
    "grappler":             {"name":"Grappler",            "desc":"Advantage on attacks vs grappled creatures, pin creatures, restrained condition"},
    "gunner":               {"name":"Gunner",              "desc":"You have a quick hand and keen eye when employing firearms. +1 Dex, gain firearm proficiency, ignore loading property of firearms, no disadvantage on ranged attacks in melee range."},
    "great_weapon_master":  {"name":"Great Weapon Master", "desc":"-5 attack +10 dmg with heavy melee weapon, bonus action on crit/kill"},
    "healer":               {"name":"Healer",              "desc":"Stabilize as action, heal 1d6+4+HD 1/short rest per creature"},
    "heavily_armored":      {"name":"Heavily Armored",     "desc":"+1 Str, gain heavy armor proficiency"},
    "heavy_armor_master":   {"name":"Heavy Armor Master",  "desc":"+1 Str, reduce nonmagical B/P/S by 3 from heavy armor"},
    "inspiring_leader":     {"name":"Inspiring Leader",    "desc":"10 min speech: allies gain temp HP = level + Cha mod"},
    "keen_mind":            {"name":"Keen Mind",           "desc":"+1 Int, perfect recall of last month's events/geography"},
    "lightly_armored":      {"name":"Lightly Armored",     "desc":"+1 Str/Dex, gain light armor proficiency"},
    "linguist":             {"name":"Linguist",            "desc":"+1 Int, 3 extra languages, create written ciphers"},
    "lucky":                {"name":"Lucky",               "desc":"3 rerolls per long rest on d20s"},
    "mage_slayer":          {"name":"Mage Slayer",         "desc":"Reaction attack when creature casts, disadvantage on concentration saves, advantage on saves"},
    "magic_initiate":       {"name":"Magic Initiate",     "desc":"Two cantrips + one 1st-level spell from one class's list, cast 1/day"},
    "martial_adept":        {"name":"Martial Adept",      "desc":"Two maneuvers + one superiority die (d6)"},
    "medium_armor_master":  {"name":"Medium Armor Master", "desc":"No disadvantage on stealth, +1 AC (max 3 from Dex) with medium armor"},
    "mobile":               {"name":"Mobile",             "desc":"+10 ft speed, ignore difficult terrain when dashing, no OA from attacked creature"},
    "moderately_armored":   {"name":"Moderately Armored",  "desc":"+1 Str/Dex, gain medium armor + shield proficiency"},
    "mounted_combatant":    {"name":"Mounted Combatant",   "desc":"Advantage vs unmounted foes, protect mount, mount avoids AoE on save"},
    "observant":            {"name":"Observant",           "desc":"+1 Int/Wis, +5 passive Perception/Investigation, read lips"},
    "polearm_master":       {"name":"Polearm Master",      "desc":"Bonus attack with d4 for polearms, OA when creature enters reach"},
    "resilient":            {"name":"Resilient",           "desc":"+1 to an ability, gain save proficiency in that ability"},
    "ritual_caster":        {"name":"Ritual Caster",       "desc":"Learn/cast rituals from chosen class list"},
    "savage_attacker":      {"name":"Savage Attacker",     "desc":"Once per turn, reroll melee damage and take the higher result"},
    "sentinel":             {"name":"Sentinel",            "desc":"OA hits stop movement, OA on disengage/reaction attack within 5ft"},
    "sharpshooter":         {"name":"Sharpshooter",        "desc":"Ignore range/disadvantage from cover, -5 attack +10 dmg with ranged"},
    "shield_master":        {"name":"Shield Master",       "desc":"+2 AC vs spells, shove as bonus action, no damage on Dex save"},
    "skilled":              {"name":"Skilled",             "desc":"Three skill or tool proficiencies"},
    "skulker":              {"name":"Skulker",             "desc":"No disadvantage on Perception in dim light, can hide when lightly obscured"},
    "spell_sniper":         {"name":"Spell Sniper",        "desc":"Double spell range, ignore half/three-quarters cover, learn one cantrip"},
    "tavern_brawler":       {"name":"Tavern Brawler",      "desc":"+1 Str/Con, improvised weapons d4, unarmed strike as bonus after grapple"},
    "tough":                {"name":"Tough",               "desc":"+2 HP per level"},
    "war_caster":           {"name":"War Caster",          "desc":"Adv on Con saves for concentration, somatic components with weapon/shield, cast spell as OA"},
    "weapon_master":        {"name":"Weapon Master",       "desc":"+1 Str/Dex, gain 4 weapon proficiencies"},
}

# Build name lookup for FEATS
FEAT_BY_NAME = {}
for _key, _data in FEATS.items():
    _n = _data.get("name", "").lower()
    if _n:
        FEAT_BY_NAME[_n] = _data
    # Also index by key with underscores
    FEAT_BY_NAME[_key.lower()] = _data

# ── Feature Descriptions (PHB class features) ─────────────────────────

FEATURE_DESCRIPTIONS = {
    "ability score improvement": "You can increase one ability score of your choice by 2, or two ability scores of your choice by 1. As normal, you can't increase an ability score above 20 using this feature.",
    "action surge": "On your turn, you can take one additional action on top of your regular action and a possible bonus action.",
    "arcane recovery": "Once per day when you finish a short rest, you can choose expended spell slots to recover—the total level of recovered slots must not exceed half your wizard level (rounded up).",
    "aura of courage": "You and friendly creatures within 10 feet of you can't be frightened while you are conscious.",
    "aura of protection": "Whenever you or a friendly creature within 10 feet of you must make a saving throw, the creature gains a bonus to the saving throw equal to your Charisma modifier (with a minimum bonus of +1). You must be conscious to grant this bonus.",
    "bardic inspiration": "As a bonus action, you can inspire one creature within 60 feet. The creature gains one Bardic Inspiration die (a d6). Once within the next 10 minutes, the creature can roll the die and add the number rolled to one ability check, attack roll, or saving throw it makes.",
    "channel divinity: abjure enemy": "As an action, you present your holy symbol and speak a prayer of denunciation, causing one fi end or undead within 60 feet that can see or hear you to make a Wisdom saving throw.",
    "channel divinity: destructive wrath": "When you roll lightning or thunder damage, you can use your Channel Divinity to deal maximum damage, instead of rolling.",
    "channel divinity: preserve life": "As an action, you present your holy symbol and evoke healing energy that can restore a number of hit points equal to five times your cleric level.",
    "channel divinity: turn undead": "As an action, you present your holy symbol and speak a prayer censuring the undead. Each undead that can see or hear you within 30 feet must make a Wisdom saving throw.",
    "channel divinity: vow of enmity": "As a bonus action, you can utter a vow of enmity against a creature you can see within 10 feet of you. You gain advantage on attack rolls against the creature for 1 minute or until it drops to 0 hit points or falls unconscious.",
    "cunning action": "You can take a bonus action on each of your turns in combat. This action can be used only to take the Dash, Disengage, or Hide action.",
    "divine sense": "The presence of strong evil registers on your senses like a noxious odor, and powerful good rings like heavenly music in your ears. As an action, you can open your awareness to detect such forces.",
    "divine smite": "When you hit a creature with a melee weapon attack, you can expend one spell slot to deal radiant damage to the target, in addition to the weapon's damage. The extra damage is 2d8 for a 1st-level spell slot, plus 1d8 for each spell level higher than 1st, to a maximum of 5d8.",
    "evasion": "When you are subjected to an effect that allows you to make a Dexterity saving throw to take only half damage, you instead take no damage if you succeed on the saving throw, and only half damage if you fail.",
    "extra attack": "You can attack twice, instead of once, whenever you take the Attack action on your turn.",
    "fast hands": "You can use a bonus action to make a Dexterity (Sleight of Hand) check, use your thieves' tools to disarm a trap or open a lock, or take the Use an Object action.",
    "fighting style": "You adopt a particular style of fighting as your specialty.",
    "flurry of blows": "Immediately after you take the Attack action on your turn, you can spend 1 ki point to make two unarmed strikes as a bonus action.",
    "font of inspiration": "You regain all of your expended uses of Bardic Inspiration when you finish a short or long rest.",
    "jack of all trades": "You can add half your proficiency bonus, rounded down, to any ability check you make that doesn't already include your proficiency bonus.",
    "lay on hands": "You have a pool of healing power that can restore a total number of hit points equal to your paladin level x 5. As an action, you can touch a creature and draw power from the pool to restore a number of hit points to that creature, up to the maximum amount remaining in your pool.",
    "patient defense": "You can spend 1 ki point to take the Dodge action as a bonus action on your turn.",
    "rage": "In battle, you fight with primal fury. On your turn, you can enter a rage as a bonus action. While raging, you gain advantage on Strength checks and Strength saving throws, bonus damage on melee weapon attacks using Strength, and resistance to bludgeoning, piercing, and slashing damage.",
    "second wind": "You have a limited well of stamina that you can draw on to protect yourself from harm. On your turn, you can use a bonus action to regain hit points equal to 1d10 + your fighter level.",
    "sneak attack": "Once per turn, you can deal an extra 1d6 damage to one creature you hit with an attack if you have advantage on the attack roll.",
    "song of rest": "If you or any friendly creatures who can hear your performance regain hit points at the end of the short rest by spending one or more Hit Dice, each of those creatures regains an extra 1d6 hit points.",
    "step of the wind": "You can spend 1 ki point to take the Dash or Disengage action as a bonus action on your turn, and your jump distance is doubled for the turn.",
    "stunning strike": "You can spend 1 ki point to attempt a stunning strike when you hit another creature with a melee weapon attack.",
    "uncanny dodge": "When an attacker that you can see hits you with an attack, you can use your reaction to halve the attack's damage against you.",
    "wild shape": "You can use your action to magically assume the shape of a beast that you have seen before.",
    # ── Channel Divinity sub-options used by the parser ──
    "channel divinity: knowledge of the ages": "Your granted knowledge can transcend the ages. As an action, you choose one skill or tool and gain proficiency in it for 10 minutes.",
    "channel divinity: read thoughts": "As an action, choose one creature within 60 feet. You learn the surface thoughts of that creature.",
    "channel divinity: radiance of the dawn": "As an action, you present your holy symbol, and any magical darkness within 30 feet of you is dispelled.",
    "channel divinity: sacred weapon": "As an action, you can imbue one weapon that you are holding with positive energy, using your Channel Divinity.",
    "channel divinity: war god's blessing": "When a creature within 30 feet of you makes an attack roll, you can use your reaction to grant that creature a +10 bonus to the roll.",
    "channel divinity: natures wrath": "As an action, you can cause spectral vines to ensnare a creature within 10 feet.",
    "channel divinity: abjure enemy": "As an action, you present your holy symbol and speak a prayer of denunciation, causing one fiend or undead within 60 feet to make a Wisdom save.",
    "channel divinity: turn the unholy": "As an action, you present your holy symbol and speak a prayer censuring fiends and undead.",
    "channel divinity: turn the faithless": "As an action, you present your holy symbol and speak a prayer censuring fey and fiends.",
    "channel divinity": "You can channel divine energy to fuel magical effects. You start with one use per short rest.",
}

# ── Feature → Action Type Mapping ─────────────────────────────────────

FEATURE_ACTION_TYPES = {
    # (action_type, description)
    # action_type values: "Action", "Bonus Action", "Reaction", or "Special"
    "action surge":                    ("Action", "Take one additional action this turn"),
    "bardic inspiration":              ("Bonus Action", "Inspire an ally within 60 ft"),
    "cunning action":                  ("Bonus Action", "Dash, Disengage, or Hide"),
    "divine smite":                    ("Special", "Expend spell slot after hitting for +radiant dmg"),
    "fast hands":                      ("Bonus Action", "Sleight of Hand, Use an Object, or thieves' tools"),
    "flurry of blows":                 ("Bonus Action", "2 unarmed strikes after Attack action"),
    "lay on hands":                    ("Action", "Touch to heal from your pool"),
    "patient defense":                 ("Bonus Action", "Dodge as bonus action (1 ki)"),
    "rage":                            ("Bonus Action", "Enter a rage"),
    "second wind":                     ("Bonus Action", "Regain 1d10 + level HP"),
    "step of the wind":                ("Bonus Action", "Dash/Disengage + double jump (1 ki)"),
    "wild shape":                      ("Action", "Assume beast form"),
    "channel divinity":                ("Action", "Channel divine energy"),
    # Additional limited-use features
    "hexblade's curse":                ("Bonus Action", "Curse target: crit on 19-20, +prof dmg, heal on kill"),
    "hunter's mark":                   ("Bonus Action", "Mark target for extra 1d6 damage"),
    "breath weapon":                   ("Action", "Exhale destructive energy in a cone or line"),
    "accursed specter":                ("Action", "Raise a specter from a slain humanoid"),
    "war priest":                      ("Bonus Action", "Make one weapon attack as a bonus action"),
    "frenzy":                          ("Bonus Action", "Make one melee weapon attack as a bonus action while raging"),
    "zealous presence":                ("Bonus Action", "Grant allies advantage on attack rolls and saves for 1 round"),
    # Features already stored as typed — add map entries for enrichment consistency
    "benign transposition":            ("Action", "Swap places with a willing creature within 30 ft"),
    "githzerai psionics":              ("Action", "Cast Mage Hand, Shield, or racial spell"),
    "hands of the healer":             ("Action", "Heal a creature by touch"),
    "tides of chaos":                  ("Reaction", "Gain advantage on an attack, check, or save"),
    "indomitable":                     ("Special", "Reroll a failed saving throw"),
}

# ── Metamagic Options (Sorcerer PHB p.102) ────────────────────────────

METAMAGIC_OPTIONS = {
    "careful_spell":     {"name": "Careful Spell",     "desc": "Spend 1 SP. Choose creatures that automatically succeed saves vs your spell."},
    "distant_spell":     {"name": "Distant Spell",     "desc": "Spend 1 SP. Double spell range, or 30 ft touch → 30 ft range."},
    "empowered_spell":   {"name": "Empowered Spell",   "desc": "Spend 1 SP. Reroll up to Cha mod damage dice."},
    "extended_spell":    {"name": "Extended Spell",    "desc": "Spend 1 SP. Double spell duration (max 24h)."},
    "heightened_spell":  {"name": "Heightened Spell",  "desc": "Spend 3 SP. Target has disadvantage on first save."},
    "quickened_spell":   {"name": "Quickened Spell",   "desc": "Spend 2 SP. Cast as bonus action."},
    "subtle_spell":      {"name": "Subtle Spell",      "desc": "Spend 1 SP. Cast without V or S components."},
    "twinned_spell":     {"name": "Twinned Spell",     "desc": "Spend SP = spell level. Target a second creature with a single-target spell."},
}

METAMAGIC_LEVELS = [3, 10, 17]
METAMAGIC_PICKS = {3: 2, 10: 1, 17: 1}

# ── Eldritch Invocations (Warlock PHB p.107) ──────────────────────────

INVOCATION_LEVELS = {
    "Warlock": [2, 5, 7, 9, 12, 15, 18],
}
INVOCATION_PICKS = {2: 2, 5: 1, 7: 1, 9: 1, 12: 1, 15: 1, 18: 1}
INVOCATION_OPTIONS = {
    "agonizing_blast":     {"name": "Agonizing Blast",     "desc": "Add Cha to eldritch blast damage.", "prereq": "eldritch blast cantrip"},
    "armor_of_shadows":    {"name": "Armor of Shadows",    "desc": "Cast mage armor at will."},
    "beast_speech":        {"name": "Beast Speech",        "desc": "Cast speak with animals at will."},
    "beguiling_influence": {"name": "Beguiling Influence", "desc": "Proficiency in Deception and Persuasion."},
    "bewitching_whispers": {"name": "Bewitching Whispers", "desc": "Cast compulsion 1/day using a warlock spell slot.", "prereq": "7th level"},
    "book_of_ancient_secrets": {"name": "Book of Ancient Secrets", "desc": "Inscribe rituals in Book of Shadows.", "prereq": "Pact of the Tome"},
    "chains_of_carceri":   {"name": "Chains of Carceri",   "desc": "Cast hold monster at will on celestials/fiends/elementals.", "prereq": "15th level"},
    "devils_own_luck":     {"name": "Devil's Own Luck",    "desc": "Add 1d10 to ability check or saving throw 1/short rest.", "prereq": "Pact of the Chain"},
    "devils_sight":        {"name": "Devil's Sight",       "desc": "See normally in magical darkness, 120 ft."},
    "dreadful_word":       {"name": "Dreadful Word",       "desc": "Cast confusion 1/day using a warlock spell slot.", "prereq": "7th level"},
    "eldritch_sight":      {"name": "Eldritch Sight",      "desc": "Cast detect magic at will."},
    "eldritch_spear":      {"name": "Eldritch Spear",      "desc": "Eldritch blast range 300 ft.", "prereq": "eldritch blast cantrip"},
    "eyes_of_the_runekeeper": {"name": "Eyes of the Rune Keeper", "desc": "Read all writing."},
    "fiendish_vigor":      {"name": "Fiendish Vigor",      "desc": "Cast false life at will at 1st level."},
    "gaze_of_two_minds":   {"name": "Gaze of Two Minds",   "desc": "Share senses with a willing creature."},
    "life_drinker":        {"name": "Life Drinker",        "desc": "Add Cha to pact weapon damage.", "prereq": "12th level, Pact of the Blade"},
    "mask_of_many_faces":  {"name": "Mask of Many Faces",  "desc": "Cast disguise self at will."},
    "master_of_myriad_forms": {"name": "Master of Myriad Forms", "desc": "Cast alter self at will.", "prereq": "15th level"},
    "minions_of_chaos":    {"name": "Minions of Chaos",    "desc": "Cast conjure elemental 1/day.", "prereq": "9th level"},
    "mire_the_mind":       {"name": "Mire the Mind",       "desc": "Cast slow 1/day using a warlock spell slot.", "prereq": "5th level"},
    "misty_visions":       {"name": "Misty Visions",       "desc": "Cast silent image at will."},
    "one_with_shadows":    {"name": "One with Shadows",    "desc": "Cast invisibility at will in dim light/darkness.", "prereq": "5th level"},
    "otherworldly_leap":   {"name": "Otherworldly Leap",   "desc": "Cast jump on self at will.", "prereq": "9th level"},
    "repelling_blast":     {"name": "Repelling Blast",     "desc": "Push 10 ft per eldritch blast hit.", "prereq": "eldritch blast cantrip"},
    "sculptor_of_flesh":   {"name": "Sculptor of Flesh",   "desc": "Cast polymorph 1/day using a warlock spell slot.", "prereq": "7th level"},
    "sign_of_ill_omen":    {"name": "Sign of Ill Omen",    "desc": "Cast bestow curse 1/day using a warlock spell slot.", "prereq": "5th level"},
    "thief_of_five_fates": {"name": "Thief of Five Fates", "desc": "Cast bane 1/day using a warlock spell slot."},
    "thirsting_blade":     {"name": "Thirsting Blade",     "desc": "Extra Attack with pact weapon.", "prereq": "5th level, Pact of the Blade"},
    "visions_of_distant_realms": {"name": "Visions of Distant Realms", "desc": "Cast arcane eye at will.", "prereq": "15th level"},
    "voice_of_the_chain_master": {"name": "Voice of the Chain Master", "desc": "Communicate telepathically with familiar, perceive through its senses.", "prereq": "Pact of the Chain"},
    "whispers_of_the_grave": {"name": "Whispers of the Grave", "desc": "Cast speak with dead at will.", "prereq": "9th level"},
    "witch_sight":         {"name": "Witch Sight",         "desc": "See the true form of shapechangers and invisible creatures within 30 ft.", "prereq": "15th level"},
}

# ── Pact Boon Options (Warlock PHB p.107) ─────────────────────────────

PACT_BOON_OPTIONS = {
    "pact_of_the_chain":  {"name": "Pact of the Chain",  "desc": "Improved familiar (imp, pseudodragon, quasit, sprite)."},
    "pact_of_the_blade":  {"name": "Pact of the Blade",  "desc": "Summon a magical pact weapon as an action."},
    "pact_of_the_tome":   {"name": "Pact of the Tome",   "desc": "Gain Book of Shadows with 3 extra cantrips."},
}
PACT_BOON_LEVELS = [3]

# ── Battle Master Maneuvers (Fighter PHB p.73) ────────────────────────

MANEUVER_LEVELS = [3, 7, 10, 15]
MANEUVER_OPTIONS = {
    "commander_strike":      {"name": "Commander's Strike",     "desc": "Expend superiority die and your action to grant an ally a weapon attack.", "die": "1d8"},
    "disarming_attack":      {"name": "Disarming Attack",       "desc": "Add die to damage; target drops one held item on failed Str save.", "die": "1d8"},
    "distracting_strike":    {"name": "Distracting Strike",     "desc": "Add die to damage; next attack by ally has advantage.", "die": "1d8"},
    "evasive_footwork":      {"name": "Evasive Footwork",       "desc": "Add die to AC while moving.", "die": "1d8"},
    "feinting_attack":       {"name": "Feinting Attack",        "desc": "Advantage on attack roll; add die to damage.", "die": "1d8"},
    "goading_attack":        {"name": "Goading Attack",         "desc": "Add die to damage; target has disadvantage on attacks vs your allies.", "die": "1d8"},
    "lunging_attack":        {"name": "Lunging Attack",         "desc": "Increase melee reach by 5 ft; add die to damage.", "die": "1d8"},
    "maneuvering_attack":    {"name": "Maneuvering Attack",     "desc": "Add die to damage; ally can move up to half speed.", "die": "1d8"},
    "menacing_attack":       {"name": "Menacing Attack",        "desc": "Add die to damage; target is frightened.", "die": "1d8"},
    "parry":                 {"name": "Parry",                  "desc": "Reduce melee damage by die + Dex mod.", "die": "1d8"},
    "precision_attack":      {"name": "Precision Attack",       "desc": "Add die to attack roll after seeing result.", "die": "1d8"},
    "pushing_attack":        {"name": "Pushing Attack",         "desc": "Add die to damage; push 15 ft on failed Str save.", "die": "1d8"},
    "rally":                 {"name": "Rally",                  "desc": "Grant ally temp HP equal to die + Cha mod.", "die": "1d8"},
    "riposte":               {"name": "Riposte",                "desc": "Make melee attack against missed attacker; add die to damage.", "die": "1d8"},
    "sweeping_attack":       {"name": "Sweeping Attack",        "desc": "Add die to damage; hit second creature in reach for no-cost damage.", "die": "1d8"},
    "trip_attack":           {"name": "Trip Attack",            "desc": "Add die to damage; target is prone on failed Str save.", "die": "1d8"},
}

# ── Totem Spirit Options (Barbarian PHB p.50) ─────────────────────────

TOTEM_SPIRIT_OPTIONS = {
    "bear":  {"name": "Bear",  "desc": "While raging, resistance to all damage except psychic."},
    "eagle": {"name": "Eagle", "desc": "While raging, dash as bonus action, allies have advantage on Perception checks."},
    "wolf":  {"name": "Wolf",  "desc": "While raging, allies have advantage on melee attacks against creatures within 5 ft."},
}

# ── Hunter's Prey Options (Ranger PHB p.93) ───────────────────────────

HUNTERS_PREY_OPTIONS = {
    "colossus_slayer": {"name": "Colossus Slayer", "desc": "Once per turn, deal +1d8 damage to a wounded creature (below max HP)."},
    "giant_killer":    {"name": "Giant Killer",    "desc": "When a Large+ creature misses you with an attack, you can use your reaction to attack it."},
    "horde_breaker":   {"name": "Horde Breaker",   "desc": "Once per turn, make an additional attack against a different creature within 5 ft."},
}

# ── Favored Enemy / Favored Terrain (Ranger PHB p.91) ─────────────────

FAVORED_ENEMY_OPTIONS = {
    "aberration": {"name": "Aberrations", "desc": "Aboleths, beholders, mind flayers, slaadi — alien horrors."},
    "beast":      {"name": "Beasts",      "desc": "Non-monstrous animals and giant versions."},
    "celestial":  {"name": "Celestials",  "desc": "Angels, pegasi, couatls, planetars — beings of the Upper Planes."},
    "construct":  {"name": "Constructs",  "desc": "Golems, animated armor, shield guardians — magically created beings."},
    "dragon":     {"name": "Dragons",     "desc": "True dragons and their kin — drakes, wyverns, pseudodragons."},
    "elemental":  {"name": "Elementals",  "desc": "Genies, elementals, salamanders, xorn — beings of the Elemental Planes."},
    "fey":        {"name": "Fey",         "desc": "Dryads, pixies, satyrs, hags — creatures of the Feywild."},
    "fiend":      {"name": "Fiends",      "desc": "Demons, devils, yugoloths — beings of the Lower Planes."},
    "giant":      {"name": "Giants",      "desc": "Hill, stone, frost, fire, cloud, storm giants — enormous humanoids."},
    "humanoid":   {"name": "Humanoids",   "desc": "The civilized races — goblins, orcs, humans, dwarves, elves."},
    "monstrosity":{"name": "Monstrosities","desc": "Owlbears, basilisks, hydras, manticores — unnatural creatures."},
    "ooze":       {"name": "Oozes",       "desc": "Gelatinous cubes, black puddings, ochre jellies — amorphous blobs."},
    "plant":      {"name": "Plants",      "desc": "Shambling mounds, treants, violet fungi — sentient vegetation."},
    "undead":     {"name": "Undead",      "desc": "Zombies, skeletons, vampires, liches — the unliving."},
}

FAVORED_TERRAIN_OPTIONS = {
    "arctic":     {"name": "Arctic",      "desc": "Frozen tundra, ice fields, snow-covered mountains."},
    "coast":      {"name": "Coast",       "desc": "Shorelines, beaches, sea cliffs, tidal zones."},
    "desert":     {"name": "Desert",      "desc": "Sandy wastes, rocky badlands, salt flats, oases."},
    "forest":     {"name": "Forest",      "desc": "Temperate woodlands, jungles, taiga, bamboo groves."},
    "grassland":  {"name": "Grassland",   "desc": "Prairies, savannas, steppes, meadows, pampas."},
    "mountain":   {"name": "Mountain",    "desc": "Peaks, ridges, passes, alpine meadows, caves."},
    "swamp":      {"name": "Swamp",       "desc": "Marshes, bogs, fens, bayous, mangroves."},
    "underdark":  {"name": "Underdark",   "desc": "Caves, caverns, tunnels, underground cities."},
}

# ── Artificer Infusions (Eberron) ─────────────────────────────────────

INFUSION_OPTIONS = {
    "enhanced_weapon":         {"name": "Enhanced Weapon",          "desc": "+1 weapon (upgraded to +2 at L10)."},
    "enhanced_armor":          {"name": "Enhanced Armor",           "desc": "+1 armor or shield (upgraded to +2 at L10)."},
    "enhanced_defense":        {"name": "Enhanced Defense",         "desc": "+1 bonus to AC on a suit of armor or a shield."},
    "enhanced_arcane_focus":   {"name": "Enhanced Arcane Focus",    "desc": "+1 to spell attack for a rod/staff/wand."},
    "repeating_shot":          {"name": "Repeating Shot",           "desc": "+1 ranged weapon that ignores loading and creates its own ammo."},
    "returning_weapon":        {"name": "Returning Weapon",         "desc": "+1 weapon that returns to your hand after throwing."},
    "mind_sharpener":          {"name": "Mind Sharpener",           "desc": "Advantage on concentration saves for armor's wearer."},
    "bag_of_holding":          {"name": "Bag of Holding",           "desc": "Create an extradimensional bag (or similar container)."},
    "boots_of_elvenkind":      {"name": "Boots of Elvenkind",       "desc": "Silent footsteps, advantage on Stealth (Dex)."},
    "cloak_of_elvenkind":      {"name": "Cloak of Elvenkind",       "desc": "Advantage on Stealth while worn; disadvantage on Perception to spot you."},
    "goggles_of_night":        {"name": "Goggles of Night",         "desc": "Darkvision 60 ft (if you don't already have it)."},
    "gloves_of_thievery":      {"name": "Gloves of Thievery",       "desc": "+5 Sleight of Hand checks."},
    "helm_of_awareness":       {"name": "Helm of Awareness",        "desc": "+2 to initiative, can't be surprised."},
    "repulsion_shield":        {"name": "Repulsion Shield",         "desc": "+1 shield; reaction to push attacker 15 ft."},
    "resistant_armor":         {"name": "Resistant Armor",          "desc": "Grants resistance to one damage type (chosen daily)."},
    "sending_stones":          {"name": "Sending Stones",           "desc": "Two stones: send the sending spell to paired stone 1/day."},
    "spell_refueling_ring":    {"name": "Spell-Refueling Ring",     "desc": "Regain one spell slot as a bonus action 1/day."},
    "winged_boots":            {"name": "Winged Boots",             "desc": "Fly 30 ft for 10 minutes 1/day."},
    "boots_of_striding":       {"name": "Boots of Striding",        "desc": "Speed 30 ft regardless of encumbrance."},
    "alchemy_jug":             {"name": "Alchemy Jug",              "desc": "Produces various liquids (mayonnaise, acid, poison, oil)."},
}

# ── Multiclass Prerequisites (PHB p.163) ──────────────────────────────

MULTICLASS_PREREQS = {
    "Barbarian": {"strength": 13},
    "Bard":      {"charisma": 13},
    "Cleric":    {"wisdom": 13},
    "Druid":     {"wisdom": 13},
    "Fighter":   {"strength": 13, "dexterity": 13},  # Either STR 13 OR DEX 13
    "Monk":      {"dexterity": 13, "wisdom": 13},
    "Paladin":   {"strength": 13, "charisma": 13},
    "Ranger":    {"dexterity": 13, "wisdom": 13},
    "Rogue":     {"dexterity": 13},
    "Sorcerer":  {"charisma": 13},
    "Warlock":   {"charisma": 13},
    "Wizard":    {"intelligence": 13},
}

# ── Expertise Class Levels (PHB) ──────────────────────────────────────

EXPERTISE_LEVELS = {
    "Bard":     {1: 2, 3: 1, 10: 1},           # L1 x2, L3 +1, L10 +1 (PHB p.52, 54)
    "Rogue":    {1: 2, 6: 2,  11: 1},          # L1 x2, L6 +2, L11 +1 (PHB p.95, 96)
}

# ── Subclass Selection Levels (PHB 2014 + TCE + AiME) ─────────────────

SUBCLASS_LEVELS = {
    "Barbarian": {"level": 3, "label": "Primal Path",
        "options": ["Path of the Berserker", "Path of the Totem Warrior"]},
    "Bard":      {"level": 3, "label": "Bard College",
        "options": ["College of Lore", "College of Valor"]},
    "Cleric":    {"level": 1, "label": "Divine Domain",
        "options": ["Knowledge Domain", "Life Domain", "Light Domain", "Nature Domain",
                     "Tempest Domain", "Trickery Domain", "War Domain"]},
    "Druid":     {"level": 2, "label": "Druid Circle",
        "options": ["Circle of the Land", "Circle of the Moon"]},
    "Fighter":   {"level": 3, "label": "Martial Archetype",
        "options": ["Champion", "Battle Master", "Eldritch Knight"]},
    "Monk":      {"level": 3, "label": "Monastic Tradition",
        "options": ["Way of the Open Hand", "Way of Shadow", "Way of the Four Elements"]},
    "Paladin":   {"level": 3, "label": "Sacred Oath",
        "options": ["Oath of Devotion", "Oath of the Ancients", "Oath of Vengeance"]},
    "Ranger":    {"level": 3, "label": "Ranger Archetype",
        "options": ["Hunter", "Beast Master"]},
    "Rogue":     {"level": 3, "label": "Roguish Archetype",
        "options": ["Thief", "Assassin", "Arcane Trickster"]},
    "Sorcerer":  {"level": 1, "label": "Sorcerous Origin",
        "options": ["Draconic Bloodline", "Wild Magic"]},
    "Warlock":   {"level": 1, "label": "Otherworldly Patron",
        "options": ["The Archfey", "The Fiend", "The Great Old One"]},
    "Wizard":    {"level": 2, "label": "Arcane Tradition",
        "options": ["School of Abjuration", "School of Conjuration", "School of Divination",
                     "School of Enchantment", "School of Evocation", "School of Illusion",
                     "School of Necromancy", "School of Transmutation"]},
    # TCE + homebrew
    "Artificer": {"level": 3, "label": "Artificer Specialist",
        "options": ["Alchemist", "Armorer", "Artillerist", "Battle Smith"]},
    # Adventures in Middle-earth
    "Scholar": {"level": 3, "label": "Scholarly Pursuit",
        "options": ["Master Healer", "Master Scholar"]},
    "Slayer": {"level": 3, "label": "Slayer Calling",
        "options": ["The Rider", "Foe-Hammer", "Horns Wildly Blowing"]},
    "Treasure Hunter": {"level": 3, "label": "Treasure Hunter Specialty",
        "options": ["Agent"]},
    "Wanderer": {"level": 3, "label": "Wanderer Path",
        "options": ["Hunter of Beasts", "Hunter of Shadows"]},
    "Warden": {"level": 3, "label": "Warden Calling",
        "options": ["Counsellor", "Herald", "Bounder"]},
    "Warrior": {"level": 3, "label": "Warrior Calling",
        "options": ["Knight", "Weaponmaster"]},
}

# ── Class Features per Subclass (PHB 2014) ────────────────────────────

SUBCLASS_FEATURES: dict[str, dict[int, list[str]]] = {
    "Path of the Berserker":       {3: ["Frenzy"], 6: ["Mindless Rage"], 10: ["Intimidating Presence"], 14: ["Retaliation"]},
    "Path of the Totem Warrior":   {3: ["Spirit Seeker", "Totem Spirit"], 6: ["Aspect of the Beast"], 10: ["Spirit Walker"], 14: ["Totemic Attunement"]},
    "College of Lore":             {3: ["Bonus Proficiencies", "Cutting Words"], 6: ["Additional Magical Secrets"], 14: ["Peerless Skill"]},
    "College of Valor":            {3: ["Bonus Proficiencies", "Combat Inspiration"], 6: ["Extra Attack"], 14: ["Battle Magic"]},
    "Knowledge Domain":            {1: ["Blessings of Knowledge"], 2: ["Channel Divinity: Knowledge of the Ages"], 6: ["Channel Divinity: Read Thoughts"], 8: ["Potent Spellcasting"], 17: ["Visions of the Past"]},
    "Life Domain":                 {1: ["Disciple of Life"], 2: ["Channel Divinity: Preserve Life"], 6: ["Blessed Healer"], 8: ["Divine Strike"], 17: ["Supreme Healing"]},
    "Light Domain":                {1: ["Warding Flare"], 2: ["Channel Divinity: Radiance of the Dawn"], 6: ["Improved Flare"], 8: ["Potent Spellcasting"], 17: ["Corona of Light"]},
    "Nature Domain":               {1: ["Acolyte of Nature", "Bonus Proficiency"], 2: ["Channel Divinity: Charm Animals and Plants"], 6: ["Dampen Elements"], 8: ["Divine Strike"], 17: ["Master of Nature"]},
    "Tempest Domain":              {1: ["Wrath of the Storm"], 2: ["Channel Divinity: Destructive Wrath"], 6: ["Thunderbolt Strike"], 8: ["Divine Strike"], 17: ["Stormborn"]},
    "Trickery Domain":             {1: ["Blessing of the Trickster"], 2: ["Channel Divinity: Invoke Duplicity"], 6: ["Channel Divinity: Cloak of Shadows"], 8: ["Divine Strike"], 17: ["Improved Duplicity"]},
    "War Domain":                  {1: ["War Priest"], 2: ["Channel Divinity: Guided Strike"], 6: ["Channel Divinity: War God's Blessing"], 8: ["Divine Strike"], 17: ["Avatar of Battle"]},
    "Circle of the Land":          {2: ["Bonus Cantrip", "Natural Recovery"], 6: ["Land's Stride"], 10: ["Nature's Ward"], 14: ["Nature's Sanctuary"]},
    "Circle of the Moon":          {2: ["Combat Wild Shape", "Circle Forms"], 6: ["Primal Strike"], 10: ["Elemental Wild Shape"], 14: ["Thousand Forms"]},
    "Champion":                    {3: ["Improved Critical"], 7: ["Remarkable Athlete"], 10: ["Additional Fighting Style"], 15: ["Superior Critical"], 18: ["Survivor"]},
    "Battle Master":               {3: ["Combat Superiority"], 7: ["Know Your Enemy"], 10: ["Improved Combat Superiority"], 15: ["Relentless"]},
    "Eldritch Knight":             {3: ["Spellcasting", "Weapon Bond"], 7: ["War Magic"], 10: ["Eldritch Strike"], 15: ["Arcane Charge"], 18: ["Improved War Magic"]},
    "Way of the Open Hand":        {3: ["Open Hand Technique"], 6: ["Wholeness of Body"], 11: ["Tranquility"], 17: ["Quivering Palm"]},
    "Way of Shadow":               {3: ["Shadow Arts"], 6: ["Shadow Step"], 11: ["Cloak of Shadows"], 17: ["Opportunist"]},
    "Way of the Four Elements":    {3: ["Disciple of the Elements"], 6: ["Disciple of the Elements"], 11: ["Disciple of the Elements"], 17: ["Disciple of the Elements"]},
    "Oath of Devotion":            {3: ["Channel Divinity: Sacred Weapon", "Channel Divinity: Turn the Unholy"], 7: ["Aura of Devotion"], 15: ["Purity of Spirit"], 20: ["Holy Nimbus"]},
    "Oath of the Ancients":        {3: ["Channel Divinity: Nature's Wrath", "Channel Divinity: Turn the Faithless"], 7: ["Aura of Warding"], 15: ["Undying Sentinel"], 20: ["Elder Champion"]},
    "Oath of Vengeance":           {3: ["Channel Divinity: Abjure Enemy", "Channel Divinity: Vow of Enmity"], 7: ["Relentless Avenger"], 15: ["Soul of Vengeance"], 20: ["Avenging Angel"]},
    "Hunter":                      {3: ["Hunter's Prey"], 7: ["Defensive Tactics"], 11: ["Multiattack"], 15: ["Superior Hunter's Defense"]},
    "Beast Master":                {3: ["Ranger's Companion"], 7: ["Exceptional Training"], 11: ["Bestial Fury"], 15: ["Share Spells"]},
    "Thief":                       {3: ["Fast Hands", "Second-Story Work"], 9: ["Supreme Sneak"], 13: ["Use Magic Device"], 17: ["Thief's Reflexes"]},
    "Assassin":                    {3: ["Assassinate", "Bonus Proficiencies"], 9: ["Infiltration Expertise"], 13: ["Impostor"], 17: ["Death Strike"]},
    "Arcane Trickster":            {3: ["Spellcasting", "Mage Hand Legerdemain"], 9: ["Magical Ambush"], 13: ["Versatile Trickster"], 17: ["Spell Thief"]},
    "Draconic Bloodline":          {1: ["Dragon Ancestor", "Draconic Resilience"], 6: ["Elemental Affinity"], 14: ["Dragon Wings"], 18: ["Draconic Presence"]},
    "Wild Magic":                  {1: ["Wild Magic Surge", "Tides of Chaos"], 6: ["Bend Luck"], 14: ["Controlled Chaos"], 18: ["Spell Bombardment"]},
    "The Archfey":                 {1: ["Expanded Spell List", "Fey Presence"], 6: ["Misty Escape"], 10: ["Beguiling Defenses"], 14: ["Dark Delirium"]},
    "The Fiend":                   {1: ["Expanded Spell List", "Dark One's Blessing"], 6: ["Dark One's Own Luck"], 10: ["Fiendish Resilience"], 14: ["Hurl Through Hell"]},
    "The Great Old One":           {1: ["Expanded Spell List", "Awakened Mind"], 6: ["Entropic Ward"], 10: ["Thought Shield"], 14: ["Create Thrall"]},
    # XGtE Warlock subclasses
    "The Hexblade":                {1: ["Expanded Spell List", "Hexblade's Curse", "Hex Warrior"], 6: ["Accursed Specter"], 10: ["Armor of Hexes"], 14: ["Master of Hexes"]},
    "The Celestial":               {1: ["Expanded Spell List", "Bonus Cantrips", "Healing Light"], 6: ["Radiant Soul"], 10: ["Celestial Resilience"], 14: ["Searing Vengeance"]},
    "School of Abjuration":        {2: ["Abjuration Savant", "Arcane Ward"], 6: ["Projected Ward"], 10: ["Improved Abjuration"], 14: ["Spell Resistance"]},
    "School of Conjuration":       {2: ["Conjuration Savant", "Minor Conjuration"], 6: ["Benign Transposition"], 10: ["Focused Conjuration"], 14: ["Durable Summons"]},
    "School of Divination":        {2: ["Divination Savant", "Portent"], 6: ["Expert Divination"], 10: ["The Third Eye"], 14: ["Greater Portent"]},
    "School of Enchantment":       {2: ["Enchantment Savant", "Hypnotic Gaze"], 6: ["Instinctive Charm"], 10: ["Split Enchantment"], 14: ["Alter Memories"]},
    "School of Evocation":         {2: ["Evocation Savant", "Sculpt Spells"], 6: ["Potent Cantrip"], 10: ["Empowered Evocation"], 14: ["Overchannel"]},
    "School of Illusion":          {2: ["Illusion Savant", "Improved Minor Illusion"], 6: ["Malleable Illusions"], 10: ["Illusory Self"], 14: ["Illusory Reality"]},
    "School of Necromancy":     {2: ["Necromancy Savant", "Grim Harvest"], 6: ["Undead Thralls"], 10: ["Inured to Death"], 14: ["Command Undead"]},
    "School of Transmutation":     {2: ["Transmutation Savant", "Minor Alchemy"], 6: ["Transmuter's Stone"], 10: ["Shapechanger"], 14: ["Master Transmuter"]},
    # DMG subclasses
    "Death Domain": {1: ["Death Domain Spells", "Bonus Proficiency", "Reaper"], 2: ["Channel Divinity: Touch of Death"], 6: ["Inescapable Destruction"], 8: ["Divine Strike"], 17: ["Improved Reaper"]},
    "Oathbreaker": {3: ["Oathbreaker Spells", "Channel Divinity: Control Undead", "Channel Divinity: Dreadful Aspect"], 7: ["Aura of Hate"], 15: ["Supernatural Resistance"], 20: ["Dread Lord"]},
}

# ── PHB Limited-Use Features ──────────────────────────────────────────

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

# ── Scaled Equipment by Class and Level ───────────────────────────────

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
        5:  ["+1 Longsword","Splint Mail","Shield +1","Dungeoneer's Pack","Potion of Hill Giant Strength"],
        10: ["+2 Longsword","Plate Armor","Shield +2","Cloak of Protection","Ring of Protection","Gauntlets of Ogre Power"],
        15: ["+2 Longsword","Plate Armor +1","Shield +2","Cloak of Displacement","Ring of Protection","Belt of Giant Strength (Fire)","Mantle of Spell Resistance"],
        20: ["+3 Longsword","Plate Armor +2","Shield +3","Cloak of Displacement","Ring of Protection","Belt of Storm Giant Strength","Mantle of Spell Resistance"],
    },
    "Monk": {
        1:  ["Shortsword","Dungeoneer's Pack","10 Darts"],
        5:  ["+1 Shortsword","Dungeoneer's Pack","Cloak of Protection","Bracers of Defense"],
        10: ["+2 Shortsword","Cloak of Protection","Bracers of Defense","Ring of Protection"],
        15: ["+2 Shortsword","Cloak of Displacement","Bracers of Defense","Ring of Protection","Mantle of Spell Resistance"],
        20: ["+3 Shortsword","Cloak of Displacement","Bracers of Defense","Ring of Protection","Mantle of Spell Resistance"],
    },
    "Paladin": {
        1:  ["Longsword","Shield","5 Javelins","Priest's Pack","Chain Mail","Holy Symbol"],
        5:  ["+1 Longsword","Splint Mail","Shield +1","Holy Symbol","Priest's Pack"],
        10: ["+2 Longsword","Plate Armor","Shield +2","Cloak of Protection","Ring of Protection","Holy Symbol"],
        15: ["+2 Longsword","Plate Armor +1","Shield +2","Cloak of Displacement","Ring of Protection","Holy Symbol","Mantle of Spell Resistance"],
        20: ["+3 Longsword","Plate Armor +2","Shield +3","Cloak of Displacement","Ring of Protection","Holy Symbol","Mantle of Spell Resistance"],
    },
    "Ranger": {
        1:  ["Longbow + 20 Arrows","Shortsword","Scale Mail","Explorer's Pack"],
        5:  ["+1 Longbow","+1 Shortsword","Half Plate","Explorer's Pack","Cloak of Elvenkind"],
        10: ["+2 Longbow","+2 Shortsword","Half Plate +1","Cloak of Elvenkind","Ring of Protection","Boots of Elvenkind"],
        15: ["+2 Longbow","+2 Shortsword","Half Plate +2","Cloak of Displacement","Ring of Protection","Boots of Elvenkind"],
        20: ["+3 Longbow","+3 Shortsword","Studded Leather +3","Cloak of Displacement","Ring of Protection","Boots of Elvenkind"],
    },
    "Rogue": {
        1:  ["Rapier","Shortbow + 20 Arrows","Burglar's Pack","Leather Armor","2 Daggers","Thieves' Tools"],
        5:  ["+1 Rapier","Studded Leather","Burglar's Pack","Thieves' Tools","Cloak of Elvenkind"],
        10: ["+2 Rapier","Studded Leather +1","Gloves of Thievery","Cloak of Elvenkind","Ring of Protection"],
        15: ["+2 Rapier","Studded Leather +2","Gloves of Thievery","Cloak of Displacement","Ring of Protection","Boots of Elvenkind"],
        20: ["+3 Rapier","Studded Leather +3","Gloves of Thievery","Cloak of Displacement","Ring of Protection","Boots of Elvenkind"],
    },
    "Sorcerer": {
        1:  ["Light Crossbow + 20 Bolts","Arcane Focus","Dungeoneer's Pack","2 Daggers"],
        5:  ["+1 Light Crossbow","Arcane Focus","Dungeoneer's Pack","Cloak of Protection","Wand of the War Mage +1"],
        10: ["+2 Light Crossbow","Arcane Focus","Cloak of Protection","Ring of Protection","Wand of the War Mage +2"],
        15: ["Arcane Focus","Cloak of Displacement","Ring of Protection","Robe of the Archmagi","Wand of the War Mage +2"],
        20: ["Arcane Focus","Cloak of Displacement","Ring of Protection","Robe of the Archmagi","Wand of the War Mage +3"],
    },
    "Warlock": {
        1:  ["Light Crossbow + 20 Bolts","Arcane Focus","Scholar's Pack","Leather Armor","Dagger"],
        5:  ["+1 Dagger","Arcane Focus","Scholar's Pack","Studded Leather","Cloak of Protection"],
        10: ["+2 Dagger","Arcane Focus","Cloak of Protection","Ring of Protection","Rod of the Pact Keeper +1"],
        15: ["+2 Dagger","Arcane Focus","Cloak of Displacement","Ring of Protection","Rod of the Pact Keeper +2"],
        20: ["+3 Dagger","Arcane Focus","Cloak of Displacement","Ring of Protection","Rod of the Pact Keeper +3"],
    },
    "Wizard": {
        1:  ["Quarterstaff","Arcane Focus","Scholar's Pack","Spellbook"],
        5:  ["Quarterstaff","Arcane Focus","Scholar's Pack","Spellbook","Cloak of Protection","Wand of the War Mage +1"],
        10: ["Quarterstaff","Arcane Focus","Spellbook","Cloak of Protection","Ring of Protection","Wand of the War Mage +2"],
        15: ["Arcane Focus","Spellbook","Cloak of Displacement","Ring of Protection","Robe of the Archmagi","Wand of the War Mage +2"],
        20: ["Arcane Focus","Spellbook","Cloak of Displacement","Ring of Protection","Robe of the Archmagi","Wand of the War Mage +3"],
    },
}

# ── Recommended Feats by Class (PHB optimal picks) ────────────────────

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

# ── Multiclass Proficiency Grants ─────────────────────────────────────

MULTICLASS_PROFICIENCIES = {
    "Barbarian": {"armor": "light,medium,shields", "weapons": "simple,martial"},
    "Bard":      {"armor": "light", "weapons": "simple,hand crossbows,longswords,rapiers,shortswords", "skills": 1},
    "Cleric":    {"armor": "light,medium,shields", "weapons": "simple"},
    "Druid":     {"armor": "light,medium,shields (non-metal)", "weapons": "clubs,daggers,darts,javelins,maces,quarterstaffs,scimitars,sickles,slings,spears"},
    "Fighter":   {"armor": "light,medium,heavy,shields", "weapons": "simple,martial"},
    "Monk":      {"weapons": "simple,shortswords"},
    "Paladin":   {"armor": "light,medium,heavy,shields", "weapons": "simple,martial"},
    "Ranger":    {"armor": "light,medium,shields", "weapons": "simple,martial", "skills": 1},
    "Rogue":     {"armor": "light", "skills": 1},
    "Sorcerer":  {},
    "Warlock":   {"weapons": "simple", "armor": "light"},
    "Wizard":    {},
}

# ── RACIAL_TRAIT_EFFECTS (mechanical effects per trait name) ──────────

RACIAL_TRAIT_EFFECTS = {
    "Brave":              {"condition_immune": ["frightened"]},
    "Breath Weapon":      {},
    "Damage Resistance":  {"damage_resist": []},  # filled by ancestry
    "Darkvision":         {"darkvision": 60},
    "Draconic Ancestry":  {},
    "Dwarven Combat Training": {"weapon_profs": ["Battleaxe", "Handaxe", "Light Hammer", "Warhammer"]},
    "Dwarven Resilience": {"damage_resist": ["Poison"], "condition_immune": ["poisoned"]},
    "Dwarven Toughness":  {"hp_per_level": 1},
    "Elf Weapon Training": {"weapon_profs": ["Longsword", "Shortsword", "Shortbow", "Longbow"]},
    "Fey Ancestry":       {"condition_immune": ["charmed"]},
    "Gnome Cunning":      {},
    "Hellish Resistance": {"damage_resist": ["Fire"]},
    "High Elf Cantrip":   {},
    "Infernal Legacy":    {},
    "Keen Senses":        {"skill_profs": ["Perception"]},
    "Lucky":              {},
    "Stonecunning":       {},
    "Superior Darkvision": {"darkvision": 120},
    "Trance":             {},
    "Skill Versatility":  {},
    "Extra Language":     {},
    "Nimble Escape":      {},
    "Drow Magic":         {},
    "Sunlight Sensitivity": {},
    "Drow Weapon Training": {"weapon_profs": ["Rapier", "Shortsword", "Hand Crossbow"]},
    "Duergar Resilience": {},
    "Duergar Magic":      {},
    "Duergar Weapon Training": {"weapon_profs": ["Battleaxe", "Handaxe", "Light Hammer", "Warhammer"]},
    "Fleet of Foot":      {"speed": 35},
    "Mask of the Wild":   {},
    "Naturally Stealthy": {},
    "Stout Resilience":   {"damage_resist": ["Poison"]},
    "Halfling Nimbleness": {},
    "Relentless Endurance": {},
    "Savage Attacks":     {},
    "Human Determination": {},
    "Skill Proficiency":  {},
    "Feat":               {},
    "Variable Trait":     {},
}

# ── RACIAL_TRAIT_DESCS ────────────────────────────────────────────────

RACIAL_TRAIT_DESCS = {
    "Brave":              "You have advantage on saving throws against being frightened.",
    "Breath Weapon":      "You can use your action to exhale destructive energy. Your draconic ancestry determines the size, shape, and damage type of your exhalation.",
    "Darkvision":         "You can see in dim light within 60 feet as if it were bright light, and in darkness as if it were dim light.",
    "Damage Resistance":  "You have resistance to the damage type associated with your draconic ancestry.",
    "Dwarven Resilience": "You have advantage on saving throws against poison, and you have resistance against poison damage.",
    "Dwarven Toughness":  "Your hit point maximum increases by 1, and it increases by 1 every time you gain a level.",
    "Elf Weapon Training": "You have proficiency with the longsword, shortsword, shortbow, and longbow.",
    "Fey Ancestry":       "You have advantage on saving throws against being charmed, and magic can't put you to sleep.",
    "Fleet of Foot":      "Your base walking speed increases to 35 feet.",
    "Hellish Resistance": "You have resistance to fire damage.",
    "Keen Senses":        "You have proficiency in the Perception skill.",
    "Lucky":              "When you roll a 1 on an attack roll, ability check, or saving throw, you can reroll the die and must use the new roll.",
    "Mask of the Wild":   "You can attempt to hide even when you are only lightly obscured by foliage, heavy rain, falling snow, mist, and other natural phenomena.",
    "Naturally Stealthy": "You can attempt to hide even when you are obscured only by a creature that is at least one size larger than you.",
    "Relentless Endurance": "When you are reduced to 0 hit points but not killed outright, you can drop to 1 hit point instead. You can't use this feature again until you finish a long rest.",
    "Savage Attacks":     "When you score a critical hit with a melee weapon attack, you can roll one of the weapon's damage dice one additional time and add it to the extra damage of the critical hit.",
    "Stonecunning":       "Whenever you make an Intelligence (History) check related to the origin of stonework, you are considered proficient in the History skill and add double your proficiency bonus to the check.",
    "Stout Resilience":   "You have advantage on saving throws against poison and resistance against poison damage.",
    "Superior Darkvision":"Your darkvision has a radius of 120 feet.",
    "Trance":             "Elves don't need to sleep. Instead, they meditate deeply, remaining semiconscious, for 4 hours a day.",
    "Dwarven Combat Training": "You have proficiency with the battleaxe, handaxe, light hammer, and warhammer.",
    "Halfling Nimbleness":"You can move through the space of any creature that is of a size larger than yours.",
    "Fey Ancestry":       "You have advantage on saving throws against being charmed, and magic can't put you to sleep.",
    "Skill Versatility":  "You gain proficiency in two skills of your choice.",
    "Infernal Legacy":    "You know the thaumaturgy cantrip. When you reach 3rd level, you can cast hellish rebuke as a 2nd-level spell once per day. When you reach 5th level, you can cast darkness once per day.",
    "Nimble Escape":      "You can take the Disengage or Hide action as a bonus action on each of your turns.",
    "Radiant Soul":        "At 3rd level, you can use a bonus action to manifest spectral wings for 1 minute. While transformed, you gain a flying speed of 30 ft, and once per turn when you deal damage to a target, you can add your level to the radiant damage. Usable once per long rest.",
    "Radiant Consumption":"At 3rd level, you can use a bonus action to unleash a searing light for 1 minute. While transformed, you shed bright light in 10 ft and dim light in 10 ft beyond, and each creature within 10 ft that hits you with a melee attack takes radiant damage equal to half your level (rounded up). You also take this damage at the end of each turn. Usable once per long rest.",
    "Necrotic Shroud":    "At 3rd level, you can use a bonus action to manifest a terrifying aspect for 1 minute. While transformed, creatures within 10 ft that can see you must succeed on a Charisma saving throw (DC 8 + proficiency bonus + Charisma modifier) or become frightened of you until the end of your next turn. Once per turn when you deal damage, you can add your level to the necrotic damage. Usable once per long rest.",
}


# ── RACES, CLASSES, RACE_NAMES loaded from exported JSON ──────────────
import json as _json
import os as _os

_data_dir = _os.path.dirname(_os.path.abspath(__file__))

_races_path = _os.path.join(_data_dir, "data", "exports", "races_export.json")
if _os.path.exists(_races_path):
    with open(_races_path) as _f:
        RACES = _json.load(_f)

_classes_path = _os.path.join(_data_dir, "data", "exports", "classes_export.json")
# Subclasses that replace base class features.
# Maps subclass name → ["favored_enemy", "favored_terrain", "spellcasting", ...]
# Used by the creation wizard and LU wizard to skip pickers for replaced features.
SUBCLASS_FEATURE_REPLACEMENTS = {
    "Peerless Scout": ["favored_enemy", "favored_terrain"],
}

if _os.path.exists(_classes_path):
    with open(_classes_path) as _f:
        CLASSES = _json.load(_f)

_rn_path = _os.path.join(_data_dir, "data", "exports", "race_names_export.json")
if _os.path.exists(_rn_path):
    with open(_rn_path) as _f:
        RACE_NAMES = _json.load(_f)
