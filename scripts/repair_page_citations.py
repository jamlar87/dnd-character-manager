#!/usr/bin/env python3
"""Repair page citations that do not point at the record.

Ingestion cites the first page of the chunk it read, not the record's own page, so a record can
name the right book and the wrong page — the badge then opens the wrong place. This finds the
page where the record's name actually appears and rewrites just the page number, leaving the
book (and therefore the attribution) untouched.

Records whose name is found nowhere in the cited book are reported, never guessed at.

Shared pagination with the audit: scripts/audit_ingest_attribution.py (same loader, so both see
the same pages, including the two-column caches).

Dry run by default; --apply to write.

Run: .venv/bin/python3 scripts/repair_page_citations.py [--apply] [--slug MPMM ...] [--show 12]
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

CATEGORIES = ("races", "spells", "magic_items", "equipment", "monsters",
              "npcs", "feats", "backgrounds", "subclasses", "traps")
MERGED = HERE / "data" / "manual_data"
WINDOW = 2


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def is_toc_line(text: str, needle: str) -> bool:
    """True when the name appears only on a contents line (leader dots to a page number)."""
    for line in text.split("\n"):
        if re.search(needle, line, re.I):
            return line.count(".") > 6
    return False


_PHONETIC = re.compile(r"\b[A-Z]{2,}-[A-Za-z'’-]{2,}")   # NAR-ul, KAS-ah-lan-ter, awl-SEE-door


def looks_like_name_list(text: str) -> bool:
    """True for a glossary-shaped page: a pronunciation guide or dramatis personae.

    Such a page names the record without being its entry, and its lines look exactly like
    headings. WDH p.5 is a two-column pronunciation list, and the citation for Nar'I Xibrindas
    landed there instead of the appendix-B stat block on p.212 — the name sits on its own line,
    so the heading test matched. Phonetic tokens ("NAR-ul zeh-BRIN-das") give it away; a real
    entry page does not carry four or more of them.
    """
    return len(_PHONETIC.findall(text)) >= 4


def heading_page(pages: dict[int, str], name: str) -> int | None:
    """Page whose line *is* the name (a heading), not a page that merely mentions it."""
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", name) if len(w) > 2]
    if not words:
        return None
    pattern = re.compile(r"[\s\-–—:,'’]*".join(re.escape(w) for w in words), re.I)
    for n in sorted(pages):
        if looks_like_name_list(pages[n]):
            continue
        for raw_line in pages[n].split("\n"):
            line = raw_line.strip()
            if not line or len(line) > len(name) + 12:
                continue
            if pattern.search(line):
                return n
    return None


def find_in_book(pages: dict[int, str], name: str) -> int | None:
    """The page holding the record: prefer a heading, then a substantial content page.

    The words must appear in order with only whitespace/punctuation between them — that survives
    hyphenation and line breaks ("Shadar-kai Shadow\\nDancer") while rejecting pages that merely
    share the words scattered around ("Eyes of the Owl" must not land on the DMG's owl). Contents
    and index pages are skipped: a TOC mention is not the entry, and the badge would open the
    wrong place.
    """
    hit = heading_page(pages, name)
    if hit is not None:
        return hit
    words = [w for w in re.findall(r"[A-Za-z0-9'-]+", name) if len(w) > 2]
    if not words:
        return None
    pattern = r"\b" + r"[\s\-–—:,'’]*".join(re.escape(w) for w in words) + r"\b"
    for n in sorted(pages):
        text = pages[n]
        if len(text.strip()) < 800:      # front matter, contents, index: too thin to be the entry
            continue
        if looks_like_name_list(text):   # pronunciation guide / roster: names it, is not its entry
            continue
        flat = re.sub(r"\s+", " ", text)
        if re.search(pattern, flat, re.I) and not is_toc_line(text, pattern):
            return n
    return None


def main() -> int:
    apply = "--apply" in sys.argv
    only = set()
    if "--slug" in sys.argv:
        args = sys.argv[sys.argv.index("--slug") + 1:]
        only = {a for a in args if not a.startswith("--")}
    show = 12
    if "--show" in sys.argv:
        show = int(sys.argv[sys.argv.index("--show") + 1])

    pages_cache: dict[str, dict[int, str]] = {}
    fixed: list[str] = []
    not_found: list[str] = []
    per_book: Counter = Counter()

    for cat in CATEGORIES:
        path = MERGED / f"{cat}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            continue
        changed = False
        for it in data:
            if not isinstance(it, dict):
                continue
            slug = str(it.get("_source_manual") or "")
            if not slug or (only and slug not in only):
                continue
            if slug.strip().lower() in NON_BOOK_SOURCES:
                continue
            m = re.match(r"^\((.+?),\s*p\.(\d+)\)$", str(it.get("source") or ""))
            if not m:
                continue
            display, cited = m.group(1), int(m.group(2))
            name = str(it.get("name") or "")
            if not name or name.lower() in ("unknown", ""):
                continue
            pages = pages_cache.setdefault(slug, load_pages(slug))
            if not pages:
                continue  # no paginated text for this book: nothing to verify against
            exact, loose = name_on_pages(pages, name, cited, WINDOW)
            if exact or loose:
                continue
            real = find_in_book(pages, name)
            if real is None:
                not_found.append(f"{cat}/{name}: cited {slug} p.{cited}, name absent from {slug}")
                continue
            if real != cited:
                it["source"] = f"({display}, p.{real})"
                changed = True
                per_book[slug] += 1
                fixed.append(f"{cat}/{name}: {slug} p.{cited} -> p.{real}")
        if changed and apply:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"{'APPLIED' if apply else 'DRY RUN'} — page citations corrected: {len(fixed)}")
    for row in fixed[:show]:
        print(f"   - {row}")
    if len(fixed) > show:
        print(f"   ... and {len(fixed) - show} more")
    print(f"\nby book: {dict(per_book.most_common())}")
    print(f"\nname not found anywhere in the cited book (left alone, needs a human): {len(not_found)}")
    for row in not_found[:show]:
        print(f"   - {row}")
    if len(not_found) > show:
        print(f"   ... and {len(not_found) - show} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
