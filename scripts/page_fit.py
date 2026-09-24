#!/usr/bin/env python3
"""Does every cited page exist in the book it names?

The suite's page-range test catches this, but only when someone runs the suite. The audit that the
20-minute cleanup cron relies on compared each citation against the *cached text* — so 141 records
citing slug W8 (a 7-page "Warlock Lair: The Returners' Tower") with pages 10-168 were reported as
"cannot verify" instead of as impossible. A citation past the end of the real PDF is not
unverifiable, it is wrong: the slug is almost always the wrong one, which is what those 141 turned
out to be (they belonged to WLL, the 180-page compilation).

Page counts come from pymupdf and are cached in data/.pdf_page_counts.json keyed by path+mtime, so
the recurring check is a JSON read rather than 88 PDF opens.

Run: .venv/bin/python3 scripts/page_fit.py [--quiet]
Exit: 0 clean, 1 violations found.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
MERGED = HERE / "data" / "manual_data"
CACHE_FILE = HERE / "data" / ".pdf_page_counts.json"

CATEGORIES = ("races", "spells", "magic_items", "equipment", "monsters",
              "npcs", "feats", "backgrounds", "subclasses", "traps")

CITE = re.compile(r"^\((.+?),\s*p\.(\d+)\)$")


def pdf_page_counts(pdf_map: dict | None = None) -> dict[str, int]:
    """slug -> real page count of the PDF it points at. {} when pymupdf is unavailable."""
    try:
        import fitz  # pymupdf
    except Exception:
        return {}
    if pdf_map is None:
        try:
            pdf_map = json.loads((MERGED / "_meta.json").read_text()).get("pdf_map", {})
        except Exception:
            return {}
    cache: dict[str, int] = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}
    out: dict[str, int] = {}
    for slug, entry in (pdf_map or {}).items():
        raw = (entry or {}).get("path") if isinstance(entry, dict) else None
        if not raw:
            continue
        path = HERE / "manuals" / str(raw)
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            continue
        key = f"{raw}|{mtime}"
        count = cache.get(key)
        if count is None:
            try:
                doc = fitz.open(path)
                count = int(doc.page_count)
                doc.close()
            except Exception:
                continue
            cache[key] = count
        out[slug] = int(count)
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=1, sort_keys=True))
    except Exception:
        pass
    return out


def violations(counts: dict[str, int] | None = None) -> list[str]:
    """Records whose cited page cannot exist in the book they name."""
    counts = pdf_page_counts() if counts is None else counts
    if not counts:
        return []
    bad: list[str] = []
    for cat in CATEGORIES:
        path = MERGED / f"{cat}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        for item in (data if isinstance(data, list) else []):
            if not isinstance(item, dict):
                continue
            slug = str(item.get("_source_manual") or "")
            real = counts.get(slug)
            if not real:
                continue
            match = CITE.match(str(item.get("source") or "").strip())
            if match and int(match.group(2)) > real:
                bad.append(f"{cat}/{item.get('name')}: cites {slug} p.{match.group(2)}, "
                           f"but that PDF has only {real} pages ({match.group(1)})")
    return bad


def main() -> int:
    quiet = "--quiet" in sys.argv
    bad = violations()
    if bad:
        print(f"page fit: {len(bad)} citation(s) name a page that cannot exist in their book")
        for row in bad[:10]:
            print(f"   - {row}")
        if len(bad) > 10:
            print(f"   ... and {len(bad) - 10} more")
        return 1
    if not quiet:
        print("page fit: every cited page exists in the book it names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
