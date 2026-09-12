#!/usr/bin/env python3
"""Append ONE manual extraction into data/manual_data/*.json (safe path).

Why this exists: `ingest_manual.py --merge` REBUILDS every merged file from
`data/manual_cache/*_extracted.json` alone. When older extractions have been
pruned from the cache (normal after a while), a rebuild silently deletes every
book whose extraction is gone — monsters 1540 → 118, spells 324 → 20, etc.
That is the "merge footgun" guarded against in `merge_all_extractions()`.

This script instead folds a single extraction INTO the existing merged files,
using the same dedup rules as the merge, and touches `_meta.json` keys
pdf_map / source_manuals / totals. Categories the extraction does not provide
are left untouched.

Usage:
  python3 scripts/append_extraction.py FGFD --dry-run
  python3 scripts/append_extraction.py FGFD
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CACHE_DIR = HERE / "data" / "manual_cache"
OUTPUT_DIR = HERE / "data" / "manual_data"

CATEGORIES = [
    "races", "spells", "magic_items", "equipment", "monsters",
    "npcs", "feats", "backgrounds", "subclasses", "traps",
]

# Mirror of ingest_manual._normalize_merged_sources().book_title_map — the
# display name used for the "(Display, p.N)" source form.
DISPLAY_OVERRIDES = {
    "FGFD": "Field Guide to Floral Dragons",
}


def _load_json(path: Path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save_json(path: Path, data) -> None:
    # Match ingest_manual._save_json exactly (indent=2) — a different indent
    # re-formats the whole merged file and buries the real diff.
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize_name(name: str) -> str:
    """Mirror of ingest_manual._normalize_name (dedup key; keeps parentheticals)."""
    n = (name or "").lower().strip()
    if n.startswith("the "):
        n = n[4:]
    n = n.replace("-", " ").replace("\u2013", " ").replace("\u2014", " ")
    n = re.sub(r"\s+", " ", n).strip().rstrip(".,;:!?")
    if n.endswith("s") and not n.endswith("ss") and len(n) > 4:
        n = n[:-1]
    return n


def _core(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", (name or "").lower()).strip()


def _normalize_source(raw: str, display: str) -> str:
    """Force a raw extraction source into the app's "(Display, p.N)" form.

    Raw LLM output is inconsistent ("Floral Dragons p.14", "FLORAL DRAGONS
    p.82", "p.139", chapter names, even a hallucinated "Fizban's Treasury of
    Dragons p.97"), and the slug→display map is the only trustworthy book name.
    Mirrors ingest_manual._normalize_merged_sources: keep the page number,
    discard everything else.
    """
    m = re.search(r"(?:p\.?\s*|page\s+)(\d+)", str(raw or ""), re.IGNORECASE)
    if m:
        return f"({display}, p.{m.group(1)})"
    return f"({display})"


def fix_sources(slug: str, display: str, dry_run: bool = False) -> int:
    """Re-normalize `source` for every entry already tagged with this slug."""
    total = 0
    for cat in CATEGORIES:
        path = OUTPUT_DIR / f"{cat}.json"
        data = _load_json(path)
        if not isinstance(data, list):
            continue
        fixed = 0
        for item in data:
            if item.get("_source_manual") != slug:
                continue
            new = _normalize_source(item.get("source"), display)
            if new != item.get("source"):
                item["source"] = new
                fixed += 1
        if fixed:
            if not dry_run:
                _save_json(path, data)
            print(f"  {cat}.json: {fixed} source(s) normalized")
            total += fixed
    verb = "would be normalized" if dry_run else "normalized"
    print(f"{total} source string(s) {verb} to ({display}, p.N)")
    return total


def append_extraction(slug: str, dry_run: bool = False) -> int:
    ext_path = CACHE_DIR / f"{slug}_extracted.json"
    data = _load_json(ext_path)
    if not isinstance(data, dict) or not data.get("_completed"):
        print(f"ERROR: {ext_path.name} missing or not marked _completed")
        return 1

    display = DISPLAY_OVERRIDES.get(slug) or data.get("_book_title", slug)
    added_total = 0
    per_cat_added: dict[str, int] = {}

    for cat in CATEGORIES:
        new_items = data.get(cat) or []
        path = OUTPUT_DIR / f"{cat}.json"
        existing = _load_json(path)
        if existing is None:
            existing = []
        if not isinstance(existing, list):
            continue
        if not new_items:
            per_cat_added[cat] = 0
            continue

        seen = {_normalize_name(i.get("name", "")) for i in existing}
        seen_core = {_core(i.get("name", "")) for i in existing}

        added, skipped = 0, 0
        for item in new_items:
            name = item.get("name", "")
            nn = _normalize_name(name)
            nc = _core(name)
            if not nn:
                continue
            if nn in seen:
                # Same dedup rules as ingest_manual.merge_all_extractions()
                if "(" not in name:
                    if nc in seen_core:
                        skipped += 1
                        continue
                else:
                    skipped += 1
                    continue
            if "(" in name and nc in seen_core:
                skipped += 1
                continue
            item["_source_manual"] = slug
            item["source"] = _normalize_source(item.get("source"), display)
            existing.append(item)
            seen.add(nn)
            seen_core.add(nc)
            added += 1

        per_cat_added[cat] = added
        added_total += added
        print(f"  {cat}.json: +{added} (skipped {skipped} duplicate(s)) → {len(existing)} total")
        if added and not dry_run:
            _save_json(path, existing)

    if dry_run:
        print(f"\nDRY RUN — {added_total} entries would be added (nothing written)")
        return 0

    # ── _meta.json: pdf_map / source_manuals / totals ──────────────────────
    meta_path = OUTPUT_DIR / "_meta.json"
    meta = _load_json(meta_path) or {}
    if not isinstance(meta, dict):
        meta = {}

    added_by_cat = {c: n for c, n in per_cat_added.items() if n}
    totals = meta.get("totals")
    if isinstance(totals, dict):
        for cat, n in added_by_cat.items():
            totals[cat] = int(totals.get(cat, 0)) + n
    meta["totals"] = totals if isinstance(totals, dict) else added_by_cat

    src = meta.get("source_manuals")
    if not isinstance(src, list):
        src = []
    if slug not in src:
        src.append(slug)
    meta["source_manuals"] = src

    title = data.get("_book_title", slug)
    pdf_map = meta.get("pdf_map")
    if not isinstance(pdf_map, dict):
        pdf_map = {}
    # Path must be a BARE filename: _ensure_manual_cache() resolves
    # MANUALS_DIR / path, so a nested path would double the directory.
    pdf_map[slug] = {
        "title": title,
        "filename": title.replace(" ", "_") + ".pdf",
        "path": title.replace(" ", "_") + ".pdf",
    }
    meta["pdf_map"] = pdf_map
    meta["merged_at"] = time.time()
    _save_json(meta_path, meta)

    print(f"\n_meta.json updated: totals{added_by_cat}, source_manuals+{slug}, pdf_map[{slug}]")
    print(f"Appended {added_total} entr{'y' if added_total == 1 else 'ies'} from {slug}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if len(args) != 1:
        print(__doc__.strip())
        return 1
    slug = args[0]
    if "--fix-sources" in sys.argv:
        ext = _load_json(CACHE_DIR / f"{slug}_extracted.json") or {}
        display = DISPLAY_OVERRIDES.get(slug) or ext.get("_book_title", slug)
        fix_sources(slug, display, dry_run=dry_run)
        return 0
    return append_extraction(slug, dry_run=dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
