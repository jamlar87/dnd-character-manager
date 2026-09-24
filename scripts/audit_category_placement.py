#!/usr/bin/env python3
"""Is every record in the right core file?

The ten core files are the app's category stores: races.json, spells.json, magic_items.json,
equipment.json, monsters.json, npcs.json, feats.json, backgrounds.json, subclasses.json,
traps.json. A record that lands in the wrong one is invisible where it belongs and wrong where
it is.

Rather than hard-code a schema per category (which would go stale), this derives each file's
signature from the data: the fields that appear in most of its records. Each record is then
scored against every file's signature and placed where it fits — a record whose best fit is a
different file is reported, with the margin, so near-misses (spells and magic items share
plenty of fields) can be judged rather than blindly "fixed".

Run: .venv/bin/python3 scripts/audit_category_placement.py [--min-margin 0.25] [--show 15]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

FILES = ("races", "spells", "magic_items", "equipment", "monsters",
         "npcs", "feats", "backgrounds", "subclasses", "traps")


def load(cat: str) -> list[dict]:
    path = HERE / "data" / "manual_data" / f"{cat}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    items = data if isinstance(data, list) else (data.get(cat) or [])
    return [x for x in items if isinstance(x, dict)]


def signature(records: list[dict], floor: float = 0.5) -> set[str]:
    """Fields present in at least `floor` of the records — the file's own fingerprint."""
    if not records:
        return set()
    counts: Counter = Counter()
    for r in records:
        counts.update(r.keys())
    return {k for k, n in counts.items() if n / len(records) >= floor}


def fit(record: dict, sig: set[str]) -> float:
    """How well does this record look like that file? (Jaccard over the signature)"""
    if not sig:
        return 0.0
    have = set(record.keys())
    return len(have & sig) / len(sig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-margin", type=float, default=0.25,
                    help="only report when the better-fitting file wins by this much")
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    store = {c: load(c) for c in FILES}
    sigs = {c: signature(recs) for c, recs in store.items()}

    print("file                 records   signature fields")
    for c in FILES:
        sig = sorted(sigs[c] - {"name", "description", "source", "_source_manual"})
        print(f"  {c:14} {len(store[c]):6}   {', '.join(sig[:7]) or '(too small to fingerprint)'}")

    print()
    misplaced = []
    unfingerprinted = []
    for cat, recs in store.items():
        own = sigs[cat]
        if not own:
            unfingerprinted.append(cat)
            continue
        for r in recs:
            mine = fit(r, own)
            best_cat, best = cat, mine
            for other in FILES:
                if other == cat:
                    continue
                score = fit(r, sigs[other])
                if score > best:
                    best_cat, best = other, score
            if best_cat != cat and (best - mine) >= args.min_margin:
                misplaced.append((cat, best_cat, round(mine, 2), round(best, 2),
                                  str(r.get("name") or "?")))

    print(f"records that fit a different core file better: {len(misplaced)}")
    for cat, best_cat, mine, best, name in misplaced[:args.show]:
        print(f"   - {cat}/{name}: looks like {best_cat} (fit {mine} vs {best})")
    if len(misplaced) > args.show:
        print(f"   ... and {len(misplaced) - args.show} more")

    # Cross-check against what the app's loader actually publishes: if a category's count in
    # the loader differs wildly from the file, records are going somewhere unexpected.
    from services.data_loader import load_manual_data
    load_manual_data()
    import data as app_data
    print()
    print("what the app actually loads (file count -> live collection):")
    live = {}
    for cat in FILES:
        coll = getattr(app_data, cat.upper(), None)
        if isinstance(coll, (dict, list)):
            live[cat] = len(coll)
        print(f"   {cat:14} file {len(store[cat]):6} -> live {live.get(cat, 'n/a')}")

    print()
    ok = not misplaced and not unfingerprinted
    print("RESULT:", "every record sits in the file for its category" if ok else "see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
