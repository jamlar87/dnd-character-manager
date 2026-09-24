"""Sync seeded subclass/subrace sources from the manual records, then from book TOCs.

Two shadowing traps this fixes, both instances of the same defect:

  * The app seeds CLASSES/RACES from data/exports/*.json, so `_subclass_sources` and
    `_subrace_sources` there override data/manual_data — a source naming a book the library
    does not have is an unopenable badge.
  * A page map's or an older ingest's citation can name the WRONG book while the write-up is
    really in a book we own.

Phase 1 copies a citation across from the manual record when that citation resolves.
Phase 2 applies EVIDENCE, taken from each book's own table of contents / index in
data/manual_cache/<SLUG>.txt — every entry below was read out of the book it points at, e.g.
"Primal Path: Path of the Hive Tender ..... 14" in the Margreve Player's Guide's TOC, and
"Riverfolk: The Scattered People of the Trade ... 12" in Warlock 17's. Nothing is guessed; a
citation that cannot be sourced is left alone.

Usage: python3 scripts/sync_seeded_sources.py [--apply]
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services.sources import resolves_to_book, is_placeholder_source  # noqa: E402

CLASSES_EXPORT = REPO / "data" / "exports" / "classes_export.json"
RACES_EXPORT = REPO / "data" / "exports" / "races_export.json"
SUBCLASSES = REPO / "data" / "manual_data" / "subclasses.json"
RACES = REPO / "data" / "manual_data" / "races.json"
CLASS_MAP = REPO / "data" / "page_maps" / "class_page_map.json"
RACE_MAP = REPO / "data" / "page_maps" / "race_page_map.json"
ITEM_MAP = REPO / "data" / "page_maps" / "item_page_map.json"
BACKUP = REPO / "data" / "backups" / f"seeded-sources-{datetime.now():%Y%m%d-%H%M%S}"

MPG = "Margreve Player's Guide"          # MPG  — midgard/margreve player's guide
W4 = "Warlock 22: Druids"                # W4
W3 = "Warlock 17"                        # W3
TCE = "Tasha's Cauldron of Everything"   # TCE
XGE = "Xanathar's Guide to Everything"   # XGE
SCAG = "Sword Coast Adventurer's Guide"  # SCAG

# name -> (citation, slug, evidence)   — evidence read from the book's own TOC/index text
CLASS_SUBCLASS_EVIDENCE = {
    "Path of the Hive Tender": (f"({MPG}, p.14)", "MPG", "MPG TOC: 'Primal Path: Path of the Hive Tender' 14"),
    "Path of the Shadow Chewer": (f"({MPG}, p.15)", "MPG", "MPG TOC: 15"),
    "Hunt Domain": (f"({MPG}, p.16)", "MPG", "MPG TOC: 'Divine Domain: Hunt Domain' 16"),
    "Circle of Oaks": (f"({MPG}, p.17)", "MPG", "MPG TOC: 'Druid Circle: Circle of Oaks' 17"),
    "Circle of Roses": (f"({MPG}, p.20)", "MPG", "MPG TOC: 'Druid Circle: Circle of Roses' 20"),
    "Griffon Scout": (f"({MPG}, p.22)", "MPG", "MPG TOC: 'Ranger Archetype: Griffon Scout' 22"),
    "Grove Warden": (f"({MPG}, p.23)", "MPG", "MPG TOC: 'Ranger Archetype: Grove Warden' 23"),
    "Spear of the Weald": (f"({MPG}, p.24)", "MPG", "MPG TOC: 'Spear of the Weald' 24"),
    "The Underfoot": (f"({MPG}, p.24)", "MPG", "MPG TOC: 'Roguish Archetype: The Underfoot' 24"),
    "The Hunter in Darkness": (f"({MPG}, p.26)", "MPG", "MPG TOC: 26"),
    "The Old Wood": (f"({MPG}, p.27)", "MPG", "MPG TOC: 'Warlock Patron: The Old Wood' 27"),
    "Circle of Fermentation": (f"({W4}, p.8)", "W4", "W4 TOC: 'Circle of Fermentation: Life in the Winewood' 8"),
    "Circle of the Weald": (f"({W4}, p.12)", "W4", "W4 TOC: 'Circle of the Weald: Blood on the Leaves' 12"),
    "Rune Knight": (f"({TCE}, p.44)", "TCE", "TCE TOC: 'Rune Knight' 44"),
    "Genie": (f"({TCE}, p.73)", "TCE", "TCE TOC: 'The Genie' 73"),
    "College of Glamour": (f"({XGE}, p.14)", "XGE", "XGE TOC: 'College of Glamour' 14"),
    "Circle of Dreams": (f"({XGE}, p.22)", "XGE", "XGE TOC: 'Circle of Dreams' 22"),
    "Way of the Sun Soul": (f"({XGE}, p.35)", "XGE", "XGE TOC: 'Way of the Sun Soul' 35"),
    "Oath of Conquest": (f"({XGE}, p.37)", "XGE", "XGE TOC: 'Oath of Conquest' 37"),
    "Mastermind": (f"({XGE}, p.46)", "XGE", "XGE TOC: 'Mastermind' 46"),
    "Swashbuckler": (f"({XGE}, p.47)", "XGE", "XGE TOC: 'Swashbuckler' 47"),
    "Way of the Long Death": (f"({SCAG})", "SCAG", "SCAG text: 'Monks of the Way of the Long Death'"),
    "Oath of the Crown": (f"({SCAG})", "SCAG", "SCAG text: 'The Oath of the Crown is sworn to the ideals'"),
    "The Undying": (f"({SCAG}, p.139)", "SCAG", "source's own page + SCAG Chapter 4"),
    "Arcana Domain": (f"({SCAG}, p.125)", "SCAG", "SCAG Chapter 4 page 125"),
    "Oathbreaker": ("(Dungeon Master's Guide, p.97)", "DMG", "DMG chapter 4, p.97"),
}

# class-level source overrides
CLASS_SOURCE_EVIDENCE = {
    # "TCE 2020 / Eberron: Rising from the Last War" named two books, one of them absent
    "Artificer": (f"({TCE})", "TCE", "TCE (2020) is the canonical Artificer printing; ERftLW is not in the library"),
}

# subrace -> (citation, slug, evidence)
SUBRACE_EVIDENCE = {
    "Dwarves of the Iron Hills": ("(Lonely Mountain Region Guide, p.129)", "LMRG",
                                  "LMRG TOC: 'Dwarves of the Iron Hills' 129"),
    "Harfoot": ("(Adventures in Middle-earth Player's Guide, p.47)", "AIPG",
                "AIPG index: 'Harfoot, 47'"),
    "Riverfolk Halfling": (f"({W3}, p.12)", "W3",
                           "W3 TOC: 'Riverfolk: The Scattered People of the Trade' 12"),
    "Courtfolk Halfling": (f"({W3}, p.22)", "W3",
                           "W3 TOC: 'Courtfolk: The Quiet People of the Covenant' 22"),
}


# race name -> (citation, slug, evidence) — the entry's slug already opened, but its displayed
# text named the book by a name openSourceRef() cannot match (or named a book we do not own).
RACE_SOURCE_EVIDENCE = {
    "Stygian Shade": ("(Book of Ebon Tides, p.36)", "EBT", "EBT text: 'Stygian Shade Traits' p.36"),
    "Alseid": (f"({MPG}, p.8-9)", "MPG", "MPG TOC: 'Alseid' 8"),
    "Piney": (f"({MPG}, p.10)", "MPG", "MPG TOC: 'Piney' 10"),
    "Grung": ("(Volo's Guide to Monsters, p.156)", "VGM", "VGM index: 'Grung' 156"),
    "Changeling": ("(Wayfinder's Guide to Eberron, p.61)", "WGE", "WGE text: 'Changeling Traits' p.61"),
    "Kalashtar": ("(Wayfinder's Guide to Eberron, p.63)", "WGE", "WGE text: 'Kalashtar Traits' p.63"),
    "Shifter": ("(Wayfinder's Guide to Eberron, p.65)", "WGE", "WGE text: 'Shifter Traits' p.65"),
    "Warforged": ("(Wayfinder's Guide to Eberron, p.68)", "WGE", "WGE text: 'Warforged Traits' p.68"),
}


# page maps OVERRIDE the records they shadow, so a stale entry there beats correct data.
# slug-style source_str matches the app's own convention ("TTP p.4", "VGM p.200").
RACE_PAGE_MAP_FIX = {
    # ERLW is not in the library; these four races are all in Wayfinder's Guide to Eberron,
    # pages read from WGE.txt's own page markers.
    "changeling": ("WGE", 61, "WGE text 'Changeling Traits' p.61"),
    "kalashtar": ("WGE", 63, "WGE text 'Kalashtar Traits' p.63"),
    "shifter": ("WGE", 65, "WGE text 'Shifter Traits' p.65"),
    "warforged": ("WGE", 68, "WGE text 'Warforged Traits' p.68"),
    "beasthide shifter": ("WGE", 64, "WGE subrace list p.64"),
    "longtooth shifter": ("WGE", 64, "WGE subrace list p.64"),
    "swiftstride shifter": ("WGE", 64, "WGE subrace list p.64"),
    "wildhunt shifter": ("WGE", 64, "WGE subrace list p.64"),
    # OGA (One Grung Above) is not in the library either; the grung write-up is in VGM
    "grung": ("VGM", 156, "VGM index: 'Grung' 156"),
}
ITEM_PAGE_MAP_FIX = {
    # 'p.57' named no book at all; the item record itself cites Call of the Netherdeep
    "potions of healing": ("CotN", 57, "record source '(Call of the Netherdeep)'; page unchanged"),
    # 'Fane of the Night Serpent' is a Tomb of Annihilation appendix, not a shelf entry
    "shield (tortoise-shell shield)": ("ToA", 117, "record source '(Tomb of Annihilation, p.117)'"),
}


def main() -> int:
    import main as app

    slug_map = app._get_source_slug_map()
    displays = {s: str((i or {}).get("display") or (i or {}).get("title") or "") for s, i in slug_map.items()}

    def opens(src, slug="") -> bool:
        s = str(src or "").strip()
        if not s or is_placeholder_source(s):
            return False
        if slug and str(slug).strip() in slug_map:
            return True
        return resolves_to_book(s, displays)

    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    classes = json.loads(CLASSES_EXPORT.read_text())
    races_export = json.loads(RACES_EXPORT.read_text())
    subclass_recs = json.loads(SUBCLASSES.read_text())
    race_recs = json.loads(RACES.read_text())
    sub_by_name = {r.get("name"): r for r in subclass_recs if isinstance(r, dict) and r.get("name")}
    race_by_name = {r.get("name"): r for r in race_recs if isinstance(r, dict) and r.get("name")}
    class_map = json.loads(CLASS_MAP.read_text())

    changes, left = [], []

    def record(where, name, old, new, why):
        changes.append((where, name, old, new, why))

    for cls_name, cls in classes.items():
        if not isinstance(cls, dict):
            continue
        # class-level
        if not opens(cls.get("source")):
            new, _slug, why = CLASS_SOURCE_EVIDENCE.get(cls_name, ("", "", ""))
            if not new:
                mapped = (class_map.get(cls_name) or {}).get("source_str") or ""
                new = mapped if opens(mapped) else ""
                why = "class_page_map" if new else ""
            if new:
                record("class", cls_name, cls.get("source"), new, why)
                if args.apply:
                    cls["source"] = new
            else:
                left.append(("class", cls_name, cls.get("source")))
        # subclass-level
        subs = cls.get("_subclass_sources") or {}
        for sub, src in list(subs.items()):
            if opens(src):
                continue
            new, why = "", ""
            if sub in CLASS_SUBCLASS_EVIDENCE:
                new, _slug, why = CLASS_SUBCLASS_EVIDENCE[sub]
            else:
                rec = sub_by_name.get(sub) or {}
                if opens(rec.get("source"), rec.get("_source_manual")):
                    new, why = rec["source"], "manual record"
            if new:
                record("subclass", sub, src, new, why)
                if args.apply:
                    subs[sub] = new
                    rec = sub_by_name.get(sub)
                    if rec is not None and rec.get("source") != new:
                        rec["source"] = new          # keep the ingest record in step
            else:
                left.append(("subclass", sub, src))

    for race_name, race in races_export.items():
        if not isinstance(race, dict):
            continue
        # Race-level display text: a resolvable slug can still sit next to text no book matches
        # (e.g. "Tome of Heroes p.37" for a race whose write-up is in Book of Ebon Tides).
        src = str(race.get("source") or "").strip()
        if src and not resolves_to_book(src, displays) and race_name in RACE_SOURCE_EVIDENCE:
            new, _slug, why = RACE_SOURCE_EVIDENCE[race_name]
            record("race", race_name, src, new, why)
            if args.apply:
                race["source"] = new
                rec = race_by_name.get(race_name)
                if rec is not None and rec.get("source") != new:
                    rec["source"] = new      # keep the ingest record in step
        subs = race.get("_subrace_sources") or {}
        for sub, src in list(subs.items()):
            if opens(src):
                continue
            new, why = "", ""
            if sub in SUBRACE_EVIDENCE:
                new, _slug, why = SUBRACE_EVIDENCE[sub]
            else:
                rec = next((s for s in (race_by_name.get(race_name) or {}).get("subraces", [])
                            if isinstance(s, dict) and s.get("name") == sub), {})
                if opens(rec.get("source"), rec.get("_source_manual")):
                    new, why = rec["source"], "manual record"
            if new:
                record("subrace", f"{race_name}/{sub}", src, new, why)
                if args.apply:
                    subs[sub] = new
            else:
                left.append(("subrace", f"{race_name}/{sub}", src))

    # phase 3 — page maps override the records they shadow, so a stale entry there beats
    # correct data and must be fixed too.
    writes: dict = {}
    for map_path, table, label in ((RACE_MAP, RACE_PAGE_MAP_FIX, "race_pagemap"),
                                   (ITEM_MAP, ITEM_PAGE_MAP_FIX, "item_pagemap")):
        if not map_path.exists():
            continue
        data = json.loads(map_path.read_text())
        writes[str(map_path)] = data
        for key, (slug, page, why) in table.items():
            ent = data.get(key)
            if not isinstance(ent, dict):
                continue
            new_src = f"{slug} p.{page}"
            if str(ent.get("source_str") or "") == new_src and ent.get("page") == page:
                continue
            if not resolves_to_book(new_src, displays):
                left.append((label, key, new_src + "  [evidence text does not resolve]"))
                continue
            record(label, key, f"{ent.get('source_str')} (page {ent.get('page')})",
                   f"{new_src} (page {page})", why)
            if args.apply:
                ent["source_str"] = new_src
                ent["page"] = page

    for where, name, old, new, why in changes:
        print(f"  FIX  {where:<8} {name[:32]:<33} {str(old)[:32]!r:<34} -> {str(new)[:32]!r}  [{why}]")
    print(f"\n  fixed: {len(changes)} | still naming a book we do not have: {len(left)}")
    for where, name, src in left:
        print(f"  LEFT {where:<8} {name[:32]:<33} {str(src)[:56]!r}")

    if args.apply and (changes or left):
        BACKUP.mkdir(parents=True, exist_ok=True)
        for path in (CLASSES_EXPORT, RACES_EXPORT, SUBCLASSES, RACES, RACE_MAP, ITEM_MAP):
            if path.exists():
                shutil.copy2(path, BACKUP / path.name)
        CLASSES_EXPORT.write_text(json.dumps(classes, indent=2, ensure_ascii=False) + "\n")
        RACES_EXPORT.write_text(json.dumps(races_export, indent=2, ensure_ascii=False) + "\n")
        SUBCLASSES.write_text(json.dumps(subclass_recs, indent=2, ensure_ascii=False) + "\n")
        RACES.write_text(json.dumps(race_recs, indent=2, ensure_ascii=False) + "\n")
        for map_path_str, data in writes.items():
            Path(map_path_str).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"\n  applied; backup {BACKUP}")
    elif changes:
        print("\n  (dry run — add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
