"""Wire a new manual PDF into the library: file in the tree, symlink in the repo, pdf_map entry.

The library's layout (follow it or the app will not see the book):

    /media/james/SlowDisk1tb/home-move/DnD-Manuals/<Name>.pdf        <- the real file
    manuals/<Name>.pdf -> DnD-Manuals/<Name>.pdf                     <- relative symlink (repo)
    data/manual_data/_meta.json  pdf_map["<SLUG>"] = {title, filename, path}

`_get_source_slug_map()` builds its cache by iterating `pdf_map`, so a book whose file is on the
shelf but has no pdf_map key is invisible: the slug does not exist, badges raise "Could not find
the source book", and `title` is what `resolves_to_book()` matches a record's source text
against — it must contain the wording the records use ("Monsters of the Multiverse").

Backs up _meta.json, is idempotent, and optionally proves a page really holds what the records
cite (`--check-page 34 --expect Tortle`), which is the evidence a citation needs.

Usage:
  python3 scripts/wire_manual.py MPMM /path/to/book.pdf \
      --name "D&D 5E - Mordenkainen Presents Monsters of the Multiverse.pdf" \
      --title "D&D 5E Mordenkainen Presents Monsters of the Multiverse" \
      --check-page 34 --expect Tortle [--apply]
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUALS = REPO / "manuals"
TREE = (MANUALS / "DnD-Manuals").resolve()
META = REPO / "data" / "manual_data" / "_meta.json"
BACKUP = REPO / "data" / "backups" / f"manual-wire-{datetime.now():%Y%m%d-%H%M%S}"


def die(msg: str) -> int:
    print(f"  ERROR {msg}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("src")
    ap.add_argument("--name", required=True, help="filename to use in the library")
    ap.add_argument("--title", required=True, help="pdf_map title; must contain the wording records cite")
    ap.add_argument("--check-page", type=int)
    ap.add_argument("--expect", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    if not src.is_file():
        return die(f"source not found: {src}")
    if src.read_bytes()[:5] != b"%PDF-":
        return die(f"not a PDF: {src}")
    if not TREE.is_dir():
        return die(f"library tree not mounted: {TREE}")

    dest = TREE / args.name
    link = MANUALS / args.name

    # evidence: does the page the records cite really hold what they say?
    if args.check_page:
        out = subprocess.run(["pdftotext", "-f", str(args.check_page), "-l", str(args.check_page),
                              str(src), "-"], capture_output=True, text=True).stdout
        found = args.expect.lower() in out.lower() if args.expect else bool(out.strip())
        print(f"  evidence  page {args.check_page} "
              f"{'contains' if found else 'does NOT contain'} {args.expect!r}"
              f"  ({len(out)} chars extracted)")
        if not found:
            print("  refusing to wire on failed evidence (use --check-page 0 to skip)")
            return 1

    info = subprocess.run(["pdfinfo", str(src)], capture_output=True, text=True).stdout
    pages = next((l.split()[-1] for l in info.splitlines() if l.startswith("Pages")), "?")
    print(f"  source    {src.name}  {src.stat().st_size} bytes, {pages} pages")

    meta = json.loads(META.read_text())
    entry = {"title": args.title, "filename": args.name, "path": f"DnD-Manuals/{args.name}"}
    cur = (meta.get("pdf_map") or {}).get(args.slug)
    print(f"  pdf_map   {args.slug}: {json.dumps(cur)[:64] if cur else 'ABSENT'} -> {json.dumps(entry)[:64]}")
    print(f"  tree      {'present' if dest.exists() else 'copy in'} {dest}")
    print(f"  symlink   {'ok' if link.is_symlink() else 'create'} {link.name}")

    if not args.apply:
        print("\n  (dry run — add --apply)")
        return 0

    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(META, BACKUP / "_meta.json")
    if not dest.exists():
        shutil.copy2(src, dest)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(f"DnD-Manuals/{args.name}")
    meta.setdefault("pdf_map", {})[args.slug] = entry
    META.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    print(f"\n  wired {args.slug}; restart the service to rebuild the slug map")
    print(f"  restore: {BACKUP}/_meta.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
