"""Batch-search monster names in PDFs to find page numbers."""
import json, subprocess, re
from pathlib import Path

data = json.load(open("data/manual_data/monsters.json"))
items = list(data.values()) if isinstance(data, dict) else data

# Build slug → monster names lookup
missing = {}
for entry in items:
    source = entry.get("source", "")
    slug = (entry.get("_source_manual") or "").strip().upper()
    name = entry.get("name", "?")
    m = re.match(r'^\(([^,]+)\)$', source)
    if m and slug:
        missing.setdefault(slug, []).append(entry)

# PDF Paths
MANUALS = Path("/home/james/dnd-character-manager/manuals")
PDF_PATHS = {
    "MTF": MANUALS / "DnD-Manuals/D&D 5E - Mordenkainen's Tome of Foes.pdf",
    "VGM": MANUALS / "DnD-Manuals/D&D 5E - Volo's Guide to Monsters.pdf",
    "TOA": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Tomb of Annihilation.pdf",
    "COTN": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Call of the Netherdeep.pdf",
    "GGR": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Guildmasters' Guide to Ravnica.pdf",
    "EGW": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Explorer's Guide to Wildemount.pdf",
    "WDH": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Waterdeep - Dragon Heist.pdf",
    "TCSR": MANUALS / "DnD-Manuals/Campaigns/Tal'Dorei Campaign Setting Reborn.pdf",
    "HOTDQ": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Hoard of the Dragon Queen.pdf",
    "ROT": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Tyranny of Dragons - The Rise of Tiamat.pdf",
    "LMOP": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Lost Mine of Phandelver.pdf",
    "DMG": MANUALS / "D&D 5E - Dungeon Master's Guide.pdf",
    "PHB": MANUALS / "D&D 5E - Player's Handbook.pdf",
    "XGE": MANUALS / "D&D 5E - Xanathar's Guide to Everything.pdf",
    "SCAG": MANUALS / "D&D 5E - Sword Coast Adventurer's Guide.pdf",
    "EEPC": MANUALS / "D&D 5E - Elemental Evil Player's Companion.pdf",
    "TTP": MANUALS / "D&D 5E - The Tortle Package.pdf",
    "CC": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Creature_Codex_(5E)_v2.1.pdf",
    "MPG": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Tales_Margreve_Players_Guide_5E_DnD.pdf",
    "TMFRV": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Tales_Margreve_5E_FINAL_REOPTIMIZED_v2.pdf",
    "CSF": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Courts_Shadow_Fey_5E.pdf",
    "EBT": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Book_of_Ebon_Tides.pdf",
    "TFS": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Tales_from_the_Shadows.pdf",
    "SOM": MANUALS / "DnD-Manuals/5e Kobold Press Resources/DDEX13_Shadows_over_the_Moonsea.pdf",
    "SME": MANUALS / "DnD-Manuals/5e Kobold Press Resources/177004-Saltmarsh_Encounters.pdf",
    "SDQ": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Shadows_of_the_Dusk_Queen_5E_FINAL.pdf",
    "WRKF": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Wrath_of_the_River_King.pdf",
    "TLT": MANUALS / "DnD-Manuals/5e Kobold Press Resources/1346683-The_Tortured_Land_-_Taster.pdf",
    "EIA": MANUALS / "DnD-Manuals/5e Kobold Press Resources/378310-Encounters_In_Avernus.pdf",
    "WLL": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Warlock-Lair-9-The-Returners-Tower.pdf",
    "W2": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Warlock-7.pdf",
    "W5": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Warlock-32.pdf",
    "W7": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Warlock-Bestiary.pdf",
    "BGDIA": MANUALS / "DnD-Manuals/Campaigns/D&D 5E - Baldur's Gate - Descent into Avernus.pdf",
    "RRG": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Rhovanion-Region-Guide.pdf",
    "ERIA": MANUALS / "DnD-Manuals/5e Kobold Press Resources/Eriador_Adventures.pdf",
    "DD": None,
}

# Also load SRD monster page map for SRD monsters (already have page numbers mostly)
# Manual monsters don't have page numbers — we're adding them

total_found = 0
total_missing = 0

for slug in ["MTF", "VGM", "TOA", "COTN", "GGR", "EGW", "WDH", "TCSR", 
             "HOTDQ", "ROT", "LMOP", "CSF", "EBT", "TFS", "WRKF", "EIA",
             "TLT", "SDQ", "SME", "W2", "W5", "W7", "RRG", "ERIA", "MPG", "DMG", "PHB", "XGE"]:
    
    entries = missing.get(slug, [])
    if not entries:
        continue
    
    pdf_path = PDF_PATHS.get(slug)
    if not pdf_path or not pdf_path.exists():
        print(f"{slug}: PDF not found ({pdf_path}) — skipping {len(entries)} entries")
        continue
    
    print(f"\n{slug}: Searching {len(entries)} monsters in {pdf_path.name}...")
    
    # Get page count
    r = subprocess.run(['pdfinfo', str(pdf_path)], capture_output=True, text=True, timeout=10)
    total_pages = 0
    for line in r.stdout.split('\n'):
        if 'Pages' in line:
            total_pages = int(line.split(':')[1].strip())
            break
    
    found_names = []
    
    for entry in entries:
        name = entry.get("name", "")
        if not name:
            continue
        
        # Search page by page for the monster name
        found_page = None
        # First try a faster approach: search the full text for line numbers
        # then map line numbers to pages
        pr = subprocess.run(['pdftotext', str(pdf_path), '-'], 
                           capture_output=True, text=True, timeout=60)
        full_text = pr.stdout
        
        # Find the name in the text
        idx = full_text.find(name)
        if idx >= 0:
            # Try to find the page number by extracting the page near this position
            # pdftotext outputs form feed characters between pages
            text_before = full_text[:idx]
            page_count = text_before.count('\x0c') + 1  # form feeds = page breaks
            found_page = page_count
        
        if found_page and found_page <= total_pages:
            clean_src = re.sub(r'[\x00-\x1f]', ' ', entry['source'])[1:-1]
            entry["source"] = f"({clean_src}, p.{found_page})"
            found_names.append((name, found_page))
    
    if found_names:
        print(f"  Found: {len(found_names)}/{len(entries)}")
        for n, p in found_names[:5]:
            print(f"    {n}: p.{p}")

# Save updated data
json.dump(data, open("data/manual_data/monsters.json", "w"), indent=2, ensure_ascii=False)

print(f"\n\nDone. Total found: {total_found}")
