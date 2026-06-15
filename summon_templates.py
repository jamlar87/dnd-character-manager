# ── Summon / Familiar Templates ─────────────────────────────────────────
# 
# monster_index: fetch real stats from monster DB via /api/dm/monster/{index}
# spell_base_level + spell_level_field: Tasha summons scale with spell level
# hp_scaling / ac_scaling / atk_scaling: per-spell-level above base
# feature_descs: full ability descriptions (used when not fetching from monster DB)

SUMMON_TEMPLATES: dict[str, dict] = {
    # ── Find Familiar forms (PHB p.240) — pulled from monster DB ──
    "familiar_bat": {
        "name": "Bat", "category": "familiar", "source": "find_familiar",
        "monster_index": "bat",
    },
    "familiar_cat": {
        "name": "Cat", "category": "familiar", "source": "find_familiar",
        "monster_index": "cat",
    },
    "familiar_hawk": {
        "name": "Hawk", "category": "familiar", "source": "find_familiar",
        "monster_index": "hawk",
    },
    "familiar_owl": {
        "name": "Owl", "category": "familiar", "source": "find_familiar",
        "monster_index": "owl",
    },
    "familiar_rat": {
        "name": "Rat", "category": "familiar", "source": "find_familiar",
        "monster_index": "rat",
    },
    "familiar_raven": {
        "name": "Raven", "category": "familiar", "source": "find_familiar",
        "monster_index": "raven",
    },
    "familiar_weasel": {
        "name": "Weasel", "category": "familiar", "source": "find_familiar",
        "monster_index": "weasel",
    },
    "familiar_spider": {
        "name": "Spider", "category": "familiar", "source": "find_familiar",
        "monster_index": "spider",
    },
    "familiar_snake": {
        "name": "Poisonous Snake", "category": "familiar", "source": "find_familiar",
        "monster_index": "poisonous-snake",
    },
    "familiar_octopus": {
        "name": "Octopus", "category": "familiar", "source": "find_familiar",
        "monster_index": "octopus",
    },

    # ── Pact of the Chain special familiars (PHB p.107) — pulled from monster DB ──
    "chain_imp": {
        "name": "Imp", "category": "pact_chain", "source": "pact_of_the_chain",
        "monster_index": "imp",
    },
    "chain_pseudodragon": {
        "name": "Pseudodragon", "category": "pact_chain", "source": "pact_of_the_chain",
        "monster_index": "pseudodragon",
    },
    "chain_quasit": {
        "name": "Quasit", "category": "pact_chain", "source": "pact_of_the_chain",
        "monster_index": "quasit",
    },
    "chain_sprite": {
        "name": "Sprite", "category": "pact_chain", "source": "pact_of_the_chain",
        "monster_index": "sprite",
    },

    # ── Mounts (PHB p.157, MM) — pulled from monster DB ──
    "mount_warhorse": {
        "name": "Warhorse", "category": "mount", "source": "mount",
        "monster_index": "warhorse",
    },
    "mount_riding_horse": {
        "name": "Riding Horse", "category": "mount", "source": "mount",
        "monster_index": "riding-horse",
    },
    "mount_pony": {
        "name": "Pony", "category": "mount", "source": "mount",
        "monster_index": "pony",
    },
    "mount_mastiff": {
        "name": "Mastiff", "category": "mount", "source": "mount",
        "monster_index": "mastiff",
    },
    "mount_camel": {
        "name": "Camel", "category": "mount", "source": "mount",
        "monster_index": "camel",
    },
    "mount_draft_horse": {
        "name": "Draft Horse", "category": "mount", "source": "mount",
        "monster_index": "draft-horse",
    },
    "mount_mule": {
        "name": "Mule", "category": "mount", "source": "mount",
        "monster_index": "mule",
    },
    "mount_elk": {
        "name": "Elk", "category": "mount", "source": "mount",
        "monster_index": "elk",
    },

    # ── Vehicles (PHB p.157, DMG p.119, GoS) — manual stats ──
    "vehicle_cart": {
        "name": "Cart", "category": "vehicle", "source": "vehicle",
        "ac_base": 10, "hp_base": 30, "hp_note": "Pulled by 1 draft animal. Holds 1/2 ton cargo.",
        "size": "Large", "speed": "as draft animal",
        "stats": {"str":14,"dex":10,"con":12,"int":2,"wis":11,"cha":5},
        "features": ["Cargo Hauler","Animal-Pulled"],
        "feature_descs": {
            "Cargo Hauler": "Carries up to 500 lb of cargo plus 2 passengers.",
            "Animal-Pulled": "Speed depends on the draft animal pulling it.",
        },
    },
    "vehicle_wagon": {
        "name": "Wagon", "category": "vehicle", "source": "vehicle",
        "ac_base": 10, "hp_base": 50, "hp_note": "Pulled by 2 draft animals. Holds 1 ton cargo.",
        "size": "Large", "speed": "as draft animals",
        "stats": {"str":16,"dex":10,"con":12,"int":2,"wis":11,"cha":5},
        "features": ["Cargo Hauler","Covered Option"],
        "feature_descs": {
            "Cargo Hauler": "Carries up to 1 ton of cargo plus 4 passengers.",
            "Covered Option": "Can be fitted with canvas cover for weather protection.",
        },
    },
    "vehicle_rowboat": {
        "name": "Rowboat", "category": "vehicle", "source": "vehicle",
        "ac_base": 11, "hp_base": 50, "hp_note": "Oars: 2 crew. Swim 1.5 mph.",
        "size": "Large", "speed": "swim 15 ft.",
        "stats": {"str":8,"dex":10,"con":12,"int":0,"wis":0,"cha":0},
        "features": ["Oars"],
        "feature_descs": {
            "Oars": "Requires 2 crew to row. Moves 1.5 mph over water.",
        },
    },
    "vehicle_keelboat": {
        "name": "Keelboat", "category": "vehicle", "source": "vehicle",
        "ac_base": 15, "hp_base": 100, "hp_note": "Crew: 3. Passengers: 4. Cargo: 1/2 ton.",
        "size": "Gargantuan", "speed": "swim 10 ft., sail 30 ft.",
        "stats": {"str":16,"dex":12,"con":14,"int":0,"wis":0,"cha":0},
        "features": ["Oars","Sail","Ballista"],
        "feature_descs": {
            "Oars": "Can be rowed at 1 mph when wind is unfavorable.",
            "Sail": "Moves 3 mph under sail with favorable wind.",
            "Ballista": "Carries 1 ballista (3d10 piercing, range 120/480 ft).",
        },
    },
    "vehicle_sailing_ship": {
        "name": "Sailing Ship", "category": "vehicle", "source": "vehicle",
        "ac_base": 15, "hp_base": 300, "hp_note": "Crew: 20. Passengers: 20. Cargo: 100 tons.",
        "size": "Gargantuan", "speed": "sail 30 ft.",
        "stats": {"str":20,"dex":12,"con":16,"int":0,"wis":0,"cha":0},
        "features": ["Sails","2 Ballistas","Cargo Hold"],
        "feature_descs": {
            "Sails": "Moves 3 mph under sail in favorable wind.",
            "2 Ballistas": "Armed with 2 ballistas (3d10 piercing, range 120/480 ft).",
            "Cargo Hold": "Carries 100 tons of cargo in addition to crew and passengers.",
        },
    },
    "vehicle_longship": {
        "name": "Longship", "category": "vehicle", "source": "vehicle",
        "ac_base": 15, "hp_base": 300, "hp_note": "Crew: 40. Passengers: 100. Cargo: 10 tons.",
        "size": "Gargantuan", "speed": "row 20 ft., sail 45 ft.",
        "stats": {"str":20,"dex":14,"con":16,"int":0,"wis":0,"cha":0},
        "features": ["Oars + Sail","Shallow Draft","Beaching"],
        "feature_descs": {
            "Oars + Sail": "Moves 5 mph under sail, 2 mph under oar against wind.",
            "Shallow Draft": "Navigates rivers and shallows that larger ships can't reach.",
            "Beaching": "Can be beached on shore without a dock.",
        },
    },
    "vehicle_warship": {
        "name": "Warship", "category": "vehicle", "source": "vehicle",
        "ac_base": 15, "hp_base": 500, "hp_note": "Crew: 40. Passengers: 60. Cargo: none.",
        "size": "Gargantuan", "speed": "sail 25 ft.",
        "stats": {"str":22,"dex":12,"con":18,"int":0,"wis":0,"cha":0},
        "features": ["Sails","2 Ballistas + Ram","Reinforced Hull"],
        "feature_descs": {
            "Sails": "Moves 2.5 mph under sail.",
            "2 Ballistas + Ram": "2 ballistas + reinforced ram (5d10 bludgeoning on collision).",
            "Reinforced Hull": "Thick hull gives high durability in naval combat.",
        },
    },
    "vehicle_galley": {
        "name": "Galley", "category": "vehicle", "source": "vehicle",
        "ac_base": 15, "hp_base": 500, "hp_note": "Crew: 80. Passengers: 40. Cargo: 150 tons.",
        "size": "Gargantuan", "speed": "row 30 ft.",
        "stats": {"str":22,"dex":10,"con":18,"int":0,"wis":0,"cha":0},
        "features": ["Oars","Ram","2 Ballistas"],
        "feature_descs": {
            "Oars": "80 crew row at 3 mph. Does not require wind.",
            "Ram": "Reinforced ram deals 5d10 bludgeoning on ship collision.",
            "2 Ballistas": "Armed with 2 ballistas (3d10 piercing, range 120/480 ft).",
        },
    },
    "vehicle_airship": {
        "name": "Airship", "category": "vehicle", "source": "vehicle",
        "ac_base": 13, "hp_base": 300, "hp_note": "Crew: 10. Passengers: 20. Cargo: 1 ton. Fly 8 mph.",
        "size": "Gargantuan", "speed": "fly 80 ft. (hover)",
        "stats": {"str":14,"dex":12,"con":14,"int":0,"wis":0,"cha":0},
        "features": ["Elemental-Powered","Hover","Ballista"],
        "feature_descs": {
            "Elemental-Powered": "A bound fire elemental provides lift. Losing the elemental causes the ship to descend at 30 ft/round.",
            "Hover": "Can hover in place indefinitely without moving.",
            "Ballista": "Armed with 1 ballista (3d10 piercing, range 120/480 ft).",
        },
    },

    # ── Tasha's Summon Spells (DTCOE) — calculated from character level ──
    "tashas_beast": {
        "name": "Summon Beast", "category": "tashas_summon", "source": "summon_beast",
        "spell_base_level": 2,
        "ac_base": 11, "ac_scaling": 0,
        "hp_base": 30, "hp_scaling": 5,
        "atk_bonus_base": 6, "atk_bonus_scaling": 0,
        "size": "Small", "speed": "30 ft.",
        "stats": {"str":18,"dex":11,"con":16,"int":4,"wis":14,"cha":5},
        "senses": "darkvision 60 ft., passive Perception 12",
        "features": ["Pack Tactics (land)","Flyby (air)","Swim 30 ft (water)"],
        "feature_descs": {
            "Pack Tactics (land)": "The beast has advantage on an attack roll against a creature if at least one of the beast's allies is within 5 feet of the creature and the ally isn't incapacitated.",
            "Flyby (air)": "The beast doesn't provoke opportunity attacks when it flies out of an enemy's reach.",
            "Swim 30 ft (water)": "The beast gains a swimming speed of 30 ft and can breathe water.",
        },
        "attacks": [{"name":"Maul","damage_base":"1d8","damage_scaling":0}],
    },
    "tashas_undead": {
        "name": "Summon Undead", "category": "tashas_summon", "source": "summon_undead",
        "spell_base_level": 3,
        "ac_base": 11, "ac_scaling": 0,
        "hp_base": 30, "hp_scaling": 10,
        "atk_bonus_base": 5, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "30 ft.",
        "stats": {"str":14,"dex":8,"con":15,"int":6,"wis":10,"cha":5},
        "senses": "darkvision 60 ft., passive Perception 10",
        "features": ["Ghostly / Putrid / Skeletal form","Festering Aura (putrid)","Grave Bolt (skeletal, ranged 150 ft)"],
        "feature_descs": {
            "Ghostly / Putrid / Skeletal form": "Choose a form when casting. Ghostly: fly 30 ft (hover), can move through creatures/objects as difficult terrain, deals 1d10 force if ending turn inside. Putrid: Festering Aura poisons nearby creatures. Skeletal: Grave Bolt ranged attack, darkvision 60 ft.",
            "Festering Aura (putrid)": "At the start of each of the undead's turns, each creature within 5 feet of it must succeed on a Constitution save or be poisoned until the start of the undead's next turn.",
            "Grave Bolt (skeletal, ranged 150 ft)": "Ranged Spell Attack: your spell attack modifier to hit, range 150 ft., one target. Hit: 2d4 + 3 + spell level necrotic damage.",
        },
        "attacks": [{"name":"Deathly Touch","damage_base":"1d8","damage_scaling":0}],
    },
    "tashas_fey": {
        "name": "Summon Fey", "category": "tashas_summon", "source": "summon_fey",
        "spell_base_level": 3,
        "ac_base": 14, "ac_scaling": 0,
        "hp_base": 30, "hp_scaling": 10,
        "atk_bonus_base": 6, "atk_bonus_scaling": 0,
        "size": "Small", "speed": "30 ft.",
        "stats": {"str":8,"dex":18,"con":14,"int":14,"wis":14,"cha":16},
        "senses": "darkvision 60 ft., passive Perception 12",
        "features": ["Fuming / Mirthful / Tricksy mood","Fey Step (bonus action teleport 30 ft)"],
        "feature_descs": {
            "Fuming / Mirthful / Tricksy mood": "Choose a mood when casting. Fuming: advantage on attacks vs charmed/frightened creatures. Mirthful: charm a creature (DC 13 Wis) as a bonus action for 1 min. Tricksy: fill a 5-ft cube within 30 ft with magical darkness until end of next turn.",
            "Fey Step (bonus action teleport 30 ft)": "The fey magically teleports up to 30 feet to an unoccupied space it can see. It then has advantage on the next attack roll it makes before the end of that turn.",
        },
        "attacks": [{"name":"Gleaming Blade","damage_base":"1d10","damage_scaling":0}],
    },
    "tashas_shadowspawn": {
        "name": "Summon Shadowspawn", "category": "tashas_summon", "source": "summon_shadowspawn",
        "spell_base_level": 3,
        "ac_base": 14, "ac_scaling": 0,
        "hp_base": 35, "hp_scaling": 15,
        "atk_bonus_base": 6, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "40 ft.",
        "stats": {"str":13,"dex":16,"con":16,"int":4,"wis":10,"cha":16},
        "senses": "darkvision 120 ft., passive Perception 10",
        "features": ["Fury / Despair / Fear form","Dreadful Scream (fear form, 30 ft AoE)"],
        "feature_descs": {
            "Fury / Despair / Fear form": "Choose a form when casting. Fury: advantage on attack rolls vs frightened creatures. Despair: creatures within 5 ft have disadvantage on saves. Fear: Dreadful Scream AoE fear.",
            "Dreadful Scream (fear form, 30 ft AoE)": "The shadowspawn screams. Each creature of its choice within 30 feet must succeed on a Wisdom save or be frightened for 1 minute. A creature can repeat the save at end of its turns.",
        },
        "attacks": [{"name":"Chilling Rend","damage_base":"1d12","damage_scaling":0}],
    },
    "tashas_aberration": {
        "name": "Summon Aberration", "category": "tashas_summon", "source": "summon_aberration",
        "spell_base_level": 4,
        "ac_base": 14, "ac_scaling": 0,
        "hp_base": 40, "hp_scaling": 10,
        "atk_bonus_base": 5, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "30 ft.",
        "stats": {"str":14,"dex":14,"con":16,"int":16,"wis":6,"cha":4},
        "senses": "darkvision 120 ft., passive Perception 8",
        "features": ["Beholderkin / Slaad / Star Spawn form","Regeneration 5 (star spawn)"],
        "feature_descs": {
            "Beholderkin / Slaad / Star Spawn form": "Choose a form when casting. Beholderkin: fly 30 ft (hover), eye rays. Slaad: regeneration 5 at start of each turn. Star Spawn: psychic mirror — reflects 1d6 psychic to attackers within 10 ft.",
            "Regeneration 5 (star spawn)": "The aberration regains 5 hit points at the start of its turn if it has at least 1 hit point.",
        },
        "attacks": [{"name":"Multiattack (2 claws)","damage_base":"1d8","damage_scaling":0}],
    },
    "tashas_fiend_devil": {
        "name": "Summon Fiend (Devil)", "category": "tashas_summon", "source": "summon_fiend",
        "spell_base_level": 6,
        "ac_base": 12, "ac_scaling": 1,
        "hp_base": 40, "hp_scaling": 15,
        "atk_bonus_base": 6, "atk_bonus_scaling": 1,
        "size": "Large", "speed": "40 ft., fly 60 ft.",
        "stats": {"str":13,"dex":16,"con":15,"int":10,"wis":10,"cha":16},
        "senses": "darkvision 60 ft., Devil's Sight, passive Perception 10",
        "features": ["Devil's Sight","Magic Resistance"],
        "feature_descs": {
            "Devil's Sight": "Magical darkness doesn't impede the fiend's darkvision.",
            "Magic Resistance": "The fiend has advantage on saving throws against spells and other magical effects.",
        },
        "attacks": [{"name":"Hurl Flame (ranged 150 ft)","damage_base":"2d6","damage_scaling":1}],
    },
    "tashas_fiend_demon": {
        "name": "Summon Fiend (Demon)", "category": "tashas_summon", "source": "summon_fiend",
        "spell_base_level": 6,
        "ac_base": 12, "ac_scaling": 1,
        "hp_base": 50, "hp_scaling": 15,
        "atk_bonus_base": 6, "atk_bonus_scaling": 1,
        "size": "Large", "speed": "40 ft., climb 40 ft.",
        "stats": {"str":13,"dex":16,"con":15,"int":10,"wis":10,"cha":16},
        "senses": "darkvision 60 ft., passive Perception 10",
        "features": ["Death Throes (2d10+spell lvl fire AoE)","Magic Resistance"],
        "feature_descs": {
            "Death Throes (2d10+spell lvl fire AoE)": "When the fiend drops to 0 hit points or the spell ends, the fiend explodes. Each creature within 10 feet must make a Dex save vs your spell DC, taking 2d10 + spell level fire damage on a failed save, or half on a success.",
            "Magic Resistance": "The fiend has advantage on saving throws against spells and other magical effects.",
        },
        "attacks": [{"name":"Bite","damage_base":"1d12","damage_scaling":1}],
    },
    "tashas_fiend_yugoloth": {
        "name": "Summon Fiend (Yugoloth)", "category": "tashas_summon", "source": "summon_fiend",
        "spell_base_level": 6,
        "ac_base": 12, "ac_scaling": 1,
        "hp_base": 60, "hp_scaling": 15,
        "atk_bonus_base": 6, "atk_bonus_scaling": 1,
        "size": "Large", "speed": "40 ft., climb 40 ft.",
        "stats": {"str":13,"dex":16,"con":15,"int":10,"wis":10,"cha":16},
        "senses": "darkvision 60 ft., passive Perception 10",
        "features": ["Teleport 30 ft after claw","Magic Resistance"],
        "feature_descs": {
            "Teleport 30 ft after claw": "Immediately after making a claw attack (hit or miss), the fiend can magically teleport up to 30 feet to an unoccupied space it can see.",
            "Magic Resistance": "The fiend has advantage on saving throws against spells and other magical effects.",
        },
        "attacks": [{"name":"Claws + Teleport","damage_base":"1d8","damage_scaling":1}],
    },

    # ── Class Summons ──
    "wildfire_spirit": {
        "name": "Wildfire Spirit", "category": "class_feature", "source": "Circle of Wildfire",
        "spell_base_level": 0,
        "ac_base": 13, "ac_scaling": 0,
        "hp_base": 5, "hp_per_level": 5,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Small", "speed": "30 ft., fly 30 ft. (hover)",
        "stats": {"str":10,"dex":14,"con":14,"int":13,"wis":15,"cha":11},
        "senses": "darkvision 60 ft., passive Perception 12",
        "features": ["Flame Seed","Fiery Teleportation"],
        "feature_descs": {
            "Flame Seed": "Ranged Spell Attack: your spell attack modifier to hit, range 60 ft., one target you can see. Hit: 1d6 + PB fire damage.",
            "Fiery Teleportation": "The spirit and each willing creature of your choice within 5 feet of it teleport up to 15 feet. Then each creature within 5 feet of the space the spirit left must succeed on a Dex save vs your spell DC or take 1d6 + PB fire damage.",
        },
        "attacks": [{"name":"Flame Seed (60 ft)","damage_base":"1d6","damage_scaling":0}],
    },
    "steel_defender": {
        "name": "Steel Defender", "category": "class_feature", "source": "Battle Smith",
        "spell_base_level": 0,
        "ac_base": 15, "ac_scaling": 0,
        "hp_base": 2, "hp_per_level": 5,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "40 ft.",
        "stats": {"str":14,"dex":12,"con":14,"int":4,"wis":10,"cha":6},
        "skills": "Perception +4",
        "senses": "darkvision 60 ft., passive Perception 14",
        "features": ["Deflect Attack","Repair","Vigilant"],
        "feature_descs": {
            "Deflect Attack": "The defender imposes disadvantage on the attack roll of one creature it can see that is within 5 feet of it, provided the attack roll is against a creature other than the defender.",
            "Repair": "The magical mechanisms inside the defender restore 2d8 + PB hit points to itself or to one construct or object within 5 feet of it. (3/day)",
            "Vigilant": "The defender can't be surprised.",
        },
        "attacks": [{"name":"Bite","damage_base":"1d8","damage_scaling":0}],
    },
    # ── Hexblade Warlock: Accursed Specter (XGtE p.56, MM p.279) ──
    "accursed_specter": {
        "name": "Accursed Specter", "category": "class_feature", "source": "Accursed Specter",
        "monster_index": "specter",
        "hp_note": "Add temp HP = ½ warlock level. Attack bonus + CHA mod. Max one at a time.",
        "size": "Medium", "speed": "0 ft., fly 50 ft. (hover)",
    },
}
