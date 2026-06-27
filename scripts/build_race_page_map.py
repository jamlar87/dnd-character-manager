#!/usr/bin/env python3
"""
Build a race/subrace→page mapping for D&D 5e races.
Uses known PHB/Volo's/MTF/SCAG/EEPC/GGR page references.

Output: data/race_page_map.json
"""
import json
from pathlib import Path

DATA_DIR = Path("/home/james/dnd-character-manager/data")
OUTPUT_PATH = DATA_DIR / "page_maps" / "race_page_map.json"

# ── PHB Races ──
PHB_RACES = {
    "dwarf": (18, "PHB 2014"),
    "hill dwarf": (20, "PHB 2014"),
    "mountain dwarf": (20, "PHB 2014"),  # implied, subrace
    "elf": (21, "PHB 2014"),
    "high elf": (23, "PHB 2014"),
    "wood elf": (24, "PHB 2014"),
    "drow": (24, "PHB 2014"),
    "halfling": (26, "PHB 2014"),
    "lightfoot halfling": (28, "PHB 2014"),
    "stout halfling": (28, "PHB 2014"),
    "human": (29, "PHB 2014"),
    "variant human": (31, "PHB 2014"),
    "dragonborn": (32, "PHB 2014"),
    "gnome": (35, "PHB 2014"),
    "forest gnome": (37, "PHB 2014"),
    "rock gnome": (37, "PHB 2014"),
    "deep gnome": (37, "PHB 2014"),  # svirfneblin — also in EEPC
    "half-elf": (38, "PHB 2014"),
    "half-orc": (40, "PHB 2014"),
    "tiefling": (42, "PHB 2014"),
    "svirfneblin": (37, "PHB 2014"),  # deep gnome alias
}

# ── Volo's Guide to Monsters Races ──
VGM_RACES = {
    "aasimar": (104, "VGM"),
    "protector aasimar": (105, "VGM"),
    "scourge aasimar": (105, "VGM"),
    "fallen aasimar": (105, "VGM"),
    "firbolg": (106, "VGM"),
    "goliath": (108, "VGM"),
    "kenku": (109, "VGM"),
    "lizardfolk": (111, "VGM"),
    "tabaxi": (113, "VGM"),
    "triton": (115, "VGM"),
    "bugbear": (119, "VGM"),
    "goblin": (119, "VGM"),
    "hobgoblin": (119, "VGM"),
    "kobold": (119, "VGM"),
    "orc": (120, "VGM"),
    "yuan-ti pureblood": (120, "VGM"),
}

# ── Mordenkainen's Tome of Foes Races ──
MTF_RACES = {
    "eladrin": (61, "MTF"),
    "sea elf": (62, "MTF"),
    "shadar-kai": (62, "MTF"),
    "duergar": (81, "MTF"),
    "githyanki": (96, "MTF"),
    "githzerai": (96, "MTF"),
    "baalzebul tiefling": (21, "MTF"),  # tiefling subraces
    "dispater tiefling": (21, "MTF"),
    "fierna tiefling": (21, "MTF"),
    "glasya tiefling": (21, "MTF"),
    "levistus tiefling": (21, "MTF"),
    "mammon tiefling": (21, "MTF"),
    "mephistopheles tiefling": (21, "MTF"),
    "zariel tiefling": (21, "MTF"),
}

# ── Sword Coast Adventurer's Guide Races ──
SCAG_RACES = {
    "duergar": (104, "SCAG"),
    "ghostwise halfling": (110, "SCAG"),
    "deep gnome": (115, "SCAG"),
}

# ── Elemental Evil Player's Companion Races ──
EEPC_RACES = {
    "aarakocra": (3, "EEPC"),
    "genasi": (7, "EEPC"),
    "air genasi": (9, "EEPC"),
    "earth genasi": (9, "EEPC"),
    "fire genasi": (9, "EEPC"),
    "water genasi": (10, "EEPC"),
    "goliath": (10, "EEPC"),
    "deep gnome": (11, "EEPC"),
}

# ── Guildmasters' Guide to Ravnica Races ──
GGR_RACES = {
    "centaur": (15, "GGR"),
    "loxodon": (17, "GGR"),
    "minotaur": (18, "GGR"),
    "simic hybrid": (20, "GGR"),
    "vedalken": (21, "GGR"),
    "goblin": (16, "GGR"),  # also in GGR
}

