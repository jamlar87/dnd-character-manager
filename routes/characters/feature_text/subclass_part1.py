"""Subclass feature text, part 1 of 7 (merged in __init__)."""

SUBCLASS_PART1: dict[str, str] = {
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
}
