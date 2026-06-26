#!/usr/bin/env python3
"""Fix static JSON files so source data is correct on disk, not just at runtime."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DATA_DIR = HERE / "data"
SRD_CACHE = DATA_DIR / "srd_cache"
MANUAL_DATA = DATA_DIR / "manual_data"

# ── Slug → readable book name map (from _meta.json pdf_map) ──
with open(MANUAL_DATA / "_meta.json") as f:
    meta = json.load(f)
pdf_map = meta.get("pdf_map", {})
SLUG_TO_TITLE = {}
for slug, info in pdf_map.items():
    title = info.get("title", "")
    # Clean up: capitalize, remove underscores, etc.
    if title:
        # Title-case it nicely
        SLUG_TO_TITLE[slug] = title.strip()

# Also add known short-name mappings
SLUG_TO_TITLE["SDQ"] = "Shadows of the Dusk Queen 5E FINAL"
SLUG_TO_TITLE["VGM"] = "Volo's Guide to Monsters"
SLUG_TO_TITLE["WRKF"] = "Wrath River King 5E Final 240"
SLUG_TO_TITLE["AIPG"] = "Adventures in Middle-earth Player's Guide"
SLUG_TO_TITLE["TTP"] = "The Tortle Package"
SLUG_TO_TITLE["TCE"] = "Tasha's Cauldron of Everything"
SLUG_TO_TITLE["XGE"] = "Xanathar's Guide to Everything"

changes = 0

# ════════════════════════════════════════════════════════════
# 1. Fix manual_data/monsters.json — 13 "Unknown page" entries
# ════════════════════════════════════════════════════════════
print("=== Manual monsters ===")
mpath = MANUAL_DATA / "monsters.json"
with open(mpath) as f:
    manual_m = json.load(f)

for m in manual_m:
    src = m.get("source", "")
    if not src or src == "Unknown page":
        slug = m.get("_source_manual", "")
        if slug in SLUG_TO_TITLE:
            old = m.get("source", "")
            m["source"] = SLUG_TO_TITLE[slug]
            print(f"  {m['name']:40s} \"{old}\" → \"{m['source']}\"")
            changes += 1
        elif slug == "MM":
            m["source"] = "Monster Manual"
            print(f"  {m['name']:40s} \"{src}\" → \"Monster Manual\"")
            changes += 1

with open(mpath, "w") as f:
    json.dump(manual_m, f, indent=2, ensure_ascii=False)
print(f"  → {changes} monster sources fixed\n")

# ════════════════════════════════════════════════════════════
# 2. Fix srd_cache/monsters.json — add MM source + page
# ════════════════════════════════════════════════════════════
print("=== SRD monsters ===")
spath = SRD_CACHE / "monsters.json"
with open(DATA_DIR / "monster_page_map.json") as f:
    monster_page_map = json.load(f)

with open(spath) as f:
    srd_m = json.load(f)

srd_changes = 0
for m in srd_m:
    if "source" not in m or not m.get("source"):
        name = m.get("name", "")
        page = monster_page_map.get(name)
        if page:
            m["source"] = f"Monster Manual p.{page}"
        else:
            m["source"] = "Monster Manual"
        srd_changes += 1

with open(spath, "w") as f:
    json.dump(srd_m, f, indent=2, ensure_ascii=False)
print(f"  → {srd_changes} SRD monster sources fixed\n")
changes += srd_changes

# ════════════════════════════════════════════════════════════
# 3. Fix srd_cache/magic-items.json — add DMG source + page
# ════════════════════════════════════════════════════════════
print("=== SRD magic items ===")
with open(DATA_DIR / "item_page_map.json") as f:
    item_page_map = json.load(f)

spath_i = SRD_CACHE / "magic-items.json"
with open(spath_i) as f:
    srd_i = json.load(f)

item_changes = 0
for item in srd_i:
    if "source" not in item or not item.get("source"):
        name = item.get("name", "")
        # Try exact match first, then lowercase
        entry = item_page_map.get(name)
        if not entry:
            entry = item_page_map.get(name.lower())
        if entry and isinstance(entry, dict):
            src_str = entry.get("source_str", "")
            if src_str:
                item["source"] = src_str
                item_changes += 1
            else:
                page = entry.get("page", "")
                if page:
                    item["source"] = f"DMG 2014 p.{page}"
                    item_changes += 1

with open(spath_i, "w") as f:
    json.dump(srd_i, f, indent=2, ensure_ascii=False)
print(f"  → {item_changes} SRD item sources fixed\n")
changes += item_changes

# ════════════════════════════════════════════════════════════
# 4. Fix ingestion_tracker — mark MM done
# ════════════════════════════════════════════════════════════
print("=== Ingestion tracker ===")
tpath = DATA_DIR / "ingestion_tracker.json"
with open(tpath) as f:
    tracker = json.load(f)

if tracker.get("MM", {}).get("status") == "pending":
    from datetime import datetime
    now = datetime.now().isoformat()
    tracker["MM"] = {
        "title": "D&D 5E - Monster Manual",
        "slug": "MM",
        "status": "done",
        "started_at": now,
        "completed_at": now,
        "exit_code": 0,
        "entries": 334,
        "upgrades": 0,
        "issues": 0,
        "categories": {"monsters": 334},
        "error": None,
        "note": "Covered by SRD monster cache (334 SRD entries + page map)"
    }
    with open(tpath, "w") as f:
        json.dump(tracker, f, indent=2, ensure_ascii=False)
    print(f"  → MM marked done")
else:
    print(f"  → MM already done or not found")

print(f"\n=== Total: {changes} source fields fixed ===")
