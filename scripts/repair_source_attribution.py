#!/usr/bin/env python3
"""Resolve records whose citation points at the wrong book or a page that lacks them.

Three buckets come out of scripts/audit_ingest_attribution.py:

  * PAGE      - the record is in the cited book, but not on the cited page
  * BOOK      - the name is nowhere in the cited book, yet another book has it
  * UNOPENABLE- the slug has no openable file at all

For each, this searches **every** openable book's cached text for the record's name. A single
confident hit elsewhere is a re-attribution; a hit in the cited book on another page is a page
fix. Ambiguity (several books contain the name) and absence everywhere are reported, never
guessed at — a wrong book on a record is worse than a flagged one.

Matching is on normalised alphanumerics ("Demonomicon of Iggwilv" -> "demonomiconofiggwilv"), so
hyphenation, spacing and punctuation do not matter, and it is a plain substring test, which makes
scanning 90 books cheap.

Dry run by default; --apply writes the unambiguous fixes.

Run: .venv/bin/python3 scripts/repair_source_attribution.py [--apply] [--show 12]
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

from audit_ingest_attribution import load_pages, name_on_pages, NON_BOOK_SOURCES  # noqa: E402
from repair_page_citations import find_in_book  # noqa: E402

CATEGORIES = ("races", "spells", "magic_items", "equipment", "monsters",
              "npcs", "feats", "backgrounds", "subclasses", "traps")
MERGED = HERE / "data" / "manual_data"
FILES = HERE / "data" / "manual_cache"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def key_variants(name: str) -> list[str]:
    """Match keys for a record name.

    The app composes labels the books never print — "Gray Dwarf (Duergar)", "Mark of Finding
    (Human)", "Tiefling (Baalzebul)" — so matching the literal name reported hundreds of
    records as "in no book's text" when the book simply says "Duergar" or "Mark of Finding".
    Try the whole name, the name without its trailing parenthetical, and the parenthetical.
    """
    out = [norm(name)]
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", str(name))
    if m:
        out += [norm(m.group(1)), norm(m.group(2))]
    return [k for k in dict.fromkeys(out) if k]


def display_for(slug: str) -> str:
    import main as app_main  # heavy import, only when re-attributing
    return (app_main._get_source_slug_map().get(slug) or {}).get("display") or slug


def main() -> int:
    apply = "--apply" in sys.argv
    show = 12
    if "--show" in sys.argv:
        show = int(sys.argv[sys.argv.index("--show") + 1])

    pdf_map = json.loads((MERGED / "_meta.json").read_text()).get("pdf_map", {})
    openable = {s: e for s, e in pdf_map.items()
                if (e or {}).get("path") and (HERE / "manuals" / str(e["path"])).exists()}

    books: dict[str, str] = {}
    for slug in openable:
        txt = FILES / f"{slug}.txt"
        if txt.exists():
            books[slug] = norm(txt.read_text(errors="replace"))
    print(f"openable books with readable text: {len(books)}")

    page_fixes: list[tuple] = []
    book_fixes: list[tuple] = []
    ambiguous: list[str] = []
    nowhere: list[str] = []
    store: dict[str, list] = {}

    for cat in CATEGORIES:
        path = MERGED / f"{cat}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            continue
        store[cat] = data
        for it in data:
            if not isinstance(it, dict):
                continue
            slug = str(it.get("_source_manual") or "")
            name = str(it.get("name") or "")
            m = re.match(r"^\((.+?),\s*p\.(\d+)\)$", str(it.get("source") or ""))
            if not name or not m or slug.strip().lower() in NON_BOOK_SOURCES:
                continue
            cited = int(m.group(2))
            keys = key_variants(name)
            if not keys:
                continue
            in_cited = any(k in books.get(slug, "") for k in keys)
            # A book whose cached text stops before the cited page cannot be judged: "absent"
            # there proves nothing. WLL's cache ends at p.7 while its items cite p.163, and
            # re-attributing those would have moved records out of the right book.
            src_pages = load_pages(slug) if slug in openable else {}
            truncated = bool(src_pages) and cited > max(src_pages)

            if slug in openable and in_cited and not truncated:
                pgs = src_pages
                if pgs and not any(name_on_pages(pgs, name, cited, 2)):
                    real = find_in_book(pgs, name)
                    hits = sorted(n for n, t in pgs.items() if any(k in norm(t) for k in keys))
                    target = real or (hits[0] if hits else None)
                    if target and target != cited:
                        page_fixes.append((cat, name, slug, cited, target, it))
                continue
            if truncated:
                continue

            where = [s for s, blob in books.items() if any(k in blob for k in keys)]
            if len(where) == 1:
                book_fixes.append((cat, name, slug, cited, where[0], it))
            elif where:
                ambiguous.append(f"{cat}/{name}: cited {slug} p.{cited}; in {len(where)} books: {where[:4]}")
            else:
                nowhere.append(f"{cat}/{name}: cited {slug} p.{cited}; in no book's text")

    print(f"\nPAGE fixes (right book, wrong page): {len(page_fixes)}")
    for cat, name, slug, cited, target, _ in page_fixes[:show]:
        print(f"   - {cat}/{name}: {slug} p.{cited} -> p.{target}")
    print(f"\nBOOK re-attributions (one book has it): {len(book_fixes)}")
    for cat, name, slug, cited, target, _ in book_fixes[:show]:
        print(f"   - {cat}/{name}: {slug} p.{cited} -> {target}")
    print(f"\nambiguous, several books contain the name: {len(ambiguous)}")
    for row in ambiguous[:show]:
        print(f"   - {row}")
    print(f"\nname in no book's text at all: {len(nowhere)}")
    for row in nowhere[:show]:
        print(f"   - {row}")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to fix the unambiguous ones.")
        return 0

    # Apply: page fixes rewrite the number; re-attributions change the book and the display.
    touched = Counter()
    for cat, name, slug, cited, target, it in page_fixes:
        it["source"] = re.sub(r"p\.\d+\)$", f"p.{target})", str(it["source"]))
        touched[cat] += 1
    for cat, name, slug, cited, target, it in book_fixes:
        pgs = load_pages(target)
        page = find_in_book(pgs, name) if pgs else None
        if page is None:
            continue  # cannot place it in the new book: leave for a human
        it["_source_manual"] = target
        it["source"] = f"({display_for(target)}, p.{page})"
        touched[cat] += 1

    for cat in CATEGORIES:
        if not touched[cat]:
            continue
        # Write back the list we mutated. Re-reading here would silently discard every edit.
        path = MERGED / f"{cat}.json"
        path.write_text(json.dumps(store[cat], indent=2, ensure_ascii=False))
    print(f"\nAPPLIED: {sum(touched.values())} record(s) corrected {dict(touched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
