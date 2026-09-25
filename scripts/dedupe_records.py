#!/usr/bin/env python3
"""Merge duplicate records found by scripts/audit_duplicates.py, and normalise their names.

Every pair is listed explicitly with the reason it is a duplicate and which twin survives: a
duplicate is a judgement, not a pattern to apply blindly. An earlier attempt chose the survivor by
looking for the name printed as a line in the book's cached text, and it was wrong on the pairs that
matter most — it kept "Sword of the pa runs" and "Freezing Sphere", both OCR artifacts, because a
wrapped heading and a split word match a line test. The reasons below are checkable instead.

Two rules are used and both are evidenced:
  * apostrophes: the library writes the ASCII form 259 times against 14 typographic ones.
  * the PHB equipment table was parsed twice, as "Item - Qualifier" and "Item (Qualifier)".
    The pairs merge into the parenthetical form, and the two rows with no twin are renamed to match,
    otherwise the same mis-parse returns as a duplicate on the next ingest.

Nothing is dropped silently: each line prints what changed, and a pair whose names are not both
present is reported rather than skipped quietly. Dry run by default; --apply writes, merging the
better of the two (the book's name, the longer description, whichever twin has a page).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
MERGED = HERE / "data" / "manual_data"

ASCII = "keep the ASCII apostrophe (library: 259 ASCII vs 14 typographic)"
OCR = "OCR artifact"

MERGES = [
    # (category, keep, drop, reason)
    ("magic_items", "Spearman's Shield", "Spearman’s Shield", "punctuation only, same page AIPG:159; " + ASCII),
    ("magic_items", "Gatekeeper's Lantern", "Gatekeeper’s Lantern", "punctuation only, one page apart; " + ASCII),
    ("magic_items", "Sunforger", "Sun forger", f"{OCR} (split word); survivor carries GGR:181"),
    ("magic_items", "Sword of the Paruns", "Sword of the pa runs", f"{OCR} (split word); survivor carries GGR:4"),
    ("magic_items", "Alchemist's Fire (flask)", "Flask of Alchemist's Fire", "the item's printed form is 'Alchemist's Fire (flask)'"),
    ("magic_items", "Bell Jar of Preservation", "Bell Jar", "fuller name, same page WLL:67"),
    ("monsters", "c'Naazo", "c’Naazo", "punctuation only, same page TFS:182; " + ASCII),
    ("monsters", "Crokek'toeck", "Crokek’toeck", "punctuation only; " + ASCII),
    ("monsters", "Philosopher's Ghost", "Philosopher’s Ghost", "punctuation only, same page CC:297; " + ASCII),
    ("monsters", "Deathpact Angel", "Death Pact Angel", "the GGR prints one word"),
    ("monsters", "Horncaller", "Horn Caller", "the GGR prints one word"),
    ("monsters", "Tosculi Jeweled Drone", "Tosculi, Jeweled Drone", "no comma in the book"),
    ("monsters", "Vig the Spy", "Vig, the Spy", "no comma in the book"),
    ("spells", "Leomund's Tiny Hut", "Leomundo's Tiny Hut", f"{OCR} (typo)"),
    ("spells", "Protection from Poison", "From Poison", f"{OCR} (truncated heading), both at PHB:271"),
    ("spells", "Otiluke's Freezing Sphere", "Freezing Sphere", f"{OCR} (truncated heading); survives, gains PHB:265"),
    # The PHB equipment table, parsed twice.
    ("equipment", "Wine, Common (pitcher)", "Wine - Common (pitcher)", "same table row"),
    ("equipment", "Wine, Fine (bottle)", "Wine - Fine (bottle)", "same table row"),
    ("equipment", "Hireling (Skilled)", "Hireling - Skilled", "same table row"),
    ("equipment", "Coach cab (between towns)", "Coach cab - Between towns", "same table row"),
]

# Rows with no twin: renamed to the surviving style so the same mis-parse cannot reappear as a
# duplicate on the next ingest.
RENAMES = [
    ("equipment", "Hireling - Untrained", "Hireling (Untrained)"),
    ("equipment", "Coach cab - Within a city", "Coach cab (within a city)"),
]


def load(cat: str) -> list[dict]:
    return json.loads((MERGED / f"{cat}.json").read_text())


def save(cat: str, data: list[dict]) -> None:
    (MERGED / f"{cat}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))


def find(data: list[dict], name: str) -> dict | None:
    return next((it for it in data if it.get("name") == name), None)


def merge(keep: dict, drop: dict, keep_name: str) -> dict:
    """Keep the book's name, the longer description, and whichever twin has a page."""
    out = dict(keep)
    out["name"] = keep_name
    if len(str(drop.get("description") or "")) > len(str(keep.get("description") or "")):
        out["description"] = drop.get("description")
    if not re.search(r"p\.\d+", str(keep.get("source") or "")) and \
            re.search(r"p\.\d+", str(drop.get("source") or "")):
        out["source"] = drop.get("source")
    for field in ("_source_manual", "type", "rarity", "attunement"):
        if not out.get(field) and drop.get(field):
            out[field] = drop.get(field)
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    merged, renamed, problems = 0, 0, []
    by_cat: dict[str, list[dict]] = {}

    for cat, keep_name, drop_name, reason in MERGES:
        data = by_cat.setdefault(cat, load(cat))
        keep, drop = find(data, keep_name), find(data, drop_name)
        if keep is None or drop is None:
            missing = keep_name if keep is None else drop_name
            problems.append(f"{cat}: {missing!r} not present (the pair may already be merged)")
            continue
        print(f"  [{cat}] {keep_name!r}  <-  {drop_name!r}\n        {reason}"
              f"  |  {keep.get('source')}")
        if apply:
            data[data.index(keep)] = merge(keep, drop, keep_name)
            data.remove(drop)
        merged += 1

    for cat, old, new in RENAMES:
        data = by_cat.setdefault(cat, load(cat))
        rec = find(data, old)
        if rec is None:
            problems.append(f"{cat}: {old!r} not present, nothing to rename")
            continue
        print(f"  [{cat}] rename {old!r} -> {new!r}  (same table row, one style)")
        if apply:
            rec["name"] = new
        renamed += 1

    if apply:
        for cat, data in by_cat.items():
            save(cat, data)
    print(f"\n{'APPLIED' if apply else 'DRY RUN'} — merged: {merged}, renamed: {renamed}")
    for row in problems:
        print(f"  not done: {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
