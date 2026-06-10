#!/usr/bin/env python3
"""Deep audit: XGE spells comparison."""
import json, fitz
from pathlib import Path

MANUAL = Path("/home/james/dnd-character-manager/data/manual_data")
SRD = Path("/home/james/dnd-character-manager/data/srd_cache")

# Load what we have
with open(MANUAL / "spells.json") as f:
    manual_spells = json.load(f)

xge_ours = set()
for s in manual_spells:
    if s.get("_source_manual") == "XGE":
        xge_ours.add(s["name"].lower())

with open(SRD / "spells.json") as f:
    srd_spells = json.load(f)
srd_names = {s.get("name", "").lower() for s in srd_spells}

# Official XGE spell list
XGE_SPELL_LIST = [
    "Control Flames", "Create Bonfire", "Frostbite", "Gust", "Infestation",
    "Magic Stone", "Mold Earth", "Primal Savagery", "Shape Water", "Thunderclap", "Toll the Dead", "Word of Radiance",
    "Absorb Elements", "Beast Bond", "Catapult", "Cause Fear", "Ceremony", "Chaos Bolt",
    "Ice Knife", "Snare", "Zephyr Strike",
    "Aganazzar's Scorcher", "Dragon's Breath", "Dust Devil", "Earthbind", "Healing Spirit",
    "Maximillian's Earthen Grasp", "Mind Spike", "Pyrotechnics", "Shadow Blade", "Skywrite",
    "Snilloc's Snowball Swarm", "Warding Wind",
    "Catnap", "Enemies Abound", "Erupting Earth", "Flame Arrows", "Life Transference",
    "Melf's Minute Meteors", "Summon Lesser Demons", "Thunder Step",
    "Tidal Wave", "Tiny Servant", "Wall of Sand", "Wall of Water",
    "Charm Monster", "Elemental Bane", "Guardian of Nature", "Shadow of Moil",
    "Sickening Radiance", "Storm Sphere", "Summon Greater Demon", "Vitriolic Sphere", "Watery Sphere",
    "Control Winds", "Danse Macabre", "Dawn", "Enervation", "Far Step",
    "Holy Weapon", "Immolation", "Infernal Calling", "Maelstrom", "Negative Energy Flood",
    "Skill Empowerment", "Steel Wind Strike", "Synaptic Static",
    "Transmute Rock", "Wall of Light", "Wrath of Nature",
    "Bones of the Earth", "Druid Grove", "Investiture of Flame", "Investiture of Ice",
    "Investiture of Stone", "Investiture of Wind", "Mental Prison", "Scatter", "Soul Cage",
    "Tenser's Transformation",
    "Crown of Stars", "Power Word Pain", "Temple of the Gods", "Whirlwind",
    "Abi-Dalzim's Horrid Wilting", "Illusory Dragon", "Maddening Darkness", "Mighty Fortress",
    "Invulnerability", "Mass Polymorph", "Psychic Scream",
]

official = set(n.lower() for n in XGE_SPELL_LIST)

in_srd = official & srd_names
only_manual = official & xge_ours
missing = official - xge_ours - srd_names

print(f"XGE official: {len(official)} spells")
print(f"  In manual (XGE-tagged): {len(only_manual)}")
print(f"  In SRD: {len(in_srd)}")
print(f"  MISSING: {len(missing)}\n")

if missing:
    print("MISSING XGE SPELLS:")
    for name in sorted(missing):
        print(f"  x {name.title()}")
else:
    print("All XGE spells accounted for!")

# What extras do we have?
extra = xge_ours - official
if extra:
    print(f"\nExtra XGE-tagged (not in official list, {len(extra)}):")
    for name in sorted(extra):
        print(f"  ? {name.title()}")
