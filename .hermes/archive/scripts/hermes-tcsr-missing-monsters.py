#!/usr/bin/env python3
"""Add missing TCSR monsters from Chapter 6 that weren't auto-extracted."""
import json, sys, os

DATA_DIR = '/home/james/dnd-character-manager/data/manual_data'

# Load existing monsters
with open(os.path.join(DATA_DIR, 'monsters.json')) as f:
    monsters = json.load(f)

existing_names = {m['name'] for m in monsters}
added = []

def make(name, **kw):
    if name in existing_names:
        print(f"  SKIP (exists): {name}")
        return
    entry = {
        "name": name,
        "size": "Medium",
        "type": "humanoid",
        "alignment": "any",
        "armor_class": 10,
        "hit_points": "1 (1d8)",
        "speed": "30 ft.",
        "ability_scores": {
            "strength": 10, "dexterity": 10, "constitution": 10,
            "intelligence": 10, "wisdom": 10, "charisma": 10
        },
        "senses": "passive Perception 10",
        "languages": "Common",
        "challenge_rating": 0,
        "source": "TCSR",
        "source_title": "Tal'Dorei Campaign Setting Reborn"
    }
    entry.update(kw)
    monsters.append(entry)
    added.append(name)
    print(f"  ADDED: {name} (CR {kw.get('challenge_rating', '?')})")

print("=== Adding missing TCSR monsters ===\n")

