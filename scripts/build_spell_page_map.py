#!/usr/bin/env python3
"""
Build a spell→page mapping by searching D&D PDFs with pymupdf.
Searches for spell names as headings in the appropriate sourcebooks.

Output: data/spell_page_map.json
  {spell_name_lower: {"page": N, "source_str": "PHB 2014 p.211"}}
"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

try:
    import fitz
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    print("WARNING: pymupdf not available", file=sys.stderr)

DATA_DIR = Path("/home/james/dnd-character-manager/data")
MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
OUTPUT_PATH = DATA_DIR / "spell_page_map.json"

SOURCE_TO_PDF = {
    "PHB 2014": ("D&D 5E - Player's Handbook.pdf", "PHB 2014"),
    "Player's Handbook": ("D&D 5E - Player's Handbook.pdf", "PHB 2014"),
    "Xanathar's Guide to Everything": ("D&D 5E - Xanathar's Guide to Everything.pdf", "XGE"),
    "Elemental Evil Player's Companion": ("D&D 5E - Elemental Evil Player's Companion.pdf", "EEPC"),
    "Sword Coast Adventurer's Guide": ("D&D 5E - Sword Coast Adventurer's Guide.pdf", "SCAG"),
    "Guildmasters' Guide to Ravnica": ("D&D 5E - Guildmasters' Guide to Ravnica.pdf", "GGR"),
    "Volo's Guide to Monsters": ("D&D 5E - Volo's Guide to Monsters.pdf", "VGM"),
}

HAS_PAGE_RE = re.compile(r'\b[pP]\.?\s*\d+')

# SRD→PHB spell name mapping (SRD strips proper names for copyright reasons)
SRD_TO_PHB_NAME = {
    "acid arrow": "melf's acid arrow",
    "arcane hand": "bigby's hand",
    "arcane sword": "mordenkainen's sword",
    "arcanist's magic aura": "nystul's magic aura",
    "faithful hound": "mordenkainen's faithful hound",
    "floating disk": "tenser's floating disk",
    "hideous laughter": "tasha's hideous laughter",
    "instant summons": "drawmij's instant summons",
    "irresistible dance": "otto's irresistible dance",
    "magnificent mansion": "mordenkainen's magnificent mansion",
    "private sanctum": "mordenkainen's private sanctum",
    "resilient sphere": "otiluke's resilient sphere",
    "secret chest": "leomund's secret chest",
    "telepathic bond": "rary's telepathic bond",
    "tiny hut": "leomund's tiny hut",
    "crushing hand": "bigby's hand",  # 9th-level version, same page
    "grasping hand": "bigby's hand",  # 7th-level version
    "clenched fist": "bigby's hand",  # 8th-level version
    "forceful hand": "bigby's hand",  # 6th-level version
    "interposing hand": "bigby's hand",  # 5th-level version
    "freezing sphere": "otiluke's freezing sphere",
    "acid arrow": "melf's acid arrow",
}

def resolve_source(source_str):
    if not source_str:
        return None
    if source_str in SOURCE_TO_PDF:
        return SOURCE_TO_PDF[source_str]
    lower = source_str.lower()
    for key, value in SOURCE_TO_PDF.items():
        if key.lower() in lower:
            return value
    if 'phb' in lower or "player's handbook" in lower:
        return SOURCE_TO_PDF["PHB 2014"]
    if 'xge' in lower or 'xanathar' in lower:
        return SOURCE_TO_PDF["Xanathar's Guide to Everything"]
    if 'ee' in lower or 'elemental evil' in lower:
        return SOURCE_TO_PDF["Elemental Evil Player's Companion"]
    if 'scag' in lower or 'sword coast' in lower:
        return SOURCE_TO_PDF["Sword Coast Adventurer's Guide"]
    if 'ggr' in lower or 'ravnica' in lower:
        return SOURCE_TO_PDF["Guildmasters' Guide to Ravnica"]
    if 'vgm' in lower or "volo" in lower:
        return SOURCE_TO_PDF["Volo's Guide to Monsters"]
    return None


def search_pdf_for_spell(pdf_path, spell_name, page_texts, is_phb=False):
    """Search page_texts for a spell name. Returns page number (1-indexed) or None."""
    name_lower = spell_name.lower().strip()
    name_words = name_lower.split()

    # For PHB, also try the SRD→PHB name mapping
    search_names = [name_lower]
    if is_phb and name_lower in SRD_TO_PHB_NAME:
        search_names.append(SRD_TO_PHB_NAME[name_lower])

    # For PHB, only search spell description pages (211+)
    start_page = 210 if is_phb else 0  # 0-indexed, so 210 = PHB page 211

    for search_name in search_names:
        sn_lower = search_name.lower()

        # Strategy 1: Exact line match + two-line join
        for pg, text in enumerate(page_texts):
            if pg < start_page:
                continue
            lines = text.split('\n')
            for i in range(len(lines)):
                line = lines[i].strip().lower()
                if line == sn_lower:
                    context = '\n'.join(lines[max(0,i-1):min(i+5, len(lines))]).lower()
                    if any(w in context for w in ['cantrip', 'level', 'evocation', 'conjuration',
                        'necromancy', 'abjuration', 'transmutation', 'enchantment',
                        'divination', 'illusion', '1st', '2nd', '3rd', '4th',
                        '5th', '6th', '7th', '8th', '9th']):
                        return pg + 1
                if i + 1 < len(lines):
                    joined = (lines[i].strip() + ' ' + lines[i+1].strip()).lower()
                    if joined == sn_lower:
                        context = '\n'.join(lines[max(0,i-1):min(i+6, len(lines))]).lower()
                        if any(w in context for w in ['cantrip', 'level', 'evocation', 'conjuration',
                            'necromancy', 'abjuration', 'transmutation', 'enchantment',
                            'divination', 'illusion', '1st', '2nd', '3rd', '4th',
                            '5th', '6th', '7th', '8th', '9th']):
                            return pg + 1

        # Strategy 2: Broad substring search
        for pg, text in enumerate(page_texts):
            if pg < start_page:
                continue
            if sn_lower in text.lower():
                idx = text.lower().find(sn_lower)
                nearby = text[max(0,idx-200):idx+len(sn_lower)+300].lower()
                if any(w in nearby for w in ['cantrip', 'level', 'evocation', 'conjuration',
                    'necromancy', 'abjuration', 'transmutation', 'enchantment',
                    'divination', 'illusion', '1st-level', '2nd-level', '3rd-level',
                    '4th-level', '5th-level', '6th-level', '7th-level',
                    '8th-level', '9th-level', 'casting time', 'range', 'duration']):
                    return pg + 1

        # Strategy 3: Unrestricted (only for non-PHB or as fallback)
        if is_phb:
            for pg, text in enumerate(page_texts):
                if pg >= start_page:
                    continue
                if sn_lower in text.lower():
                    return pg + 1

    return None


def main():
    print("=== Building Spell Page Map ===\n")

    # Load spells
    with open(DATA_DIR / "srd_cache/spells.json") as f:
        srd_spells = json.load(f)
    with open(DATA_DIR / "manual_data/spells.json") as f:
        man_spells = json.load(f)

    # Build item list
    spell_items = []
    for s in srd_spells:
        name = s.get('name', '')
        if name:
            spell_items.append((name.lower(), name, s.get('source', '') or 'PHB 2014'))
    for s in man_spells:
        name = s.get('name', '')
        if name:
            src = s.get('source', '')
            if not src and s.get('_source_manual'):
                src = s['_source_manual']
            spell_items.append((name.lower(), name, src or 'PHB 2014'))

    print(f"Total spells: {len(spell_items)}")

    # Group by PDF
    by_pdf = defaultdict(list)
    unknown = []
    for key, name, src in spell_items:
        resolved = resolve_source(src)
        if resolved:
            pdf_filename, display_prefix = resolved
            pdf_path = MANUALS_DIR / pdf_filename
            if pdf_path.exists():
                by_pdf[str(pdf_path)].append((key, name, display_prefix))
            else:
                unknown.append((key, name, src))
        else:
            unknown.append((key, name, src))

    print(f"Unknown sources: {len(unknown)}")
    print(f"PDFs to search: {len(by_pdf)}")
    for pdf_path, items in sorted(by_pdf.items(), key=lambda x: -len(x[1])):
        print(f"  {Path(pdf_path).name}: {len(items)} spells")

    # Search each PDF
    results = {}

    for pdf_path_str, items in sorted(by_pdf.items(), key=lambda x: -len(x[1])):
        pdf_filename = Path(pdf_path_str).name
        print(f"\n--- Searching {pdf_filename} ({len(items)} spells) ---")

        if not HAS_FITZ:
            print(f"  (no pymupdf, skipping)")
            for key, name, display_prefix in items:
                results[key] = {"page": None, "source_str": display_prefix}
            continue

        doc = fitz.open(pdf_path_str)
        total_pages = len(doc)

        # Pre-extract page text
        page_texts = []
        for p in range(total_pages):
            page_texts.append(doc[p].get_text("text"))
        doc.close()

        found = 0
        is_phb = 'player' in pdf_filename.lower()
        for key, name, display_prefix in items:
            page = search_pdf_for_spell(pdf_path_str, name, page_texts, is_phb=is_phb)
            if page:
                results[key] = {"page": page, "source_str": f"{display_prefix} p.{page}"}
                found += 1
            else:
                results[key] = {"page": None, "source_str": display_prefix}

        print(f"  Found: {found}, Not found: {len(items) - found}")

    # Stats
    total_found = sum(1 for v in results.values() if v.get("page"))
    total_missing = len(results) - total_found
    print(f"\n=== RESULTS ===")
    print(f"Total spells: {len(results)}")
    print(f"With page numbers: {total_found}")
    print(f"Without page numbers: {total_missing}")

    if total_missing:
        print("\nSample missing:")
        shown = 0
        for key, val in sorted(results.items()):
            if not val.get("page") and shown < 15:
                print(f"  {key!r}: {val['source_str']!r}")
                shown += 1

    # Save
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
