#!/usr/bin/env python3
"""Ingest every library book that has text but no structured extraction.

Per book, safely:
  1. `process_manual` (ingest_manual) -> data/manual_cache/<ENGINE_SLUG>_extracted.json
  2. `append_extraction` (scripts/)     -> folds it into data/manual_data/*.json
  3. verify                             -> every merged file must still parse, and no
                                           category may shrink (append-only by design)
  4. remap the source slug              -> some books have two slugs; the app's pdf_map
                                           key is what the source badge links, so the
                                           records must carry THAT one or the badge 404s

Never calls `merge_all_extractions()`: that rebuilds the merged files from the extractions
still on disk, and most books' extractions were pruned long ago — it would delete ~1500
monsters. (`ingest_manual.py "<name>"` calls it automatically; do not use that path.)

Resumable: a book whose extraction exists and is `_completed` is skipped.
Bounded: `--max-seconds` stops cleanly between books so a cron slot is never overrun.

Usage:
  .venv/bin/python3 scripts/ingest_sweep.py --list
  .venv/bin/python3 scripts/ingest_sweep.py --max-seconds 500
  .venv/bin/python3 scripts/ingest_sweep.py --only MPMM PHB
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

import ingest_manual as ing  # noqa: E402
from append_extraction import append_extraction  # noqa: E402

CACHE = HERE / "data" / "manual_cache"
MERGED = HERE / "data" / "manual_data"
LOCK = HERE / "data" / ".ingest_sweep.lock"
LOG = HERE / "data" / "ingest_sweep.log"

CATEGORIES = ["races", "spells", "magic_items", "equipment", "monsters",
              "npcs", "feats", "backgrounds", "subclasses", "traps"]

# The engine's slug -> the app's pdf_map key, where they differ. Records must carry the
# pdf_map key: that is what /api/reference/open/<slug> and the source badge use.
SLUG_ALIAS = {
    "DMPMOT": "MPMM",   # Mordenkainen Presents Monsters of the Multiverse
    "W": "W2", "WFV": "W3", "WDZ": "W4", "WF": "W5", "WLB": "WLB",
    "WLL": "W8", "WWOTBK": "W9", "WPOTMQ": "W1",
    "DTCOE": "TCE",
}

# Books that matter most to the table first; everything else follows alphabetically.
PRIORITY = [
    "MPMM", "PHB", "MM", "DMG", "XGE", "TCE", "VGM", "MTF", "SCAG", "EEPC",
    "GGR", "ToA", "WDH", "LMoP", "HotDQ", "RoT", "CC", "EBT", "CSF",
    "MPG", "LMRG", "TMFRV", "MOM", "BLRG", "AIPG", "LMG", "ERIA", "EREA",
    "RVR", "RRG", "WLA", "MWC", "KW", "TFS", "SDQ", "RAT", "WSC", "RGEO",
    "SME", "EIA", "TTLT", "WLL", "WLL1", "WLL2", "WO", "WSE", "ETR", "EOM",
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def counts() -> dict:
    out = {}
    for cat in CATEGORIES:
        try:
            d = json.loads((MERGED / f"{cat}.json").read_text())
            out[cat] = len(d) if isinstance(d, list) else 0
        except Exception:
            out[cat] = -1
    return out


def remap_source_slug(engine_slug: str, app_slug: str) -> int:
    """Point records at the slug the app can actually open."""
    if engine_slug == app_slug:
        return 0
    total = 0
    for cat in CATEGORIES:
        path = MERGED / f"{cat}.json"
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        hit = 0
        for item in data:
            if isinstance(item, dict) and item.get("_source_manual") == engine_slug:
                item["_source_manual"] = app_slug
                hit += 1
        if hit:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            total += hit
    return total


def book_plan() -> list[tuple[str, str, dict]]:
    """(app_slug, engine_slug, engine_manual) for every book needing extraction."""
    meta = json.loads((MERGED / "_meta.json").read_text())
    pdf_map = meta.get("pdf_map", {})
    manuals = {m["slug"]: m for m in ing.discover_manuals()}
    # match engine manuals to app books by filename (titles differ in punctuation)
    by_fname = {}
    for m in ing.discover_manuals():
        by_fname[Path(m.get("filename") or "").name.lower()] = m

    plan = []
    seen_engine: set[str] = set()
    folded = set(meta.get("source_manuals") or [])
    for app_slug, entry in pdf_map.items():
        fname = Path(str(entry.get("path") or "")).name.lower()
        m = by_fname.get(fname)
        if not m and app_slug in manuals:
            m = manuals[app_slug]
        if not m:
            continue
        eng_slug = m["slug"]
        if eng_slug in seen_engine:
            continue  # the same book under a second app slug (W1 vs WPOTMQ): ingest once
        seen_engine.add(eng_slug)
        if app_slug in folded or eng_slug in folded:
            continue  # already folded into the merged data (see _meta.json source_manuals)
        if not (CACHE / f"{app_slug}.txt").exists() and not (CACHE / f"{eng_slug}.txt").exists():
            continue
        plan.append((app_slug, eng_slug, m))

    def rank(item):
        slug = item[0]
        return (PRIORITY.index(slug) if slug in PRIORITY else len(PRIORITY), slug)

    return sorted(plan, key=rank)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show the plan and exit")
    ap.add_argument("--only", nargs="*", default=None, help="app slugs to process")
    ap.add_argument("--max-seconds", type=float, default=1e9)
    args = ap.parse_args()

    plan = book_plan()
    if args.only:
        plan = [p for p in plan if p[0] in set(args.only)]
    if args.list:
        print(f"{len(plan)} book(s) to ingest:")
        for app_slug, eng_slug, m in plan:
            print(f"  {app_slug:8} <- {eng_slug:8} {m['title'][:52]}")
        return 0
    if not plan:
        log("nothing to do — every book with text is already extracted")
        return 0

    # single instance only — the lock holds a pid, and a long sweep must not be
    # mistaken for a stale lock just because it has been running a while.
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip() or 0)
        except Exception:
            pid = 0
        if pid and Path(f"/proc/{pid}").exists():
            log(f"another sweep is running (pid {pid}) — exiting")
            return 0
        log(f"clearing stale lock (pid {pid or '?'} is gone)")
    LOCK.write_text(str(os.getpid()))

    started = time.time()
    done = failed = 0
    before = counts()
    needs_action = 0
    log(f"starting sweep: {len(plan)} book(s); totals before {before}")
    try:
        for app_slug, eng_slug, m in plan:
            if time.time() - started > args.max_seconds:
                log(f"time budget reached — {len(plan) - done - failed} book(s) left for the next run")
                break
            log(f"--- {app_slug} ({m['title'][:46]}) ---")
            pre = counts()
            ext = CACHE / f"{eng_slug}_extracted.json"
            have_ext = False
            if ext.exists():
                try:
                    have_ext = bool(json.loads(ext.read_text()).get("_completed"))
                except Exception:
                    have_ext = False
            if have_ext:
                log("  extraction already on disk and complete — folding it")
            elif ext.exists() and (time.time() - ext.stat().st_mtime) < 1200:
                log("  extraction in progress elsewhere — leaving it for the next run")
                continue
            else:
                try:
                    res = ing.process_manual(m)
                except Exception as exc:  # noqa: BLE001
                    log(f"  EXTRACTION ERROR {type(exc).__name__}: {exc}")
                    failed += 1
                    continue
                if not res:
                    log("  extraction produced nothing — skipped")
                    failed += 1
                    continue
            if not ext.exists():
                log(f"  no extraction file at {ext.name} — skipped")
                failed += 1
                continue
            try:
                added = append_extraction(eng_slug)
            except Exception as exc:  # noqa: BLE001
                log(f"  FOLD ERROR {type(exc).__name__}: {exc}")
                failed += 1
                continue
            remapped = remap_source_slug(eng_slug, app_slug)
            post = counts()
            shrink = {c: (pre[c], post[c]) for c in CATEGORIES if post[c] < pre[c]}
            if shrink:
                log(f"  !! SHRINK DETECTED {shrink} — stopping so nothing else is touched")
                return 2
            log(f"  ok: {added} entr(ies) added, {remapped} source slug(s) remapped; totals {post}")
            # Every trait the app marks limited-use needs a known action type, or the sheet
            # cannot render it. One second to check here; otherwise it surfaces at the next
            # full-suite run (MPMM's races did exactly that). Warn loudly, keep going — the
            # fix is a registration in data.py, not a reason to strand the sweep.
            guard = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                 "tests/test_sheet_helpers_regression.py::TestFeatureActionTypeCoverage"
                 "::test_runtime_limited_use_keys_resolve_via_clean_strip"],
                cwd=HERE, capture_output=True, text=True, timeout=300)
            if guard.returncode != 0:
                needs_action += 1
                log(f"  !! ACTION-TYPE GAP after {app_slug}: a limited-use trait has no "
                    f"registered action type — add it to FEATURE_ACTION_TYPES in data.py")
                for line in (guard.stdout or "").strip().split("\n")[-6:]:
                    log(f"     {line.strip()}")
            done += 1
    finally:
        LOCK.unlink(missing_ok=True)
    log(f"sweep run finished: {done} ingested, {failed} failed, {time.time() - started:.0f}s"
        + (f"; {needs_action} book(s) left an unregistered action type — see ACTION-TYPE GAP above"
           if needs_action else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
