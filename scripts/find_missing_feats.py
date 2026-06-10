#!/usr/bin/env python3
"""Brute-force find exact pages for the 4 missing feats."""
import fitz
from pathlib import Path

MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

targets = [
    ("collector_of_secrets", "Collector of Secrets", "GGR"),
    ("guildmasters_confidant", "Guildmaster's Confidant", "GGR"),
    ("inner_circle", "Inner Circle", "GGR"),
    ("svirfneblin_magic", "Svirfneblin Magic", "EEPC"),
]

PDF_MAP = {
    "GGR": "D&D 5E - Guildmasters' Guide to Ravnica.pdf",
    "EEPC": "D&D 5E - Elemental Evil Player's Companion.pdf",
    "SCAG": "D&D 5E - Sword Coast Adventurer's Guide.pdf",
    "PHB": "D&D 5E - Player's Handbook.pdf",
}

for key, name, book in targets:
    print(f"\n=== {name} ({book}) ===")
    
    for book_key in [book, "SCAG", "EEPC", "PHB"]:
        pdf_filename = PDF_MAP.get(book_key)
        if not pdf_filename:
            continue
        pdf_path = MANUAL_DIR / pdf_filename
        if not pdf_path.exists():
            continue
        
        doc = fitz.open(str(pdf_path))
        found_pages = []
        
        # Search for exact name
        for pg in range(len(doc)):
            text = doc[pg].get_text()
            if name.lower() in text.lower():
                found_pages.append(pg + 1)
        
        if found_pages:
            print(f"  [{book_key}] Found on pages: {found_pages}")
            # Show context from best page (typically the first non-ToC page)
            best_pg = found_pages[0]
            for pg in found_pages:
                if pg > 5:  # Skip front matter/ToC
                    best_pg = pg
                    break
            
            text = doc[best_pg - 1].get_text()
            idx = text.lower().find(name.lower())
            start = max(0, idx - 80)
            end = min(len(text), idx + 400)
            context = text[start:end].strip()
            print(f"  Page {best_pg} context:")
            print(f"    ...{context[:300]}...")
            break
        else:
            print(f"  [{book_key}] Not found")
        
        doc.close()
    
    print()
