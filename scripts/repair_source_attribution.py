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

# The page guards live with the page repair tool; sharing them keeps the two from disagreeing
# about what counts as a spell's entry page (a class list names every spell and describes none).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import repair_page_citations as rpc  # noqa: E402

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


def cache_is_stale(slug: str) -> bool:
    """True when the cached text holds far less than the PDF offers.

    A thin cache makes a name look absent from a book that really contains it, so the only
    re-attributions worth applying are those whose original book cannot be judged.
    """
    import fitz
    try:
        pdf_map = json.loads((MERGED / "_meta.json").read_text()).get("pdf_map", {})
        rel = (pdf_map.get(slug) or {}).get("path") or ""
        pdf = HERE / "manuals" / str(rel)
        cache = HERE / "data" / "manual_cache" / f"{slug}.txt"
        if not pdf.exists() or not cache.exists():
            return True
        doc = fitz.open(str(pdf))
        real = sum(len(p.get_text("text")) for p in doc)
        doc.close()
        return real > 1000 and cache.stat().st_size < 0.6 * real
    except Exception:
        return False


def main() -> int:
    apply = "--apply" in sys.argv
    apply_books = "--apply-books" in sys.argv
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
    far_moves: list[str] = []
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
                    if real is None:
                        # find_in_book rejects list, index and class-list pages on purpose. This
                        # fallback must not undo that: "the first page containing the words" is the
                        # class spell list for every PHB spell — Dominate Person -> p.111 (the bard
                        # list), Nondetection -> p.208, Finger of Death -> p.210. Require a page
                        # that is neither a list nor thin (index/contents pages are thin).
                        for pno in sorted(pgs):
                            blob = pgs[pno]
                            if not any(k in norm(blob) for k in keys):
                                continue
                            if rpc.looks_like_class_list(blob) or rpc.looks_like_name_list(blob):
                                continue
                            if len(blob.strip()) < 800:
                                continue
                            real = pno
                            break
                    target = real
                    if target and target != cited:
                        # A page repair should be a correction, not a relocation. Every PHB spell's
                        # name appears in its class spell lists, far from the entry, and that is what
                        # a name search finds first (Dominate Person p.241 -> p.111, the bard list).
                        # Large moves are reported, never proposed — they need a human.
                        if abs(target - cited) > 15:
                            far_moves.append(
                                f"{cat}/{name}: {slug} p.{cited} -> p.{target} "
                                f"({abs(target - cited)} pages away — too far to trust a name match)")
                            continue
                        page_fixes.append((cat, name, slug, cited, target, it))
                continue
            if truncated:
                continue

            where = [s for s, blob in books.items() if any(k in blob for k in keys)]
            # Prefer candidates the app can actually open: a stale slug whose own cache still
            # exists (WS -> WSE "Shadows Envy", TLT -> TTLT "The Tortured Land") otherwise looks
            # ambiguous when only one of the two is a real library book.
            openable_hits = [s for s in where if s in openable]
            if len(openable_hits) == 1:
                book_fixes.append((cat, name, slug, cited, openable_hits[0], it))
            elif len(where) == 1:
                book_fixes.append((cat, name, slug, cited, where[0], it))
            elif where:
                ambiguous.append(f"{cat}/{name}: cited {slug} p.{cited}; in {len(where)} books: {where[:4]}")
            else:
                nowhere.append(f"{cat}/{name}: cited {slug} p.{cited}; in no book's text")

    print(f"\nPAGE fixes (right book, wrong page): {len(page_fixes)}")
    for cat, name, slug, cited, target, _ in page_fixes[:show]:
        print(f"   - {cat}/{name}: {slug} p.{cited} -> p.{target}")
    print(f"\nlarge page moves, held back (a name match alone is not enough): {len(far_moves)}")
    for row in far_moves[:show]:
        print(f"   - {row}")
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
    skipped_book: list[str] = []
    for cat, name, slug, cited, target, it in page_fixes:
        it["source"] = re.sub(r"p\.\d+\)$", f"p.{target})", str(it["source"]))
        touched[cat] += 1

    for cat, name, slug, cited, target, it in (book_fixes if apply_books else []):
        # Only when the book it currently cites cannot be judged: a missing slug, or a cache too
        # thin to have found the name in. Otherwise the likelier cause is a name the app composes
        # or a reprint, and "fixing" it would move a correct record onto a book that merely
        # reprints it.
        if slug in openable and not cache_is_stale(slug):
            skipped_book.append(f"{cat}/{name}: {slug} p.{cited} -> {target} (cited book intact)")
            continue
        pgs = load_pages(target)
        page = find_in_book(pgs, name) if pgs else None
        if page is None and pgs:
            # The heading lookup is strict (it exists to avoid citing a table-of-contents). For a
            # re-attribution the book is already known to contain the name, so fall back to the
            # first page carrying every significant word of it.
            words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 3]
            for pno in sorted(pgs):
                blob = pgs[pno].lower()
                if words and all(w in blob for w in words):
                    page = pno
                    break
        if page is None:
            continue  # cannot place it in the new book: leave for a human
        it["_source_manual"] = target
        it["source"] = f"({display_for(target)}, p.{page})"
        touched[cat] += 1
    if book_fixes and not apply_books:
        print(f"\n{len(book_fixes)} re-attribution(s) NOT applied — book changes need --apply-books.")
    for row in skipped_book[:show]:
        print(f"   held back: {row}")
    if skipped_book:
        print(f"   ({len(skipped_book)} re-attribution(s) held back: the cited book is intact, so the "
              f"name is likelier an app-composed label or a reprint than a wrong book)")

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
