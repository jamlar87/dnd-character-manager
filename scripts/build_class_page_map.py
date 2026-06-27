#!/usr/bin/env python3
"""
Build a class/subclass→page mapping for D&D 5e classes.
Uses known PHB/XGE/SCAG page references (classes have multi-page sections).

Output: data/class_page_map.json
"""
import json
from pathlib import Path

DATA_DIR = Path("/home/james/dnd-character-manager/data")
OUTPUT_PATH = DATA_DIR / "page_maps" / "class_page_map.json"

# ── PHB Class pages ──
CLASS_PAGES = {
    "barbarian": (46, "PHB 2014"),
    "bard": (51, "PHB 2014"),
    "cleric": (56, "PHB 2014"),
    "druid": (64, "PHB 2014"),
    "fighter": (70, "PHB 2014"),
    "monk": (76, "PHB 2014"),
    "paladin": (82, "PHB 2014"),
    "ranger": (89, "PHB 2014"),
    "rogue": (94, "PHB 2014"),
    "sorcerer": (99, "PHB 2014"),
    "warlock": (105, "PHB 2014"),
    "wizard": (112, "PHB 2014"),
}

# ── PHB Subclass pages ──
SUBCLASS_PAGES = {
    # Barbarian
    "berserker": (49, "PHB 2014"),
    "path of the berserker": (49, "PHB 2014"),
    "path of the totem warrior": (50, "PHB 2014"),
    "path of the battlerager": (50, "PHB 2014"),  # SCAG actually
    # Bard
    "college of lore": (54, "PHB 2014"),
    "college of valor": (55, "PHB 2014"),
    # Cleric
    "life domain": (60, "PHB 2014"),
    "knowledge domain": (59, "PHB 2014"),
    "light domain": (60, "PHB 2014"),
    "nature domain": (61, "PHB 2014"),
    "tempest domain": (62, "PHB 2014"),
    "trickery domain": (62, "PHB 2014"),
    "war domain": (63, "PHB 2014"),
    # Druid
    "circle of the land": (68, "PHB 2014"),
    "circle of the moon": (69, "PHB 2014"),
    # Fighter
    "champion": (72, "PHB 2014"),
    "battle master": (73, "PHB 2014"),
    "eldritch knight": (74, "PHB 2014"),
    # Monk
    "way of the open hand": (79, "PHB 2014"),
    "way of shadow": (80, "PHB 2014"),
    "way of the four elements": (80, "PHB 2014"),
    # Paladin
    "oath of devotion": (85, "PHB 2014"),
    "oath of the ancients": (86, "PHB 2014"),
    "oath of vengeance": (87, "PHB 2014"),
    # Ranger
    "hunter": (93, "PHB 2014"),
    "beast master": (93, "PHB 2014"),
    # Rogue
    "thief": (97, "PHB 2014"),
    "assassin": (97, "PHB 2014"),
    "arcane trickster": (98, "PHB 2014"),
    # Sorcerer
    "draconic bloodline": (102, "PHB 2014"),
    "wild magic": (103, "PHB 2014"),
    # Warlock
    "the fiend": (109, "PHB 2014"),
    "the archfey": (108, "PHB 2014"),
    "the great old one": (110, "PHB 2014"),
    # Wizard
    "school of evocation": (117, "PHB 2014"),
    "school of abjuration": (115, "PHB 2014"),
    "school of conjuration": (116, "PHB 2014"),
    "school of divination": (116, "PHB 2014"),
    "school of enchantment": (117, "PHB 2014"),
    "school of illusion": (118, "PHB 2014"),
    "school of necromancy": (118, "PHB 2014"),
    "school of transmutation": (119, "PHB 2014"),
}

# ── XGE Subclass pages ──
XGE_SUBCLASSES = {
    # Barbarian (XGE pp.9-11)
    "path of the ancestral guardian": (9, "XGE"),
    "path of the storm herald": (10, "XGE"),
    "path of the zealot": (11, "XGE"),
    # Bard (XGE pp.13-15)
    "college of glamour": (14, "XGE"),
    "college of swords": (15, "XGE"),
    "college of whispers": (16, "XGE"),
    # Cleric (XGE pp.17-21)
    "forge domain": (18, "XGE"),
    "grave domain": (19, "XGE"),
    # Druid (XGE pp.22-24)
    "circle of dreams": (22, "XGE"),
    "circle of the shepherd": (23, "XGE"),
    # Fighter (XGE pp.28-32)
    "arcane archer": (28, "XGE"),
    "cavalier": (30, "XGE"),
    "samurai": (31, "XGE"),
    # Monk (XGE pp.33-36)
    "way of the drunken master": (33, "XGE"),
    "way of the kensei": (34, "XGE"),
    "way of the sun soul": (35, "XGE"),
    # Paladin (XGE pp.37-39)
    "oath of conquest": (37, "XGE"),
    "oath of redemption": (38, "XGE"),
    # Ranger (XGE pp.40-43)
    "gloom stalker": (41, "XGE"),
    "horizon walker": (42, "XGE"),
    "monster slayer": (43, "XGE"),
    # Rogue (XGE pp.44-48)
    "inquisitive": (45, "XGE"),
    "mastermind": (46, "XGE"),
    "scout": (47, "XGE"),
    "swashbuckler": (48, "XGE"),
    # Sorcerer (XGE pp.49-54)
    "divine soul": (50, "XGE"),
    "shadow magic": (51, "XGE"),
    "storm sorcery": (52, "XGE"),
    # Warlock (XGE pp.54-57)
    "the celestial": (54, "XGE"),
    "the hexblade": (55, "XGE"),
    # Wizard (XGE pp.58-62)
    "war magic": (59, "XGE"),
}

