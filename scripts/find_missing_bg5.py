#!/usr/bin/env python3
"""Final pass — look at HotDQ appendix A and GGR backgrounds section."""
import fitz
from pathlib import Path

MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

# Check HotDQ p.85-95 for backgrounds appendix
print("=== HotDQ pages 85-95 (Backgrounds Appendix) ===")
pdf_path = MANUAL_DIR / "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"
doc = fitz.open(str(pdf_path))
for pg in range(84, min(95, len(doc))):
    text = doc[pg].get_text()
    # Show first 300 chars
    preview = text[:400].replace('\n', ' ')
    print(f"\n--- p.{pg+1} ---")
    print(preview)
doc.close()

# Check GGR backgrounds section (Orzhov Representative)
print("\n\n=== GGR Backgrounds section ===")
pdf_path = MANUAL_DIR / "D&D 5E - Guildmasters' Guide to Ravnica.pdf"
doc = fitz.open(str(pdf_path))
for pg in range(len(doc)):
    text = doc[pg].get_text()
    # Look for "Orzhov Representative" pattern
    if "orzhov" in text.lower() and "background" in text.lower():
        print(f"\n--- GGR p.{pg+1} ---")
        idx = text.lower().find("orzhov")
        print(text[max(0,idx-100):idx+300].replace('\n', ' '))
doc.close()
