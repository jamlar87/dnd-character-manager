# ── Summon / Familiar Templates ─────────────────────────────────────────
# 
# monster_index: fetch real stats from monster DB via /api/dm/monster/{index}
# spell_base_level + spell_level_field: Tasha summons scale with spell level
# hp_scaling / ac_scaling / atk_scaling: per-spell-level above base

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
        "features": ["Bestial Spirit (air/land/water)","Pack Tactics (land)","Flyby (air)","Swim 30 ft (water)"],
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
        "features": ["Flame Seed (1d6+PB fire, 60 ft)","Fiery Teleportation (15 ft AoE)"],
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
        "features": ["Deflect Attack (reaction)","Repair (3/day, 2d8+PB)","Vigilant"],
        "attacks": [{"name":"Bite","damage_base":"1d8","damage_scaling":0}],
    },
}
