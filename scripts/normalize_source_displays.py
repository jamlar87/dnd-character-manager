#!/usr/bin/env python3
"""Normalise every record's `source` display to the name the app expects.

The loader validates each record against the display in `_slug_displays` (see
`main._get_source_slug_map`) and warns when they differ:

    ⚠ feats.json: Devil's Sight — display 'D&D 5E  Player's Handbook' ≠ expected 'Player's Handbook'

Ingestion writes the engine's own title ("D&D 5E  <Title>", with its double space), so ingested
records fail that check. This rewrites them via append_extraction.fix_sources, which keeps the
page number and rebuilds the string as "(<display>, p.N)".

Dry run by default; pass --apply to write.

Run: .venv/bin/python3 scripts/normalize_source_displays.py [--apply] [--slug MPMM ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

import main as app_main  # noqa: E402
from append_extraction import fix_sources  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    only = []
    if "--slug" in sys.argv:
        only = [a for a in sys.argv[sys.argv.index("--slug") + 1:] if not a.startswith("--")]

    slug_map = app_main._get_source_slug_map()
    print(f"{'APPLYING' if apply else 'DRY RUN'} — records whose display differs from the "
          f"app's expected name:\n")
    total = 0
    for slug in sorted(slug_map):
        if only and slug not in only:
            continue
        display = slug_map[slug].get("display") or slug
        n = fix_sources(slug, display, dry_run=not apply)
        if n:
            print(f"  {slug:8} {n:5} record(s) -> {display!r}")
            total += n
    print(f"\n{total} record(s) {'normalised' if apply else 'would be normalised'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
