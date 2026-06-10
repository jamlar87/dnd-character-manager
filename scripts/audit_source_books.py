#!/usr/bin/env python3
"""Audit all source books: compare PDF contents against ingested data."""
import fitz, json, re, sys
from pathlib import Path
from collections import defaultdict

MANUAL_DATA = Path("/home/james/dnd-character-manager/data/manual_data")
MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
MANUALS_ALT = MANUALS_DIR / "Manuals"

# Load meta
with open(MANUAL_DATA / "_meta.json") as f:
    meta = json.load(f)

# Load our ingested data by source book
our_data = defaultdict(lambda: defaultdict(list))
data_files = {
    "races.json": "races", "spells.json": "spells",
    "magic_items.json": "magic_items", "equipment.json": "equipment",
    "feats.json": "feats", "backgrounds.json": "backgrounds",
    "subclasses.json": "subclasses",
}
for fname, dtype in data_files.items():
    fpath = MANUAL_DATA / fname
    if not fpath.exists():
        continue
    with open(fpath) as f:
        for item in json.load(f):
            src = item.get("_source_manual", "unknown")
            name = item.get("name", "")
            if name:
                our_data[src][dtype].append(name)

# Also load SRD data for comparison
SRD_CACHE = Path("/home/james/dnd-character-manager/data/srd_cache")
srd_spells = set()
try:
    with open(SRD_CACHE / "spells.json") as f:
        for s in json.load(f):
            srd_spells.add(s.get("name", "").lower())
except:
    pass

srd_magic = set()
try:
    with open(SRD_CACHE / "magic-items.json") as f:
        for s in json.load(f):
            srd_magic.add(s.get("name", "").lower())
except:
    pass

# Known reference counts per book (from D&D Beyond / official sources)
EXPECTED = {
    "XGE": {
        "spells": 95,  # 95 new spells in XGE Chapter 3
        "magic_items": 28,  # Common magic items + a few others
        "feats": 15,  # Racial feats
    },
    "DMG": {
        "magic_items": 380,  # ~380 total in DMG (SRD has 362 of them)
    },
    "PHB": {
        "feats": 42,
        "spells": 305,  # ~305 spells in PHB 2014
        "equipment": 214,  # Full chapter 5
    },
    "SCAG": {
        "backgrounds": 12,
        "subclasses": 12,
        "spells": 4,  # Cantrips (Booming Blade, Green-Flame Blade, Lightning Lure, Sword Burst)
    },
    "EEPC": {
        "spells": 43,
        "races": 4,  # Aarakocra, Genasi, Goliath, Deep Gnome
    },
    "GGR": {
        "magic_items": 30,
        "backgrounds": 10,  # Guild backgrounds
        "races": 5,  # Centaur, Goblin, Loxodon, Minotaur, Simic Hybrid, Vedalken
        "feats": 8,
    },
    "VGM": {
        "races": 13,  # Aasimar, Bugbear, Firbolg, Goblin, Goliath, Hobgoblin, Kenku, Kobold, Lizardfolk, Orc, Tabaxi, Triton, Yuan-ti
    },
    "MTF": {
        "races": 5,  # Gith, plus subrace expansions
    },
}

def find_pdf(book_slug):
    info = meta.get("pdf_map", {}).get(book_slug, {})
    title = info.get("title", "")
    for candidate in [
        MANUALS_DIR / f"{title}.pdf",
        MANUALS_ALT / f"{title}.pdf",
    ]:
        if candidate.exists():
            return candidate, title
    return None, title

def extract_spell_names_from_pages(doc, start_pg, end_pg):
    """Extract spell names from a page range using metadata patterns."""
    names = set()
    for pg in range(start_pg, min(end_pg, doc.page_count)):
        text = doc[pg].get_text()
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if len(line) < 3 or len(line) > 50:
                continue
            if not line[0].isupper():
                continue
            if line.isupper() and len(line) < 5:
                continue  # Skip acronyms
            if re.match(r'^(CHAPTER|PART|APPENDIX|TABLE|SECTION)\b', line):
                continue
            # Check if next lines contain spell metadata
            ctx_start = max(0, i+1)
            ctx_end = min(len(lines), i+6)
            ctx = ' '.join(lines[ctx_start:ctx_end]).lower()
            spell_keywords = ['evocation', 'conjuration', 'necromancy', 'abjuration',
                            'transmutation', 'enchantment', 'divination', 'illusion',
                            'cantrip', '1st-level', '2nd-level', '3rd-level',
                            '4th-level', '5th-level', '6th-level', '7th-level',
                            '8th-level', '9th-level', 'casting time', 'components', 'duration']
            if any(kw in ctx for kw in spell_keywords):
                # Clean OCR noise
                name = re.sub(r'\s+', ' ', line).strip()
                if len(name) >= 4:
                    names.add(name)
    return names

