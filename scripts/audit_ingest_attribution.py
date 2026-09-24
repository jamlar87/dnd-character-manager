#!/usr/bin/env python3
"""Are ingested records wired to the right place?

For every record that library ingestion contributed, check four things:

  1. its `_source_manual` is a slug the app can actually open (pdf_map)
  2. its display string still resolves to a book (the app's own resolves_to_book, so the
     📚 badge cannot fail) AND the book that string names is the same book as _source_manual
     — a citation that resolves by substring to the wrong book counts as wired wrong
  3. the cited page of that book really contains the record's name, within a small window
     (extraction assigns the page range of the chunk it read). This is the check that
     catches "wired to the wrong page" rather than just "wired to something that opens".
  4. the record sits in the file for its own category (no monster in races.json)

Run: .venv/bin/python3 scripts/audit_ingest_attribution.py [--slug MPMM ...] [--window 2]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

CATEGORIES = ("races", "spells", "magic_items", "equipment", "monsters",
              "npcs", "feats", "backgrounds", "subclasses", "traps")


def load_pages(slug: str) -> dict[int, str]:
    """Page number -> text for a book's cache.

    Caches are not uniform: most write `--- PAGE 7 ---`, but the two-column scans (TTP) write
    `--- PAGE 7 (left) ---` / `(right)`, and a regex that demanded the bare form silently
    returned {} for them — reported as "no page markers" when the markers were there all along.
    Both halves of a page are concatenated, not overwritten.
    """
    path = HERE / "data" / "manual_cache" / f"{slug}.txt"
    if not path.exists():
        return {}
    parts = re.split(r"---\s*PAGE\s+(\d+)[^-]*---", path.read_text(errors="replace"))
    pages: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        n = int(parts[i])
        pages[n] = pages.get(n, "") + parts[i + 1]
    return pages


def name_on_pages(pages: dict[int, str], needle: str, cited: int, window: int) -> tuple[list[int], list[int]]:
    """(exact-phrase pages, all-words pages) within the window.

    Books break statblock headings across lines ("SHADAR-KAI SHADOW\\nDANCER") and print them
    in a different word order from the record's name ("Wizard, Transmuter" vs "Transmuter
    Wizard"), so a literal phrase match reports false misattributions. Normalise whitespace,
    then fall back to "every distinctive word is on this page".
    """
    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(t).lower())

    phrase = re.compile(r"\b" + re.escape(needle) + r"\b", re.I)
    # Normalise the needle the same way as the page text: "Shadar-kai" -> "shadar kai", or the
    # hyphen stops it matching text that hyphenation had already collapsed.
    words = [norm(w) for w in re.findall(r"[A-Za-z0-9'-]+", needle) if len(w) > 2]
    words = [w for w in (w.strip() for w in words) if len(w) > 2]
    exact, loose = [], []
    for n, text in pages.items():
        if abs(n - cited) > window:
            continue
        flat = re.sub(r"\s+", " ", text)
        if phrase.search(flat):
            exact.append(n)
            continue
        if words:
            ntext = norm(text)
            if all(w in ntext for w in words):
                loose.append(n)
    return sorted(exact), sorted(loose)


NON_BOOK_SOURCES = {"", "homebrew", "srd", "custom", "unknown", "n/a", "none"}


def alias_groups(slug_displays: dict[str, str]) -> dict[str, set[str]]:
    """Book identity by display name: the engine and the app use different slugs for one book
    (DTCOE/TCE), so comparing slugs alone reports a misattribution that is not one."""
    groups: dict[str, set[str]] = {}
    for slug, disp in slug_displays.items():
        key = re.sub(r"[^a-z0-9]", "", str(disp).lower()) or slug.lower()
        groups.setdefault(key, set()).add(slug)
    out: dict[str, set[str]] = {}
    for members in groups.values():
        for slug in members:
            out[slug] = members
    return out


def book_of(display: str, slug_displays: dict[str, str], slugs: list[str]) -> str:
    """Which book does this display string name? Exact/containment match, like the badge."""
    d = str(display or "").strip().lower()
    if not d:
        return ""
    for slug in slugs:
        if slug.lower() == d or str(slug_displays.get(slug, "")).lower() == d:
            return slug
    for slug in slugs:
        s = str(slug_displays.get(slug, "")).lower()
        if s and (s in d or d in s):
            return slug
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", help="limit to these app slugs (repeatable)")
    ap.add_argument("--window", type=int, default=2, help="page tolerance (default 2)")
    ap.add_argument("--show", type=int, default=10, help="max failures to print per check")
    args = ap.parse_args()

    import main as app_main  # noqa: E402

    pdf_map = json.loads((HERE / "data" / "manual_data" / "_meta.json").read_text()).get("pdf_map", {})
    slug_map = app_main._get_source_slug_map()
    slug_displays = {s: v.get("display", s) for s, v in slug_map.items()}
    slugs = sorted(slug_displays)
    from services.sources import resolves_to_book  # noqa: E402

    wanted = set(args.slug) if args.slug else None
    pages_cache: dict[str, dict[int, str]] = {}
    aliases = alias_groups(slug_displays)

    per_book: collections.Counter = collections.Counter()
    bad_slug, bad_display, bad_book, bad_page, bad_file = [], [], [], [], []
    bad_book_absent: list[str] = []
    page_markerless: list[str] = []
    loose_pages: list[str] = []
    beyond_cache: list[str] = []
    non_book: list[str] = []
    checked_pages = 0

    for cat in CATEGORIES:
        path = HERE / "data" / "manual_data" / f"{cat}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        items = data if isinstance(data, list) else (data.get(cat) or [])
        for it in items:
            if not isinstance(it, dict):
                continue
            slug = str(it.get("_source_manual") or "")
            if not slug:
                continue
            if wanted and slug not in wanted:
                continue
            name = str(it.get("name") or "")
            per_book[slug] += 1

            # 1. opens? (an alias slug whose book is openable under another slug is fine;
            #    "Homebrew"/"SRD" are not books and are not a wiring error)
            openable = slug in pdf_map or any(s in pdf_map for s in aliases.get(slug, {slug}))
            if not openable:
                if slug.strip().lower() in NON_BOOK_SOURCES:
                    non_book.append(f"{cat}/{name}: source is {slug!r}, which is not a library book")
                    continue
                bad_slug.append(f"{cat}/{name}: _source_manual {slug!r} is not an openable slug")
                continue

            # 2. display resolves, and names this same book?
            disp = str(it.get("source") or "")
            if disp and not resolves_to_book(disp, slug_displays):
                bad_display.append(f"{cat}/{name}: {disp!r} does not resolve — badge would alert")
            parsed = re.match(r"^\((.+?),\s*p\.(\d+)\)$", disp)
            if parsed:
                named = book_of(parsed.group(1), slug_displays, slugs)
                if named and named not in aliases.get(slug, {slug}):
                    bad_book.append(f"{cat}/{name}: citation names {parsed.group(1)!r} -> {named}, "
                                    f"but _source_manual is {slug}")

            # 3. does the cited page contain the name?
            page = int(parsed.group(2)) if parsed else None
            if page and name and name.lower() not in ("", "unknown"):
                pages = pages_cache.setdefault(slug, load_pages(slug))
                if not pages:
                    page_markerless.append(
                        f"{cat}/{name}: cites {slug} p.{page} — {slug}'s text cache has no page "
                        f"markers, so the page cannot be verified (text is present, unused)")
                    continue
                if page > max(pages):
                    beyond_cache.append(f"{cat}/{name}: cites {slug} p.{page}; cached text ends at "
                                        f"p.{max(pages)} (cannot verify)")
                    continue
                checked_pages += 1
                exact, loose = name_on_pages(pages, name, page, args.window)
                if exact:
                    pass
                elif loose:
                    loose_pages.append(f"{cat}/{name}: cited p.{page} of {slug} — heading "
                                       f"reworded/split; its words are on p.{loose[0]}")
                else:
                    # Nothing near the cited page. The decisive question is whether the book
                    # contains the name at all: absent means the *book* attribution is suspect,
                    # which matters far more than a page being off by a few.
                    words_all = [re.sub(r"[^a-z0-9]+", " ", w.lower()).strip()
                                 for w in re.findall(r"[A-Za-z0-9'-]+", name)]
                    words_all = [w for w in words_all if len(w) > 2]
                    whole_book = pages_cache.setdefault(f"{slug}:whole", {0: "\n".join(pages.values())})
                    absent = bool(words_all) and not all(
                        w.lower() in re.sub(r"[^a-z0-9]+", " ", whole_book[0].lower()) for w in words_all)
                    where = sorted(n for n, t in pages.items()
                                   if re.search(r"\b" + re.escape(name.split()[0]) + r"\b", t, re.I))
                    if absent:
                        bad_book_absent.append(
                            f"{cat}/{name}: cited as {slug} p.{page}, but the name is nowhere in "
                            f"{slug} — book attribution looks wrong")
                    else:
                        bad_page.append(
                            f"{cat}/{name}: cited p.{page} of {slug}, name not within +/-{args.window}"
                            + (f" (first word appears on {where[:4]})" if where else ""))

            # 4. right file for the category?
            if cat == "monsters" and "traits" in it and "armor_class" not in it and "challenge_rating" not in it:
                bad_file.append(f"monsters/{name}: has race-shaped fields (traits, no AC/CR)")
            if cat == "races" and ("armor_class" in it or "challenge_rating" in it):
                bad_file.append(f"races/{name}: has monster-shaped fields")

    print("records checked (grouped by _source_manual):")
    for slug, n in per_book.most_common():
        flag = "" if slug in pdf_map else "  <-- NOT OPENABLE"
        print(f"   {slug:8} {n:5}{flag}")
    print(f"\ntotal: {sum(per_book.values())} | citations cross-checked against book text: {checked_pages}\n")

    for label, rows in (("slug not openable", bad_slug),
                        ("citation does not resolve (badge would alert)", bad_display),
                        ("citation names a different book", bad_book),
                        ("name is nowhere in the cited book (attribution suspect)", bad_book_absent),
                        ("cited page does not contain the record", bad_page),
                        ("record in the wrong file", bad_file)):
        print(f"{label}: {len(rows)}")
        for row in rows[:args.show]:
            print(f"   - {row}")
        if len(rows) > args.show:
            print(f"   ... and {len(rows) - args.show} more")
    print(f"cited page holds the record's words but reworded/split heading: {len(loose_pages)}")
    for row in loose_pages[:args.show]:
        print(f"   - {row}")
    print(f"cannot verify: cache has no page markers for that book: {len(page_markerless)}")
    for row in page_markerless[:args.show]:
        print(f"   - {row}")
    print(f"cannot verify (no cached text / citation past the end of it): {len(beyond_cache)}")
    for row in beyond_cache[:args.show]:
        print(f"   - {row}")
    print(f"source is not a library book (Homebrew/SRD — informational): {len(non_book)}")
    for row in non_book[:args.show]:
        print(f"   - {row}")
    ok = not (bad_slug or bad_display or bad_book or bad_page or bad_file)
    print("\nRESULT:", "all wired correctly" if ok else "problems above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
