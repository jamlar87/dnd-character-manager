#!/usr/bin/env python3
"""PDF page-finder: resolves 'p.?' source references by searching PDFs.

For each ingested entry with a corrupted source (p.?), searches the
corresponding PDF for the entity name and fills in the page number.

Run: python3 scripts/resolve_pages.py [--dry-run] [--verbose]
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter

try:
    import fitz  # pymupdf
except ImportError:
    print("ERROR: pymupdf not installed. Run: pip install pymupdf")
    sys.exit(1)

HERE = Path(__file__).resolve().parent.parent
MANUAL_DATA = HERE / "data" / "manual_data"
MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

# ── Book name → PDF path mapping ──────────────────────────────────────
# Normalized book names → absolute PDF paths
BOOK_PDF_MAP = {
    "guildmasters' guide to ravnica": MANUAL_DIR / "D&D 5E - Guildmasters' Guide to Ravnica.pdf",
    "ggr": MANUAL_DIR / "D&D 5E - Guildmasters' Guide to Ravnica.pdf",
    "monster manual": MANUAL_DIR / "D&D 5E - Monster Manual.pdf",
    "mm": MANUAL_DIR / "D&D 5E - Monster Manual.pdf",
    "rise of tiamat": MANUAL_DIR / "Campaigns" / "D&D 5E - Tyranny of Dragons - The Rise of Tiamat.pdf",
    "tomb of annihilation": MANUAL_DIR / "Campaigns" / "D&D 5E - Tomb of Annihilation.pdf",
    "waterdeep: dragon heist": MANUAL_DIR / "Campaigns" / "D&D 5E - Waterdeep - Dragon Heist.pdf",
    "dungeon master's tools": MANUAL_DIR / "D&D 5E - Xanathar's Guide to Everything.pdf",
    "xanathar's guide to everything": MANUAL_DIR / "D&D 5E - Xanathar's Guide to Everything.pdf",
    "dragon season": MANUAL_DIR / "Campaigns" / "D&D 5E - Waterdeep - Dragon Heist.pdf",
    "dragon's season": MANUAL_DIR / "Campaigns" / "D&D 5E - Waterdeep - Dragon Heist.pdf",
    "hoard of the dragon queen": MANUAL_DIR / "Campaigns" / "D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf",
    "lost mine of phandelver": MANUAL_DIR / "Campaigns" / "D&D 5E - Lost Mine of Phandelver.pdf",
    "elemental evil player's companion": MANUAL_DIR / "D&D 5E - Elemental Evil Player's Companion.pdf",
    "sword coast adventurer's guide": MANUAL_DIR / "D&D 5E - Sword Coast Adventurer's Guide.pdf",
    "volo's guide to monsters": MANUAL_DIR / "D&D 5E - Volo's Guide to Monsters.pdf",
    "mordenkainen's tome of foes": MANUAL_DIR / "D&D 5E - Mordenkainen's Tome of Foes.pdf",
    "wayfinder's guide to eberron": MANUAL_DIR / "D&D 5E - Wayfinders Guide to Eberron.pdf",
    "the tortle package": MANUAL_DIR / "D&D 5E - The Tortle Package.pdf",
    "ancestral weapons": MANUAL_DIR / "Ancestral_Weapons_Final_v1.2.pdf",
    "the wild sheep chase": MANUAL_DIR / "Campaigns" / "The_Wild_Sheep_Chase_V2.pdf",
    "tomb of the nine gods": MANUAL_DIR / "Campaigns" / "D&D 5E - Tomb of Annihilation.pdf",
    "dungeon of the mad mage": MANUAL_DIR / "Campaigns" / "D&D 5E - Waterdeep - Dungeon of the Mad Mage.pdf",
}

# Canonical book display names (what to write in source field)
BOOK_DISPLAY = {
    "dungeon master's tools": "Xanathar's Guide to Everything",
    "dragon season": "Waterdeep: Dragon Heist",
    "dragon's season": "Waterdeep: Dragon Heist",
}


def find_page_in_pdf(pdf_path: Path, entity_name: str, verbose: bool = False) -> int | None:
    """Search a PDF for an entity name, return the best page number (1-indexed).
    
    Returns None if not found.
    Picks the page where the name appears most times, with a bias toward
    pages that look like stat blocks or item descriptions.
    """
    if not pdf_path.exists():
        if verbose:
            print(f"    PDF not found: {pdf_path}")
        return None
    
    doc = fitz.open(str(pdf_path))
    name_lower = entity_name.lower().strip()
    
    # Strip common prefixes/suffixes for broader matching
    search_terms = [name_lower]
    # Also try without leading "+1 ", "+2 ", "+3 "
    for n in range(1, 4):
        if name_lower.startswith(f"+{n} "):
            search_terms.append(name_lower[len(f"+{n} "):])
    # Try without parenthetical (e.g., "arrows of dragon slaying" from "arrows of dragon slaying (3)")
    if " (" in name_lower:
        search_terms.append(name_lower.split(" (")[0])
    
    # Normalize search terms: collapse whitespace, handle OCR artifacts
    def normalize_for_search(s: str) -> str:
        """Collapse whitespace and remove spacing artifacts for fuzzy matching."""
        s = re.sub(r'\s+', ' ', s).strip()
        return s
    
    search_terms = [normalize_for_search(t) for t in search_terms]
    
    page_scores = {}  # page_num → score
    page_contexts = {}  # page_num → context snippet
    
    for pn in range(doc.page_count):
        raw_text = doc[pn].get_text().lower()
        # Normalize PDF text: collapse whitespace
        text = normalize_for_search(raw_text)
        score = 0
        
        for term in search_terms:
            count = text.count(term)
            if count > 0:
                score += count * 10
                # Bonus if the term appears near typical header patterns
                if re.search(r'\b' + re.escape(term) + r'\b', text):
                    score += 5  # Whole-word match bonus
            else:
                # Fallback: word-level matching for multi-word names
                words = term.split()
                if len(words) >= 3:
                    word_matches = sum(1 for w in words if len(w) > 2 and w in text)
                    if word_matches >= len(words) * 0.7:
                        score += word_matches * 3  # Partial match score
        
        if score > 0:
            page_scores[pn + 1] = score
            # Capture context
            idx = text.find(search_terms[0])
            ctx = text[max(0, idx-50):idx+100].replace('\n', ' ').strip()
            page_contexts[pn + 1] = ctx[:120]
    
    doc.close()
    
    if not page_scores:
        return None
    
    # Pick the highest-scoring page (usually the stat block / item entry)
    best_page = max(page_scores, key=page_scores.get)
    
    if verbose:
        ctx = page_contexts.get(best_page, "")
        print(f"    Found on page {best_page} (score={page_scores[best_page]}): ...{ctx}...")
    
    return best_page


def extract_book_from_source(source: str) -> str:
    """Extract the book name from a source string like 'Rise of Tiamat p.?'."""
    # Strip page reference
    book = re.sub(r'\s*p\.\?+$', '', source).strip()
    book = re.sub(r'\s*p\.1P$', '', book).strip()
    return book


def resolve_entry(name: str, source: str, verbose: bool = False) -> tuple[str, bool]:
    """Try to resolve a p.? source. Returns (new_source, resolved)."""
    book = extract_book_from_source(source)
    book_lower = book.lower().strip()
    
    # Skip unmappable books
    if book_lower in ("adventure", "winter wizardry", ""):
        return source, False
    
    # Look up PDF
    pdf_path = BOOK_PDF_MAP.get(book_lower)
    if not pdf_path:
        if verbose:
            print(f"  No PDF mapping for: {book}")
        return source, False
    
    # Find page
    page = find_page_in_pdf(pdf_path, name, verbose=verbose)
    if page is None:
        return source, False
    
    # Build new source
    display_book = BOOK_DISPLAY.get(book_lower, book)
    return f"{display_book} p.{page}", True


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    if dry_run:
        print("=== DRY RUN ===\n")
    
    json_files = sorted(MANUAL_DATA.glob("*.json"))
    json_files = [f for f in json_files if f.name != "_meta.json"]
    
    total_resolved = 0
    total_unresolved = 0
    
    for filepath in json_files:
        with open(filepath) as f:
            data = json.load(f)
        
        resolved = 0
        unresolved = 0
        
        for entry in data:
            src = entry.get("source", "")
            if "p.?" not in src and "p.1P" not in src and "p.??" not in src:
                continue
            
            name = entry.get("name", "?")
            new_src, was_resolved = resolve_entry(name, src, verbose=verbose)
            
            if was_resolved:
                if verbose:
                    print(f"  ✓ {name[:40]:40s} {src[:40]:40s} → {new_src}")
                entry["source"] = new_src
                resolved += 1
            else:
                if verbose:
                    print(f"  ✗ {name[:40]:40s} {src[:40]:40s} — could not resolve")
                unresolved += 1
        
        if resolved > 0 or unresolved > 0:
            print(f"\n── {filepath.name} ({resolved} resolved, {unresolved} unresolved)")
        
        total_resolved += resolved
        total_unresolved += unresolved
        
        if not dry_run and resolved > 0:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
    
    print(f"\n{'='*50}")
    print(f"Total resolved: {total_resolved}")
    print(f"Total unresolved: {total_unresolved}")
    
    if dry_run:
        print("\n⚠ DRY RUN — no files modified.")
    else:
        print("\n✓ Files updated.")


if __name__ == "__main__":
    main()