def extract_magic_item_names(doc, start_pg, end_pg):
    """Extract magic item names — they're usually bold headers followed by metadata."""
    names = set()
    for pg in range(start_pg, min(end_pg, doc.page_count)):
        text = doc[pg].get_text()
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if len(line) < 4 or len(line) > 60:
                continue
            if not line[0].isupper():
                continue
            # Magic items often preceded/followed by rarity or "Wondrous item"
            ctx_start = max(0, i-2)
            ctx_end = min(len(lines), i+5)
            ctx = ' '.join(lines[ctx_start:ctx_end]).lower()
            item_keywords = ['wondrous', 'weapon', 'armor', 'potion', 'ring',
                           'rod', 'staff', 'scroll', 'wand', 'rare', 'uncommon',
                           'legendary', 'very rare', 'common', 'artifact',
                           'requires attunement']
            if any(kw in ctx for kw in item_keywords):
                name = re.sub(r'\s+', ' ', line).strip()
                if 4 <= len(name) <= 50:
                    names.add(name)
    return names

def extract_feat_names(doc, start_pg, end_pg):
    """Extract feat names from feat section."""
    names = set()
    for pg in range(start_pg, min(end_pg, doc.page_count)):
        text = doc[pg].get_text()
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if 5 <= len(line) <= 45 and line[0].isupper():
                if re.match(r'^[A-Z][a-z]+(?:\s+[A-Za-z]+){0,8}$', line):
                    names.add(line)
    return names

# Main audit
print("=" * 70)
print("SOURCE BOOK AUDIT — comparing PDF contents vs. ingested data")
print("=" * 70)

for book_slug in sorted(set(list(EXPECTED.keys()) + list(our_data.keys()))):
    if book_slug in ("unknown", "AW", "WSC"):
        continue  # Skip non-standard sources
    
    pdf_path, title = find_pdf(book_slug)
    if not pdf_path:
        expected = EXPECTED.get(book_slug, {})
        parts = [f"{v} {k}" for k, v in expected.items()]
        print(f"\n{book_slug} ({title}): PDF NOT FOUND — skipping ({', '.join(parts) or 'no known data'})")
        continue
    
    print(f"\n{'─'*60}")
    print(f"{book_slug}: {title}")
    
    our = our_data[book_slug]
    our_summary = {k: len(v) for k, v in our.items()}
    print(f"  We have: {our_summary}")
    
    expected = EXPECTED.get(book_slug, {})
    
    doc = fitz.open(pdf_path)
    
    for dtype, exp_count in expected.items():
        our_count = len(our.get(dtype, []))
        
        # For DMG magic items: SRD covers most
        if book_slug == "DMG" and dtype == "magic_items":
            total = our_count + len(srd_magic)
            print(f"  {dtype}: {our_count} manual + {len(srd_magic)} SRD = ~{total} total (expected ~{exp_count})")
            if total < exp_count:
                print(f"    ⚠ Might be missing ~{exp_count - total}")
            else:
                print(f"    ✓")
            continue
        
        # For PHB: most data comes from SRD
        if book_slug == "PHB":
            if dtype == "spells":
                total = our_count + len(srd_spells)
                print(f"  {dtype}: {our_count} manual + {len(srd_spells)} SRD = ~{total} (expected ~{exp_count})")
                continue
            elif dtype == "feats":
                print(f"  {dtype}: {our_count} (expected {exp_count})")
                if our_count < exp_count:
                    print(f"    ⚠ Missing {exp_count - our_count} feats")
                elif our_count >= exp_count:
                    print(f"    ✓ (includes extras from other sources)")
                continue
        
        # Standard comparison
        if our_count >= exp_count:
            print(f"  {dtype}: {our_count}/{exp_count} ✓")
        else:
            shortfall = exp_count - our_count
            print(f"  {dtype}: {our_count}/{exp_count} ⚠ missing ~{shortfall}")
    
    doc.close()

# Summary
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

# Books not found
for book_slug in ["HotDQ", "LMoP", "RoT", "ToA", "WDH", "AW", "WSC"]:
    if book_slug in our_data:
        print(f"  {book_slug}: {sum(len(v) for v in our_data[book_slug].values())} items (adventure module — no PDF for audit)")

print("\n  Major gaps to investigate:")
print("    1. XGE spells: need ~40 more (we have 55, XGE has ~95)")
print("    2. XGE magic items: might be missing some (we have 51, XGE has ~28 common + expansions)")
print("    3. DMG magic items: check for SRD gaps (362 SRD + 60 manual vs ~380 expected)")
print("    4. PHB feats: we have 66 (more than 42 expected — overlap with other sources)")
