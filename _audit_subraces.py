"""Audit all subraces in the export for missing trait descriptions."""
import json

with open('data/exports/races_export.json') as f:
    races_dict = json.load(f)

races = list(races_dict.values())

# Global trait desc lookup
import sys
sys.path.insert(0, '.')
from data import RACIAL_TRAIT_DESCS

# ---- 1. Races that list subraces ----
with_subraces = [r for r in races if r.get('Subraces')]
print("=== RACES WITH SUBRACES ===")
for r in with_subraces:
    sub_names = [s.get('Name','') for s in r.get('Subraces',[])]
    print(f"  {r['Name']}: {sub_names}")

# ---- 2. Races that ARE subraces ----
are_subraces = [r for r in races if r.get('SubraceOf')]
print(f"\n=== SUBRACES (entries with SubraceOf) ===")
for r in are_subraces:
    print(f"  {r['Name']} → parent: {r.get('SubraceOf','?')}")

# ---- 3. Check trait descriptions for each subrace ----
print("\n=== SUBRACE TRAIT AUDIT ===")
for r in are_subraces:
    name = r['Name']
    missing = []
    present = []
    for t in r.get('Traits', []):
        tname = t.get('Name', '')
        if tname not in RACIAL_TRAIT_DESCS and tname not in r.get('_effects', {}):
            missing.append(tname)
        else:
            present.append(tname)
    status = "✅" if not missing else "❌ MISSING"
    print(f"{status} {name} (parent: {r.get('SubraceOf','?')})")
    if missing:
        for m in missing:
            print(f"       - {m}")
    # Also check parent race's Subraces list references
    srs = r.get('Subrace', '')
    if srs:
        print(f"       subrace field: {srs}")

# ---- 4. Also check subraces for top-level entries ----
print("\n=== SUBRACE TRAIT AUDIT (subraces in top-level entries) ===")
for r in with_subraces:
    for s in r.get('Subraces', []):
        sname = s.get('Name', '')
        straits = s.get('Traits', [])
        missing = []
        for t in straits:
            tname = t.get('Name', '')
            if tname not in RACIAL_TRAIT_DESCS:
                missing.append(tname)
        status = "✅" if not missing else "❌ MISSING"
        print(f"{status} {sname} (child of {r['Name']})")
        if missing:
            for m in missing:
                print(f"       - {m}")

# ---- 5. Cross-reference: any races that have Subrace field but no SubraceOf ----
print("\n=== ENTRIES WITH 'subrace' FIELD (not SubraceOf) ===")
for r in races:
    s = r.get('subrace', '')
    if s and not r.get('SubraceOf'):
        print(f"  {r['Name']} → subrace field: '{s}'")

# Show some stats
total_entries = len(races)
print(f"\nTotal export entries: {total_entries}")
print(f"Races with Subraces: {len(with_subraces)}")
print(f"Races marked as SubraceOf: {len(are_subraces)}")
