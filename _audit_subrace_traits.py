"""Audit ALL subrace traits for missing descriptions."""
import json, sys
sys.path.insert(0, '.')

from data import RACIAL_TRAIT_DESCS, RACIAL_TRAIT_EFFECTS

with open('data/exports/races_export.json') as f:
    races = json.load(f)

# Build a full picture: collect all unique trait names referenced anywhere
# and check if they have descriptions

all_traits = {}  # trait_name -> list of (race, subrace, context)

# 1. Check subrace-specific trait names from RACIAL_TRAIT_DESCS used by subraces
# The trait names that are subrace-specific are things like:
# "Dwarven Toughness" (Hill Dwarf), "Dwarven Combat Training" (Mountain Dwarf), etc.
# These must be in RACIAL_TRAIT_DESCS to show a description

# 2. Check parent _effects for subrace-tagged entries
# Some entries in _effects might be arrays indexed per-subrace

# 3. Check the manual data for subrace entries
with open('data/manual_data/races.json') as f:
    manual = json.load(f)

for entry in manual:
    name = entry.get('name', '?')
    subraces = entry.get('subraces', [])
    for sr in subraces:
        sr_name = sr.get('name', '?')
        for t in sr.get('traits', []):
            tname = t.get('name', '?')
            if tname not in all_traits:
                all_traits[tname] = []
            all_traits[tname].append(f"{name} → subrace {sr_name} (manual)")

# 4. Check SRD cache for subrace-specific traits
with open('data/srd_cache/subraces.json') as f:
    srd_subraces_list = json.load(f)

# Find all trait names referenced in subraces
for entry in srd_subraces_list:
    sr_name = entry.get('name', '?')
    race_name = entry.get('race', {}).get('name', '?') if isinstance(entry.get('race'), dict) else '?'
    
    # Check racial traits  
    for t in entry.get('racial_traits', []):
        tname = t.get('name', '?')
        if tname not in all_traits:
            all_traits[tname] = []
        all_traits[tname].append(f"{race_name} → {sr_name} (SRD cache, racial_traits)")
    
    # Check starting proficiencies
    for t in entry.get('starting_proficiencies', []):
        tname = t.get('name', '?')
        if tname not in all_traits:
            all_traits[tname] = []
        all_traits[tname].append(f"{race_name} → {sr_name} (SRD cache, starting_proficiencies)")

# 5. Scan through export subraces field and check for any traits on the parent
# that have SubraceName field
for race_name, race_data in races.items():
    for t in race_data.get('Traits', []):
        subrace = t.get('SubraceName', '')
        if subrace:
            tname = t.get('Name', '?')
            if tname not in all_traits:
                all_traits[tname] = []
            all_traits[tname].append(f"{race_name} → {subrace} (export trait)")

# 6. Check _effects for subrace-specific entries
# _effects might be a dict with trait names as keys
for race_name, race_data in races.items():
    effects = race_data.get('_effects', {})
    if isinstance(effects, dict):
        for tname in effects:
            val = effects[tname]
            # If value has subrace-specific structure, note it
            if isinstance(val, dict):
                continue  # assumption: all _effects values are dicts

# Now check each trait name against RACIAL_TRAIT_DESCS and RACIAL_TRAIT_EFFECTS
print("=== TRAIT DESCRIPTION AUDIT ===")
print(f"Checking {len(all_traits)} unique subrace-referenced trait names...\n")

missing_desc = []
present = []
for tname, refs in sorted(all_traits.items()):
    has_desc = tname in RACIAL_TRAIT_DESCS
    has_effect = tname in RACIAL_TRAIT_EFFECTS
    
    if not has_desc and not has_effect:
        missing_desc.append((tname, refs))
    else:
        present.append(tname)

print(f"Present: {len(present)} trait names have descriptions/effects")
print(f"Missing: {len(missing_desc)} trait names need descriptions\n")

if missing_desc:
    print("=== MISSING TRAIT DESCRIPTIONS ===")
    for tname, refs in missing_desc:
        print(f"\n❌ {tname}")
        for r in refs[:3]:  # show first 3 references
            print(f"    Referenced by: {r}")

# Also check: are there any trait names in RACIAL_TRAIT_DESCS that reference
# subrace names directly but mismatch?
print("\n\n=== ADDITIONAL: MANUAL DATA SUBRACE AUDIT ===")
# Check manual races.json for subrace entries that might not have effect descriptions
for entry in manual:
    name = entry.get('name', '?')
    subraces = entry.get('subraces', [])
    effects = entry.get('_effects', {})
    for sr in subraces:
        sr_name = sr.get('name', '?')
        sr_effects = sr.get('_effects', {})
        sr_traits = sr.get('traits', [])
        
        for t in sr_traits:
            tname = t.get('name', '?')
            # Check if this trait name has a description somewhere
            desc = t.get('description', '') or RACIAL_TRAIT_DESCS.get(tname, '')
            if not desc and tname not in sr_effects:
                print(f"  ❌ {name} → {sr_name}: '{tname}' has no description and no effects entry")

print("\n=== AUDIT COMPLETE ===")
