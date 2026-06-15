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

    # ── Druid Wild Shape / Conjure Animals (PHB p.66, p.225) ──
    "druid_brown_bear": {
        "name": "Brown Bear (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "brown-bear",
    },
    "druid_dire_wolf": {
        "name": "Dire Wolf (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "dire-wolf",
    },
    "druid_giant_eagle": {
        "name": "Giant Eagle (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-eagle",
    },
    "druid_giant_hyena": {
        "name": "Giant Hyena (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-hyena",
    },
    "druid_giant_spider": {
        "name": "Giant Spider (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-spider",
    },
    "druid_giant_toad": {
        "name": "Giant Toad (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-toad",
    },
    "druid_giant_octopus": {
        "name": "Giant Octopus (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-octopus",
    },
    "druid_tiger": {
        "name": "Tiger (CR 1)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "tiger",
    },
    "druid_giant_constrictor": {
        "name": "Giant Constrictor Snake (CR 2)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-constrictor-snake",
    },
    "druid_giant_elk": {
        "name": "Giant Elk (CR 2)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-elk",
    },
    "druid_giant_boar": {
        "name": "Giant Boar (CR 2)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-boar",
    },
    "druid_polar_bear": {
        "name": "Polar Bear (CR 2)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "polar-bear",
    },
    "druid_saber_tooth": {
        "name": "Saber-Toothed Tiger (CR 2)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "saber-toothed-tiger",
    },
    "druid_giant_scorpion": {
        "name": "Giant Scorpion (CR 3)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-scorpion",
    },
    "druid_killer_whale": {
        "name": "Killer Whale (CR 3)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "killer-whale",
    },
    "druid_elephant": {
        "name": "Elephant (CR 4)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "elephant",
    },
    "druid_giant_crocodile": {
        "name": "Giant Crocodile (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-crocodile",
    },
    "druid_giant_shark": {
        "name": "Giant Shark (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "giant-shark",
    },
    "druid_triceratops": {
        "name": "Triceratops (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "triceratops",
    },
    # Elemental wild shapes (Moon Druid 10+)
    "druid_air_elemental": {
        "name": "Air Elemental (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "air-elemental",
    },
    "druid_earth_elemental": {
        "name": "Earth Elemental (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "earth-elemental",
    },
    "druid_fire_elemental": {
        "name": "Fire Elemental (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "fire-elemental",
    },
    "druid_water_elemental": {
        "name": "Water Elemental (CR 5)", "category": "druid_wildshape", "source": "wild_shape",
        "monster_index": "water-elemental",
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

    
    # ── Ranger Beast Master Companions (PHB p.93, CR ≤ 1/4, ≤ Medium) ──
    "ranger_wolf": {
        "name": "Wolf", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "wolf",
    },
    "ranger_panther": {
        "name": "Panther", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "panther",
    },
    "ranger_giant_badger": {
        "name": "Giant Badger", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-badger",
    },
    "ranger_boar": {
        "name": "Boar", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "boar",
    },
    "ranger_giant_poisonous_snake": {
        "name": "Giant Poisonous Snake", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-poisonous-snake",
    },
    "ranger_giant_wolf_spider": {
        "name": "Giant Wolf Spider", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-wolf-spider",
    },
    "ranger_mastiff": {
        "name": "Mastiff", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "mastiff",
    },
    "ranger_blood_hawk": {
        "name": "Blood Hawk", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "blood-hawk",
    },
    "ranger_flying_snake": {
        "name": "Flying Snake", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "flying-snake",
    },
    "ranger_giant_weasel": {
        "name": "Giant Weasel", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-weasel",
    },
    "ranger_giant_crab": {
        "name": "Giant Crab", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-crab",
    },
    "ranger_poisonous_snake": {
        "name": "Poisonous Snake", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "poisonous-snake",
    },
    "ranger_stirge": {
        "name": "Stirge", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "stirge",
    },
    "ranger_giant_fire_beetle": {
        "name": "Giant Fire Beetle", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-fire-beetle",
    },
    "ranger_badger": {
        "name": "Badger", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "badger",
    },
    "ranger_crab": {
        "name": "Crab", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "crab",
    },
    "ranger_giant_frog": {
        "name": "Giant Frog", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-frog",
    },
    "ranger_giant_lizard": {
        "name": "Giant Lizard", "category": "ranger_companion", "source": "Beast Master",
        "monster_index": "giant-lizard",
    },
    # Manual stats (not in SRD)
    "ranger_pteranodon": {
        "name": "Pteranodon", "category": "ranger_companion", "source": "Beast Master",
        "ac_base": 13, "hp_base": 13, "hp_note": "3d8", "size": "Medium", "speed": "10 ft., fly 60 ft.",
        "stats": {"str":12,"dex":15,"con":10,"int":2,"wis":9,"cha":5},
        "features": ["Flyby"],
        "feature_descs": {
            "Flyby": "The pteranodon doesn't provoke opportunity attacks when it flies out of an enemy's reach.",
        },
        "atk_name": "Bite", "atk_bonus": 3, "atk_damage": "1d4+1", "atk_type": "piercing",
    },
    "ranger_velociraptor": {
        "name": "Velociraptor", "category": "ranger_companion", "source": "Beast Master",
        "ac_base": 13, "hp_base": 10, "hp_note": "3d4+3", "size": "Small", "speed": "30 ft.",
        "stats": {"str":6,"dex":14,"con":13,"int":4,"wis":12,"cha":6},
        "features": ["Pack Tactics","Multiattack"],
        "feature_descs": {
            "Pack Tactics": "Advantage on attack rolls if an ally is within 5 ft. of the target.",
            "Multiattack": "The velociraptor makes two attacks: one with its bite and one with its claws.",
        },
        "atk_name": "Bite", "atk_bonus": 4, "atk_damage": "1d6+2", "atk_type": "piercing",
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

    # ── Siege Equipment (DMG p.255) — manual stats ──
    "siege_ballista": {
        "name": "Ballista", "category": "siege", "source": "siege",
        "ac_base": 15, "hp_base": 50, "hp_note": "3d10 piercing. Range 120/480 ft. Crew: 1 action to load+aim+fire.",
        "size": "Large", "speed": "0 ft. (stationary, can be moved on wheels)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Bolt","Armor Piercing"],
        "feature_descs": {
            "Bolt": "Ranged Weapon Attack: +6 to hit, range 120/480 ft, one target. Hit: 16 (3d10) piercing damage.",
            "Armor Piercing": "Ballista bolts ignore nonmagical damage resistance.",
        },
    },
    "siege_mangonel": {
        "name": "Mangonel", "category": "siege", "source": "siege",
        "ac_base": 15, "hp_base": 100, "hp_note": "5d10 bludgeoning. Range 200/800 ft. Min 60 ft. Crew: 2 actions.",
        "size": "Large", "speed": "0 ft. (stationary)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Mangonel Stone"],
        "feature_descs": {
            "Mangonel Stone": "Ranged Weapon Attack: +5 to hit, range 200/800 ft (can't hit within 60 ft), one target. Hit: 27 (5d10) bludgeoning damage.",
        },
    },
    "siege_trebuchet": {
        "name": "Trebuchet", "category": "siege", "source": "siege",
        "ac_base": 15, "hp_base": 150, "hp_note": "8d10 bludgeoning. Range 300/1200 ft. Min 60 ft. Crew: 3 actions.",
        "size": "Huge", "speed": "0 ft. (stationary)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Trebuchet Stone"],
        "feature_descs": {
            "Trebuchet Stone": "Ranged Weapon Attack: +5 to hit, range 300/1200 ft (can't hit within 60 ft), one target. Hit: 44 (8d10) bludgeoning damage.",
        },
    },
    "siege_cannon": {
        "name": "Cannon", "category": "siege", "source": "siege",
        "ac_base": 19, "hp_base": 75, "hp_note": "8d10 bludgeoning. Range 600/2400 ft. Crew: 3 actions. Needs gunpowder.",
        "size": "Large", "speed": "0 ft. (stationary)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Cannon Ball"],
        "feature_descs": {
            "Cannon Ball": "Ranged Weapon Attack: +6 to hit, range 600/2400 ft, one target. Hit: 44 (8d10) bludgeoning damage. Misfire: cannon takes the damage.",
        },
    },
    "siege_ram": {
        "name": "Battering Ram", "category": "siege", "source": "siege",
        "ac_base": 15, "hp_base": 100, "hp_note": "3d10 vs structures. Double damage to structures. Crew: 4.",
        "size": "Large", "speed": "as crew pushing (15 ft)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Ram","Siege Monster"],
        "feature_descs": {
            "Ram": "Melee Weapon Attack: +8 to hit, reach 5 ft, one object or structure. Hit: 16 (3d10) bludgeoning.",
            "Siege Monster": "The ram deals double damage to objects and structures.",
        },
    },
    "siege_tower": {
        "name": "Siege Tower", "category": "siege", "source": "siege",
        "ac_base": 15, "hp_base": 200, "hp_note": "Holds 10 crew. Provides full cover. Crew: 10 to move.",
        "size": "Gargantuan", "speed": "as crew pushing (10 ft)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Mobile Cover","Boarding Ramp"],
        "feature_descs": {
            "Mobile Cover": "Creatures inside the tower have total cover from attacks outside.",
            "Boarding Ramp": "When adjacent to a wall, creatures inside can move onto the wall as part of their movement.",
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
    # ── Summon Elemental (Tasha's, 4th level) — 4 forms ──
    "tashas_elemental": {
        "name": "Summon Elemental", "category": "tashas_summon", "source": "summon_elemental",
        "spell_base_level": 4,
        "ac_base": 15, "ac_scaling": 0,
        "hp_base": 40, "hp_scaling": 10,
        "atk_bonus_base": 8, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "30 ft. (Air: fly 30 ft hover, Earth: burrow 30 ft, Water: swim 30 ft)",
        "stats": {"str":18,"dex":15,"con":17,"int":4,"wis":10,"cha":16},
        "senses": "darkvision 60 ft., passive Perception 10",
        "features": ["Air / Earth / Fire / Water form","Amorphous Form (air/water/fire)"],
        "feature_descs": {
            "Air / Earth / Fire / Water form": "Air: fly 30 ft hover, slam deals lightning. Earth: burrow 30 ft, slam deals bludgeoning, resists nonmagical B/P/S. Fire: immune fire, slam deals fire, sheds light 30 ft. Water: swim 30 ft, slam deals cold, can breathe water.",
            "Amorphous Form (air/water/fire)": "The elemental can move through a space as narrow as 1 inch without squeezing.",
        },
        "attacks": [{"name":"Slam","damage_base":"1d10","damage_scaling":0}],
    },
    # ── Summon Draconic Spirit (Fizban's, 5th level) ──
    "tashas_dragon": {
        "name": "Summon Draconic Spirit", "category": "tashas_summon", "source": "summon_draconic_spirit",
        "spell_base_level": 5,
        "ac_base": 14, "ac_scaling": 1,
        "hp_base": 50, "hp_scaling": 10,
        "atk_bonus_base": 8, "atk_bonus_scaling": 0,
        "size": "Large", "speed": "30 ft., fly 60 ft.",
        "stats": {"str":19,"dex":14,"con":17,"int":10,"wis":14,"cha":14},
        "senses": "blindsight 30 ft., darkvision 60 ft., passive Perception 12",
        "features": ["Draconic Essence","Breath Weapon","Shared Resistances"],
        "feature_descs": {
            "Draconic Essence": "Choose a dragon type when casting: Metallic (AC+1, temp HP 5/spell lvl), Chromatic (extra 1d6 dmg on Rend), or Gem (fly speed 70 ft, psionic 1d6 psychic).",
            "Breath Weapon": "The spirit exhales energy in a 30-foot cone. Each creature in the area makes a Dex save vs your spell DC, taking 2d6 damage of the dragon's type on a failure, or half on success. Damage increases by 1d6 per spell level above 5th.",
            "Shared Resistances": "You and the spirit gain resistance to the damage type associated with the dragon's essence while within 30 ft of each other.",
        },
        "attacks": [{"name":"Rend","damage_base":"1d6","damage_scaling":0},{"name":"Breath (30 ft cone)","damage_base":"2d6","damage_scaling":1}],
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

    # ── Spell-Created Persistent Objects ──
    "spell_bigbys_hand": {
        "name": "Bigby's Hand", "category": "spell_summon", "source": "bigbys_hand",
        "spell_base_level": 5,
        "ac_base": 20, "ac_scaling": 0,
        "hp_base": 0, "hp_per_level": 0,
        "atk_bonus_base": 8, "atk_bonus_scaling": 0,
        "size": "Large", "speed": "fly 60 ft.",
        "stats": {"str":26,"dex":10,"con":18,"int":0,"wis":0,"cha":0},
        "features": ["Clenched Fist","Forceful Hand","Grasping Hand","Interposing Hand"],
        "feature_descs": {
            "Clenched Fist": "Melee Spell Attack: your spell attack to hit, reach 5 ft, one target. Hit: 4d8 force damage.",
            "Forceful Hand": "Shove a creature within 5 ft. STR check = 26 (+8). Push 5 ft + 5\u00d7spell level above 5th.",
            "Grasping Hand": "Grapple a Huge or smaller creature within 5 ft. Escape DC = your spell DC. Deals 2d6+spell lvl bludgeoning each turn grappled.",
            "Interposing Hand": "The hand interposes between you and a creature. The creature can't move through the hand's space if its STR \u2264 26.",
        },
        "hp_note": "HP = your hit point maximum. Uses your spell attack bonus.",
    },
    "spell_spiritual_weapon": {
        "name": "Spiritual Weapon", "category": "spell_summon", "source": "spiritual_weapon",
        "spell_base_level": 2,
        "ac_base": 18, "ac_scaling": 0,
        "hp_base": 0, "hp_per_level": 0,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Small", "speed": "fly 20 ft. (hover, bonus action move)",
        "stats": {"str":1,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Bonus Action Attack","Force Damage"],
        "feature_descs": {
            "Bonus Action Attack": "As a bonus action, move up to 20 ft and make a melee spell attack. Hit: 1d8 + spellcasting mod force damage.",
            "Force Damage": "Damage increases by 1d8 for every 2 spell levels above 2nd.",
        },
        "hp_note": "HP = your hit point maximum (for dispel/break). Uses your spell attack.",
    },
    "spell_guardian_of_faith": {
        "name": "Guardian of Faith", "category": "spell_summon", "source": "guardian_of_faith",
        "ac_base": 18, "hp_base": 60, "hp_note": "Damages hostiles entering 10 ft radius. Fades after 60 dmg dealt.",
        "size": "Large", "speed": "0 ft. (stationary)",
        "stats": {"str":1,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Radiant Rebuke"],
        "feature_descs": {
            "Radiant Rebuke": "Hostile entering 10 ft: Dex save vs your DC. Fail: 20 radiant. Pass: half. Guardian fades after dealing 60 total damage.",
        },
    },
    "spell_mordenkainens_sword": {
        "name": "Mordenkainen's Sword", "category": "spell_summon", "source": "mordenkainens_sword",
        "spell_base_level": 7,
        "ac_base": 19, "hp_base": 40, "hp_note": "Bonus action melee spell atk, 3d10 force. Move 20 ft.",
        "size": "Small", "speed": "fly 20 ft.",
        "stats": {"str":1,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Floating Blade"],
        "feature_descs": {
            "Floating Blade": "Bonus action: move 20 ft and melee spell atk (your spell attack bonus). Hit: 3d10 force damage.",
        },
    },
    "spell_animate_objects": {
        "name": "Animated Object (Tiny)", "category": "spell_summon", "source": "animate_objects",
        "ac_base": 18, "hp_base": 20, "hp_per_level": 2, "hp_note": "Up to 10 objects. Each +8 atk, 1d4+4 dmg.",
        "size": "Tiny", "speed": "fly 30 ft.",
        "stats": {"str":4,"dex":18,"con":10,"int":3,"wis":3,"cha":1},
        "features": ["Blindsight 30 ft"],
        "feature_descs": {
            "Blindsight 30 ft": "Blindsight out to 30 ft. Blind beyond this radius.",
        },
        "attacks": [{"name":"Slam","damage_base":"1d4","damage_scaling":0}],
    },
    # ── Artificer Companions ──
    "eldritch_cannon": {
        "name": "Eldritch Cannon", "category": "class_feature", "source": "Artillerist",
        "spell_base_level": 0,
        "ac_base": 18, "ac_scaling": 0,
        "hp_base": 0, "hp_per_level": 5,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Small", "speed": "0 ft. (climb 15 ft with legs)",
        "stats": {"str":10,"dex":10,"con":10,"int":0,"wis":0,"cha":0},
        "features": ["Flamethrower","Force Ballista","Protector"],
        "feature_descs": {
            "Flamethrower": "15 ft cone, Dex save vs DC. 2d8 fire (+1d8 per slot above 1st). Half on success.",
            "Force Ballista": "Ranged Spell Atk, 120 ft, 2d8 force (+1d8 per slot above 1st). Push 5 ft.",
            "Protector": "Bonus action: grant 1d8+INT temp HP to allies within 10 ft.",
        },
    },
    "homunculus_servant": {
        "name": "Homunculus Servant", "category": "class_feature", "source": "Artificer",
        "spell_base_level": 0,
        "ac_base": 13, "ac_scaling": 0,
        "hp_base": 1, "hp_per_level": 5,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Tiny", "speed": "20 ft., fly 30 ft.",
        "stats": {"str":4,"dex":15,"con":12,"int":10,"wis":10,"cha":7},
        "senses": "darkvision 60 ft., passive Perception 10",
        "features": ["Evasion","Channel Magic"],
        "feature_descs": {
            "Evasion": "Dex save for half dmg: takes none on success, half on failure.",
            "Channel Magic": "Delivers touch spells for you as a reaction. +4 to Stealth, Perception.",
        },
    },
    # ── Tasha's Beast Master: Primal Companions ──
    "primal_beast_land": {
        "name": "Primal Companion: Beast of Land", "category": "class_feature", "source": "Beast Master",
        "spell_base_level": 0,
        "ac_base": 13, "ac_scaling": 0,
        "hp_base": 5, "hp_per_level": 5,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "40 ft., climb 40 ft.",
        "stats": {"str":14,"dex":14,"con":15,"int":8,"wis":14,"cha":11},
        "senses": "darkvision 60 ft., passive Perception 12",
        "features": ["Charge","Primal Bond"],
        "feature_descs": {
            "Charge": "Move 20 ft straight and hit: +1d6 dmg, target DC 12 STR or prone.",
            "Primal Bond": "Add PB to AC, atk, dmg, saves, and skills. HP = 5 + 5\u00d7ranger level (max 105). Uses spell atk modifier.",
        },
        "attacks": [{"name":"Maul","damage_base":"1d8","damage_scaling":0}],
    },
    "primal_beast_sea": {
        "name": "Primal Companion: Beast of Sea", "category": "class_feature", "source": "Beast Master",
        "spell_base_level": 0,
        "ac_base": 13, "ac_scaling": 0,
        "hp_base": 5, "hp_per_level": 5,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Medium", "speed": "5 ft., swim 60 ft.",
        "stats": {"str":14,"dex":14,"con":15,"int":8,"wis":14,"cha":11},
        "senses": "darkvision 60 ft., passive Perception 12",
        "features": ["Amphibious","Primal Bond"],
        "feature_descs": {
            "Amphibious": "Can breathe both air and water.",
            "Primal Bond": "Add PB to AC, atk, dmg, saves, and skills. HP = 5 + 5\u00d7ranger level (max 105). Uses spell atk modifier.",
        },
        "attacks": [{"name":"Tail","damage_base":"1d8","damage_scaling":0}],
    },
    "primal_beast_sky": {
        "name": "Primal Companion: Beast of Sky", "category": "class_feature", "source": "Beast Master",
        "spell_base_level": 0,
        "ac_base": 13, "ac_scaling": 0,
        "hp_base": 4, "hp_per_level": 4,
        "atk_bonus_base": 0, "atk_bonus_scaling": 0,
        "size": "Small", "speed": "10 ft., fly 60 ft.",
        "stats": {"str":6,"dex":16,"con":13,"int":8,"wis":14,"cha":11},
        "senses": "darkvision 60 ft., passive Perception 12",
        "features": ["Flyby","Primal Bond"],
        "feature_descs": {
            "Flyby": "Doesn't provoke opportunity attacks when flying out of enemy reach.",
            "Primal Bond": "Add PB to AC, atk, dmg, saves, and skills. HP = 4 + 4\u00d7ranger level (max 84). Uses spell atk modifier.",
        },
        "attacks": [{"name":"Shred","damage_base":"1d6","damage_scaling":0}],
    },
    # ── Warlock: Summon Demon (XGtE) — common CR picks via monster_index ──
    "summon_lesser_demons": {
        "name": "Summon Lesser Demons", "category": "class_feature", "source": "summon_lesser_demons",
        "monster_index": "quasit",
        "hp_note": "Summons 4-8 demons CR 1/2 or lower (Dretch, Manes, Quasit). Roll randomly.",
        "size": "Small", "speed": "varies",
    },
    "summon_greater_demon": {
        "name": "Summon Greater Demon", "category": "class_feature", "source": "summon_greater_demon",
        "monster_index": "barlgura",
        "hp_note": "Summons 1 demon up to CR 5. Demon may break free each round (CHA save vs DC).",
        "size": "Large", "speed": "varies",
    },

}
