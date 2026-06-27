#!/usr/bin/env python3
"""
Build a background→page mapping for D&D 5e backgrounds.
PHB backgrounds are on pp.125-141, GGR backgrounds are in GGR.

Output: data/background_page_map.json
"""
import json
from pathlib import Path

DATA_DIR = Path("/home/james/dnd-character-manager/data")
OUTPUT_PATH = DATA_DIR / "page_maps" / "background_page_map.json"

# ── PHB 2014 Backgrounds (Chapter 4, pp.125-141) ──
PHB_BACKGROUNDS = {
    "Acolyte": 127,
    "Charlatan": 128,
    "Criminal": 129,
    "Entertainer": 130,
    "Folk Hero": 131,
    "Guild Artisan": 132,
    "Hermit": 134,
    "Noble": 135,
    "Outlander": 136,
    "Sage": 137,
    "Sailor": 139,
    "Soldier": 140,
    "Urchin": 141,
}

# ── GGR Backgrounds ──
GGR_BACKGROUNDS = {
    "Azorius Functionary": (32, "GGR"),
    "Boros Legionnaire": (32, "GGR"),
    "Dimir Operative": (32, "GGR"),
    "Golgari Agent": (32, "GGR"),
    "Gruul Anarch": (32, "GGR"),
    "Izzet Engineer": (32, "GGR"),
    "Orzhov Representative": (32, "GGR"),
    "Rakdos Cultist": (32, "GGR"),
    "Selesnya Initiate": (32, "GGR"),
    "Simic Scientist": (32, "GGR"),
}


def main():
    print("=== Building Background Page Map ===\n")
    
    results = {}
    
    # PHB backgrounds
    for name, page in PHB_BACKGROUNDS.items():
        results[name] = f"PHB 2014 p.{page}"
    print(f"PHB backgrounds: {len(PHB_BACKGROUNDS)}")
    
    # GGR backgrounds
    for name, (page, book) in GGR_BACKGROUNDS.items():
        results[name] = f"{book} p.{page}"
    print(f"GGR backgrounds: {len(GGR_BACKGROUNDS)}")
    
    # Load manual backgrounds for additional sources
    manual_path = DATA_DIR / "manual_data" / "backgrounds.json"
    if manual_path.exists():
        with open(manual_path) as f:
            manual_bgs = json.load(f)
        
        import re
        added = 0
        for bg in manual_bgs:
            name = bg.get("name", "")
            if name in results:
                continue
            
            source = bg.get("source", "")
            m = re.search(r'[pP]\.?\s*(\d+)', source)
            if m:
                page = m.group(1)
                lower = source.lower()
                if "guildmaster" in lower or "ggr" in lower:
                    prefix = "GGR"
                elif "sword coast" in lower or "scag" in lower:
                    prefix = "SCAG"
                elif "eberron" in lower:
                    prefix = "ERLW"
                elif "tomb of an" in lower or "toa" in lower:
                    prefix = "ToA"
                elif "waterdeep" in lower or "wdh" in lower:
                    prefix = "WDH"
                elif "curse of strahd" in lower or "cos" in lower:
                    prefix = "CoS"
                elif "out of the abyss" in lower or "oota" in lower:
                    prefix = "OotA"
                else:
                    prefix = "PHB 2014"
                results[name] = f"{prefix} p.{page}"
                added += 1
            else:
                # Source without page
                if "guildmaster" in source.lower():
                    results[name] = "GGR"
                elif "sword coast" in source.lower():
                    results[name] = "SCAG"
                else:
                    results[name] = "PHB 2014"
                added += 1
        
        print(f"Manual additions: {added}")
    
    # Stats
    total = len(results)
    with_page = sum(1 for v in results.values() if "p." in v)
    print(f"\nTotal: {total}, with pages: {with_page}, without: {total - with_page}")
    
    # Show missing
    missing = {k: v for k, v in sorted(results.items()) if "p." not in v}
    if missing:
        print("\nWithout page numbers:")
        for name, val in missing.items():
            print(f"  {name}: {val}")
    
    # Save
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
