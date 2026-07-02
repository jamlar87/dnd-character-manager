#!/usr/bin/env python3
"""Check TTP and WGE entries are present in merged manual_data files."""
import json
from pathlib import Path

MANUAL_DATA = Path(__file__).parent.parent / "data" / "manual_data"

cats = ["races", "spells", "magic_items", "equipment", "monsters", 
        "npcs", "feats", "backgrounds", "subclasses", "traps"]

total_ttp = 0
total_wge = 0
missing_ttp = []
missing_wge = []

print("=" * 60)
print("Checking TTP & WGE entries in merged manual_data/")
print("=" * 60)

for cat in cats:
    path = MANUAL_DATA / f"{cat}.json"
    if not path.exists():
        print(f"  {cat}: FILE MISSING")
        continue
    data = json.load(open(path))
    if not isinstance(data, list):
        print(f"  {cat}: not a list ({type(data).__name__})")
        continue
    
    ttp = [i for i in data if i.get("_source_manual") == "TTP"]
    wge = [i for i in data if i.get("_source_manual") == "WGE"]
    total_ttp += len(ttp)
    total_wge += len(wge)
    
    status = ""
    if len(ttp) == 0:
        missing_ttp.append(cat)
        status += "  ** NO TTP **"
    if len(wge) == 0:
        missing_wge.append(cat)
        status += "  ** NO WGE **"
    
    ttp_names = ", ".join(i["name"] for i in ttp[:3])
    wge_names = ", ".join(i["name"] for i in wge[:3])
    
    print(f"  {cat:15s}: TTP={len(ttp):2d}  WGE={len(wge):2d}{status}")
    if ttp_names:
        print(f"    └─ TTP: {ttp_names}{'...' if len(ttp)>3 else ''}")
    if wge_names:
        print(f"    └─ WGE: {wge_names}{'...' if len(wge)>3 else ''}")

print()
print(f"Totals: TTP={total_ttp}, WGE={total_wge}")

# Check _meta.json
meta_path = MANUAL_DATA / "_meta.json"
meta = json.load(open(meta_path))
print(f"\n_meta.json: source_manuals={meta.get('source_manuals')}")
tallies = meta.get("tallies", meta.get("totals", {}))
print(f"_meta.json: totals={tallies}")

# Check the extracted cache files to see what was originally extracted
print("\n--- Cache file sanity check ---")
cache_dir = Path(__file__).parent.parent / "data" / "manual_cache"
for cf in sorted(cache_dir.glob("*_extracted.json")):
    data = json.load(open(cf))
    slug = data.get("_book_slug", "???")
    completed = data.get("_completed", False)
    counts = {cat: len(data.get(cat, [])) for cat in cats if data.get(cat)}
    print(f"  {slug}: completed={completed} {counts}")
