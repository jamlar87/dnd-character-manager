#!/usr/bin/env python3
"""Fix race/subrace issues in manual_data/races.json and races_export.json."""
import json, os

MANUAL = '/home/james/dnd-character-manager/data/manual_data/races.json'
EXPORT = '/home/james/dnd-character-manager/data/races_export.json'

with open(MANUAL) as f:
    races = json.load(f)

with open(EXPORT) as f:
    export = json.load(f)

changes = []

# ── 1. Remove Sea Elf (already a subrace of Elf) ──
races = [r for r in races if r.get('name') != 'Sea Elf']
changes.append("Removed Sea Elf — already a subrace of Elf")

# ── 2. Remove Fallen Aasimar (already a subrace of Aasimar as 'Fallen') ──
races = [r for r in races if r.get('name') != 'Fallen Aasimar']
changes.append("Removed Fallen Aasimar — already a subrace of Aasimar")

# ── 3. Remove Genasi (Fire) (already a subrace of Genasi as 'Fire Genasi') ──
races = [r for r in races if r.get('name') != 'Genasi (Fire)']
changes.append("Removed Genasi (Fire) — already a subrace of Genasi")

# ── 4. Fix Aasimar subrace naming to match compiled ──
for r in races:
    if r.get('name') == 'Aasimar':
        old_subs = [s.get('name','') for s in r.get('subraces', [])]
        # Rename 'Protector Aasimar' -> 'Protector', 'Scourge Aasimar' -> 'Scourge'
        for sr in r.get('subraces', []):
            if sr['name'] == 'Protector Aasimar':
                sr['name'] = 'Protector'
            elif sr['name'] == 'Scourge Aasimar':
                sr['name'] = 'Scourge'
        new_subs = [s.get('name','') for s in r.get('subraces', [])]
        changes.append(f"Fixed Aasimar subraces: {old_subs} -> {new_subs}")
        break

# ── 5. Ensure Pallid Elf is in Elf's subraces in export ──
if 'Elf' in export:
    elf_subs = export['Elf'].get('subraces', [])
    if 'Pallid Elf' not in elf_subs:
        elf_subs.append('Pallid Elf')
        export['Elf']['subraces'] = elf_subs
        changes.append("Added Pallid Elf to Elf's subraces in races_export.json")

# ── 6. Ensure Lotusden Halfling is in Halfling's subraces in export ──
if 'Halfling' in export:
    halfling_subs = export['Halfling'].get('subraces', [])
    if 'Lotusden Halfling' not in halfling_subs:
        halfling_subs.append('Lotusden Halfling')
        export['Halfling']['subraces'] = halfling_subs
        changes.append("Added Lotusden Halfling to Halfling's subraces in races_export.json")

# ── Save both files ──
with open(MANUAL, 'w') as f:
    json.dump(races, f, indent=2)

with open(EXPORT, 'w') as f:
    json.dump(export, f, indent=2)

print(f"Fixed: {len(races)} races in manual, {len(export)} races in export")
for c in changes:
    print(f"  • {c}")

# Verify no remaining issues
print("\n=== Verification ===")
# Check no duplicates left
manual_names = [r['name'] for r in races]
for n in manual_names:
    if manual_names.count(n) > 1:
        print(f"  ❌ DUPLICATE: {n} appears {manual_names.count(n)} times")

# Check all manual races aren't known subraces of compiled
known_subs = {}
for race, data in export.items():
    for s in data.get('subraces', []):
        known_subs[s.lower()] = race

for r in races:
    n = r['name']
    nl = n.lower()
    if nl in known_subs:
        print(f"  ❌ STILL DUPLICATE: '{n}' is subrace of '{known_subs[nl]}'")
    else:
        print(f"  ✅ {n}")

# Clean subrace naming
for r in races:
    if r.get('name') == 'Aasimar':
        subs = [s['name'] for s in r.get('subraces', [])]
        expected = ['Protector', 'Scourge']
        if subs == expected:
            print(f"  ✅ Aasimar subraces correct: {subs}")
        else:
            print(f"  ❌ Aasimar subraces WRONG: {subs} (expected {expected})")