# ── Eberron: Rising from the Last War Races ──
ERLW_RACES = {
    "changeling": (18, "ERLW"),
    "kalashtar": (29, "ERLW"),
    "shifter": (31, "ERLW"),
    "warforged": (35, "ERLW"),
    "beasthide shifter": (33, "ERLW"),
    "longtooth shifter": (33, "ERLW"),
    "swiftstride shifter": (33, "ERLW"),
    "wildhunt shifter": (33, "ERLW"),
}

# ── The Tortle Package ──
TTP_RACES = {
    "tortle": (4, "TTP"),
}

# ── One Grung Above (Grung) ──
GRUNG = {
    "grung": (1, "OGA"),  # unofficial-ish
}

# ── Misc / Volo's extras ──
EXTRA_RACES = {
    "aasimar (volo's guide)": (104, "VGM"),
    "fallen aasimar (volo's guide)": (105, "VGM"),
    "protector aasimar (volo's guide)": (105, "VGM"),
    "scourge aasimar (volo's guide)": (105, "VGM"),
}


def main():
    print("=== Building Race/Subrace Page Map ===\n")
    
    all_known = {}
    for name, (page, prefix) in PHB_RACES.items():
        all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
    print(f"PHB: {len(PHB_RACES)}")
    
    for name, (page, prefix) in VGM_RACES.items():
        all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
    print(f"VGM: {len(VGM_RACES)}")
    
    for d, label in [(MTF_RACES, "MTF"), (SCAG_RACES, "SCAG"), 
                      (EEPC_RACES, "EEPC"), (GGR_RACES, "GGR"),
                      (ERLW_RACES, "ERLW"), (TTP_RACES, "TTP"),
                      (EXTRA_RACES, "EXTRA")]:
        for name, (page, prefix) in d.items():
            all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
        print(f"{label}: {len(d)}")
    
    # Load manual data and patch in any remaining
    with open(DATA_DIR / "manual_data/races.json") as f:
        man_races = json.load(f)
    
    added = 0
    import re
    for r in man_races:
        name = r.get("name", "").lower()
        if name in all_known:
            continue
        src = r.get("source", "")
        m = re.search(r'\b[pP]\.?\s*(\d+)', src)
        if m:
            page = int(m.group(1))
            lower = src.lower()
            if 'volo' in lower or 'vgm' in lower:
                prefix = "VGM"
            elif 'mordenkainen' in lower or 'mtf' in lower:
                prefix = "MTF"
            elif 'sword coast' in lower or 'scag' in lower:
                prefix = "SCAG"
            elif 'elemental evil' in lower or 'ee' in lower:
                prefix = "EEPC"
            elif 'ravnica' in lower or 'ggr' in lower:
                prefix = "GGR"
            elif 'eberron' in lower or 'erlw' in lower:
                prefix = "ERLW"
            elif 'tortle' in lower or 'ttp' in lower:
                prefix = "TTP"
            else:
                prefix = "PHB 2014"
            all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
            added += 1
        else:
            # No page — keep bare source
            if 'volo' in src.lower():
                all_known[name] = {"page": None, "source_str": "VGM"}
            elif 'mordenkainen' in src.lower():
                all_known[name] = {"page": None, "source_str": "MTF"}
            elif 'sword coast' in src.lower():
                all_known[name] = {"page": None, "source_str": "SCAG"}
            elif 'eberron' in src.lower():
                all_known[name] = {"page": None, "source_str": "ERLW"}
            elif 'tortle' in src.lower():
                all_known[name] = {"page": None, "source_str": "TTP"}
            else:
                all_known[name] = {"page": None, "source_str": "PHB 2014"}
    
    print(f"Manual additions: {added}")
    
    # Stats
    total = len(all_known)
    with_page = sum(1 for v in all_known.values() if v.get("page"))
    print(f"\nTotal: {total}, with pages: {with_page}, without: {total - with_page}")
    
    missing = {k: v for k, v in sorted(all_known.items()) if not v.get("page")}
    if missing:
        print("\nMissing page numbers:")
        for name, val in missing.items():
            print(f"  {name}: {val['source_str']!r}")
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(all_known, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