# ── SCAG Subclass pages ──
SCAG_SUBCLASSES = {
    "path of the battlerager": (121, "SCAG"),
    "college of swords": (122, "SCAG"),
    "arcane domain": (126, "SCAG"),
    "way of the long death": (128, "SCAG"),
    "way of the sun soul": (128, "SCAG"),
    "oath of the crown": (132, "SCAG"),
    "mastermind": (133, "SCAG"),
    "swashbuckler": (135, "SCAG"),
    "storm sorcery": (137, "SCAG"),
    "the undying": (139, "SCAG"),
    "bladesinging": (141, "SCAG"),
}

# ── DMG Subclass pages ──
DMG_SUBCLASSES = {
    "oathbreaker": (97, "DMG 2014"),
    "death domain": (96, "DMG 2014"),
}

# ── Other manual subclasses ──
# Map known-ish source strings to normalized ones
SOURCE_FIXES = {
    "Player's Handbook (Barbarian class section)": "PHB 2014 p.46-49",
    "Player's Handbook (Paladin class section)": "PHB 2014 p.82-88",
    "Player's Handbook (Wizard class, Arcane Traditions section)": "PHB 2014 p.115-119",
    "Player's Handbook (inferred from class content)": "PHB 2014",
    "Player's Handbook (inferred from context)": "PHB 2014",
    "Player's Handbook (Light Domain section, p.60-61)": "PHB 2014 p.60-61",
    "Player's Handbook (Nature Domain section, p.61)": "PHB 2014 p.61",
    "Player's Handbook (page inferred as ~73-74)": "PHB 2014 p.73-74",
    "Part 1 Classes": "PHB 2014",
    "Part 1 Classes, p.6": "PHB 2014",
    "Unknown page": "PHB 2014",
    "Sword Coast Adventurer's Guide (Chapter 4: Classes)": "SCAG p.121-141",
    "Sword Coast Adventurer's Guide (Sword Coast Adventurer's Guide) p.137": "SCAG p.137",
    "Tasha's Cauldron of Everything p.141 (inferred from context)": "TCE p.141",
    "Dungeon Master's Guide p.96": "DMG 2014 p.96",
}

def normalize_source(src):
    """Clean up source strings."""
    if src in SOURCE_FIXES:
        return SOURCE_FIXES[src]
    # Strip trailing notes
    src = src.replace(" (inferred from context)", "").replace(" (inferred)", "")
    return src

def main():
    print("=== Building Class/Subclass Page Map ===\n")
    
    results = {}
    
    # 1. Classes from known pages
    for name, (page, prefix) in CLASS_PAGES.items():
        results[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
    print(f"Classes: {len(CLASS_PAGES)}")

    # 2. Subclasses from known pages
    all_known = {}
    all_known.update(SUBCLASS_PAGES)
    all_known.update(XGE_SUBCLASSES)
    all_known.update(SCAG_SUBCLASSES)
    all_known.update(DMG_SUBCLASSES)
    
    for name, (page, prefix) in all_known.items():
        results[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
    print(f"Known subclasses: {len(all_known)}")

    # 3. Manual subclasses with source strings
    with open(DATA_DIR / "manual_data/subclasses.json") as f:
        man_subclasses = json.load(f)

    # Add entries from manual data that aren't in known pages
    added = 0
    for s in man_subclasses:
        name = s.get("name", "").lower()
        if name in results:
            continue
        src = normalize_source(s.get("source", ""))
        if not src:
            src = normalize_source(s.get("_source_manual", "PHB 2014"))
        
        # Extract page if present
        import re
        m = re.search(r'\b[pP]\.?\s*(\d+)', src)
        if m:
            page = int(m.group(1))
            # Determine prefix
            lower = src.lower()
            if 'xanathar' in lower or 'xge' in lower:
                prefix = "XGE"
            elif 'sword coast' in lower or 'scag' in lower:
                prefix = "SCAG"
            elif 'dungeon master' in lower or 'dmg' in lower:
                prefix = "DMG 2014"
            elif 'tasha' in lower or 'tce' in lower:
                prefix = "TCE"
            else:
                prefix = "PHB 2014"
            results[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
            added += 1
        else:
            # No page — keep as-is with just the source prefix
            if 'xanathar' in src.lower() or 'xge' in src.lower():
                results[name] = {"page": None, "source_str": "XGE"}
            elif 'sword coast' in src.lower() or 'scag' in src.lower():
                results[name] = {"page": None, "source_str": "SCAG"}
            elif 'dungeon master' in src.lower() or 'dmg' in src.lower():
                results[name] = {"page": None, "source_str": "DMG 2014"}
            else:
                results[name] = {"page": None, "source_str": "PHB 2014"}
    
    print(f"Manual additions: {added}")

    # Stats
    total = len(results)
    with_page = sum(1 for v in results.values() if v.get("page"))
    print(f"\nTotal: {total}, with pages: {with_page}, without: {total - with_page}")

    # Show what's missing pages
    missing = {k: v for k, v in sorted(results.items()) if not v.get("page")}
    if missing:
        print("\nMissing page numbers:")
        for name, val in missing.items():
            print(f"  {name}: {val['source_str']!r}")

    # Save
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
