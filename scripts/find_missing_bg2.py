#!/usr/bin/env python3
"""Second pass — search for the 3 missing + fix index-hit pages."""
import fitz
from pathlib import Path

MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

# Remaining searches
SEARCHES = [
    ("Cult of the Dragon", "HotDQ", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
    ("Dragon Scholar", "HotDQ", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
    ("spy", "PHB", "D&D 5E - Player's Handbook.pdf"),  # Spy variant
]

for search_name, book, pdf_name in SEARCHES:
    pdf_path = MANUAL_DIR / pdf_name
    if not pdf_path.exists():
        print(f"  {search_name} ← PDF not found: {pdf_path}")
        continue
    
    doc = fitz.open(str(pdf_path))
    found = []
    for pg in range(len(doc)):
        text = doc[pg].get_text()
        if search_name.lower() in text.lower():
            page = pg + 1
            if page > 5:  # Skip ToC
                idx = text.lower().find(search_name.lower())
                ctx = text[max(0,idx-50):idx+200].replace('\n',' ').strip()
                found.append((page, ctx[:150]))
    
    print(f"\n=== {search_name} ({book}) === Found on {len(found)} pages:")
    for pg, ctx in found[:10]:
        print(f"  p.{pg:3d}: ...{ctx}...")
    
    doc.close()
