"""Subclass feature text, part 3 of 7 (merged in __init__)."""

SUBCLASS_PART3: dict[str, str] = {
    # ── Wizard: School of Illusion (PHB p.118) ──
    "illusion savant":
        "At 2nd level, the gold and time you must spend to copy an illusion spell into your "
        "spellbook is halved.",
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
}
