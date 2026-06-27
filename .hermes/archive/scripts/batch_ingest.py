#!/usr/bin/env python3
"""Batch ingestion wrapper — runs D&D manuals sequentially with progress tracking.

Usage:
    python3 batch_ingest.py              # Run all pending manuals
    python3 batch_ingest.py --status     # Show tracker status
    python3 batch_ingest.py --reset XGE  # Reset one manual so it re-runs
    python3 batch_ingest.py --retry-failed  # Re-run failed manuals

The tracker lives at data/ingestion_tracker.json and records every run:
status, entry counts, category breakdown, issues, timestamps, exit codes.
"""

import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ingest_manual import (
    discover_manuals, process_manual, merge_all_extractions,
    SKIP_EXTRACTION, CACHE_DIR, _load_json, _save_json,
)

TRACKER_PATH = HERE / "data" / "ingestion_tracker.json"

# Process order — sourcebooks first (by size), then adventure modules
BATCH_ORDER = [
    # Core sourcebooks (smallest first as smoke tests)
    "TTP",    # Tortle Package — 2.6MB
    "EEPC",   # Elemental Evil — 22MB, races + spells
    "SCAG",   # Sword Coast — 56MB, subclasses + backgrounds
    "VGM",    # Volo's Guide — 56MB, races + monsters
    "XGE",    # Xanathar's — 82MB, biggest content drop
    "MTF",    # Mordenkainen's — 88MB, races + subraces
    "DMG",    # Dungeon Master's Guide — 88MB, magic items + NPCs
    "GGR",    # Guildmasters' Ravnica — 65MB, races + subclasses
    "WGE",    # Wayfinders Eberron — 10MB, races
    # Adventure modules (NPCs, monsters, magic items)
    "LMoP",   # Lost Mine of Phandelver
    "HotDQ",  # Hoard of the Dragon Queen
    "RoT",    # Rise of Tiamat
    "ToA",    # Tomb of Annihilation
    "WDH",    # Waterdeep: Dragon Heist
    "WSC",    # Wild Sheep Chase
    # Third-party / homebrew
    "AW",     # Ancestral Weapons
]


def load_tracker() -> dict:
    """Load or initialize the tracker."""
    if TRACKER_PATH.exists():
        data = _load_json(TRACKER_PATH)
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("_batch_started", None)
    data.setdefault("_batch_completed", None)
    data.setdefault("_order", BATCH_ORDER)
    return data


def save_tracker(tracker: dict):
    """Persist the tracker to disk."""
    _save_json(TRACKER_PATH, tracker)


def init_manual_entry(tracker: dict, slug: str, title: str) -> dict:
    """Ensure a tracker entry exists for a manual."""
    if slug not in tracker:
        tracker[slug] = {
            "title": title,
            "slug": slug,
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "exit_code": None,
            "entries": 0,
            "upgrades": 0,
            "issues": 0,
            "categories": {},
            "error": None,
            "note": "",
        }
    return tracker[slug]


