"""Wire the Kobold Press "Warlock" zines into the source slug map.

main.py's curated `_slug_displays` already names them ("W2": "Warlock 7", "W4":
"Warlock 22: Druids", "W8": "Warlock Lair: The Returners' Tower", ...) and the manual
records already carry those slugs in `_source_manual` — but `data/manual_data/_meta.json`
`pdf_map` has no W2..W9 keys, and `_get_source_slug_map()` builds its cache by iterating
pdf_map. So the curated names were dead entries, and every "(Warlock 7, p.12)"-style source
in magic_items/monsters/spells/npcs failed to resolve: the badge raised "Could not find the
source book" even though the PDF sits in the library at
DnD-Manuals/5e Kobold Press Resources/.

Page counts confirm the pairing (validator's own max_pages vs the real PDFs):
W2=30 vs Warlock-007 (30), W5=32 vs Warlock-032 (32), W7=38 vs Warlock-Bestiary (38).

Also corrects WLL: its display is "Warlock Lairs: Into the Wilds" and its records cite
p.163/165, but it pointed at Warlock-Lair-9-The-Returners-Tower.pdf (~13 pages). The real
"Into the Wilds" compilation is Warlock_Lairs_1_Into_the_Wilds_FINAL.pdf (~180 pages).

Backs up _meta.json first.   Usage: python3 scripts/wire_warlock_manuals.py [--apply]
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
META = REPO / "data" / "manual_data" / "_meta.json"
BACKUP = REPO / "data" / "backups" / f"warlock-slugs-{datetime.now():%Y%m%d-%H%M%S}"

REL = "DnD-Manuals/5e Kobold Press Resources"

# slug -> (title as pdf_map stores it, filename)
ADD = {
    # W1's display name in main.py is "Pride of the Mushroom Queen" and spell_page_map points
    # the spell "putrescent faerie circle" at "W1 p.8" — page 8 of this PDF is literally
    # "PUTRESCENT FAERIE CIRCLE / 5th-level conjuration", so the file is the book the page map
    # has been describing all along. It just had no pdf_map key, so the slug never existed.
    "W1": ("WL24 Pride of the Mushroom Queen", "WL24-Pride-of-the-Mushroom-Queen.pdf"),
    "W2": ("Warlock 007", "Warlock-007.pdf"),
    "W3": ("Warlock 017 FINAL v2", "Warlock-017-FINAL-v2.pdf"),
    "W4": ("Warlock 022 Druids zkpxhg", "Warlock-022-Druids-zkpxhg.pdf"),
    "W5": ("Warlock 032 FINAL 1uxx3u", "Warlock-032-FINAL-1uxx3u.pdf"),
    "W6": ("Warlock 034 onlvlm", "Warlock-034-onlvlm.pdf"),
    "W7": ("Warlock Bestiary", "Warlock-Bestiary.pdf"),
    "W8": ("Warlock Lair 9 The Returners Tower", "Warlock-Lair-9-The-Returners-Tower.pdf"),
    "W9": ("Warlock Lair The Dark Aerie 082219", "Warlock-Lair-The-Dark-Aerie-082219.pdf"),
}

# slug -> (filename it should point at) where the existing entry points at the wrong book
REPOINT = {
    "WLL": ("Warlock Lairs 1 Into the Wilds FINAL", "Warlock_Lairs_1_Into_the_Wilds_FINAL.pdf"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    meta = json.loads(META.read_text())
    pdf_map = meta["pdf_map"]

    changed = 0
    for slug, (title, filename) in ADD.items():
        entry = {"title": title, "filename": filename, "path": f"{REL}/{filename}"}
        exists = pdf_map.get(slug)
        state = "already" if exists == entry else ("MISSING" if exists is None else "differs")
        print(f"  {slug:<4} {state:<8} -> display via main.py _slug_displays | {filename}")
        if exists != entry:
            if args.apply:
                pdf_map[slug] = entry
            changed += 1

    for slug, (title, filename) in REPOINT.items():
        cur = pdf_map.get(slug, {})
        was = cur.get("filename")
        print(f"  {slug:<4} repoint  {was} -> {filename}")
        if was != filename:
            if args.apply:
                if changed == 0:
                    BACKUP.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(META, BACKUP / "_meta.json")
                pdf_map[slug] = {"title": title, "filename": filename, "path": f"{REL}/{filename}"}
            changed += 1

    if args.apply and changed:
        if not BACKUP.exists():
            BACKUP.mkdir(parents=True, exist_ok=True)
            shutil.copy2(META, BACKUP / "_meta.json")
        META.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(f"\n{changed} entry change(s)"
          + (f"; backup {BACKUP}" if args.apply and changed else " (dry run — add --apply)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
