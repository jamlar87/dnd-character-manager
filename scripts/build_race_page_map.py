#!/usr/bin/env python3
"""
Build a race/subrace→page mapping for D&D 5e races.

Hand tables below give the book and page; the app's OWN records
(data/manual_data/races.json) and the per-book extraction caches then correct any
same-book page they disagree with. A race printed in several books keeps the map's
own book choice — that is a choice, not an error.

Output: data/page_maps/race_page_map.json
"""
import json
import sys
from pathlib import Path

REPO = Path("/home/james/dnd-character-manager")
sys.path.insert(0, str(REPO))          # slug_for_source lives in the app
DATA_DIR = REPO / "data"
OUTPUT_PATH = DATA_DIR / "page_maps" / "race_page_map.json"

# ── PHB Races ──
PHB_RACES = {
    "dwarf": (18, "PHB 2014"),
    "hill dwarf": (20, "PHB 2014"),
    "mountain dwarf": (20, "PHB 2014"),  # implied, subrace
    "elf": (21, "PHB 2014"),
    "high elf": (23, "PHB 2014"),
    "wood elf": (24, "PHB 2014"),
    "drow": (24, "PHB 2014"),
    "halfling": (26, "PHB 2014"),
    "lightfoot halfling": (28, "PHB 2014"),
    "stout halfling": (28, "PHB 2014"),
    "human": (29, "PHB 2014"),
    "variant human": (31, "PHB 2014"),
    "dragonborn": (32, "PHB 2014"),
    "gnome": (35, "PHB 2014"),
    "forest gnome": (37, "PHB 2014"),
    "rock gnome": (37, "PHB 2014"),
    "deep gnome": (37, "PHB 2014"),  # svirfneblin — also in EEPC
    "half-elf": (38, "PHB 2014"),
    "half-orc": (40, "PHB 2014"),
    "tiefling": (42, "PHB 2014"),
    "svirfneblin": (37, "PHB 2014"),  # deep gnome alias
}

# ── Volo's Guide to Monsters Races ──
VGM_RACES = {
    "aasimar": (104, "VGM"),
    "protector aasimar": (105, "VGM"),
    "scourge aasimar": (105, "VGM"),
    "fallen aasimar": (105, "VGM"),
    "firbolg": (106, "VGM"),
    "goliath": (108, "VGM"),
    "kenku": (109, "VGM"),
    "lizardfolk": (111, "VGM"),
    "tabaxi": (113, "VGM"),
    "triton": (115, "VGM"),
    "bugbear": (119, "VGM"),
    "goblin": (119, "VGM"),
    "hobgoblin": (119, "VGM"),
    "kobold": (119, "VGM"),
    "orc": (120, "VGM"),
    "yuan-ti pureblood": (120, "VGM"),
}

# ── Mordenkainen's Tome of Foes Races ──
MTF_RACES = {
    "eladrin": (61, "MTF"),
    "sea elf": (62, "MTF"),
    "shadar-kai": (62, "MTF"),
    "duergar": (81, "MTF"),
    "githyanki": (96, "MTF"),
    "githzerai": (96, "MTF"),
    "baalzebul tiefling": (21, "MTF"),  # tiefling subraces
    "dispater tiefling": (21, "MTF"),
    "fierna tiefling": (21, "MTF"),
    "glasya tiefling": (21, "MTF"),
    "levistus tiefling": (21, "MTF"),
    "mammon tiefling": (21, "MTF"),
    "mephistopheles tiefling": (21, "MTF"),
    "zariel tiefling": (21, "MTF"),
}

# ── Sword Coast Adventurer's Guide Races ──
SCAG_RACES = {
    "duergar": (104, "SCAG"),
    "ghostwise halfling": (110, "SCAG"),
    "deep gnome": (115, "SCAG"),
}

# ── Elemental Evil Player's Companion Races ──
EEPC_RACES = {
    "aarakocra": (3, "EEPC"),
    "genasi": (7, "EEPC"),
    "air genasi": (9, "EEPC"),
    "earth genasi": (9, "EEPC"),
    "fire genasi": (9, "EEPC"),
    "water genasi": (10, "EEPC"),
    "goliath": (10, "EEPC"),
    "deep gnome": (11, "EEPC"),
}

# ── Guildmasters' Guide to Ravnica Races ──
GGR_RACES = {
    "centaur": (15, "GGR"),
    "loxodon": (17, "GGR"),
    "minotaur": (18, "GGR"),
    "simic hybrid": (20, "GGR"),
    "vedalken": (21, "GGR"),
    "goblin": (16, "GGR"),  # also in GGR
}