# --- Demonfeed Spider (page 238) ---
make("Demonfeed Spider",
    size="Large", type="fiend", alignment="chaotic evil",
    armor_class=16, armor_class_note="natural armor",
    hit_points="75 (10d10 + 20)",
    speed="40 ft., climb 40 ft.",
    ability_scores={"strength": 16, "dexterity": 16, "constitution": 15,
                    "intelligence": 6, "wisdom": 10, "charisma": 6},
    saving_throws="Dex +7",
    skills="Perception +3, Stealth +7",
    damage_resistances="cold, fire, lightning",
    damage_immunities="poison",
    condition_immunities="poisoned",
    senses="darkvision 120 ft., passive Perception 13",
    languages="—",
    challenge_rating=8,
    features=[
        {"name": "Spider Climb", "description": "The spider can climb difficult surfaces, including upside down on ceilings, without needing to make an ability check."},
        {"name": "Web Walker", "description": "The spider ignores movement restrictions caused by webbing."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The spider makes two melee attacks: one with its bite and one with its stinger; or the spider can make one attack with its web and one with its bite."},
        {"name": "Bite", "description": "Melee Weapon Attack: +7 to hit, reach 5 ft., one target. Hit: 8 (1d10 + 3) piercing damage plus 10 (3d6) poison damage."},
        {"name": "Stinger", "description": "Melee Weapon Attack: +7 to hit, reach 5 ft., one target. Hit: 11 (2d8 + 3) piercing damage, and the target must succeed on a DC 14 Constitution saving throw or be poisoned for 1 minute. The target can repeat the save at the end of each of its turns, ending the effect on a success."},
        {"name": "Web (Recharge 5-6)", "description": "Ranged Weapon Attack: +6 to hit, range 30/60 ft., one target. Hit: The target is restrained by webbing. As an action, the restrained target can make a DC 14 Strength check, breaking the webbing on a success. The webbing can also be attacked and destroyed (AC 10, hp 15, fire vulnerability, immunity to slashing, bludgeoning, and psychic damage)."}
    ]
)

# --- Plainscow (page 249 - found alongside Young Magma Landshark) ---
make("Plainscow",
    size="Large", type="beast", alignment="unaligned",
    armor_class=12, armor_class_note="natural armor",
    hit_points="38 (7d10)",
    speed="30 ft.",
    ability_scores={"strength": 18, "dexterity": 8, "constitution": 14,
                    "intelligence": 2, "wisdom": 10, "charisma": 4},
    senses="passive Perception 10",
    languages="—",
    challenge_rating=1/4,
    actions=[
        {"name": "Gore", "description": "Melee Weapon Attack: +6 to hit, reach 5 ft., one target. Hit: 8 (1d8 + 4) piercing damage."},
    ]
)

# --- Young Magma Landshark (page 249) ---
make("Young Magma Landshark",
    size="Large", type="elemental", alignment="neutral evil",
    armor_class=16, armor_class_note="natural armor",
    hit_points="105 (10d10 + 50)",
    speed="40 ft., burrow 40 ft., swim 40 ft. (lava only)",
    ability_scores={"strength": 20, "dexterity": 11, "constitution": 21,
                    "intelligence": 2, "wisdom": 10, "charisma": 5},
    damage_immunities="fire",
    skills="Perception +4",
    senses="darkvision 60 ft., tremorsense 120 ft., passive Perception 14",
    languages="—",
    challenge_rating=9,
    features=[
        {"name": "Searing Presence", "description": "Any creature that starts its turn within 10 feet of the landshark takes 11 (2d10) fire damage."},
        {"name": "Standing Leap", "description": "The landshark's long jump is up to 30 feet and its high jump is up to 20 feet, with or without a running start."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The landshark makes two attacks: one with its bite and one with its tail."},
        {"name": "Bite", "description": "Melee Weapon Attack: +9 to hit, reach 10 ft., one target. Hit: 16 (2d10 + 5) piercing damage and 7 (2d6) fire damage."},
        {"name": "Tail", "description": "Melee Weapon Attack: +9 to hit, reach 10 ft., one target. Hit: 11 (1d12 + 5) bludgeoning damage."},
        {"name": "Magma Geyser (Recharge 5-6)", "description": "The landshark erupts with magma in a 30-foot cone. Each creature in that area must make a DC 16 Dexterity saving throw, taking 28 (8d6) fire damage on a failure or half as much on a success."}
    ]
)

# --- Kraghammer Goat Knight (page 247) ---
# The stat block text mentions "Goat-Knight Steeds" and uses giant goat stats
make("Kraghammer Goat Knight",
    size="Medium", type="humanoid", alignment="lawful good",
    armor_class=18, armor_class_note="plate",
    hit_points="65 (10d8 + 20)",
    speed="30 ft.",
    ability_scores={"strength": 16, "dexterity": 10, "constitution": 14,
                    "intelligence": 10, "wisdom": 14, "charisma": 12},
    skills="Animal Handling +5, Athletics +6, Perception +5",
    senses="passive Perception 15",
    languages="Common, Dwarvish",
    challenge_rating=4,
    features=[
        {"name": "Mounted Combatant", "description": "While mounted, the goat knight has advantage on melee attack rolls against creatures smaller than its mount."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The goat knight makes two melee attacks with its warhammer."},
        {"name": "Warhammer", "description": "Melee Weapon Attack: +6 to hit, reach 5 ft., one target. Hit: 8 (1d8 + 4) bludgeoning damage, or 9 (1d10 + 4) bludgeoning damage if used with two hands."},
        {"name": "Heavy Crossbow", "description": "Ranged Weapon Attack: +3 to hit, range 100/400 ft., one target. Hit: 6 (1d10 + 1) piercing damage."}
    ]
)

# --- Ravager Slaughter Lord (page 250-251) ---
make("Ravager Slaughter Lord",
    size="Large", type="humanoid", alignment="chaotic evil",
    armor_class=17, armor_class_note="Unarmored Defense",
    hit_points="157 (15d10 + 75)",
    speed="30 ft.",
    ability_scores={"strength": 22, "dexterity": 14, "constitution": 20,
                    "intelligence": 12, "wisdom": 16, "charisma": 16},
    saving_throws="Str +10, Con +9, Wis +7",
    skills="Intimidation +11, Religion +5",
    senses="darkvision 60 ft., passive Perception 13",
    languages="Abyssal, Common, one other language",
    challenge_rating=9,
    features=[
        {"name": "Innate Spellcasting", "description": "The Slaughter Lord's innate spellcasting ability is Wisdom (spell save DC 15, +7 to hit with spell attacks). It can innately cast the following spells, requiring no material components: At will: thaumaturgy. 3/day each: flame strike, spirit guardians. 1/day each: control weather, divine word, fire storm."},
        {"name": "Legendary Resistance (2/Day)", "description": "If the Slaughter Lord fails a saving throw, it can choose to succeed instead."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The Slaughter Lord makes three greataxe attacks."},
        {"name": "Greataxe", "description": "Melee Weapon Attack: +10 to hit, reach 5 ft., one target. Hit: 19 (2d12 + 6) slashing damage plus 7 (2d6) necrotic damage."},
        {"name": "Frightful Presence", "description": "Each creature of the Slaughter Lord's choice within 30 feet must succeed on a DC 16 Wisdom saving throw or be frightened for 1 minute. A target can repeat the save at the end of each of its turns, ending the effect on a success."}
    ]
)

# --- Remnant Cultist (page 252-253) ---
make("Remnant Cultist",
    size="Medium", type="humanoid", alignment="neutral evil",
    armor_class=13, armor_class_note="leather armor",
    hit_points="39 (6d8 + 12)",
    speed="30 ft.",
    ability_scores={"strength": 10, "dexterity": 14, "constitution": 14,
                    "intelligence": 12, "wisdom": 10, "charisma": 14},
    skills="Arcana +3, Deception +4, Religion +3",
    senses="passive Perception 10",
    languages="Abyssal, Common, Infernal",
    challenge_rating=2,
    features=[
        {"name": "Dark Devotion", "description": "The cultist has advantage on saving throws against being charmed or frightened."},
        {"name": "One-Eyed Focus", "description": "The cultist has removed one eye as a rite of initiation. It has advantage on Perception checks that rely on sight."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The cultist makes two melee attacks with its ritual dagger."},
        {"name": "Ritual Dagger", "description": "Melee Weapon Attack: +4 to hit, reach 5 ft., one target. Hit: 5 (1d4 + 2) piercing damage plus 4 (1d8) necrotic damage."},
        {"name": "Whisper of the Void (Recharge 5-6)", "description": "The cultist whispers forbidden secrets. Each creature of the cultist's choice within 30 feet that can hear it must succeed on a DC 12 Wisdom saving throw or be stunned until the end of the cultist's next turn."}
    ]
)

# --- Rivermaw Brawler (OCR says "Vermaw Brawler") (page 255) ---
make("Rivermaw Brawler",
    size="Medium", type="humanoid", alignment="chaotic good",
    armor_class=15, armor_class_note="Unarmored Defense",
    hit_points="65 (10d8 + 20)",
    speed="40 ft.",
    ability_scores={"strength": 17, "dexterity": 14, "constitution": 15,
                    "intelligence": 14, "wisdom": 16, "charisma": 8},
    saving_throws="Str +5, Wis +5",
    skills="Acrobatics +4, Athletics +5, Perception +5",
    senses="passive Perception 15",
    languages="Common, Giant",
    challenge_rating=4,
    features=[
        {"name": "Unarmored Defense", "description": "While the brawler is wearing no armor and wielding no shield, its AC includes its Wisdom modifier."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The brawler makes three unarmed strikes."},
        {"name": "Unarmed Strike", "description": "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 7 (1d8 + 3) bludgeoning damage, and the brawler can choose one of the following additional effects: Brute: The target must succeed on a DC 14 Strength saving throw or be pushed 10 feet. Trip: The target must succeed on a DC 14 Dexterity saving throw or be knocked prone."}
    ]
)

# --- S'Skyriss Serpentfolk (page 259) ---
make("S'Skyriss Serpentfolk",
    size="Large", type="monstrosity", alignment="neutral evil",
    armor_class=14, armor_class_note="natural armor",
    hit_points="60 (8d10 + 16)",
    speed="40 ft., swim 40 ft.",
    ability_scores={"strength": 16, "dexterity": 15, "constitution": 15,
                    "intelligence": 12, "wisdom": 10, "charisma": 14},
    skills="Perception +2, Stealth +4",
    damage_immunities="poison",
    condition_immunities="poisoned",
    senses="darkvision 60 ft., passive Perception 12",
    languages="Common, Draconic",
    challenge_rating=3,
    features=[
        {"name": "Hold Breath", "description": "The serpentfolk can hold its breath for 30 minutes."},
        {"name": "Innate Spellcasting", "description": "The serpentfolk's innate spellcasting ability is Charisma (spell save DC 12). It can innately cast the following spells, requiring no material components: At will: animal friendship, speak with animals (snakes only). 2/day each: sleep, suggestion. 1/day: pass without trace."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The serpentfolk makes two scimitar attacks."},
        {"name": "Bite", "description": "Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. Hit: 6 (1d6 + 3) piercing damage plus 7 (2d6) poison damage."},
        {"name": "Scimitar", "description": "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 6 (1d6 + 3) slashing damage."},
        {"name": "Constrict", "description": "Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. Hit: 7 (1d8 + 3) bludgeoning damage, and the target is grappled (escape DC 13). Until this grapple ends, the creature is restrained, and the serpentfolk can't constrict another target."}
    ]
)

# --- Vos'skyriss Serpentfolk Ghost (page 259) ---
make("Vos'skyriss Serpentfolk Ghost",
    size="Large", type="undead", alignment="neutral evil",
    armor_class=13,
    hit_points="55 (10d10)",
    speed="0 ft., fly 40 ft. (hover)",
    ability_scores={"strength": 7, "dexterity": 16, "constitution": 10,
                    "intelligence": 12, "wisdom": 10, "charisma": 16},
    skills="Perception +2, Stealth +7",
    damage_resistances="acid, fire, lightning, thunder; bludgeoning, piercing, and slashing from nonmagical attacks",
    damage_immunities="cold, necrotic, poison",
    condition_immunities="charmed, exhaustion, frightened, grappled, paralyzed, poisoned, prone, restrained",
    senses="darkvision 60 ft., passive Perception 12",
    languages="Common, Draconic",
    challenge_rating=4,
    features=[
        {"name": "Ethereal Sight", "description": "The ghost can see 60 feet into the Ethereal Plane when it is on the Material Plane, and vice versa."},
        {"name": "Incorporeal Movement", "description": "The ghost can move through other creatures and objects as if they were difficult terrain. It takes 5 (1d10) force damage if it ends its turn inside an object."},
        {"name": "Etherealness", "description": "The ghost enters the Ethereal Plane from the Material Plane, or vice versa. It is visible on the Material Plane while in the Border Ethereal, and vice versa, yet it can't affect or be affected by anything on the other plane."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The ghost makes two spear attacks."},
        {"name": "Spear", "description": "Melee or Ranged Weapon Attack: +5 to hit, reach 5 ft. or range 20/60 ft., one target. Hit: 6 (1d6 + 3) piercing damage plus 7 (2d6) necrotic damage. If the ghost makes a ranged attack, its spear rematerializes in its hands after the attack is resolved."},
        {"name": "Terrifying Hiss", "description": "The ghost emits an uncanny, rasping hiss. Each non-undead creature within 30 feet of the ghost that can hear it must succeed on a DC 13 Wisdom saving throw or be frightened for 1 minute. A frightened target can repeat the saving throw at the end of each of its turns, ending the condition on itself on a success. If a target's saving throw is successful or the effect ends for it, the target is immune to this ghost's Terrifying Hiss for the next 24 hours."}
    ]
)

# --- Wraithroot Tree (page 260) ---
make("Wraithroot Tree",
    size="Huge", type="plant", alignment="neutral evil",
    armor_class=16, armor_class_note="natural armor",
    hit_points="270 (20d12 + 140)",
    speed="30 ft.",
    ability_scores={"strength": 23, "dexterity": 8, "constitution": 24,
                    "intelligence": 12, "wisdom": 16, "charisma": 12},
    skills="Perception +8",
    damage_resistances="bludgeoning, piercing",
    damage_vulnerabilities="fire",
    condition_immunities="stunned, frightened, charmed, paralyzed, poisoned",
    senses="passive Perception 18",
    languages="Common, Druidic, Elvish, Sylvan",
    challenge_rating=14,
    features=[
        {"name": "Vengeful Restoration", "description": "The wraithroot tree was created through foul necromancy and transmutation rituals that awakened it, gifting it with intelligence and imbuing its roots with mobility, while necromantic energy filled it with rage and evil."},
        {"name": "Siege Monster", "description": "The tree deals double damage to objects and structures."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The wraithroot tree makes two slam attacks."},
        {"name": "Slam", "description": "Melee Weapon Attack: +11 to hit, reach 10 ft., one target. Hit: 16 (3d6 + 6) bludgeoning damage."},
        {"name": "Rock", "description": "Ranged Weapon Attack: +11 to hit, range 60/180 ft., one target. Hit: 28 (4d10 + 6) bludgeoning damage."},
        {"name": "Wraithstorm (Recharge 5-6)", "description": "The wraithroot tree pulls life energy from the area around it. Each creature that is not a construct or undead within 30 feet of the tree must succeed on a DC 16 Constitution saving throw or have its speed halved and gain vulnerability to bludgeoning damage until the end of the tree's next turn."}
    ]
)

# --- Jourrael, the Caedogeist (page 246) ---
make("Jourrael, the Caedogeist",
    size="Medium", type="fiend", subtype="drow", alignment="chaotic evil",
    armor_class=19, armor_class_note="studded leather armor",
    hit_points="152 (16d8 + 80)",
    speed="80 ft., fly 40 ft.",
    ability_scores={"strength": 13, "dexterity": 24, "constitution": 20,
                    "intelligence": 14, "wisdom": 17, "charisma": 15},
    saving_throws="Dex +12, Con +10, Wis +8",
    skills="Acrobatics +12, Perception +13, Stealth +17",
    damage_resistances="acid, fire, lightning, cold, thunder; bludgeoning, piercing, and slashing from nonmagical attacks",
    damage_immunities="poison",
    condition_immunities="frightened, grappled, paralyzed, poisoned, prone, restrained",
    senses="darkvision 120 ft., passive Perception 23",
    languages="Abyssal, Common, Infernal, Undercommon",
    challenge_rating=15,
    features=[
        {"name": "Incorporeal Movement", "description": "Jourrael can move through other creatures and objects as if they were difficult terrain. She takes 5 (1d10) force damage if she ends her turn inside an object."},
        {"name": "Magic Resistance", "description": "Jourrael has advantage on saving throws against spells and other magical effects."},
        {"name": "Shadow Step", "description": "As a bonus action, Jourrael can teleport up to 60 feet to an unoccupied space she can see that is in dim light or darkness."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Jourrael makes two shortsword attacks."},
        {"name": "Shortsword", "description": "Melee Weapon Attack: +12 to hit, reach 5 ft., one target. Hit: 11 (1d6 + 7) piercing damage plus 14 (4d6) poison damage."},
        {"name": "Hand Crossbow", "description": "Ranged Weapon Attack: +12 to hit, range 30/120 ft., one target. Hit: 10 (1d6 + 7) piercing damage plus 14 (4d6) poison damage, and the target must succeed on a DC 18 Constitution saving throw or be poisoned for 1 minute."}
    ],
    legendary_actions=[
        {"name": "Move", "description": "Jourrael moves up to her speed without provoking opportunity attacks."},
        {"name": "Attack", "description": "Jourrael makes one weapon attack."},
        {"name": "Vanishing Strike (Costs 2 Actions)", "description": "Jourrael makes one weapon attack, then teleports up to 60 feet to an unoccupied space she can see."}
    ]
)

# --- Grog Strongjaw (page 262) ---
make("Grog Strongjaw",
    size="Medium", type="humanoid", subtype="half-giant", alignment="chaotic good",
    armor_class=19,
    hit_points="290 (20d12 + 120 + 40)",
    speed="50 ft.",
    ability_scores={"strength": 26, "dexterity": 15, "constitution": 22,
                    "intelligence": 8, "wisdom": 10, "charisma": 13},
    saving_throws="Str +14, Con +12",
    skills="Animal Handling +6, Athletics +14, Intimidation +7, Survival +6",
    senses="darkvision 60 ft., passive Perception 10",
    languages="Common, Dwarvish, Giant (limited reading and writing)",
    challenge_rating=18,
    features=[
        {"name": "Brutal Critical", "description": "When Grog scores a critical hit, he rolls double weapon damage dice as usual, then rolls 3 additional weapon damage dice."},
        {"name": "Feral Instinct", "description": "Grog has advantage on initiative rolls."},
        {"name": "Rage", "description": "Grog can enter a rage as a bonus action. While raging, he has advantage on Strength checks and saving throws, gains a +4 bonus to damage rolls with melee weapon attacks, and has resistance to bludgeoning, piercing, and slashing damage. The rage lasts for 1 minute or until Grog is incapacitated."},
        {"name": "Reckless Attack", "description": "When Grog makes his first attack on his turn, he can decide to attack recklessly, gaining advantage on all melee weapon attacks during that turn, but attack rolls against him have advantage until his next turn."},
        {"name": "Toughness", "description": "Grog has an additional 40 hit points (included)."},
        {"name": "Special Equipment", "description": "Grog wears the Titanstone Knuckles (see page 212)."},
        {"name": "Brutal", "description": "When Grog hits a target with a melee weapon attack and the target is a creature, he can force the target to make a DC 22 Strength saving throw or be knocked prone."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Grog makes three melee attacks."},
        {"name": "Bloodaxe", "description": "Melee Weapon Attack: +14 to hit, reach 5 ft., one target. Hit: 19 (2d8 + 8) slashing damage, or 21 (2d10 + 8) slashing damage when used with two hands."},
        {"name": "Bloodaxe Throw", "description": "Ranged Weapon Attack: +14 to hit, range 30/60 ft., one target. Hit: 18 (4d4 + 8) slashing damage."},
        {"name": "Fist", "description": "Melee Weapon Attack: +14 to hit, reach 5 ft., one target. Hit: 11 (1d4 + 8) bludgeoning damage."}
    ]
)

# --- Keyleth, Voice of the Tempest (page 264) ---
make("Keyleth, Voice of the Tempest",
    size="Medium", type="humanoid", subtype="half-elf", alignment="chaotic good",
    armor_class=17, armor_class_note="+2 leather armor, +2 ring of protection",
    hit_points="150 (20d8 + 60)",
    speed="30 ft.",
    ability_scores={"strength": 14, "dexterity": 15, "constitution": 16,
                    "intelligence": 15, "wisdom": 22, "charisma": 15},
    saving_throws="Str +4, Dex +4, Con +5, Int +10, Wis +14, Cha +4",
    skills="Athletics +8, Insight +12, Intimidation +8, Nature +12, Perception +12, Persuasion +8, Stealth +8, Survival +12",
    senses="darkvision 60 ft., passive Perception 22",
    languages="Auran, Common, Druidic, Elvish",
    challenge_rating=18,
    features=[
        {"name": "Fey Ancestry", "description": "Keyleth has advantage on saving throws against being charmed, and magic can't put her to sleep."},
        {"name": "Focused", "description": "Keyleth has advantage on Constitution saving throws made to maintain concentration on spells."},
        {"name": "Special Equipment", "description": "Keyleth wears the Spire of Conflux (see page 209)."},
        {"name": "Spellcasting", "description": "Keyleth is an 18th-level spellcaster. Her spellcasting ability is Wisdom (spell save DC 20, +12 to hit with spell attacks). She has the following druid spells prepared: Cantrips (at will): druidcraft, guidance, mending, shillelagh. 1st level (4 slots): animal friendship, detect magic, entangle, faerie fire, fog cloud, speak with animals, thunderwave. 2nd level (3 slots): beast sense, darkvision, enlarge/reduce, heat metal, hold person, lesser restoration, moonbeam, pass without trace. 3rd level (3 slots): call lightning, conjure animals, dispel magic, plant growth, sleet storm, speak with plants, tidal wave. 4th level (3 slots): blight, conjure woodland beings, control water, dominate beast, freedom of the waves, hallucinatory terrain, stone shape. 5th level (3 slots): commune with nature, conjure elemental, greater restoration, reincarnate, tree stride, wrath of nature. 6th level (2 slots): heal, heroes' feast, sunbeam, transport via plants, wind walk. 7th level (2 slots): fire storm, plane shift. 8th level (1 slot): animal shapes, control weather. 9th level (1 slot): shapechange."},
        {"name": "Wild Shape", "description": "Keyleth can use her action to magically assume the shape of a beast she has seen before. She can use this feature 2 times and regains expended uses on a short or long rest. While wild shaped, she retains her game statistics but uses the target beast's Strength, Dexterity, and Constitution."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Keyleth makes two melee attacks."},
        {"name": "Shillelagh", "description": "Melee Weapon Attack: +12 to hit, reach 5 ft., one target. Hit: 12 (1d8 + 6) bludgeoning damage plus 9 (2d8) lightning damage."}
    ]
)

# --- Percival de Rolo (page 267) ---
make("Percival de Rolo",
    size="Medium", type="humanoid", subtype="human", alignment="chaotic good",
    armor_class=18, armor_class_note="studded leather",
    hit_points="190 (20d10 + 40 + 40)",
    speed="30 ft.",
    ability_scores={"strength": 12, "dexterity": 22, "constitution": 14,
                    "intelligence": 20, "wisdom": 16, "charisma": 14},
    saving_throws="Str +7, Con +8",
    skills="Acrobatics +12, History +11, Perception +9, Persuasion +8",
    damage_resistances="lightning",
    senses="passive Perception 19",
    languages="Celestial, Common, Elvish",
    challenge_rating=18,
    features=[
        {"name": "Ranged Master", "description": "Percy's ranged weapon attacks have a +2 bonus to hit (included in attacks), ignore half cover and three-quarters cover, and have no range penalty. Additionally, Percy can take a -5 penalty to any ranged attack roll to gain a +10 bonus to the damage roll."},
        {"name": "Special Equipment", "description": "Percy wears a ring of lightning resistance and Cabal's Ruin (see page 203). He wields Animus and Bad News, which are both +1 weapons."},
        {"name": "Quick Draw", "description": "Percy adds his proficiency bonus to initiative rolls, giving him a +12 to initiative rolls."},
        {"name": "Toughness", "description": "Percy has an additional 40 hit points (included)."},
        {"name": "Action Surge (2/Short Rest)", "description": "Once on his turn, Percy can take one additional action."},
        {"name": "Cabal's Ruin", "description": "Cabal's Ruin grants Percy advantage on saving throws against spells or magical effects. Additionally, Cabal's Ruin has 10 charges. When Percy hits with a weapon attack, he can expend up to 10 charges to have the attack deal 1d6 extra lightning damage per charge spent."},
        {"name": "Indomitable (3/Long Rest)", "description": "Percy can reroll a failed saving throw."},
        {"name": "Misfire", "description": "When Percy rolls a 2 or lower on the d20 when making an attack with Animus or Bad News, the attack misses and he can't use the weapon again until he spends an action to repair it. On a misfire with Animus, he takes 7 (2d6) psychic damage."},
        {"name": "No Mercy", "description": "Percy's weapon attacks score a critical hit on a roll of 19-20."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Percy makes four ranged attacks with Animus, or two ranged attacks with Bad News. When Percy hits with an attack with either weapon, he can choose one of the following additional effects: Deadeye: Percy gains advantage on his next attack roll this turn made against the same target. Disarm: The target must make a DC 20 Strength saving throw. On a failure, the target drops one item it is holding of Percy's choice."},
        {"name": "Animus", "description": "Ranged Weapon Attack: +15 to hit, range 320 ft., one target. Hit: 11 (1d10 + 6) piercing damage and 3 (1d6) psychic damage."},
        {"name": "Bad News", "description": "Ranged Weapon Attack: +15 to hit, range 800 ft., one target. Hit: 19 (2d12 + 6) piercing damage."},
        {"name": "Absorb Magic (Recharges after a Short or Long Rest)", "description": "When Percy is targeted by an enemy's spell, he can absorb a portion of the spell's energy into Cabal's Ruin. The spell affects him normally, but the cloak gains a number of charges equal to the level of the spell. When he does so, he also gains resistance to one type of damage dealt by the spell until the end of his next turn."}
    ]
)

# --- Vex'ahlia (page 268) ---
make("Vex'ahlia",
    size="Medium", type="humanoid", subtype="half-elf", alignment="chaotic good",
    armor_class=21, armor_class_note="+2 studded leather, +2 ring of protection",
    hit_points="130 (20d10 + 20)",
    speed="30 ft., fly 50 ft. (with broom of flying)",
    ability_scores={"strength": 7, "dexterity": 20, "constitution": 12,
                    "intelligence": 14, "wisdom": 16, "charisma": 17},
    saving_throws="Str +6, Dex +13, Con +3, Int +4, Wis +5, Cha +5",
    skills="Acrobatics +17, Athletics +4, Deception +9, Insight +9, Investigation +8, Perception +15, Persuasion +9, Stealth +17, Survival +9",
    damage_resistances="cold",
    senses="darkvision 60 ft., passive Perception 25",
    languages="Abyssal, Common, Draconic, Elvish, thieves' cant, Undercommon",
    challenge_rating=18,
    features=[
        {"name": "Fey Ancestry", "description": "Vex has advantage on saving throws against being charmed, and magic can't put her to sleep."},
        {"name": "Special Equipment", "description": "Vex wears +2 studded leather armor and a +2 ring of protection. She carries a broom of flying and a raven's slumber (see page 197), and wields Fenthras (see page 205)."},
        {"name": "Spellcasting", "description": "Vex is a 13th-level spellcaster. Her spellcasting ability is Wisdom (spell save DC 17, +9 to hit with spell attacks). She has the following ranger spells prepared: 1st level (4 slots): cure wounds, entangle, hail of thorns, hunter's mark. 2nd level (3 slots): healing spirit, pass without trace, silence. 3rd level (3 slots): conjure barrage, conjure volley, lightning arrow. 4th level (1 slot): freedom of the waves."},
        {"name": "Favored Foe", "description": "Once on each of her turns when Vex hits a creature with a weapon attack, she can mark the target as her favored foe for 1 minute, dealing an extra 1d4 damage to it on each hit."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Vex makes three ranged attacks with Fenthras, or two ranged attacks with Fenthras and one melee attack with her dagger."},
        {"name": "Fenthras", "description": "Ranged Weapon Attack: +13 to hit, range 600 ft., one target. Hit: 12 (1d8 + 7) piercing damage plus 3 (1d6) lightning damage. Vex can use one of the following additional effects: Bramble Shot (2/Short Rest): The attack deals an extra 18 (4d8) piercing damage, and the target must succeed on a DC 17 Strength saving throw or be restrained. The target can repeat the saving throw at the end of each of its turns, ending the effect on itself on a success."},
        {"name": "Dagger", "description": "Melee or Ranged Weapon Attack: +13 to hit, reach 5 ft. or range 20/60 ft., one target. Hit: 6 (1d4 + 5) piercing damage."}
    ],
    bonus_actions=[
        {"name": "Cunning Action", "description": "Vex takes the Dash, Disengage, or Hide action."},
        {"name": "Hunter's Mark", "description": "Vex casts hunter's mark to mark a creature she can see within 90 feet of her. For 1 hour, Vex's weapon attacks deal an extra 1d6 damage to the marked creature, and she has advantage on Wisdom (Perception) and Wisdom (Survival) checks made to find it."}
    ],
    reactions=[
        {"name": "Uncanny Dodge", "description": "When an attacker that Vex can see hits her with an attack, she takes half damage from the attack."}
    ]
)

# --- Pike Trickfoot (page 270) ---
make("Pike Trickfoot",
    size="Small", type="humanoid", subtype="gnome", alignment="chaotic good",
    armor_class=23, armor_class_note="Plate of the Dawnmartyr, shield",
    hit_points="170 (20d8 + 80)",
    speed="25 ft.",
    ability_scores={"strength": 19, "dexterity": 12, "constitution": 18,
                    "intelligence": 14, "wisdom": 20, "charisma": 14},
    saving_throws="Wis +11, Cha +8",
    skills="Athletics +10, Perception +11, Persuasion +8, Religion +8, Stealth +7",
    damage_resistances="fire; bludgeoning, piercing, and slashing from nonmagical attacks",
    condition_immunities="frightened",
    senses="darkvision 60 ft., passive Perception 21",
    languages="Common, Dwarvish, Gnomish, Undercommon",
    challenge_rating=17,
    features=[
        {"name": "Gnome Cunning", "description": "Pike has advantage on Intelligence, Wisdom, and Charisma saving throws against magic."},
        {"name": "Special Equipment", "description": "Pike wields a mace of disruption and wears gauntlets of ogre power, boots of speed, and the Plate of the Dawnmartyr (see page 202)."},
        {"name": "Spellcasting", "description": "Pike is a 19th-level spellcaster. Her spellcasting ability is Wisdom (spell save DC 19, +11 to hit with spell attacks). She has the following cleric spells prepared: Cantrips (at will): guidance, light, mending, sacred flame, thaumaturgy. 1st level (4 slots): bless, cure wounds, detect magic, guiding bolt, healing word, sanctuary, shield of faith. 2nd level (3 slots): aid, gentle repose, hold person, lesser restoration, prayer of healing, spiritual weapon. 3rd level (3 slots): beacon of hope, dispel magic, mass healing word, revivify, spirit guardians, tongues. 4th level (3 slots): banishment, death ward, guardian of faith, freedom of the waves. 5th level (3 slots): commune, greater restoration, mass cure wounds, raise dead, scrying. 6th level (2 slots): heal, word of recall. 7th level (2 slots): divine word, fire storm, regenerate. 8th level (1 slot): holy aura. 9th level (1 slot): mass heal."},
        {"name": "Divine Intervention", "description": "Pike can call upon the Everlight for aid. Once per 7 days, she can use her action to request intervention (automatic success as the Everlight always answers)."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Pike makes two melee attacks with her mace of disruption."},
        {"name": "Mace of Disruption", "description": "Melee Weapon Attack: +12 to hit, reach 5 ft., one target. Hit: 10 (1d6 + 7) bludgeoning damage plus 7 (2d6) radiant damage. If the target is a fiend or undead, it takes an extra 10 (3d6) radiant damage and must succeed on a DC 15 Wisdom saving throw or be destroyed on a hit that reduces it to 25 hit points or fewer."},
        {"name": "Turn Undead (3/Short Rest)", "description": "Pike presents her holy symbol and speaks a prayer. Each undead within 30 feet that can see or hear her must make a DC 19 Wisdom saving throw. On a failure, it is turned for 1 minute or until it takes damage."}
    ]
)

# --- Scanlan Shorthalt (page 272) ---
make("Scanlan Shorthalt",
    size="Small", type="humanoid", subtype="gnome", alignment="chaotic good",
    armor_class=16, armor_class_note="+2 studded leather, +2 ring of protection",
    hit_points="190 (20d8 + 60 + 40)",
    speed="25 ft.",
    ability_scores={"strength": 13, "dexterity": 11, "constitution": 16,
                    "intelligence": 16, "wisdom": 7, "charisma": 22},
    saving_throws="Str +3, Dex +7, Con +5, Int +5, Wis +0, Cha +13",
    skills="Acrobatics +5, Arcana +8, Athletics +6, Deception +16, Intimidation +11, Investigation +13, Performance +16, Persuasion +16",
    damage_resistances="acid",
    senses="darkvision 60 ft., passive Perception 8",
    languages="Common, Gnomish, Marquesian",
    challenge_rating=15,
    features=[
        {"name": "Gnome Cunning", "description": "Scanlan has advantage on Intelligence, Wisdom, and Charisma saving throws against magic."},
        {"name": "Focused", "description": "Scanlan has advantage on Constitution saving throws made to maintain concentration on spells."},
        {"name": "Toughness", "description": "Scanlan has an additional 40 hit points (included)."},
        {"name": "Spellcasting", "description": "Scanlan is an 18th-level spellcaster. His spellcasting ability is Charisma (spell save DC 20, +12 to hit with spell attacks). He has the following bard spells prepared: Cantrips (at will): mage hand, message, prestidigitation, vicious mockery. 1st level (4 slots): charm person, detect magic, healing word, illusory script, thunderwave, unseen servant. 2nd level (3 slots): cloud of daggers, enlarge/reduce, heat metal, invisibility, shatter, suggestion. 3rd level (3 slots): counterspell, hypnotic pattern, lightning bolt, stinking cloud, tiny hut. 4th level (3 slots): dimension door, polymorph, resilient sphere. 5th level (3 slots): arcane hand, dominate person, modify memory, seeming. 6th level (2 slots): eyebite. 7th level (2 slots): magnificent mansion, reverse gravity. 8th level (1 slot): dominate monster. 9th level (1 slot): true polymorph."}
    ],
    actions=[
        {"name": "Multiattack", "description": "Scanlan makes two attacks with Mythcarver, or one attack and casts a cantrip."},
        {"name": "Mythcarver", "description": "Melee Weapon Attack: +9 to hit, reach 5 ft., one target. Hit: 7 (1d8 + 3) piercing damage and 3 (1d6) force damage."}
    ],
    bonus_actions=[
        {"name": "Arcane Fist (8th Level)", "description": "Melee Spell Attack: +11 to hit, reach 5 ft., one target. Hit: 45 (10d8) force damage. Scanlan can use this bonus action only while concentrating on the arcane hand spell."},
        {"name": "Bardic Inspiration (6/Short Rest)", "description": "Scanlan grants a Bardic Inspiration die to one creature other than himself within 60 feet of him who can hear him. Once within the next 10 minutes, the creature can roll a d12 and add the number rolled to one ability check, attack roll, or saving throw it makes. When Scanlan uses this ability, he gains advantage on attack rolls with Mythcarver until the end of his turn."},
        {"name": "Healing Word (5th Level)", "description": "Scanlan casts healing word, restoring 18 (5d4 + 6) hit points to himself or another creature he can see within 60 feet of him."}
    ],
    reactions=[
        {"name": "Cutting Words", "description": "When a creature Scanlan can see within 60 feet of him makes an attack roll, an ability check, or a damage roll, he can expend a use of Bardic Inspiration to roll a d12 and subtract the number rolled from the creature's roll. The creature also has disadvantage on its next saving throw."}
    ]
)

# --- Doty X (page 273) ---
make("Doty X",
    size="Medium", type="construct", alignment="unaligned",
    armor_class=17, armor_class_note="natural armor",
    hit_points="78 (9d8 + 18 + 20)",
    speed="40 ft.",
    ability_scores={"strength": 14, "dexterity": 12, "constitution": 14,
                    "intelligence": 4, "wisdom": 10, "charisma": 6},
    saving_throws="Dex +4, Con +5",
    skills="Athletics +7, Perception +8",
    damage_immunities="poison",
    condition_immunities="charmed, exhaustion, poisoned",
    senses="darkvision 60 ft., passive Perception 18",
    languages="understands Common but can't speak (limited vocabulary: 'Tary', 'Yes', 'Correct', 'Absolutely', 'Soon', 'Handsome')",
    challenge_rating=6,
    features=[
        {"name": "Vigilant", "description": "Doty can't be surprised."},
        {"name": "Dedicated Servant", "description": "Doty has an additional 20 hit points (included), and has a +2 modifier to saving throws, ability checks using skill proficiencies, and attack rolls."},
        {"name": "Limited Vocabulary", "description": "Doty can say the following words in Common: 'Tary', 'Yes', 'Correct', 'Absolutely', 'Soon', and 'Handsome.'"}
    ],
    actions=[
        {"name": "Empowered Fist", "description": "Melee Weapon Attack: +7 to hit, reach 5 ft., one target. Hit: 8 (1d8 + 4) force damage."},
        {"name": "Repair (3/Day)", "description": "Doty regains 15 (2d8 + 6) hit points."}
    ],
    reactions=[
        {"name": "Deflect", "description": "If a creature within 5 feet of Doty that he can see makes an attack against a creature other than Doty, he can impose disadvantage on the attack roll. The attacker then takes 6 (1d4 + 4) force damage."}
    ]
)

# --- Champion of Ravens (page 276) ---
make("Champion of Ravens",
    size="Medium", type="celestial", alignment="lawful neutral",
    armor_class=20, armor_class_note="Deathwalker's Ward",
    hit_points="150 (20d10 + 40)",
    speed="30 ft., fly 60 ft.",
    ability_scores={"strength": 14, "dexterity": 20, "constitution": 14,
                    "intelligence": 16, "wisdom": 14, "charisma": 17},
    saving_throws="Str +5, Dex +15, Con +5, Int +13, Wis +5, Cha +6",
    skills="Acrobatics +9, Intimidation +10, Investigation +10, Perception +16, Persuasion +10, Sleight of Hand +12, Stealth +19",
    damage_resistances="radiant, necrotic, poison; bludgeoning, piercing, and slashing from nonmagical attacks",
    condition_immunities="charmed, exhaustion, frightened, unconscious",
    senses="darkvision 60 ft., passive Perception 26",
    languages="Abyssal, Celestial, Common, Druidic, Elvish, thieves' cant",
    challenge_rating=21,
    features=[
        {"name": "Assassinate", "description": "During his first turn, the Champion has advantage on attack rolls against any creature that hasn't taken a turn. Any hit the Champion scores against a surprised creature is a critical hit."},
        {"name": "Aura of Protection", "description": "While the Champion is conscious, he and friendly creatures within 10 feet of him have a +3 bonus to saving throws (included above)."},
        {"name": "Eternal Champion", "description": "When the Champion is reduced to 0 hit points or dies, his body is destroyed but his spirit returns to the Raven Queen's side, and he gains a new body after 1d4 days."},
        {"name": "Evasion", "description": "If the Champion is subjected to an effect that allows him to make a Dexterity saving throw to take only half damage, he instead takes no damage if he succeeds on the saving throw, and only half damage if he fails."},
        {"name": "Fate-Touched (4/Day)", "description": "When the Champion makes an attack roll, ability check, or saving throw, he can reroll the d20 and use either roll. Alternatively, when a creature the Champion can see makes an attack roll against him, or a saving throw against one of his spells or features, he can force that creature to reroll the d20 and use the new roll."},
        {"name": "Special Equipment", "description": "The Champion wears the Deathwalker's Ward and wields Whisper (see page 211), and wears boots of haste (see page 194)."},
        {"name": "Unerring Sight", "description": "The Champion's ranged weapon attacks ignore half cover and three-quarters cover and have no range penalty."}
    ],
    actions=[
        {"name": "Multiattack", "description": "The Champion makes three attacks with Whisper."},
        {"name": "Whisper", "description": "Melee or Ranged Weapon Attack: +15 to hit, reach 5 ft. or range 60 ft., one target. Hit: 10 (1d4 + 8) piercing damage plus 4 (1d8) psychic damage. On a critical hit, the target must succeed on a DC 18 Wisdom saving throw or be frightened of the Champion for 1 minute. On a ranged attack, the Champion can teleport with Whisper, appearing within 5 feet of the target on a hit, or in a random space within 30 feet of the target on a miss."},
        {"name": "Touch of Life and Death (Recharges after a Short or Long Rest)", "description": "The Champion touches a creature within 5 feet of him, and chooses to either heal or harm that creature, restoring 55 (10d10) hit points or dealing 55 (10d10) necrotic damage. If the creature is an unwilling target, it can avoid either effect with a successful DC 20 Dexterity saving throw."}
    ],
    bonus_actions=[
        {"name": "Cunning Action", "description": "The Champion takes the Dash, Disengage, or Hide action."},
        {"name": "Boots of Haste (1/Day)", "description": "The Champion clicks his heels together, casting haste on himself. He doesn't suffer from lethargy when the spell ends."}
    ],
    reactions=[
        {"name": "Uncanny Dodge", "description": "The Champion halves the damage that he takes from an attack that hits him. He must be able to see the attacker."}
    ]
)

# Save
with open(os.path.join(DATA_DIR, 'monsters.json'), 'w') as f:
    json.dump(monsters, f, indent=2)

print(f"\n=== Summary ===")
print(f"Added: {len(added)} monsters")
print(f"Total in file: {len(monsters)}")
print(f"Source: TCSR (Tal'Dorei Campaign Setting Reborn)")
