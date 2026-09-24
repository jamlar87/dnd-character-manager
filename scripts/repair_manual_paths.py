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
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def main() -> int:
    apply = "--apply" in sys.argv
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

    fixed, missing = [], []
    for slug, entry in sorted(pdf_map.items()):
        if not isinstance(entry, dict):
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

    print(f"{'APPLIED' if apply else 'DRY RUN'} — pdf_map entries re-pointed: {len(fixed)}")
    for slug, old, new in fixed:
        print(f"   {slug:8} {old[:44]:46} -> {new}")
    if missing:
        print(f"\ncould not locate a file for {len(missing)} book(s):")
        for slug, cur in missing:
            print(f"   {slug:8} {cur[:60]}")

    if apply and fixed:
        meta["pdf_map"] = pdf_map
        META.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        print(f"\nwrote {META}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