# ── Eberron races ──
# The app ingests Wayfinder's Guide to Eberron (WGE), not ERLW, so WGE is the slug
# that resolves; pages come from the app's own race records.
ERLW_RACES = {
    "changeling": (61, "WGE"),
    "kalashtar": (63, "WGE"),
    "shifter": (65, "WGE"),
    "warforged": (68, "WGE"),
    "beasthide shifter": (64, "WGE"),
    "longtooth shifter": (64, "WGE"),
    "swiftstride shifter": (64, "WGE"),
    "wildhunt shifter": (67, "WGE"),
}

# ── The Tortle Package ──
TTP_RACES = {
    "tortle": (4, "TTP"),
}

# ── One Grung Above (Grung) ──
GRUNG = {
    "grung": (1, "OGA"),  # unofficial-ish
}

# ── Misc / Volo's extras ──
EXTRA_RACES = {
    "aasimar (volo's guide)": (104, "VGM"),
    "fallen aasimar (volo's guide)": (105, "VGM"),
    "protector aasimar (volo's guide)": (105, "VGM"),
    "scourge aasimar (volo's guide)": (105, "VGM"),
    # Monster-as-race (main.RICH_RACE_DESCS). The map used to say p.113, which is
    # the Yuan-ti section; the VGM text has the Tlincalli entry at p.194.
    "tlincalli": (194, "VGM"),
}


def prefix_of(src: str) -> str:
    """Book code for this map, resolved by the APP's own slug table.

    A hand list got this wrong (Explorer's Guide to Wildemount fell through to
    "PHB", and "Mordenkainen Presents" matches a "mordenkainen" needle aimed at
    MTF). slug_for_source already knows every ingested book.
    """
    try:
        from routes.characters.helpers import slug_for_source
        slug = slug_for_source(src)
    except Exception:
        slug = ""
    if not slug:
        return ""
    return {"PHB": "PHB 2014", "DMG": "DMG 2014"}.get(slug, slug)


def same_book_page(prior_source: str, candidates) -> int | None:
    """The page a record gives for the SAME book the map already cites.

    Only a same-book page disagreement is an error: a race printed in several
    books (Duergar is in SCAG, MTF and MPMM) is a book choice, not a mistake, and
    the map keeps its own choice.
    """
    try:
        from routes.characters.helpers import slug_for_source
    except Exception:
        return None
    want = slug_for_source(prior_source)
    if not want:
        return None
    for slug, page, _src in candidates:
        if slug == want and page:
            return page
    return None


_OCR_CACHE: dict[str, dict] = {}


def ocr_pages(book: str) -> dict:
    """{page: text} from the ingestion's own PDF text for a book (may be empty)."""
    if book not in _OCR_CACHE:
        blocks = {}
        p = DATA_DIR / "manual_cache" / f"{book}.txt"
        if p.exists():
            import re as _re
            parts = _re.split(r"--- PAGE (\d+) ---", p.read_text(errors="replace"))
            blocks = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
        _OCR_CACHE[book] = blocks
    return _OCR_CACHE[book]


_GENERIC_RACE_WORDS = {"tiefling", "gnome", "elf", "dwarf", "halfling", "genasi",
                       "shifter", "aasimar", "human", "dragonborn", "orc", "goblin"}


def book_text_supports(book: str, name: str, page: int, tol: int = 1) -> bool | None:
    """Is `name` visible in the book near `page`?

    None = cannot tell (no OCR for that book). The PDF text is noisy enough that
    only a grossly wrong page can be ruled out — which is exactly what is needed:
    it rejects the Vedalken claim of GGR p.2 (the name appears nowhere near p.2)
    while accepting Deep Gnome at EEPC p.7 and the tiefling bloodlines around
    MTF p.22.

    The full name is tried first, then its distinctive tokens: the text says
    "Glasya", not "Glasya Tiefling", and "deep" alone would match anywhere.
    """
    blocks = ocr_pages(book)
    if not blocks or not page:
        return None
    import re as _re
    needles = [name.strip()]
    needles += [w for w in name.split() if len(w) >= 5 and w.lower() not in _GENERIC_RACE_WORDS]
    for needle in needles:
        if not needle:
            continue
        pattern = _re.compile(_re.escape(needle), _re.I)
        for pg in range(page - tol, page + tol + 1):
            body = blocks.get(pg)
            if body and pattern.search(body):
                return True
    return False


