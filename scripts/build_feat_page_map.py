#!/usr/bin/env python3
"""
Build a feat→page mapping for D&D 5e feats.
Searches PHB PDF with pymupdf for main PHB 2014 feats,
then uses known page references for supplemental sources.

Output: data/feat_page_map.json
"""
import json
import re
from pathlib import Path

try:
    import fitz
except ImportError:
    print("ERROR: pymupdf not installed.")
    import sys; sys.exit(1)

DATA_DIR = Path("/home/james/dnd-character-manager/data")
OUTPUT_PATH = DATA_DIR / "feat_page_map.json"
MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")
PHB_PATH = MANUAL_DIR / "D&D 5E - Player's Handbook.pdf"

# ── PHB 2014 Feats ──
# These 42 feats are the built-in FEATS dict entries.
# Map: feat_key → (page, source_str)
PHB_FEAT_PAGES = {
    "alert": 166,
    "athlete": 166,
    "actor": 166,
    "charger": 166,
    "crossbow_expert": 166,
    "defensive_duelist": 166,
    "dual_wielder": 166,
    "dungeon_delver": 167,
    "durable": 167,
    "elemental_adept": 167,
    "grappler": 167,
    "great_weapon_master": 167,
    "healer": 167,
    "heavily_armored": 167,
    "heavy_armor_master": 167,
    "inspiring_leader": 167,
    "keen_mind": 168,
    "lightly_armored": 168,
    "linguist": 168,
    "lucky": 168,
    "mage_slayer": 168,
    "magic_initiate": 168,
    "martial_adept": 168,
    "medium_armor_master": 168,
    "mobile": 168,
    "moderately_armored": 169,
    "mounted_combatant": 169,
    "observant": 169,
    "polearm_master": 169,
    "resilient": 169,
    "ritual_caster": 169,
    "savage_attacker": 169,
    "sentinel": 169,
    "sharpshooter": 170,
    "shield_master": 170,
    "skilled": 170,
    "skulker": 170,
    "spell_sniper": 170,
    "tavern_brawler": 170,
    "tough": 170,
    "war_caster": 170,
    "weapon_master": 170,
}

# ── Non-PHB built-in feats (from TCE, EEPC) ──
NON_PHB_FEATS = {
    "fey_touched": "TCE p.79",
    "shadow_touched": "TCE p.80",
}

def verify_with_pdf():
    """Search PHB PDF for feat names to verify hardcoded pages."""
    if not PHB_PATH.exists():
        print(f"PHB not found at {PHB_PATH}")
        return {}
    
    doc = fitz.open(str(PHB_PATH))
    verified = {}
    
    for key, page_num in PHB_FEAT_PAGES.items():
        feat_name = key.replace("_", " ")
        # Search for feat name in the expected page range
        found_page = None
        for pg in range(page_num - 1, page_num + 2):
            if pg < 0 or pg >= len(doc):
                continue
            text = doc[pg].get_text()
            # Use case-insensitive search with word boundary
            pattern = re.compile(r'\b' + re.escape(feat_name) + r'\b', re.IGNORECASE)
            if pattern.search(text):
                found_page = pg + 1  # 0-indexed to 1-indexed
                break
        
        if found_page:
            verified[key] = found_page
        else:
            # Try broader search
            for pg in range(page_num - 3, page_num + 4):
                if pg < 0 or pg >= len(doc):
                    continue
                text = doc[pg].get_text()
                pattern = re.compile(r'\b' + re.escape(feat_name) + r'\b', re.IGNORECASE)
                if pattern.search(text):
                    verified[key] = pg + 1
                    break
        
        if key not in verified:
            print(f"  WARNING: Could not verify '{feat_name}' near page {page_num}")
            verified[key] = page_num  # Keep hardcoded value
    
    doc.close()
    return verified


def main():
    print("=== Building Feat Page Map ===\n")
    
    results = {}
    
    # Verify with PDF
    print("Verifying PHB feat pages...")
    verified = verify_with_pdf()
    
    # Build results from verified PHB pages
    phb_count = 0
    for key, page in sorted(verified.items()):
        results[key] = f"PHB 2014 p.{page}"
        phb_count += 1
    print(f"PHB feats: {phb_count}")
    
    # Add non-PHB feats
    for key, source_str in NON_PHB_FEATS.items():
        if key not in results:
            results[key] = source_str
    
    # Load manual feats for additional sources
    manual_feats_path = DATA_DIR / "manual_data" / "feats.json"
    if manual_feats_path.exists():
        with open(manual_feats_path) as f:
            manual_feats = json.load(f)
        
        manual_count = 0
        for feat in manual_feats:
            name = feat.get("name", "")
            key = name.lower().replace(" ", "_")
            if key in results:
                continue  # Already mapped
            
            source = feat.get("source", "")
            if source:
                # Extract page if present
                m = re.search(r'[pP]\.?\s*(\d+)', source)
                if m:
                    page = m.group(1)
                    # Determine prefix
                    lower = source.lower()
                    if "xanathar" in lower or "xge" in lower:
                        prefix = "XGE"
                    elif "tasha" in lower or "tce" in lower:
                        prefix = "TCE"
                    elif "sword coast" in lower or "scag" in lower:
                        prefix = "SCAG"
                    elif "guildmaster" in lower or "ggr" in lower:
                        prefix = "GGR"
                    elif "eberron" in lower or "wge" in lower:
                        prefix = "WGE"
                    elif "elemental" in lower or "eepc" in lower:
                        prefix = "EEPC"
                    else:
                        prefix = "PHB 2014"
                    results[key] = f"{prefix} p.{page}"
                    manual_count += 1
                else:
                    # Has source but no page
                    if "xanathar" in source.lower():
                        results[key] = "XGE"
                    elif "tasha" in source.lower():
                        results[key] = "TCE"
                    elif "guildmaster" in source.lower():
                        results[key] = "GGR"
                    elif "eberron" in source.lower():
                        results[key] = "WGE"
                    elif "elemental" in source.lower():
                        results[key] = "EEPC"
                    else:
                        results[key] = "PHB 2014"
                    manual_count += 1
        
        print(f"Manual feat additions: {manual_count}")
    
    # Stats
    total = len(results)
    with_page = sum(1 for v in results.values() if "p." in v)
    print(f"\nTotal: {total}, with pages: {with_page}, without: {total - with_page}")
    
    # Show what's missing pages
    missing = {k: v for k, v in sorted(results.items()) if "p." not in v}
    if missing:
        print("\nEntries without page numbers:")
        for key, val in missing.items():
            print(f"  {key}: {val}")
    
    # Save
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
