#!/usr/bin/env python3
"""Fourth pass — search for Orzhov, Cult of Dragon, Dragon Scholar."""
import fitz
from pathlib import Path

MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

SEARCHES = [
    # Orzhov — search for "Orzhov" near "background" or "feature" in GGR
    ("Orzhov", "GGR", "D&D 5E - Guildmasters' Guide to Ravnica.pdf"),
    # Dragon Cultist background
    ("dragon cultist", "HotDQ", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
    # Try searching for "Background" section near "Dragon" in HotDQ
    ("background", "HotDQ", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
]

for search_name, book, pdf_name in SEARCHES:
    pdf_path = MANUAL_DIR / pdf_name
    if not pdf_path.exists():
        continue
    
    doc = fitz.open(str(pdf_path))
    found = []
    for pg in range(len(doc)):
        text = doc[pg].get_text()
        if search_name.lower() in text.lower():
            page = pg + 1
            if page > 5:
                idx = text.lower().find(search_name.lower())
                ctx = text[max(0,idx-80):idx+200].replace('\n',' ').strip()
                found.append((page, ctx[:200]))
    
    print(f"\n=== {search_name} ({book}) === {len(found)} hits:")
    for pg, ctx in found[:10]:
        print(f"  p.{pg:3d}: ...{ctx}...")
    
    doc.close()

# Also check HotDQ for Backgrounds appendix
print("\n\n=== HotDQ BACKGROUNDS section ===")
pdf_path = MANUAL_DIR / "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"
doc = fitz.open(str(pdf_path))
for pg in range(len(doc)):
    text = doc[pg].get_text()
    if "background" in text.lower() and ("infiltrator" in text.lower() or "dragon scholar" in text.lower() or "dragon cultist" in text.lower() or "specialty" in text.lower()):
        print(f"  p.{pg+1}: FOUND MATCH")
        idx = text.lower().find("background")
        print(f"    {text[max(0,idx-50):idx+300]}")
doc.close()
