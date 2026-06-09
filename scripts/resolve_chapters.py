#!/usr/bin/env python3
"""Brute-force chapter-to-book resolver — indexed edition.

Pre-loads all PDF text into memory, then resolves entries by:
1. Chapter pattern → book mapping (fast, deterministic)
2. Entity name lookup in pre-loaded index (fast, in-memory)

Run: python3 scripts/resolve_chapters.py [--dry-run] [--verbose]
"""

import json
import re
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    print("ERROR: pymupdf not installed. Run: pip install pymupdf")
    sys.exit(1)

HERE = Path(__file__).resolve().parent.parent
MANUAL_DATA = HERE / "data" / "manual_data"
MANUAL_DIR = Path("/media/james/SlowDisk1tb/dnd-character-manager/manuals/DnD-Manuals")

# ── Chapter pattern → (book display name, PDF subpath) ──────────────────
CHAPTER_BOOK_MAP = [
    (r"Chapter 7[:\s/]+Treasure", "Dungeon Master's Guide",
     "D&D 5E - Dungeon Master's Guide.pdf"),
    (r"Chapter 2[:\s]+Dungeon Master'?s Tools", "Xanathar's Guide to Everything",
     "D&D 5E - Xanathar's Guide to Everything.pdf"),
    (r"Chapter 4[:\s]+Classes", "Player's Handbook",
     "D&D 5E - Player's Handbook.pdf"),
    (r"Chapter 1[:\s]+Races?\b", "Player's Handbook",
     "D&D 5E - Player's Handbook.pdf"),
    (r"Chapter 6[:\s]+Friends and Foes", "Guildmasters' Guide to Ravnica",
     "D&D 5E - Guildmasters' Guide to Ravnica.pdf"),
    (r"Chapter 4[:\s-]+Dragon Season", "Waterdeep: Dragon Heist",
     "Campaigns/D&D 5E - Waterdeep - Dragon Heist.pdf"),
    (r"Chapter 2[:\s]+The Land of Chult", "Tomb of Annihilation",
     "Campaigns/D&D 5E - Tomb of Annihilation.pdf"),
    (r"Chapter 2[:\s]+Character Races", "Volo's Guide to Monsters",
     "D&D 5E - Volo's Guide to Monsters.pdf"),
    (r"Chapter 6[:\s]+Bestiary", None, None),  # Ambiguous
    (r"Chapter [235]\b", None, None),  # Ambiguous
]

# PDFs for brute-force search (in priority order)
SEARCH_PDFS = [
    ("Dungeon Master's Guide", "D&D 5E - Dungeon Master's Guide.pdf"),
    ("Monster Manual", "D&D 5E - Monster Manual.pdf"),
    ("Xanathar's Guide to Everything", "D&D 5E - Xanathar's Guide to Everything.pdf"),
    ("Volo's Guide to Monsters", "D&D 5E - Volo's Guide to Monsters.pdf"),
    ("Mordenkainen's Tome of Foes", "D&D 5E - Mordenkainen's Tome of Foes.pdf"),
    ("Guildmasters' Guide to Ravnica", "D&D 5E - Guildmasters' Guide to Ravnica.pdf"),
    ("Sword Coast Adventurer's Guide", "D&D 5E - Sword Coast Adventurer's Guide.pdf"),
    ("Tomb of Annihilation", "Campaigns/D&D 5E - Tomb of Annihilation.pdf"),
    ("Waterdeep: Dragon Heist", "Campaigns/D&D 5E - Waterdeep - Dragon Heist.pdf"),
    ("Hoard of the Dragon Queen", "Campaigns/D&D 5E - Tyranny of Dragons - Hoard of the Dragon Queen.pdf"),
    ("Rise of Tiamat", "Campaigns/D&D 5E - Tyranny of Dragons - The Rise of Tiamat.pdf"),
    ("Lost Mine of Phandelver", "Campaigns/D&D 5E - Lost Mine of Phandelver.pdf"),
]


def norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip().lower()


def build_index(verbose=False):
    """Pre-load all PDF text into memory. Returns list of (book_name, pages_list)."""
    index = []
    for book_name, pdf_subpath in SEARCH_PDFS:
        pdf_path = MANUAL_DIR / pdf_subpath
        if not pdf_path.exists():
            continue
        if verbose:
            print(f"  Indexing {book_name}...")
        doc = fitz.open(str(pdf_path))
        pages = []
        for pn in range(doc.page_count):
            pages.append(norm(doc[pn].get_text()))
        doc.close()
        index.append((book_name, pages))
    return index