def main():
    print("=== Building Race/Subrace Page Map ===\n")
    
    all_known = {}
    for name, (page, prefix) in PHB_RACES.items():
        all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
    print(f"PHB: {len(PHB_RACES)}")
    
    for name, (page, prefix) in VGM_RACES.items():
        all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
    print(f"VGM: {len(VGM_RACES)}")
    
    for d, label in [(MTF_RACES, "MTF"), (SCAG_RACES, "SCAG"), 
                      (EEPC_RACES, "EEPC"), (GGR_RACES, "GGR"),
                      (ERLW_RACES, "ERLW"), (TTP_RACES, "TTP"),
                      (EXTRA_RACES, "EXTRA")]:
        for name, (page, prefix) in d.items():
            all_known[name] = {"page": page, "source_str": f"{prefix} p.{page}"}
        print(f"{label}: {len(d)}")
    
    # ── evidence: what the ingestion recorded for each race ──
    # data/manual_data/races.json is what the app ships and what a race row shows;
    # the per-book extraction caches are the same ingestion's raw claims.
    import re
    evidence: dict[str, list] = {}
    for r in json.loads((DATA_DIR / "manual_data" / "races.json").read_text()):
        if isinstance(r, dict) and r.get("name") and r.get("source"):
            src = str(r["source"]).strip()
            m = re.search(r"\b[pP]\.?\s*(\d+)", src)
            evidence.setdefault(r["name"].strip().lower(), []).append(
                (prefix_of(src), int(m.group(1)) if m else None, src))
    for f in sorted((DATA_DIR / "manual_cache").glob("*_extracted.json")):
        try:
            cache = json.loads(f.read_text())
        except Exception:
            continue
        for r in (cache.get("races") or []):
            if isinstance(r, dict) and r.get("name") and r.get("source"):
                src = str(r["source"]).strip()
                m = re.search(r"\b[pP]\.?\s*(\d+)", src)
                evidence.setdefault(r["name"].strip().lower(), []).append(
                    (prefix_of(src), int(m.group(1)) if m else None, src))
    print(f"races with recorded evidence: {len(evidence)}")

    added = corrected = rejected = stats_skipped = 0
    for name, cands in evidence.items():
        entry = None
        record_slug, record_page, record_src = cands[0]      # races.json comes first
        if name in all_known:
            current = all_known[name]
            cur_book = current["source_str"].rsplit(" p.", 1)[0]
            # 1. the app's own record wins when it names a page its book backs up:
            #    one source of truth, so a race row and its traits cannot disagree.
            #    A book the library cannot open would render a dead badge
            #    (see test_reference_sources), so it is never written.
            if record_page and record_slug and book_text_supports(record_slug, name, record_page) is not False:
                if record_slug != cur_book or record_page != current["page"]:
                    entry = {"page": record_page, "source_str": f"{record_slug} p.{record_page}"}
                    print(f"  corrected {name}: {current['source_str']} -> {entry['source_str']}")
                    corrected += 1
            else:
                # 2. otherwise keep the map's own book and take a same-book page
                page = same_book_page(current["source_str"], cands)
                if page and page != current["page"]:
                    if book_text_supports(cur_book, name, page) is False:
                        print(f"  KEPT    {name}: {current['source_str']} "
                              f"(record claims p.{page}; the book text has no {name} near it)")
                        rejected += 1
                    else:
                        entry = {"page": page, "source_str": f"{cur_book} p.{page}"}
                        print(f"  corrected {name}: {current['source_str']} -> {entry['source_str']}")
                        corrected += 1
                elif record_page and book_text_supports(record_slug, name, record_page) is False:
                    print(f"  KEPT    {name}: {current['source_str']} "
                          f"(record claims {record_slug} p.{record_page}; the book text has no {name} near it)")
                    rejected += 1
        else:
            if record_page and record_slug:
                entry = {"page": record_page, "source_str": f"{record_slug} p.{record_page}"}
            else:
                paged = next(((pg, src) for _s, pg, src in cands if pg and prefix_of(src)), None)
                if paged:
                    page, src = paged
                    entry = {"page": page, "source_str": f"{prefix_of(src)} p.{page}"}
                else:
                    prefix = prefix_of(cands[0][2])
                    if not prefix:
                        # A book the library cannot open must not enter the map.
                        stats_skipped += 1
                        continue
                    entry = {"page": None, "source_str": prefix}
            added += 1
        if entry:
            all_known[name] = entry
    print(f"  manual records: {added} added, {corrected} corrected, {rejected} kept (text disagrees)")
    
    print(f"Manual additions: {added}")
    
    # Stats
    total = len(all_known)
    with_page = sum(1 for v in all_known.values() if v.get("page"))
    print(f"\nTotal: {total}, with pages: {with_page}, without: {total - with_page}")
    
    missing = {k: v for k, v in sorted(all_known.items()) if not v.get("page")}
    if missing:
        print("\nMissing page numbers:")
        for name, val in missing.items():
            print(f"  {name}: {val['source_str']!r}")
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(all_known, f, indent=2, sort_keys=True)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
