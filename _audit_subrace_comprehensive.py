"""Full subrace trait description audit across all mechanisms."""

import json, sys, os
sys.path.insert(0, '/home/james/dnd-character-manager')

# ── Load the runtime RACIAL_TRAIT_DESCS ──
# We can't import main directly, but we can replicate its loading logic
from data import RACIAL_TRAIT_DESCS as BASE_DESCS, RACIAL_TRAIT_EFFECTS as BASE_EFFECTS

# Build runtime version of RACIAL_TRAIT_DESCS by processing manual data
RUNTIME_DESCS = dict(BASE_DESCS)  # start with base

_GENERIC_TRAITS = {
    "ability score increase", "ability score increases",
    "adventuring age", "age", "size", "speed", "languages", "language",
    "alignment", "subtypes", "subtype",
}

# Load manual races
with open('data/manual_data/races.json') as f:
    manual_races = json.load(f)

# Process subrace traits (replicating main.py lines ~378-430 + after the skip)
for manual_entry in manual_races:
    name = manual_entry.get("name", "")
    if not name:
        continue
    
    sr_name = manual_entry.get("subrace", "")
    
    # Process subraces within this entry
    for sr in manual_entry.get("subraces", []):
        sr_name = sr.get("name", "")
        if not sr_name:
            continue
        
        # Subrace trait descriptions
        for st in sr.get("traits", []):
            stname = st.get("name", "")
            stdesc = st.get("description", "")
            if stname and stdesc:
                key = f"{sr_name}::{stname}" if stname.lower() in _GENERIC_TRAITS else stname
                if key not in RUNTIME_DESCS:
                    RUNTIME_DESCS[key] = stdesc
    
    # Also process traits at the race level (after skip fix)
    for t in manual_entry.get("traits", []):
        tname = t.get("name", "")
        tdesc = t.get("description", "")
        if tname and tdesc:
            key = f"{name}::{tname}" if tname.lower() in _GENERIC_TRAITS else tname
            if key not in RUNTIME_DESCS:
                RUNTIME_DESCS[key] = tdesc

# ── Load all export subraces ──
with open('data/exports/races_export.json') as f:
    races_export = json.load(f)

# ── Load SRD subraces ──
with open('data/srd_cache/subraces.json') as f:
    srd_subraces = json.load(f)

# Build a map: subrace_name -> list of traits
print("=" * 70)
print("FULL SUBRACE TRAIT DESCRIPTION AUDIT")
print("=" * 70)

all_subraces = {}  # subrace_name -> {parent, traits: [{name, has_desc_in, source}]}

# 1. Manual data subraces
for entry in manual_races:
    parent = entry.get("name", "?")
    subraces = entry.get("subraces", [])
    for sr in subraces:
        sr_name = sr.get("name", "?")
        if sr_name not in all_subraces:
            all_subraces[sr_name] = {"parent": parent, "traits": []}
        for t in sr.get("traits", []):
            tname = t.get("name", "?")
            has_desc_inline = bool(t.get("description", ""))
            all_subraces[sr_name]["traits"].append({
                "name": tname,
                "has_inline": has_desc_inline,
                "has_runtime": tname in RUNTIME_DESCS,
                "has_base": tname in BASE_DESCS,
                "runtime_key": f"{sr_name}::{tname}" if tname.lower() in _GENERIC_TRAITS else tname,
                "source": "manual"
            })

# 2. SRD cache subraces
for entry in srd_subraces:
    sr_name = entry.get("name", "?")
    parent = entry.get("race", {}).get("name", "?") if isinstance(entry.get("race"), dict) else "?"
    if sr_name not in all_subraces:
        all_subraces[sr_name] = {"parent": parent, "traits": []}
    for t in entry.get("racial_traits", []):
        tname = t.get("name", "?")
        all_subraces[sr_name]["traits"].append({
            "name": tname,
            "has_inline": False,
            "has_runtime": tname in RUNTIME_DESCS,
            "has_base": tname in BASE_DESCS,
            "runtime_key": tname,
            "source": "srd_cache"
        })

# 3. Export subraces (from subraces field)
for rname, rdata in races_export.items():
    subrace_names = rdata.get("subraces", [])
    if not subrace_names:
        continue
    for sr_name in subrace_names:
        if sr_name not in all_subraces:
            all_subraces[sr_name] = {"parent": rname, "traits": []}
        # Check if SUBRACE_TRAITS has these (we can't load main easily)
        # Use the manual data to populate SUBRACE_TRAITS logic
        # Export subraces that aren't in manual data won't have custom traits

# ── REPORT ──
print(f"\n{'Subrace':35s} {'Parent':20s} {'Traits':5s} {'Missing':5s}")
print("-" * 70)

total_missing = 0
for sr_name, info in sorted(all_subraces.items()):
    parent = info["parent"]
    traits = info["traits"]
    
    missing = [t for t in traits if not t["has_runtime"] and not t["has_inline"]]
    extra_srd = [t for t in traits if t["source"] == "srd_cache" and not t["has_base"]]
    
    status = "✅" if not missing else "❌"
    print(f"{sr_name:35s} {parent:20s} {len(traits):5d} {len(missing):5d} {status}")
    
    for m in missing:
        print(f"  {'':35s} Missing desc for: {m['name']}")
        print(f"  {'':35s}   inline={m['has_inline']} runtime={m['has_runtime']} base={m['has_base']}")
        print(f"  {'':35s}   runtime_key={m['runtime_key']}")
        total_missing += 1

# ── Also check SRD-specific ──
print(f"\n{'='*70}")
print(f"SRD-specific subrace traits needing BASE DESCS entries:")
srd_missing = 0
for entry in srd_subraces:
    sr_name = entry.get("name", "?")
    for t in entry.get("racial_traits", []):
        tname = t.get("name", "?")
        if tname not in BASE_DESCS and tname not in RUNTIME_DESCS:
            print(f"  ❌ {sr_name}: '{tname}' — no description anywhere")
            srd_missing += 1
        elif tname in RUNTIME_DESCS and tname not in BASE_DESCS:
            pass  # loaded from manual data at runtime, fine

print(f"\nTotal subrace trait descriptions missing: {total_missing + srd_missing}")
print(f"  From export/manual subraces: {total_missing}")
print(f"  From SRD cache: {srd_missing}")
