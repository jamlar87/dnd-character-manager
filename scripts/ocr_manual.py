#!/usr/bin/env python3
"""OCR-clean a D&D manual PDF with custom font encoding issues.

Usage:
  python3 scripts/ocr_manual.py "Wayfinders Guide to Eberron"
  python3 scripts/ocr_manual.py --all

Processes all manuals whose cached text shows PUA garbage (Unicode
Private Use Area chars). Renders pages to images, splits two-column
layout, OCRs with tesseract, and writes cleaned text to manual_cache.

Uses pymupdf for rendering and pytesseract for OCR.
Output filename matches the slug used by ingest_manual.py.
"""

import os, sys, re, time
import fitz
import pytesseract
from PIL import Image

# Project paths
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUALS = os.path.join(PROJECT, "manuals", "DnD-Manuals")
CACHE = os.path.join(PROJECT, "data", "manual_cache")

# Slug mapping from ingest_manual.py (line ~570)
SLUG_MAP = {
    "tortle package": "TTP",
    "wayfinders guide": "WGE",
    "sword coast adventurers guide": "SCAG",
    "players handbook": "PHB",
    "monster manual": "MM",
    "dungeon masters guide": "DMG",
    "xanathars guide": "XGE",
    "tashas cauldron": "TCE",
    "volos guide": "VGM",
    "mordenkainens tome": "MTF",
    "mordenkainens monsters": "MMM",
    "fizbans treasury": "FTD",
    "spelljammer": "SAW",
    "bigby presents": "BGG",
    "van richtens": "VRG",
    "candlekeep mysteries": "CM",
    "mythic odysseys": "MOT",
    "guildmasters guide": "GGR",
    "elemental evil": "EE",
    "princes of the apocalypse": "EE",  # same key
    "out of the abyss": "OOA",
    "curse of strahd": "COS",
    "storm kings thunder": "SKT",
    "tomb of annihilation": "TOA",
    "waterdeep dragon heist": "WDH",
    "waterdeep dungeon of the mad mage": "WDMM",
    "dungeon of the mad mage": "WDMM",
    "ghosts of saltmarsh": "GOS",
    "baldurs gate": "BG",
    "icewind dale": "ID",
    "critical role": "CR",
    "explorers guide": "EGW",
    "tal'dorei": "TD",
    "homebrew": "HB",
}

DPI = 200


def find_slug(name: str) -> str:
    """Find cache slug for a manual name."""
    lower = name.lower()
    for key, slug in SLUG_MAP.items():
        if key in lower:
            return slug
    # Fallback: first 3 uppercase chars of short name
    short = re.sub(r'[^a-zA-Z0-9]', '', name)
    return short[:8].upper()


def find_pdf(name: str) -> str:
    """Find PDF file by partial name match."""
    for f in os.listdir(MANUALS):
        if name.lower() in f.lower() and f.endswith('.pdf'):
            return os.path.join(MANUALS, f)
    # Broader search: try removing "D&D 5E - " prefix
    for f in os.listdir(MANUALS):
        fname = re.sub(r'^D&D\s*5E\s*[-–—]\s*', '', f, flags=re.I)
        if name.lower() in fname.lower() and f.endswith('.pdf'):
            return os.path.join(MANUALS, f)
    return None


def ocr_manual(name: str):
    """Run OCR pipeline on a single manual."""
    pdf_path = find_pdf(name)
    if not pdf_path:
        print(f"ERROR: No PDF found matching '{name}'")
        return False

    slug = find_slug(name)
    output_path = os.path.join(CACHE, f"{slug}.txt")

    print(f"Processing: {os.path.basename(pdf_path)}")
    print(f"  Output: {output_path}")
    print(f"  Slug: {slug}")
    print()

    doc = fitz.open(pdf_path)
    total = len(doc)
    print(f"  Pages: {total}")
    print()

    all_text = []
    start = time.time()

    for i in range(total):
        page = doc[i]
        mat = fitz.Matrix(DPI / 72, DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        w = pix.width
        mid = w // 2

        # Split into left and right columns (D&D two-column layout)
        left_half = img.crop((0, 0, mid - 10, pix.height))
        right_half = img.crop((mid + 10, 0, w, pix.height))

        left_text = pytesseract.image_to_string(left_half, lang='eng')
        right_text = pytesseract.image_to_string(right_half, lang='eng')

        all_text.append(f"--- PAGE {i + 1} ---")
        all_text.append(left_text.strip())
        if right_text.strip():
            all_text.append(right_text.strip())

        elapsed = time.time() - start
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        sys.stdout.write(f"\r  [{i + 1}/{total}] {rate:.1f} pg/s, {elapsed:.0f}s elapsed")
        sys.stdout.flush()

    doc.close()

    result = "\n\n".join(all_text)
    os.makedirs(CACHE, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result)

    elapsed = time.time() - start
    print(f"\n\n  Done: {total} pages in {elapsed:.0f}s ({total/elapsed:.1f} pg/s)")
    print(f"  Output: {output_path} ({len(result):,} chars)")

    # Verify
    bad = sum(1 for c in result if 0xE000 <= ord(c) <= 0xF8FF)
    print(f"  PUA garbage: {bad} chars")
    if bad > 0:
        print("  ⚠ Some PUA remaining (may need manual fix)")
    else:
        print("  ✓ Clean!")

    return True


def check_all():
    """Check all cache files for PUA garbage and OCR-clean as needed."""
    dirty = []
    for fname in os.listdir(CACHE):
        if not fname.endswith('.txt'):
            continue
        path = os.path.join(CACHE, fname)
        text = open(path, 'r', encoding='utf-8', errors='replace').read()
        bad = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF)
        if bad > 0:
            rate = bad / (len(text) / 1000) if len(text) > 0 else 0
            dirty.append((fname, bad, rate, len(text)))

    if not dirty:
        print("All cache files clean. No OCR needed.")
        return

    print(f"\nFound {len(dirty)} dirty cache file(s):\n")
    for fname, bad, rate, size in sorted(dirty, key=lambda x: -x[1]):
        print(f"  {fname}: {bad} PUA chars ({rate:.1f}/KB), {size} bytes")

    for fname, bad, rate, size in sorted(dirty, key=lambda x: -x[1]):
        print(f"\n{'='*60}")
        print(f"Processing: {fname}")
        print(f"{'='*60}")
        name = re.sub(r'\.txt$', '', fname)
        ocr_manual(name)


if __name__ == '__main__':
    if '--all' in sys.argv:
        check_all()
    elif len(sys.argv) > 1:
        ocr_manual(' '.join(sys.argv[1:]))
    else:
        print("Usage: python3 scripts/ocr_manual.py <manual-name>  OR  --all")
        print()
        print("Examples:")
        print("  python3 scripts/ocr_manual.py \"Wayfinders Guide\"")
        print("  python3 scripts/ocr_manual.py --all")
