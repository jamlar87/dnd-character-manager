#!/usr/bin/env python3
"""Cache extracted text for every library book that has none.

The text cache (`data/manual_cache/<SLUG>.txt` with `--- PAGE N ---` markers) is the
evidence base: without it a page citation cannot be checked, which is how a wrong
"MPMM p.6" survived in three files until the text existed.

Run from the repo root:  .venv/bin/python3 scripts/cache_manual_text.py [--only SLUG ...]

Caches are named by the app's own pdf_map slug, so lookups elsewhere
(`grep -n "..." data/manual_cache/<SLUG>.txt`) use the same key the badge does.
Note the ingest engine has its own slugs for a few books (its DMPMOT is the app's
MPMM, its W/WDZ/WF are the app's W2/W4/W5); if it is ever run, it will re-extract
under its own name. That duplicate is harmless.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import ingest_manual as ing  # noqa: E402

CACHE = HERE / "data" / "manual_cache"
MANUALS = HERE / "manuals"


def main() -> int:
    only = []
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1:]

    meta = json.loads((HERE / "data" / "manual_data" / "_meta.json").read_text())
    pdf_map = meta.get("pdf_map", {})

    todo = []
    for slug, entry in sorted(pdf_map.items()):
        if only and slug not in only:
            continue
        if (CACHE / f"{slug}.txt").exists():
            continue
        pdf = MANUALS / str(entry.get("path") or "")
        todo.append((slug, pdf, entry.get("title") or ""))

    if not todo:
        print("Nothing to do — every library book has cached text.")
        return 0

    print(f"{len(todo)} book(s) without cached text:")
    ok = failed = 0
    for slug, pdf, title in todo:
        if not pdf.exists():
            print(f"  {slug:8} MISSING PDF: {pdf}")
            failed += 1
            continue
        print(f"  {slug:8} {title[:52]:54} {pdf.stat().st_size / 1e6:6.1f} MB ...", flush=True)
        t0 = time.time()
        try:
            text = ing.extract_text({"slug": slug, "abs_path": str(pdf), "name": title})
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  {slug:8} ERROR: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        if not text:
            print(f"  {slug:8} FAILED (no text extracted — may need OCR)")
            failed += 1
            continue
        pages = text.count("--- PAGE ")
        print(f"  {slug:8} ok: {len(text):,} chars, {pages} page markers, {time.time() - t0:.0f}s")
        ok += 1

    print(f"\nDone: {ok} cached, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
