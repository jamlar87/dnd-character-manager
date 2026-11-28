"""Subclass feature text, part 2 of 7 (merged in __init__)."""

SUBCLASS_PART2: dict[str, str] = {
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

}
