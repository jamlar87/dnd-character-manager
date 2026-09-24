#!/usr/bin/env python3
"""Find text caches that are truncated or stale.

`extract_text` returns an existing cache whenever it has `--- PAGE ---` markers, whatever the
PDF's mtime and whatever a caller asks for — so a cache that was written incomplete (an old tool,
an interrupted run, a partially readable PDF) stays incomplete forever, and every page citation
past its end is silently unverifiable. WLL's cache held 19,716 chars / 7 pages for a 180-page,
653,200-character book.

This compares each cache against a direct pymupdf read of the PDF and reports the ratio. A cache
holding far less than the PDF offers is stale; a cache holding *more* is fine (OCR).

--fix moves a stale cache aside (never deletes it: renamed with a .stale suffix) so the next
`cache_manual_text.py --only <SLUG>` genuinely re-extracts it.

Run: .venv/bin/python3 scripts/verify_text_caches.py [--fix] [--min-ratio 0.6]
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
MERGED = HERE / "data" / "manual_data"
CACHE = HERE / "data" / "manual_cache"
MANUALS = HERE / "manuals"


def direct_chars(pdf: pathlib.Path) -> int:
    try:
        import fitz
        doc = fitz.open(str(pdf))
        total = sum(len(page.get_text("text")) for page in doc)
        doc.close()
        return total
    except Exception:
        return 0


def main() -> int:
    fix = "--fix" in sys.argv
    min_ratio = 0.6
    if "--min-ratio" in sys.argv:
        min_ratio = float(sys.argv[sys.argv.index("--min-ratio") + 1])

    pdf_map = json.loads((MERGED / "_meta.json").read_text()).get("pdf_map", {})
    stale, ok, unknown = [], 0, 0
    for slug, entry in sorted(pdf_map.items()):
        rel = (entry or {}).get("path") or ""
        pdf = MANUALS / str(rel)
        cache = CACHE / f"{slug}.txt"
        if not pdf.exists() or not cache.exists():
            unknown += 1
            continue
        cached = cache.stat().st_size
        real = direct_chars(pdf)
        if real < 1000:            # nothing readable to compare against (true scan)
            unknown += 1
            continue
        ratio = cached / real
        if ratio < min_ratio:
            stale.append((slug, cached, real, ratio, cache))
        else:
            ok += 1

    print(f"caches that look complete: {ok}")
    print(f"too little text to judge (scans, missing files): {unknown}")
    print(f"stale/truncated caches: {len(stale)}\n")
    for slug, cached, real, ratio, _ in sorted(stale, key=lambda r: r[3]):
        print(f"  {slug:8} cache {cached:9,} vs pdf {real:9,} chars  ({ratio:5.1%})")

    if fix:
        for slug, _c, _r, _ra, cache in stale:
            aside = cache.with_suffix(".txt.stale")
            cache.rename(aside)
            print(f"  moved aside: {cache.name} -> {aside.name}")
        print(f"\n{len(stale)} cache(s) moved aside — re-extract with "
              f"scripts/cache_manual_text.py --only <SLUG> ...")
    elif stale:
        print("\nDRY RUN — run with --fix to move the stale ones aside for re-extraction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