def process_one(manual: dict, tracker: dict) -> bool:
    """Process a single manual, updating the tracker. Returns True on success."""
    slug = manual["slug"]
    title = manual["title"]
    entry = tracker[slug]

    if entry["status"] == "done":
        print(f"  ⏭ {title} ({slug}) — already done, skipping")
        return True

    if slug in SKIP_EXTRACTION:
        print(f"  ⏭ {title} ({slug}) — in SKIP_EXTRACTION, skipping")
        entry["status"] = "skipped"
        entry["note"] = "Covered by SRD base data"
        save_tracker(tracker)
        return True

    entry["status"] = "running"
    entry["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    entry["error"] = None
    save_tracker(tracker)

    t0 = time.time()
    try:
        new_data = process_manual(manual)
        elapsed = time.time() - t0

        if new_data is None:
            # Skipped or failed
            if slug in SKIP_EXTRACTION:
                entry["status"] = "skipped"
                entry["note"] = "Covered by SRD base data"
            else:
                entry["status"] = "failed"
                entry["error"] = "process_manual returned None (text extraction failed?)"
            entry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            entry["exit_code"] = -1
        else:
            # Success — parse stats
            total = sum(len(v) for v in new_data.values())
            cats = {k: len(v) for k, v in sorted(new_data.items()) if v}

            # Try to read the extracted JSON for upgrades/issues count
            ext_path = CACHE_DIR / f"{slug}_extracted.json"
            ext_data = _load_json(ext_path) if ext_path.exists() else {}

            entry["status"] = "done"
            entry["entries"] = total
            entry["categories"] = cats
            entry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            entry["exit_code"] = 0
            entry["elapsed_min"] = round(elapsed / 60, 1)

            # Report
            print(f"\n  ✓ {title} ({slug}): {total} entries in {elapsed/60:.1f}m")
            for cat, count in cats.items():
                print(f"    {cat}: {count}")
    except Exception as e:
        elapsed = time.time() - t0
        entry["status"] = "failed"
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry["exit_code"] = -2
        entry["elapsed_min"] = round(elapsed / 60, 1)
        traceback.print_exc()
        print(f"\n  ✗ {title} ({slug}): FAILED — {e}")

    save_tracker(tracker)
    return entry["status"] == "done"


def print_status(tracker: dict):
    """Pretty-print the current tracker state."""
    print("\n=== INGESTION TRACKER ===")
    batch_started = tracker.get("_batch_started", "—")
    batch_done = tracker.get("_batch_completed", "—")
    print(f"  Batch started:  {batch_started or '—'}")
    print(f"  Batch completed: {batch_done or '—'}")

    status_order = {"running": 0, "failed": 1, "pending": 2, "done": 3, "skipped": 4}
    slugs = sorted(
        [k for k in tracker if not k.startswith("_")],
        key=lambda s: (status_order.get(tracker[s].get("status", ""), 9), s),
    )

    done = sum(1 for s in slugs if tracker[s]["status"] == "done")
    failed = sum(1 for s in slugs if tracker[s]["status"] == "failed")
    total_entries = sum(tracker[s].get("entries", 0) for s in slugs)
    print(f"  Books: {len(slugs)} total, {done} done, {failed} failed, {total_entries} entries\n")

    for slug in slugs:
        e = tracker[slug]
        status = e.get("status", "?")
        icon = {"done": "✓", "failed": "✗", "running": "◷", "pending": "○", "skipped": "⏭"}.get(status, "?")
        entries = e.get("entries", 0)
        mins = e.get("elapsed_min", "—")
        issues = e.get("issues", 0)
        error = e.get("error", "")

        line = f"  {icon} {slug:5s} {status:8s} {entries:4d} entries  {mins}m"
        if issues:
            line += f"  {issues} issues"
        if error and status == "failed":
            line += f"\n      ⤷ {error[:120]}"
        print(line)


def reset_manual(tracker: dict, slug: str):
    """Reset a manual to pending so it re-runs."""
    slug = slug.upper()
    if slug in tracker:
        tracker[slug]["status"] = "pending"
        tracker[slug]["error"] = None
        tracker[slug]["note"] = "Reset for re-run"
        save_tracker(tracker)
        print(f"  ↻ {slug} reset to pending")
    else:
        print(f"  ✗ {slug} not found in tracker")


def retry_failed(tracker: dict):
    """Reset all failed manuals to pending."""
    count = 0
    for slug in list(tracker):
        if slug.startswith("_"):
            continue
        if tracker[slug].get("status") == "failed":
            tracker[slug]["status"] = "pending"
            tracker[slug]["error"] = None
            tracker[slug]["note"] = "Retry after fix"
            count += 1
    if count:
        save_tracker(tracker)
        print(f"  ↻ {count} failed manual(s) reset to pending")
    else:
        print("  No failed manuals to retry")


def run_batch(tracker: dict):
    """Run all pending manuals in order."""
    if tracker.get("_batch_started") is None:
        tracker["_batch_started"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tracker["_batch_completed"] = None
        save_tracker(tracker)

    manuals = discover_manuals()
    manual_map = {m["slug"]: m for m in manuals}

    # Initialize tracker entries for all known manuals
    for m in manuals:
        init_manual_entry(tracker, m["slug"], m["title"])
    save_tracker(tracker)

    # Process in order
    for slug in BATCH_ORDER:
        if slug not in manual_map:
            entry = tracker.get(slug, {})
            if entry.get("status") != "done":
                print(f"  ⚠ {slug} not found in manuals directory — skipping")
                entry["status"] = "skipped"
                entry["note"] = "PDF not found"
                save_tracker(tracker)
            continue

        if tracker[slug].get("status") == "done":
            print(f"  ⏭ {tracker[slug]['title']} ({slug}) — already done")
            continue

        process_one(manual_map[slug], tracker)

    # Final merge
    print(f"\n{'='*60}")
    print("Merging all extractions...")
    merge_all_extractions()

    tracker["_batch_completed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_tracker(tracker)

    # Final report
    print_status(tracker)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--run", "run"):
        tracker = load_tracker()
        run_batch(tracker)
        return

    cmd = sys.argv[1]
    tracker = load_tracker()

    if cmd == "--status":
        print_status(tracker)
    elif cmd == "--retry-failed":
        retry_failed(tracker)
    elif cmd == "--reset" and len(sys.argv) > 2:
        reset_manual(tracker, sys.argv[2])
    elif cmd == "--merge":
        merge_all_extractions()
    else:
        print(__doc__)
        print("Commands: --run (default), --status, --retry-failed, --reset SLUG, --merge")


if __name__ == "__main__":
    main()
