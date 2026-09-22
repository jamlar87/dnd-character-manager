#!/usr/bin/env python3
"""Generate the portraits the character roster is missing.

Reads the app's own prompt builder and provider (services/portraits.py), so the
batch output is identical to what the wizard produces — one implementation, no
drift. Writes ONLY `characters.portrait_url` (or `dm_npcs.portrait_url`), always
through services.images.normalize_portrait, exactly like every web writer.

    # who would be generated, and with which prompt
    .venv/bin/python3 scripts/generate_portraits.py --dry-run

    # one user, gentle pace, 5 images
    .venv/bin/python3 scripts/generate_portraits.py --user 4 --limit 5

    # everything real (skips obvious test fixtures)
    .venv/bin/python3 scripts/generate_portraits.py --all-users --delay 5

Safe to re-run: rows that already have a portrait are skipped unless --force.
Long runs are fine to interrupt — each image commits on its own, and the script
resumes where it stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "characters.db"

#: Names that the test suite and probes create. Regenerating a portrait for
#: "PDF Test Wizard" spends a free-tier request on nothing, so they are skipped
#: unless --include-fixtures is passed.
FIXTURE_PATTERNS = re.compile(
    r"(^\s*(test|con|asi|pdf|probe|zz|tmp|temp|dummy)\b"
    r"|^\s*(fighter|wizard|rogue|cleric|bard|ranger|paladin|barbarian|monk|druid|sorcerer|warlock|artificer)\s*$"
    r"|\btest\b|^\s*\d+\s*$)",
    re.IGNORECASE,
)


#: generation order matters for a long bulk run: the smallest, most
#: visible library first
REF_KINDS = ("npc", "item", "creature")


def want(kinds, kind, default=False) -> bool:
    if not kinds:
        return default
    return "all" in kinds or kind in kinds


async def run_reference(kinds, args):
    """Generate shared reference art (creatures/items/NPCs) into static/ref-portraits/."""
    from services import ref_portraits
    from services.entity_search import iter_entities

    done, failed, budget = [], [], args.limit or 0
    for kind in kinds:
        rows = list(iter_entities(kind))
        todo = [r for r in rows if not ref_portraits.have(kind, r["name"])]
        print(f"\n{kind}: {len(todo)} missing of {len(rows)} indexed "
              f"({len(rows) - len(todo)} already have art)")
        for n, row in enumerate(todo, 1):
            if budget and len(done) >= budget:
                print(f"  --limit {budget} reached")
                return done, failed
            name = row["name"]
            t0 = time.time()
            try:
                path, err = await ref_portraits.generate(
                    kind, name, row.get("subtitle") or "", row.get("snippet") or "",
                    max_wait=args.timeout, retries=args.retries)
            except Exception as exc:               # never die mid-run
                path, err = None, f"{type(exc).__name__}: {exc}"
            if path:
                kb = path.stat().st_size // 1024
                print(f"  [{len(done)+1}] {kind} {name[:38]} → {kb} KB in {time.time()-t0:.0f}s")
                done.append({"kind": kind, "name": name, "kb": kb,
                             "seconds": round(time.time() - t0, 1)})
            else:
                print(f"  [{len(done)+1}] {kind} {name[:38]} → FAILED: {err}")
                failed.append({"kind": kind, "name": name, "error": err})
            await asyncio.sleep(args.delay)
    return done, failed


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", type=int, action="append", default=None,
                   help="only this user_id (repeatable). Default: every user.")
    p.add_argument("--all-users", action="store_true",
                   help="explicitly allow every user (default when --user is absent)")
    p.add_argument("--limit", type=int, default=0, help="stop after N images (0 = no limit)")
    p.add_argument("--delay", type=float, default=5.0,
                   help="seconds between requests (free tier is rate limited; default 5)")
    p.add_argument("--timeout", type=float, default=120.0, help="per-image timeout")
    p.add_argument("--retries", type=int, default=2, help="extra attempts per image")
    p.add_argument("--force", action="store_true",
                   help="regenerate rows that already have a portrait")
    p.add_argument("--include-fixtures", action="store_true",
                   help="also do test/probe characters")
    p.add_argument("--npcs", action="store_true", help="also do DM NPC rows")
    p.add_argument("--kind", action="append", default=None,
                   choices=["character", "creature", "item", "npc", "all"],
                   help="what to generate: characters (default), or shared reference "
                        "art for creatures/items/NPCs. Repeatable; 'all' = everything.")
    p.add_argument("--stats", action="store_true",
                   help="just report how much reference art exists, then exit")
    p.add_argument("--dry-run", action="store_true", help="print the plan, generate nothing")
    p.add_argument("--report", default=None, help="write a JSON report here")
    return p.parse_args()


def connect():
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def plan(con, args):
    """Rows that need a portrait, in a stable order."""
    where = ["1=1"]
    params: list = []
    if not args.force:
        where.append("COALESCE(portrait_url,'') = ''")
    if args.user and not args.all_users:
        where.append("user_id IN (" + ",".join("?" * len(args.user)) + ")")
        params += args.user
    rows = [dict(r) for r in con.execute(
        f"""SELECT id, user_id, name, race, class_name, subclass, level, portrait_url
            FROM characters WHERE {' AND '.join(where)} ORDER BY user_id, id""", params)]

    if not args.include_fixtures:
        kept, skipped = [], []
        for r in rows:
            (skipped if FIXTURE_PATTERNS.search(r["name"] or "") else kept).append(r)
        rows = kept
    else:
        skipped = []

    npcs = []
    if args.npcs:
        nwhere = ["1=1"] + ([] if args.force else ["COALESCE(portrait_url,'') = ''"])
        if args.user and not args.all_users:
            nwhere.append("user_id IN (" + ",".join("?" * len(args.user)) + ")")
        npcs = [dict(r) for r in con.execute(
            f"""SELECT id, user_id, name, race, class_name, subclass, role, notes, portrait_url
                FROM dm_npcs WHERE {' AND '.join(nwhere)} ORDER BY user_id, id""",
            params)]
        npcs = [n for n in npcs if (n["name"] or "").strip().lower() != "__sentinel__"]
    return rows, skipped, npcs


def prompt_for(row, is_npc: bool):
    from services import portraits
    if is_npc and not (row.get("race") or row.get("class_name")):
        return portraits.npc_prompt(row.get("name", ""), row.get("notes") or "",
                                    row.get("race") or "", row.get("role") or "")
    return portraits.portrait_prompt(row.get("race") or "", row.get("class_name") or "",
                                     row.get("subclass") or "")


async def generate(prompt: str, args):
    """Call the provider, retrying through rate limits and blips."""
    from services import portraits
    last = "no attempt made"
    for attempt in range(args.retries + 1):
        data, err = await portraits.generate_portrait_image(prompt, max_wait=args.timeout)
        if data:
            return data, None
        last = err or "no image returned"
        retryable = any(t in last.lower() for t in
                        ("rate limit", "unreachable", "timeout", "returned http 5",
                         "returned http 429"))
        if attempt == args.retries or not retryable:
            break
        backoff = 20 * (attempt + 1)
        print(f"      retry in {backoff}s ({last})")
        await asyncio.sleep(backoff)
    return None, last


def store(con, table: str, row_id: int, value: str) -> None:
    """Short write, only this column — the app is live while we run."""
    con.execute(f"UPDATE {table} SET portrait_url = ? WHERE id = ?", (value, row_id))
    con.commit()


async def main() -> int:
    args = parse_args()
    kinds = args.kind or []

    if args.stats:
        from services import ref_portraits
        from services.entity_search import iter_entities
        print("reference art on disk:", json.dumps(ref_portraits.stats(), indent=2))
        for k in REF_KINDS:
            total = sum(1 for _ in iter_entities(k))
            print(f"  {k}: {ref_portraits.stats([k])[k]['images']}/{total} indexed")
        return 0

    if want(kinds, "creature") or want(kinds, "item") or want(kinds, "npc"):
        con = connect()
        ref_kinds = [k for k in REF_KINDS if want(kinds, k)]
        if args.dry_run:
            from services.entity_search import iter_entities
            from services import ref_portraits
            for k in ref_kinds:
                rows = list(iter_entities(k))
                missing = [r for r in rows if not ref_portraits.have(k, r["name"])]
                print(f"{k}: {len(missing)} of {len(rows)} need art "
                      f"(~{len(missing) * 30 // 60} min at 30s each)")
                for r in missing[:2]:
                    print(f"   e.g. {r['name']}: "
                          f"{ref_portraits.prompt_for(k, r['name'], r.get('subtitle') or '', '')[:150]}…")
            return 0
        done, failed = await run_reference(ref_kinds, args)
        print(f"\n{len(done)} generated, {len(failed)} failed")
        if args.report:
            Path(args.report).write_text(json.dumps(
                {"reference": {"generated": done, "failed": failed},
                 "stats": __import__("services.ref_portraits", fromlist=["stats"]).stats()},
                indent=2))
            print(f"report: {args.report}")
        return 1 if failed else 0

    con = connect()
    chars, skipped, npcs = plan(con, args)
    total = len(chars) + len(npcs)

    print(f"characters missing a portrait : {len(chars)}")
    print(f"dm_npcs  missing a portrait  : {len(npcs)}")
    if skipped:
        print(f"skipped as test fixtures     : {len(skipped)} "
              f"({', '.join(s['name'] for s in skipped[:6])}"
              f"{'…' if len(skipped) > 6 else ''})")
    print()
    for r in chars:
        print(f"  char {r['id']:>6} u{r['user_id']:<3} {r['name'][:30]:<30} "
              f"{(r['race'] or '?')[:14]:<14} {(r['class_name'] or '?')[:14]:<14} L{r['level']}")
    for r in npcs:
        print(f"  npc  {r['id']:>6} u{r['user_id']:<3} {r['name'][:30]:<30} (name-based prompt)")

    if args.dry_run:
        print(f"\n--dry-run: no images requested. {total} rows would be generated.")
        for r in chars[:2]:
            print(f"\nprompt for {r['name']}:\n  {prompt_for(r, False)[:300]}…")
        return 0
    if not total:
        print("\nNothing to do — every row already has a portrait.")
        return 0

    if args.limit:
        chars = chars[:args.limit]
        npcs = npcs[:max(0, args.limit - len(chars))]
        total = len(chars) + len(npcs)
        print(f"\n--limit {args.limit}: doing {total}")

    print(f"\ngenerating {total} image(s) at ~{args.delay}s spacing; Ctrl-C is safe\n")
    done, failed = [], []
    started = time.time()
    queue = [("characters", r) for r in chars] + [("dm_npcs", r) for r in npcs]

    for n, (table, row) in enumerate(queue, 1):
        prompt = prompt_for(row, table == "dm_npcs")
        label = f"[{n}/{total}] {table[:-1]} {row['id']} {row['name'][:30]}"
        t0 = time.time()
        data, err = await generate(prompt, args)
        if data:
            store(con, table, row["id"], data)
            size = len(data) // 1024
            print(f"{label} → {size} KB in {time.time()-t0:.0f}s")
            done.append({"table": table, "id": row["id"], "name": row["name"],
                         "kb": size, "seconds": round(time.time() - t0, 1)})
        else:
            print(f"{label} → FAILED: {err}")
            failed.append({"table": table, "id": row["id"], "name": row["name"], "error": err})
        if n < total:
            await asyncio.sleep(args.delay)

    print(f"\n{len(done)} generated, {len(failed)} failed "
          f"in {time.time()-started:.0f}s")
    if failed:
        for f in failed:
            print(f"  ! {f['table']} {f['id']} {f['name']}: {f['error']}")
    remaining = con.execute(
        "SELECT COUNT(*) FROM characters WHERE COALESCE(portrait_url,'')=''").fetchone()[0]
    print(f"characters still without a portrait: {remaining}")
    if args.report:
        Path(args.report).write_text(json.dumps(
            {"generated": done, "failed": failed, "remaining": remaining}, indent=2))
        print(f"report: {args.report}")
    con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