def find_entity_in_index(entity_name: str, index: list) -> tuple[str | None, int | None]:
    """Search pre-loaded index for entity name."""
    name_lower = norm(entity_name)
    
    # Build search terms
    terms = [name_lower]
    if " (" in name_lower:
        terms.append(norm(name_lower.split(" (")[0]))
    
    best_book = None
    best_page = None
    best_score = 0
    
    for book_name, pages in index:
        for pn, text in enumerate(pages):
            score = 0
            for term in terms:
                count = text.count(term)
                if count > 0:
                    score += count * 10
                    if re.search(r'\b' + re.escape(term) + r'\b', text):
                        score += 5
                elif len(term.split()) >= 3:
                    words = term.split()
                    matches = sum(1 for w in words if len(w) > 2 and w in text)
                    if matches >= len(words) * 0.6:
                        score += matches * 2
            
            if score > best_score:
                best_score = score
                best_book = book_name
                best_page = pn + 1
    
    if best_score >= 10:
        return best_book, best_page
    return None, None


def resolve_by_chapter(source: str):
    """Try chapter pattern matching. Returns (book, pdf_subpath) or (None, None)."""
    for pattern, display_name, pdf_subpath in CHAPTER_BOOK_MAP:
        if re.search(pattern, source, re.IGNORECASE):
            return display_name, pdf_subpath
    return None, None


def find_page_in_pdf(entity_name: str, pdf_subpath: str) -> int | None:
    """Quick page lookup in a specific PDF."""
    pdf_path = MANUAL_DIR / pdf_subpath
    if not pdf_path.exists():
        return None
    
    name_lower = norm(entity_name)
    doc = fitz.open(str(pdf_path))
    
    for pn in range(doc.page_count):
        text = norm(doc[pn].get_text())
        if name_lower in text:
            doc.close()
            return pn + 1
    
    doc.close()
    return None


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    if dry_run:
        print("=== DRY RUN ===\n")
    
    # Pre-load PDF index
    print("Building PDF text index...")
    pdf_index = build_index(verbose=verbose)
    print(f"  Indexed {len(pdf_index)} PDFs\n")
    
    json_files = sorted(MANUAL_DATA.glob("*.json"))
    json_files = [f for f in json_files if f.name != "_meta.json"]
    
    sys.path.insert(0, str(HERE))
    from scripts.normalize_sources import normalize_source
    
    total_resolved = 0
    chapter_hits = 0
    search_hits = 0
    
    for filepath in json_files:
        with open(filepath) as f:
            data = json.load(f)
        
        resolved = 0
        unresolved = 0
        
        for entry in data:
            src = entry.get("source", "")
            _, flags = normalize_source(src)
            
            if "no_book_name" not in flags and "corrupted" not in flags:
                continue
            
            name = entry.get("name", "?")
            
            # Step 1: Chapter pattern
            book, pdf_subpath = resolve_by_chapter(src)
            
            if book and pdf_subpath:
                # Known chapter → known book — just find the page
                page = find_page_in_pdf(name, pdf_subpath)
                new_src = f"{book} p.{page}" if page else book
                entry["source"] = new_src
                resolved += 1
                chapter_hits += 1
                if verbose:
                    print(f"  ✓ [chapter] {name[:35]:35s} → {new_src}")
                continue
            
            # Step 2: Brute-force index search
            found_book, found_page = find_entity_in_index(name, pdf_index)
            if found_book:
                new_src = f"{found_book} p.{found_page}"
                entry["source"] = new_src
                resolved += 1
                search_hits += 1
                if verbose:
                    print(f"  ✓ [search]  {name[:35]:35s} → {new_src}")
            else:
                unresolved += 1
                if verbose:
                    print(f"  ✗ {name[:35]:35s} | {src[:50]}")
        
        if resolved > 0 or unresolved > 0:
            print(f"── {filepath.name} ({resolved} resolved, {unresolved} unresolved)")
        
        total_resolved += resolved
        
        if not dry_run and resolved > 0:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
    
    print(f"\n{'='*50}")
    print(f"Resolved: {total_resolved} ({chapter_hits} by chapter, {search_hits} by search)")
    
    if dry_run:
        print("\n⚠ DRY RUN — no files modified.")
    else:
        print("\n✓ Files updated.")


if __name__ == "__main__":
    main()
