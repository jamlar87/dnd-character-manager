#!/usr/bin/env python3
"""Re-point pdf_map entries at the PDFs that actually exist.

The ingest engine writes a pdf_map entry per book using the filename it discovered, and for
several existing books that name does not exist in `manuals/` — the core rulebooks ended up
pointing at "D&D_5E__Player's_Handbook.pdf" while the file on disk is
"D&D 5E - Player's Handbook.pdf". The app then answers `PDF not found` and the source badge
fails, so the book is unopenable even though it is on disk and every citation resolves.

Matching is on normalised alphanumerics, so punctuation and spacing differences do not matter.
Nothing is moved: only the path/filename fields are corrected, and a slug whose file cannot be
found is reported rather than guessed at.

Dry run by default; --apply to write.

Run: .venv/bin/python3 scripts/repair_manual_paths.py [--apply]
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
META = HERE / "data" / "manual_data" / "_meta.json"
MANUALS = HERE / "manuals"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


# Verified slug -> file mappings, by hand. The ingest engine writes pdf_map from its own view of
# the library, and a manual fold re-serialises that view into _meta.json: it maps WLL onto W8's
# 7-page file and renames the title to match, so filename-matching cannot recover it. A path that
# merely *exists* is not evidence it is the right book — hence the pins, re-asserted every run.
PINS = HERE / "data" / "manual_data" / "_pdf_pins.json"
CACHE = HERE / "data" / "manual_cache"


def main() -> int:
    apply = "--apply" in sys.argv
    pins = json.loads(PINS.read_text()) if PINS.exists() else {}
    meta = json.loads(META.read_text())
    pdf_map = meta.get("pdf_map", {})

    index: dict[str, pathlib.Path] = {}
    # followlinks: the real PDFs live in the tree behind manuals/DnD-Manuals, and a plain
    # rglob does not descend into a symlinked directory — which is why EOM looked unfindable
    # while its file sat one symlink away.
    for base, _dirs, files in os.walk(MANUALS, followlinks=True):
        for fn in files:
            if not fn.lower().endswith(".pdf"):
                continue
            p = pathlib.Path(base) / fn
            index.setdefault(norm(p.stem), p)
            index.setdefault(norm(p.name), p)

    fixed, missing, pinned = [], [], []
    for slug, entry in sorted(pdf_map.items()):
        if not isinstance(entry, dict):
            continue
        pin = pins.get(slug)
        if pin:
            # The pin is a fact checked against the book itself, so it wins over the engine's view.
            if any(entry.get(k) != v for k, v in pin.items()):
                pinned.append((slug, entry.get("filename"), pin.get("filename")))
                entry.update(pin)
            continue
        cur = entry.get("path") or entry.get("filename") or ""
        if cur and (MANUALS / cur).exists():
            continue
        # Try the stored name, then the display title.
        cand = index.get(norm(pathlib.Path(cur).stem)) or index.get(norm(entry.get("title") or ""))
        if not cand:
            missing.append((slug, cur))
            continue
        rel = cand.relative_to(MANUALS)
        fixed.append((slug, cur, str(rel)))
        entry["filename"] = cand.name
        entry["path"] = str(rel)

    # A file that exists can still be the wrong book. Compare its real page count against the
    # highest page marker in the slug's own cached text — WLL's cache carries markers to p.177
    # while the file it pointed at had 7 pages. Report-only: folding is what usually causes this.
    mismatches = []
    try:
        import fitz
    except ImportError:
        fitz = None
    if fitz:
        for slug, entry in sorted(pdf_map.items()):
            if not isinstance(entry, dict):
                continue
            cf = CACHE / f"{slug}.txt"
            pf = MANUALS / (entry.get("path") or entry.get("filename") or "")
            if not cf.exists() or not pf.exists():
                continue
            marks = [int(m.group(1)) for m in
                     re.finditer(r"---\s*PAGE\s+(\d+)", cf.read_text(errors="replace"))]
            if not marks:
                continue
            try:
                real = fitz.open(pf).page_count
            except Exception:
                continue
            if max(marks) > real + 2:
                mismatches.append(f"{slug}: cache cites p.{max(marks)} but "
                                  f"{entry.get('filename')} has {real} pages")

    print(f"{'APPLIED' if apply else 'DRY RUN'} — pdf_map entries re-pointed: {len(fixed)}")
    if pinned:
        print(f"pinned (a fold had re-pointed them at the wrong file): {len(pinned)}")
        for slug, old, new in pinned:
            print(f"   - {slug}: {old} -> {new}")
    if mismatches:
        print(f"\nSUSPECT — file exists but may be the wrong book: {len(mismatches)}")
        for row in mismatches:
            print(f"   - {row}")
    for slug, old, new in fixed:
        print(f"   {slug:8} {old[:44]:46} -> {new}")
    if missing:
        print(f"\ncould not locate a file for {len(missing)} book(s):")
        for slug, cur in missing:
            print(f"   {slug:8} {cur[:60]}")

    if apply and (fixed or pinned):
        meta["pdf_map"] = pdf_map
        META.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        what = f"{len(fixed)} re-pointed" + (f", {len(pinned)} pinned" if pinned else "")
        print(f"\nwrote {META} ({what})")
    elif apply:
        print("\nnothing to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
