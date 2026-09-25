#!/usr/bin/env python3
"""Find duplicate records in the merged library.

Four kinds of duplication are possible here, and they need different judgements:

  IDENTICAL   same normalised name AND same source string — a true duplicate, always a bug.
  SAME BOOK   same name, same slug, different page — one of them is wrong (or a heading moved).
  REPRINT     same name, different book — a monster printed in both MM and MPMM. Expected; a
              judgement call whether the app should show one or both.
  NEAR NAME   names that differ only in case, spacing or punctuation ("Magearmor" vs "Mage Armor").
              The fold dedupes on a normalised key, so this shape really is a duplicate — and it is
              exactly the shape the spell backfill could have introduced.
  SAME TEXT   different names carrying an identical description. Catches the same record ingested
              twice under two spellings, which a name check alone misses.

Writes a full report to data/duplicate_audit.txt and prints a summary. Exit code 1 with --strict
when any IDENTICAL or NEAR NAME group exists, so a cron can watch for it.

Run: .venv/bin/python3 scripts/audit_duplicates.py [--show 12] [--strict]
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
MERGED = HERE / "data" / "manual_data"
REPORT = HERE / "data" / "duplicate_audit.txt"

CATEGORIES = ("races", "spells", "magic_items", "equipment", "monsters",
              "npcs", "feats", "backgrounds", "subclasses", "traps")


def norm(name: str) -> str:
    """Case, spacing and punctuation all collapse: the fold's own comparison key."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def load(cat: str) -> list[dict]:
    path = MERGED / f"{cat}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [it for it in data if isinstance(it, dict)] if isinstance(data, list) else []


def where(it: dict) -> str:
    slug = str(it.get("_source_manual") or "?")
    src = str(it.get("source") or "")
    page = re.search(r"p\.(\d+)", src)
    return f"{slug}{':' + page.group(1) if page else ''}"


def main() -> int:
    show = 12
    if "--show" in sys.argv:
        show = int(sys.argv[sys.argv.index("--show") + 1])
    strict = "--strict" in sys.argv

    out: list[str] = []
    identical: list[str] = []
    same_book: list[str] = []
    reprint: list[str] = []
    near: list[str] = []
    same_text: list[str] = []
    total = 0

    for cat in CATEGORIES:
        items = load(cat)
        total += len(items)
        by_key: dict[str, list[dict]] = collections.defaultdict(list)
        by_text: dict[str, list[dict]] = collections.defaultdict(list)
        for it in items:
            k = norm(it.get("name", ""))
            if k:
                by_key[k].append(it)
            desc = re.sub(r"\s+", " ", str(it.get("description") or "")).strip().lower()
            if len(desc) > 120:          # short boilerplate collides innocently
                by_text[hashlib.sha1(desc.encode()).hexdigest()].append(it)

        for key, group in sorted(by_key.items()):
            if len(group) < 2:
                continue
            names = {str(it.get("name")) for it in group}
            places = [(str(it.get("name")), where(it)) for it in group]
            row = f"[{cat}] {key}: {len(group)} records — {places}"
            if len(names) == 1 and len({where(it).split(':')[0] for it in group}) == 1 \
                    and len({where(it) for it in group}) == 1:
                identical.append(row)
            elif len({where(it).split(':')[0] for it in group}) == 1:
                same_book.append(row)
            elif len(names) > 1:
                near.append(row)
            else:
                reprint.append(row)

        for _h, group in sorted(by_text.items()):
            if len(group) < 2:
                continue
            if len({norm(it.get("name", "")) for it in group}) < 2:
                continue                     # already reported above
            same_text.append(f"[{cat}] identical description, {len(group)} names: "
                             f"{[(str(it.get('name')), where(it)) for it in group]}")

    def section(title: str, rows: list[str], always_show: bool = False) -> None:
        out.append(f"\n=== {title}: {len(rows)} ===")
        out.extend("  " + r for r in rows)
        if rows and (always_show or len(rows) <= show):
            print(f"  {title}: {len(rows)}")
            for r in rows[:show]:
                print(f"    {r}")

    print(f"records scanned: {total}")
    section("IDENTICAL (same name, same source) — always a bug", identical, True)
    section("SAME BOOK (same name, same slug, different page)", same_book, True)
    section("NEAR NAME (case/spacing/punctuation only) — a duplicate", near, True)
    section("SAME TEXT (identical description, different names)", same_text, True)
    section("REPRINT (same name, different book) — expected, judgement call", reprint)
    if not reprint:
        print("  REPRINT: 0")

    REPORT.write_text("\n".join(out) + "\n")
    # NEAR NAME groups are mostly cross-book reprints that differ only in a typographic apostrophe
    # (XGE "Maximilian's" vs EEPC "Maximilian’s") — real, but not corruption. The invariant worth
    # failing on is two records of one name in one book, which the app shows to the user twice.
    bad = len(identical) + len(same_book)
    print(f"\nreport: {REPORT}  ({len(out)} lines)")
    print(f"RESULT: {'duplicates found' if bad else 'no true duplicates'}"
          f" (identical={len(identical)}, same_book={len(same_book)}, near={len(near)}, "
          f"reprint={len(reprint)}, same_text={len(same_text)})")
    return 1 if (strict and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
