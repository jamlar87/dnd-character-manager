#!/usr/bin/env python3
"""Gap analysis across all source books."""
import json
from pathlib import Path
from collections import defaultdict

MANUAL = Path("/home/james/dnd-character-manager/data/manual_data")
SRD = Path("/home/james/dnd-character-manager/data/srd_cache")

our_by_book = defaultdict(lambda: defaultdict(list))
for fname, dtype in [
    ("spells.json", "spells"), ("magic_items.json", "magic_items"),
    ("equipment.json", "equipment"), ("feats.json", "feats"),
    ("backgrounds.json", "backgrounds"), ("subclasses.json", "subclasses"),
    ("races.json", "races"),
]:
    fpath = MANUAL / fname
    if not fpath.exists():
        continue
    with open(fpath) as f:
        for item in json.load(f):
            src = item.get("_source_manual", "unknown")
            name = item.get("name", "")
            if name:
                our_by_book[src][dtype].append(name)

EXPECTED = {
    "PHB": {"feats": 42, "backgrounds": 13, "races": 9, "subclasses": 40},
    "DMG": {"magic_items": 380},
    "XGE": {"spells": 91, "magic_items": 28, "feats": 15, "subclasses": 27},
    "EEPC": {"spells": 43, "races": 4},
    "SCAG": {"backgrounds": 12, "subclasses": 12, "spells": 4},
    "GGR": {"magic_items": 30, "backgrounds": 10, "races": 6, "feats": 8},
    "VGM": {"races": 13},
    "MTF": {"races": 5},
}

print("=== PER-BOOK COUNTS ===\n")
for book in sorted(EXPECTED.keys()):
    our = our_by_book.get(book, {})
    exp = EXPECTED[book]
    print(f"{book}:")
    for dtype, exp_count in exp.items():
        our_count = len(our.get(dtype, []))
        status = "OK" if our_count >= exp_count else f"SHORT {exp_count - our_count}"
        print(f"  {dtype}: {our_count}/{exp_count} {status}")
    print()

# XGE spells deep check
with open(MANUAL / "spells.json") as f:
    spells = json.load(f)

all_spell_names = {s["name"].lower() for s in spells}

# Also check SRD
with open(SRD / "spells.json") as f:
    srd_names = {s.get("name","").lower() for s in json.load(f)}

# XGE spells full list
xge_list = [n.lower() for n in [
    "Control Flames","Create Bonfire","Frostbite","Gust","Infestation",
    "Magic Stone","Mold Earth","Primal Savagery","Shape Water","Thunderclap",
    "Toll the Dead","Word of Radiance",
    "Absorb Elements","Beast Bond","Catapult","Cause Fear","Ceremony","Chaos Bolt",
    "Ice Knife","Snare","Zephyr Strike",
    "Aganazzar's Scorcher","Dragon's Breath","Dust Devil","Earthbind","Healing Spirit",
    "Maximillian's Earthen Grasp","Mind Spike","Pyrotechnics","Shadow Blade","Skywrite",
    "Snilloc's Snowball Swarm","Warding Wind",
    "Catnap","Enemies Abound","Erupting Earth","Flame Arrows","Life Transference",
    "Melf's Minute Meteors","Summon Lesser Demons","Thunder Step",
    "Tidal Wave","Tiny Servant","Wall of Sand","Wall of Water",
    "Charm Monster","Elemental Bane","Guardian of Nature","Shadow of Moil",
    "Sickening Radiance","Storm Sphere","Summon Greater Demon","Vitriolic Sphere","Watery Sphere",
    "Control Winds","Danse Macabre","Dawn","Enervation","Far Step",
    "Holy Weapon","Immolation","Infernal Calling","Maelstrom","Negative Energy Flood",
    "Skill Empowerment","Steel Wind Strike","Synaptic Static",
    "Transmute Rock","Wall of Light","Wrath of Nature",
    "Bones of the Earth","Druid Grove","Investiture of Flame","Investiture of Ice",
    "Investiture of Stone","Investiture of Wind","Mental Prison","Scatter","Soul Cage",
    "Tenser's Transformation",
    "Crown of Stars","Power Word Pain","Temple of the Gods","Whirlwind",
    "Abi-Dalzim's Horrid Wilting","Illusory Dragon","Maddening Darkness","Mighty Fortress",
    "Invulnerability","Mass Polymorph","Psychic Scream",
]]

xge_set = set(xge_list)
in_any = xge_set & (all_spell_names | srd_names)
missing = xge_set - in_any

print(f"=== XGE SPELLS DEEP CHECK ===")
print(f"Total XGE spells: {len(xge_set)}")
print(f"Found anywhere: {len(in_any)}")
print(f"TRULY MISSING: {len(missing)}")
for n in sorted(missing):
    print(f"  x {n.title()}")

# Check Maximillian variant
print(f"\n=== NAME VARIANTS TO FIX ===")
maxim_variants = [s for s in spells if 'maxim' in s.get('name','').lower()]
for s in maxim_variants:
    print(f"  '{s['name']}' -> should be 'Maximillian's Earthen Grasp'")
