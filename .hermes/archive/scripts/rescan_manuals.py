"""Update manuals index and fix symlinks for D&D reference PDFs.

Run this whenever new PDFs are added to the manuals directory.
It rebuilds the index and triggers a re-sweep notification.

Usage: python3 rescan_manuals.py
"""
import json, os, time, sys
from pathlib import Path

# ── Where manuals live ──
MANUAL_SOURCE = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
# ── Where the char manager expects them ──
CHAR_MANUAL_LINK = Path(os.path.expanduser("~/dnd-character-manager/manuals/DnD-Manuals"))
# ── Where the campaign expert index is ──
CE_INDEX = Path(os.path.expanduser("~/dnd-campaign-expert/references/manuals_index.json"))
# ── Also write to char manager ──
CHAR_INDEX = Path(os.path.expanduser("~/dnd-character-manager/manuals/manuals_index.json"))

CATEGORY_MAP = {
    "Dungeon Master's Guide": "Core Rulebook",
    "Player's Handbook": "Core Rulebook",
    "Monster Manual": "Core Rulebook",
    "Xanathar's Guide": "Supplement",
    "Tasha's Cauldron": "Supplement",
    "Volo's Guide": "Supplement",
    "Mordenkainen's Tome": "Supplement",
    "Sword Coast": "Supplement",
    "Elemental Evil": "Supplement",
    "Tortle Package": "Supplement",
    "Guildmasters' Guide": "Campaign",
    "Wayfinders Guide": "Campaign",
    "Eberron": "Campaign",
    "Ancestral Weapon": "Homebrew",
    "Lost Mine": "Adventure",
    "Tomb of Annihilation": "Adventure",
    "Tyranny": "Adventure",
    "Dragon Heist": "Adventure",
    "Wild Sheep": "Adventure",
}

def categorize(title: str) -> str:
    for kw, cat in CATEGORY_MAP.items():
        if kw.lower() in title.lower():
            return cat
    return "Supplement"

def scan_manuals(source: Path) -> dict:
    if not source.exists():
        return {"source": str(source), "total_manuals": 0, "manuals": [], "error": "path not found"}

    seen_titles = set()
    manuals = []
    for f in sorted(source.rglob("*")):
        if f.is_file() and f.suffix.lower() == ".pdf":
            rel = f.relative_to(source)
            title = f.stem.replace("_", " ").replace("  ", " ").strip()
            # Deduplicate on title stem (some live in root + Manuals/ subdir)
            if title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            
            category = categorize(title)
            manuals.append({
                "path": str(rel),
                "filename": f.name,
                "title": title,
                "pages": None,
                "file_size": f.stat().st_size,
                "category": category,
                "last_modified": f.stat().st_mtime,
            })

    categories = {}
    for m in manuals:
        cat = m["category"]
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "source": str(source),
        "total_manuals": len(manuals),
        "last_scan": time.time(),
        "manuals": manuals,
        "categories": categories,
    }

def fix_symlink():
    """Fix the symlink if it's broken."""
    target = str(MANUAL_SOURCE)
    if CHAR_MANUAL_LINK.exists() or CHAR_MANUAL_LINK.is_symlink():
        if CHAR_MANUAL_LINK.is_symlink():
            current = os.readlink(str(CHAR_MANUAL_LINK))
            if current.rstrip("/") == target.rstrip("/"):
                print(f"  Symlink OK: {CHAR_MANUAL_LINK}")
                return
            CHAR_MANUAL_LINK.unlink()
        else:
            # exists but not a symlink — weird, back it up
            CHAR_MANUAL_LINK.rename(CHAR_MANUAL_LINK.with_suffix(".bak"))
    CHAR_MANUAL_LINK.symlink_to(target)
    print(f"  Created symlink: {CHAR_MANUAL_LINK} → {target}")

def main():
    print(f"Rescanning manuals at: {MANUAL_SOURCE}")
    data = scan_manuals(MANUAL_SOURCE)

    if "error" in data:
        print(f"ERROR: {data['error']}")
        sys.exit(1)

    print(f"  Found {data['total_manuals']} PDFs")
    print(f"  Categories: {data.get('categories', {})}")

    # Fix symlink
    fix_symlink()

    # Write indices
    for path, label in [(CE_INDEX, "Campaign Expert"), (CHAR_INDEX, "Char Manager")]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Wrote {label} index → {path}")

    print(f"\nDone. {data['total_manuals']} manuals indexed.")

if __name__ == "__main__":
    main()
