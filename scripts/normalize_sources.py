#!/usr/bin/env python3
"""Source normalizer for D&D ingested manual data.

Reads all manual_data/*.json files, normalizes their `source` fields:
1. Expands abbreviations (PHB → Player's Handbook, etc.)
2. Standardizes format: "Book Name p.NNN" or "Book Name, Chapter X p.NNN"
3. Flags corrupted/unresolvable entries for manual review
4. Writes cleaned data back (overwrites in-place)

Run: python3 scripts/normalize_sources.py [--dry-run] [--verbose]
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent.parent
MANUAL_DATA = HERE / "data" / "manual_data"

# ── Abbreviation → Full Book Name ──────────────────────────────────────
# Order matters: longer matches first to avoid partial replacement
ABBREV_MAP = {
    # Core books
    "PHB": "Player's Handbook",
    "DMG": "Dungeon Master's Guide",
    "MM": "Monster Manual",
    # Supplements
    "XGE": "Xanathar's Guide to Everything",
    "VGM": "Volo's Guide to Monsters",
    "MTF": "Mordenkainen's Tome of Foes",
    "SCAG": "Sword Coast Adventurer's Guide",
    "EEPC": "Elemental Evil Player's Companion",
    "GGR": "Guildmasters' Guide to Ravnica",
    "WGE": "Wayfinder's Guide to Eberron",
    "TTP": "The Tortle Package",
    "AW": "Ancestral Weapons",
    # Campaigns
    "HotDQ": "Hoard of the Dragon Queen",
    "RoT": "The Rise of Tiamat",
    "LMoP": "Lost Mine of Phandelver",
    "ToA": "Tomb of Annihilation",
    "WDH": "Waterdeep: Dragon Heist",
    "WSC": "The Wild Sheep Chase",
    "TCE": "Tasha's Cauldron of Everything",
    # Common proper-name abbreviations
    "TftYP": "Tales from the Yawning Portal",
    "CoS": "Curse of Strahd",
    "SKT": "Storm King's Thunder",
    "OotA": "Out of the Abyss",
    "PotA": "Princes of the Apocalypse",
}

# Standardized short forms (no page number → canonical short citation)
SHORT_MAP = {
    "player's handbook": "Player's Handbook",
    "dungeon master's guide": "Dungeon Master's Guide", 
    "monster manual": "Monster Manual",
    "xanathar's guide to everything": "Xanathar's Guide to Everything",
    "volo's guide to monsters": "Volo's Guide to Monsters",
    "mordenkainen's tome of foes": "Mordenkainen's Tome of Foes",
    "sword coast adventurer's guide": "Sword Coast Adventurer's Guide",
    "elemental evil player's companion": "Elemental Evil Player's Companion",
    "guildmasters' guide to ravnica": "Guildmasters' Guide to Ravnica",
    "wayfinder's guide to eberron": "Wayfinder's Guide to Eberron",
    "the tortle package": "The Tortle Package",
    "ancestral weapons": "Ancestral Weapons",
    "hoard of the dragon queen": "Hoard of the Dragon Queen",
    "the rise of tiamat": "The Rise of Tiamat",
    "lost mine of phandelver": "Lost Mine of Phandelver",
    "tomb of annihilation": "Tomb of Annihilation",
    "waterdeep: dragon heist": "Waterdeep: Dragon Heist",
    "the wild sheep chase": "The Wild Sheep Chase",
    "eberron: rising from the last war": "Eberron: Rising from the Last War",
}

# ── Corrupted OCR patterns ────────────────────────────────────────────
# If source matches any of these, it's corrupted
CORRUPTION_PATTERNS = [
    re.compile(r"PART\s+I\s+CUSTOMIZ", re.IGNORECASE),
    re.compile(r"OP'flO", re.IGNORECASE),
    re.compile(r"t\s+Ih\s+'70", re.IGNORECASE),
    re.compile(r"p\.\?$"),            # Unknown page
    re.compile(r"Adventure p\.\?"),   # Generic adventure with unknown page
    re.compile(r"Adventure p\.1P$"),  # OCR "?" → "1P"
    re.compile(r"\bp\.1P\b"),         # OCR corruption of "p.?"
]


def is_corrupted(source: str) -> bool:
    """Check if source looks like corrupted OCR output."""
    for pat in CORRUPTION_PATTERNS:
        if pat.search(source):
            return True
    return False


def expand_abbrev(source: str) -> str:
    """Expand known abbreviations to full book names."""
    result = source
    # Sort by length descending to match longer abbreviations first
    for abbrev, full in sorted(ABBREV_MAP.items(), key=lambda x: -len(x[0])):
        # Match abbreviation as a whole word (surrounded by non-alpha or boundaries)
        # Use word-boundary-aware replacement
        pattern = re.compile(r'\b' + re.escape(abbrev) + r'\b')
        if pattern.search(result):
            result = pattern.sub(full, result)
    return result


def normalize_case(source: str) -> str:
    """Normalize casing of known book names."""
    lower = source.lower().strip()
    for key, canonical in SHORT_MAP.items():
        if lower == key:
            return canonical
        # Handle "book name p.NNN" or "book name, chapter X p.NNN"
        if lower.startswith(key + " p."):
            return canonical + source[len(key):]
        if lower.startswith(key + ", chapter"):
            return canonical + source[len(key):]
        if lower.startswith(key + ", p."):
            return canonical + source[len(key):]
        if lower.startswith(key + " p"):
            return canonical + source[len(key):]
    return source


def extract_page(source: str) -> str | None:
    """Extract page number(s) from source string. Returns None if none found."""
    # Match "p.123" or "p.123-124" or "page 123"
    m = re.search(r'p(?:age)?[.\s]*(\d+(?:[–-]\d+)?)', source, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def standardize_format(source: str) -> str:
    """Standardize page reference format."""
    # "Chapter X | Title p.NNN" → "Book Name, Chapter X: Title p.NNN" 
    #    (book name should already be expanded)
    # "Chapter X | Title, page NNN" → same normalization
    # Replace "page NNN" with "p.NNN"
    source = re.sub(r'page\s+(\d+)', r'p.\1', source, flags=re.IGNORECASE)
    # Replace "p NNN" with "p.NNN"
    source = re.sub(r'\bp\s+(\d)', r'p.\1', source)
    # Normalize "Chapter X |" to "Chapter X:"
    source = re.sub(r'Chapter\s+(\d+)\s*\|\s*', r'Chapter \1: ', source)
    # Normalize "Chapter X I" (OCR pipe-as-I) to "Chapter X:"
    source = re.sub(r'Chapter\s+(\d+)\s+I\s+', r'Chapter \1: ', source)
    # Clean up double spaces
    source = re.sub(r'  +', ' ', source)
    # Clean up trailing commas
    source = source.rstrip(', ')
    return source.strip()


def normalize_source(source: str) -> tuple[str, list[str]]:
    """Normalize a single source string.
    
    Returns (normalized_source, flags) where flags indicate issues found.
    """
    if not source or not source.strip():
        return "", ["empty"]
    
    flags = []
    original = source.strip()
    
    # Check corruption first
    if is_corrupted(original):
        flags.append("corrupted")
        return original, flags
    
    result = original
    
    # Step 1: Expand abbreviations
    result = expand_abbrev(result)
    if result != original:
        flags.append("expanded")
    
    # Step 2: Normalize case
    result = normalize_case(result)
    if result != original and "expanded" not in flags:
        flags.append("recased")
    
    # Step 3: Standardize format
    result = standardize_format(result)
    if result != original and not flags:
        flags.append("reformatted")
    
    # Step 4: Check for remaining issues
    if not extract_page(result):
        flags.append("no_page")
    
    # Check if source looks incomplete (only chapter, no book)
    if re.match(r'^Chapter\s+\d+', result) and not any(
        book.lower() in result.lower() for book in SHORT_MAP
    ):
        flags.append("no_book_name")
    
    # Check if source has "(page inferred" or similar
    if "inferred" in result.lower():
        flags.append("inferred")
    
    return result, flags


def normalize_file(filepath: Path, dry_run: bool = False, verbose: bool = False) -> dict:
    """Normalize all sources in a single JSON file.
    
    Returns stats dict.
    """
    with open(filepath) as f:
        data = json.load(f)
    
    stats = Counter()
    cleaned = []
    
    for entry in data:
        source = entry.get("source", "")
        name = entry.get("name", "?")
        
        new_source, flags = normalize_source(source)
        
        if flags:
            for flag in flags:
                stats[f"flag_{flag}"] += 1
            stats["changed"] += 1
            
            if verbose:
                flag_str = ",".join(flags)
                print(f"  [{flag_str}] {name[:40]:40s} | {source[:50]:50s} → {new_source[:60]}")
        
        entry["source"] = new_source
        cleaned.append(entry)
    
    stats["total"] = len(data)
    
    if not dry_run:
        # Backup original
        backup = filepath.with_suffix(".json.bak")
        if not backup.exists():
            with open(backup, 'w') as f:
                json.dump(data, f, indent=2)
        
        with open(filepath, 'w') as f:
            json.dump(cleaned, f, indent=2)
    
    return dict(stats)


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    if dry_run:
        print("=== DRY RUN (no files will be modified) ===\n")
    
    json_files = sorted(MANUAL_DATA.glob("*.json"))
    json_files = [f for f in json_files if f.name != "_meta.json"]
    
    grand_total = Counter()
    
    for filepath in json_files:
        stats = normalize_file(filepath, dry_run=dry_run, verbose=verbose)
        
        changed = stats.get("changed", 0)
        total = stats.get("total", 0)
        
        if changed > 0 or verbose:
            print(f"\n── {filepath.name} ({total} entries, {changed} changed)")
            for key in sorted(stats):
                if key.startswith("flag_"):
                    print(f"   {key[5:]}: {stats[key]}")
        
        grand_total.update(stats)
    
    print(f"\n{'='*50}")
    print(f"GRAND TOTAL: {grand_total.get('total',0)} entries across {len(json_files)} files")
    print(f"  Changed: {grand_total.get('changed',0)}")
    for key in sorted(grand_total):
        if key.startswith("flag_"):
            print(f"  {key[5:]}: {grand_total[key]}")
    
    if dry_run:
        print("\n⚠ DRY RUN — no files modified. Run without --dry-run to apply.")
    else:
        print("\n✓ All files updated. Backups saved as .json.bak")


if __name__ == "__main__":
    main()
