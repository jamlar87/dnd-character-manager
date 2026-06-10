#!/usr/bin/env python3
"""Third pass — targeted searches for tricky ones."""
import fitz
from pathlib import Path

MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

# Targeted searches with corrected terms
TARGETED = [
    # Cult of the Dragon Infiltrator — search for "infiltrator" near background sections
    ("infiltrator", "HotDQ", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
    # Dragon Scholar — maybe "scholar" or look for "specialty" backgrounds
    ("scholar", "HotDQ", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
    # Orzhov Syndicate — this might be a guild page, not background
    ("Orzhov Representative", "GGR", "D&D 5E - Guildmasters' Guide to Ravnica.pdf"),
    # Cloistered Scholar — SCAG backgrounds p.146-154
    ("cloistered scholar", "SCAG", "D&D 5E - Sword Coast Adventurer's Guide.pdf"),
    # Courtier — SCAG
    ("courtier", "SCAG", "D&D 5E - Sword Coast Adventurer's Guide.pdf"),
    # Waterdhavian Noble — SCAG
    ("waterdhavian noble", "SCAG", "D&D 5E - Sword Coast Adventurer's Guide.pdf"),
    # Anthropologist — ToA
    ("anthropologist", "ToA", "Campaigns/D&D 5E - Tomb of Annihilation.pdf"),
    # Archaeologist — ToA
    ("archaeologist", "ToA", "Campaigns/D&D 5E - Tomb of Annihilation.pdf"),
]

for search_name, book, pdf_name in TARGETED:
    pdf_path = MANUAL_DIR / pdf_name
    if not pdf_path.exists():
        print(f"  {search_name} ← MISSING PDF")
        continue
    
    doc = fitz.open(str(pdf_path))
    found = []
    for pg in range(len(doc)):
        text = doc[pg].get_text()
        if search_name.lower() in text.lower():
            page = pg + 1
            if page > 5:  # Skip front matter
                idx = text.lower().find(search_name.lower())
                ctx = text[max(0,idx-60):idx+200].replace('\n',' ').strip()
                found.append((page, ctx[:180]))
    
    print(f"\n=== {search_name} ({book}) === {len(found)} hits:")
    for pg, ctx in found[:5]:
        print(f"  p.{pg:3d}: ...{ctx}...")
    
    doc.close()
