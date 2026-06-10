#!/usr/bin/env python3
"""Brute-force find exact pages for missing backgrounds."""
import fitz, re
from pathlib import Path

MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

MISSING = [
    "Anthropologist", "Archaeologist", "City Watch", "Clan Crafter",
    "Cloistered Scholar", "Courtier", "Cult of the Dragon Infiltrator",
    "Dragon Scholar", "Faction Agent", "Far Traveler", "Guild Merchant",
    "Inheritor", "Knight of the Order", "Mercenary Veteran",
    "Orzhov Syndicate", "Rakdos Guild Spells", "Urban Bounty Hunter",
    "Uthgardt Tribe Member", "Variant Criminal: Spy", "Waterdhavian Noble",
]

PDF_MAP = {
    "SCAG": "D&D 5E - Sword Coast Adventurer's Guide.pdf",
    "ToA": "Campaigns/D&D 5E - Tomb of Annihilation.pdf",
    "HotDQ": "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf",
    "GGR": "D&D 5E - Guildmasters' Guide to Ravnica.pdf",
    "PHB": "D&D 5E - Player's Handbook.pdf",
    "WDH": "Campaigns/D&D 5E - Waterdeep - Dragon Heist.pdf",
}

# Pre-filter: where to look for each background
BG_SEARCH = {
    "City Watch": ["SCAG"],
    "Clan Crafter": ["SCAG"],
    "Cloistered Scholar": ["SCAG"],
    "Courtier": ["SCAG"],
    "Faction Agent": ["SCAG"],
    "Far Traveler": ["SCAG"],
    "Inheritor": ["SCAG"],
    "Knight of the Order": ["SCAG"],
    "Mercenary Veteran": ["SCAG"],
    "Urban Bounty Hunter": ["SCAG"],
    "Uthgardt Tribe Member": ["SCAG"],
    "Waterdhavian Noble": ["SCAG"],
    "Anthropologist": ["ToA"],
    "Archaeologist": ["ToA"],
    "Cult of the Dragon Infiltrator": ["HotDQ"],
    "Dragon Scholar": ["HotDQ"],
    "Orzhov Syndicate": ["GGR"],
    "Rakdos Guild Spells": ["GGR"],
    "Guild Merchant": ["PHB"],
    "Variant Criminal: Spy": ["PHB"],
}

for name in MISSING:
    books = BG_SEARCH.get(name, ["SCAG", "PHB", "ToA", "HotDQ", "GGR"])
    search_name = name.split(":")[0].strip()  # "Variant Criminal: Spy" → search for "Variant Criminal"
    
    found = False
    for book_key in books:
        pdf_path = MANUAL_DIR / PDF_MAP[book_key]
        if not pdf_path.exists():
            continue
        
        doc = fitz.open(str(pdf_path))
        for pg in range(len(doc)):
            text = doc[pg].get_text()
            if search_name.lower() in text.lower():
                page_num = pg + 1
                # Skip ToC/Index pages (typically < 5)
                if page_num > 5:
                    idx = text.lower().find(search_name.lower())
                    ctx = text[max(0,idx-60):idx+200].strip()
                    print(f"  {name:35s} → {book_key} p.{page_num:3d}  |  {ctx[:120]}...")
                    found = True
                    break
        
        doc.close()
        if found:
            break
    
    if not found:
        print(f"  {name:35s} → NOT FOUND")
