"""Fix unresolved ("Unknown Source") attributions in the app's reference data.

Root cause this repairs: an ingested record whose book could not be determined keeps the
literal placeholder "(Unknown Source, p.N)" *and* no `_source_manual` slug, so
services.data_loader._normalize_manual_source() — which rebuilds a source from the slug —
has nothing to rebuild from. The placeholder then flows all the way to the DM-tools badge
("📚 (Unknown Source, p.222)") and to openSourceRef(), which is a dead click.

A source is "unresolved" when services.sources.is_placeholder_source() says it names no
book — that covers "(Unknown Source, p.N)", "(Unknown sourcebook)", "(N/A)" and
non-books like "(Generic treasure)".

Every rewrite below is evidence-backed: the name's presence in that book's extracted text
(data/manual_cache/<ABBR>.txt), and for the strongest cases the recorded page matching the
book's own index entry. Book names and slugs are the app's own (normalize_sources.py
ABBREV_MAP, manual data `_source_manual` values); the output format is copied from records
the app already got right, e.g. "(Tomb of Annihilation, p.8)" + "ToA".

Backs up every touched file first.   Usage:  python3 scripts/fix_unknown_sources.py [--apply]
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from services.sources import is_placeholder_source  # noqa: E402

MANUAL = REPO / "data" / "manual_data"
BACKUP = REPO / "data" / "backups" / f"sources-{datetime.now():%Y%m%d-%H%M%S}"

PAT = re.compile(r"unknown\s+source", re.I)  # kept for reference; detection uses the helper

# name -> (display book, page, slug, evidence)
NPC_FIXES = {
    # page matches the book's own index entry -------------------------------
    "Ludmilla, the Tunnel Viper": ("Guildmasters' Guide to Ravnica", 222, "GGR",
                                   "GGR index: 'Ludmilla, the Tunnel Viper .... 222'"),
    "Volothamp \"Volo\" Geddarm": ("Tomb of Annihilation", 235, "ToA",
                                   "ToA index: 'Volothamp \"Volo\" Geddarm .... 235'"),
    "Artus Cimber": ("Tomb of Annihilation", 212, "ToA",
                     "ToA index: 'Artus Cimber .... 212'; monsters.json agrees "
                     "(record said 214)"),
    # same name in monsters.json, which has a resolved source ---------------
    "Zindar": ("Tomb of Annihilation", 239, "ToA", "same name in monsters.json"),
    "Aurinax": ("Waterdeep: Dragon Heist", 193, "WDH", "same name in monsters.json"),
    # exactly one manual contains the name, in NPC prose --------------------
    "Tharm Tharmzid": ("Hoard of the Dragon Queen", 1, "HotDQ",
                       "HotDQ: 'The chef is a dwarf named Tharm Tharmzid'"),
    "Shilau M'wenye": ("Tomb of Annihilation", 55, "ToA",
                       "ToA: 'Shilau M'wenye (LN male Chultan human priest) is in charge "
                       "of the temple'"),
    "Thaeven the Bald": ("Tomb of Annihilation", 55, "ToA",
                         "ToA: 'The stablemaster is Thaeven the Bald (N male Tethyrian "
                         "human commoner)'"),
    "Jaro": ("Tomb of Annihilation", 55, "ToA",
             "ToA: 'run by a sharp-tongued old man named Jaro (NG male ...)'; the DMG and "
             "WGE hits are OCR noise ('gJarorue', 'King Jarot')"),
    "Rahl Zuberi": ("Tomb of Annihilation", 57, "ToA",
                    "ToA: 'The head trainer for the fort's reptiles is Rahl Zuberi'"),
    "Sigbeorn Dunebar": ("Tomb of Annihilation", 57, "ToA",
                         "ToA: 'Sigbeorn Dunebar (NG male Illuskan human veteran)'"),
    "Gruta Halsdottir": ("Tomb of Annihilation", 57, "ToA",
                         "ToA: 'a castellan named Gruta Halsdottir (LN female Illuskan "
                         "human knight)'"),
    "Summerwise": ("Tomb of Annihilation", 37, "ToA", "only ToA contains the name"),
    "Fel'rekl Lafeen": ("Waterdeep: Dragon Heist", 201, "WDH", "only WDH contains the name"),
    "Soluun Xibrindas": ("Waterdeep: Dragon Heist", 201, "WDH", "only WDH contains the name"),
    "Floon Blagmaar": ("Waterdeep: Dragon Heist", 201, "WDH", "only WDH contains the name"),
    "Nar'I Xibrindas": ("Waterdeep: Dragon Heist", 21, "WDH", "only WDH contains the name"),
    "Victoro Cassalanter": ("Waterdeep: Dragon Heist", 218, "WDH",
                            "only WDH contains the name; monsters.json agrees"),
    "Finethir Shinebright": ("The Wild Sheep Chase", 2, "WSC",
                             "only WSC contains the name ('WSC' = The Wild Sheep Chase per "
                             "the app's ABBREV_MAP); race field reads 'polymorphed into "
                             "sheep', which is this adventure's premise"),
}

FEAT_FIXES = {
    "Svirfneblin Magic": ("Elemental Evil Player's Companion", 7, "EEPC",
                          "EEPC p.7 is the Svirfneblin Magic feat; recorded page matches, and "
                          "the loader independently serves 'EEPC p.7' (cross-check passes)"),
}

# A treasure trinket, not a magic item: its "source" named no book at all.
ITEM_FIXES = {
    "necklace of 22 crysoprase beads": ("The Rise of Tiamat", None, "RoT",
                                        "the record's exact wording ('a necklace of 22 crysoprase "
                                        "beads worth 20 gp each') appears only in RoT's treasure "
                                        "text; the DMG has 'Chrysoprase' merely as one line in its "
                                        "gem table. No page: the .txt cache carries no page marks"),
}

RACE_FIXES = {
    "Deep Gnome": ("Elemental Evil Player's Companion", 7, "EEPC", "EEPC deep gnome section"),
    "Fire Genasi": ("Elemental Evil Player's Companion", 10, "EEPC",
                    "EEPC genasi section, recorded page matches"),
    "Water Genasi": ("Elemental Evil Player's Companion", 10, "EEPC",
                     "EEPC genasi section, recorded page matches"),
    "Xvart": ("Volo's Guide to Monsters", 200, "VGM",
              "VGM index lists 'Xvart ... 200', and p.200's stat block carries the traits this "
              "record stores (Overbearing Pack, Raxivort's Tongue); the recorded page 55 pointed "
              "at the PHB, which has no xvart at all"),
}

SUBCLASS_FIXES = {
    "Arcana Domain": ("Sword Coast Adventurer's Guide", 125, "SCAG",
                      "SCAG p.125 is the Arcana Domain; recorded page matches"),
    "The Undying": ("Sword Coast Adventurer's Guide", 139, "SCAG",
                    "SCAG p.139 is the undying patron; recorded page matches"),
    "Tempest Domain": ("Player's Handbook", 62, "PHB",
                       "the app's own class_page_map.json says 'PHB 2014 p.62' for this domain, "
                       "and the PHB is the only book that prints it"),
    "Trickery Domain": ("Player's Handbook", 62, "PHB",
                        "the app's own class_page_map.json says 'PHB 2014 p.62' for this domain"),
}

# data/page_maps/*.json entries OVERRIDE the displayed source at load time, so a wrong
# entry there beats a correct record.  map file -> key -> (page, source_str, evidence)
PAGE_MAP_FIXES = {
    "race_page_map.json": {
        "xvart": (200, "VGM p.200",
                  "the map said 'PHB 2014 p.55' but no PHB page holds a xvart; VGM p.200 does"),
    },
}

# Records with no evidence for a book. Left alone deliberately: the loader replaces the
# displayed source for these, so rewriting would only touch the file while risking a worse
# value.  (Empty since 2026-09-24 — all placeholders are now attributed.)
SKIP: dict[str, str] = {}

TARGETS = [("npcs.json", "npcs", NPC_FIXES), ("feats.json", "feats", FEAT_FIXES),
           ("races.json", "races", RACE_FIXES), ("subclasses.json", "subclasses", SUBCLASS_FIXES),
           ("magic_items.json", "magic_items", ITEM_FIXES)]


def new_source(display, page):
    return f"({display}, p.{page})" if page else f"({display})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()
    if args.apply:
        BACKUP.mkdir(parents=True, exist_ok=True)

    total = 0
    for fname, key, fixes in TARGETS:
        path = MANUAL / fname
        doc = json.loads(path.read_text())
        recs = doc if isinstance(doc, list) else doc.get(key) or doc.get("data") or []
        changed = 0
        for r in recs:
            if not isinstance(r, dict) or not is_placeholder_source(r.get("source", "")):
                continue
            name = str(r.get("name", ""))
            if name in SKIP:
                print(f"  skip {fname:<16} {name[:32]:<32} [{SKIP[name]}]")
                continue
            if name not in fixes:
                print(f"  !! UNMAPPED {fname}: {name!r} src={r.get('source')!r}")
                continue
            display, page, slug, why = fixes[name]
            new = new_source(display, page)
            print(f"  {fname:<16} {name[:32]:<32} {str(r.get('source'))[:26]:<26} -> "
                  f"{new:<48} slug={slug:<5} [{why[:50]}]")
            if args.apply:
                if changed == 0:
                    shutil.copy2(path, BACKUP / fname)
                r["source"] = new
                r["_source_manual"] = slug
            changed += 1
        if args.apply:
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"  -> {fname}: {changed} rewritten\n")
        total += changed

    # Page maps override the displayed source at load time, so fix them in the same pass.
    pm_dir = REPO / "data" / "page_maps"
    for mname, mfixes in PAGE_MAP_FIXES.items():
        mpath = pm_dir / mname
        doc = json.loads(mpath.read_text())
        changed = 0
        for key, (page, source_str, why) in mfixes.items():
            cur = doc.get(key)
            if isinstance(cur, dict) and cur.get("page") == page and cur.get("source_str") == source_str:
                print(f"  = {mname:<20} {key:<24} already {source_str}")
                continue
            was = cur.get("source_str") if isinstance(cur, dict) else cur
            print(f"  {mname:<20} {key:<24} {str(was):<24} -> {source_str:<16} [{why[:56]}]")
            if args.apply:
                if changed == 0:
                    shutil.copy2(mpath, BACKUP / mname)
                doc[key] = {"page": page, "source_str": source_str}
            changed += 1
        if args.apply and changed:
            mpath.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"  -> {mname}: {changed} rewritten\n")
        total += changed

    print(f"total rewritten: {total}")
    print(f"backup: {BACKUP}" if args.apply else "(dry run — add --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
