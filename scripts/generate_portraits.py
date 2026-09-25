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


def select_rows(kind: str, args) -> tuple[list, list]:
    """(rows, todo) for a reference kind — the single selection both paths use.

    --dry-run must plan the same set the real run touches. It once ignored --force, --match and
    --constructs, so the plan described different work than the run would do — worse than no plan,
    when the run spends a shared free image quota.
    """
    from services import ref_portraits
    from services.entity_search import iter_entities

    rows = list(iter_entities(kind))
    if getattr(args, "constructs", False):
        if kind == "npc":
            # NPC prompts come from the character builder (npc_prompt), so construct wording is never
            # applied to them. Matching here would select rows the flag cannot affect — "Tinkerer
            # Quash Dentdruggle, Gnome · Construct specialist" is a person, not a machine.
            rows = []
        else:
            from services.ref_portraits import construct_cue
            rows = [r for r in rows if construct_cue(r["name"], r.get("subtitle") or "")]
    if getattr(args, "match", None):
        pats = [re.compile(m, re.I) for m in args.match]
        rows = [r for r in rows if any(p.search(r["name"]) for p in pats)]
    # --force must be honoured, not only for characters: a family that needs redoing (the
    # constructs) is otherwise skipped for exactly the reason it needs redoing — it already has art.
    todo = [r for r in rows if args.force or not ref_portraits.have(kind, r["name"])]
    return rows, todo


async def run_reference(kinds, args):
    """Generate shared reference art (creatures/items/NPCs) into static/ref-portraits/."""
    from services import ref_portraits

    done, failed, budget = [], [], args.limit or 0
    for kind in kinds:
        rows, todo = select_rows(kind, args)
        print(f"\n{kind}: {len(todo)} to generate of {len(rows)} indexed "
              f"({len(rows) - len(todo)} already have art)")
        for n, row in enumerate(todo, 1):
            await _yield_to_interactive()
            if budget and len(done) >= budget:
                print(f"  --limit {budget} reached")
                return done, failed
            name = row["name"]
            t0 = time.time()
            try:
                path, err = await ref_portraits.generate(
                    kind, name, row.get("subtitle") or "", row.get("snippet") or "",
                    max_wait=args.timeout, retries=args.retries, force=args.force)
            except Exception as exc:               # never die mid-run
                path, err = None, f"{type(exc).__name__}: {exc}"
            if path and args.force and (time.time() - t0) < 1.5:
                # A forced regenerate that returns instantly did not call the provider — the
                # have() short-circuit is back. Reporting that as success is how 10 rows "completed"
                # in 0s while writing nothing, so it counts as a failure.
                print(f"  [{len(done)+1}] {kind} {name[:38]} → SUSPECT: returned in "
                      f"{time.time()-t0:.1f}s with --force, nothing generated")
                failed.append({"kind": kind, "name": name,
                               "error": "force returned instantly — the have() short-circuit"})
                await asyncio.sleep(args.delay)
                continue
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
    p.add_argument("--id", dest="ids", type=int, action="append", default=None,
                   help="only these character/NPC ids (repeatable). With --force, redo a handful "
                        "of rows without re-rolling every portrait that was already correct.")
    p.add_argument("--all-users", action="store_true",
                   help="explicitly allow every user (default when --user is absent)")
    p.add_argument("--limit", type=int, default=0, help="stop after N images (0 = no limit)")
    p.add_argument("--delay", type=float, default=5.0,
                   help="seconds between requests (free tier is rate limited; default 5)")
    p.add_argument("--timeout", type=float, default=120.0, help="per-image timeout")
    p.add_argument("--retries", type=int, default=4,
                   help="extra attempts per image (default 4: the free tier "
                        "rate limits hard and each retry waits 60s+)")
    p.add_argument("--constructs", action="store_true",
                   help="only rows the prompt builder treats as constructs. Uses construct_cue(), "
                        "the same predicate the wording is chosen by, so the filter cannot select a "
                        "different set than the descriptions are applied to")
    p.add_argument("--match", action="append", default=None,
                   help="only reference art whose name matches this regex (repeatable, "
                        "case-insensitive). Use with --force to redo a family, e.g. "
                        "--kind creature --match construct --force")
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
    #: Target specific rows. Without this the only way to fix a handful of wrong portraits is a
    #: full --force pass, which re-rolls every portrait that was already correct.
    if getattr(args, "ids", None):
        where.append("id IN (" + ",".join("?" * len(args.ids)) + ")")
        params += args.ids
    # NEVER regenerate a portrait the user uploaded by hand. This runs with --force, which re-rolls
    # existing art, so without this guard a bulk pass would overwrite uploads unrecoverably.
    #
    # There is no portrait_source column, so uploads are identified by their stored form. Verified
    # against all 26 portraits in the live database, the two groups separate with no exceptions:
    # a hand-uploaded image is PNG at 1.5-2.5 MB, every generated portrait is WebP at 17-33 KB.
    # The explicit id list is a second line of defence in case a future upload arrives in another
    # format. Getting this wrong destroys work that cannot be regenerated.
    UPLOADED_IDS = (6, 78, 86, 87, 97, 2406)  # Goon Hardfoot, Garim, Orla Harbak, Capt. Debian, Skyla, Dent Cheesegrinder
    where.append("NOT (COALESCE(portrait_url,'') LIKE 'data:image/png%' AND length(portrait_url) > 1000000)")
    where.append("id NOT IN (" + ",".join("?" * len(UPLOADED_IDS)) + ")")
    params += list(UPLOADED_IDS)
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
    from services import portraits, ref_portraits
    if is_npc and not (row.get("race") or row.get("class_name")):
        # Gender comes from the notes for the same reason it does on the reference path: the records
        # have no gender field, and the prose is the only place a pronoun is ever stated.
        notes = row.get("notes") or ""
        return portraits.npc_prompt(row.get("name", ""), notes,
                                    row.get("race") or "", row.get("role") or "",
                                    ref_portraits.gender_from_text(notes))
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


async def _yield_to_interactive() -> bool:
    """Stand down while a user-triggered generation is in flight.

    The web app and this batch draw on the same free quota, and a DM clicking
    Generate must not lose that race to a backfill. The app marks its in-flight
    generations (services.ref_portraits.interactive); this loop waits them out.
    The marker ages out, so a crashed request cannot stall the batch forever.
    """
    from services import ref_portraits
    if not ref_portraits.interactive_active():
        return False
    print("   … interactive generation in flight — yielding", flush=True)
    while ref_portraits.interactive_active():
        await asyncio.sleep(5)
    return True


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
            from services import ref_portraits
            for k in ref_kinds:
                rows, missing = select_rows(k, args)
                note = "selected (--force re-does existing art)" if args.force else "need art"
                print(f"{k}: {len(missing)} of {len(rows)} {note} "
                      f"(~{len(missing) * 30 // 60} min at 30s each)")
                for r in missing[:2]:
                    print(f"   e.g. {r['name']}: "
                          f"{ref_portraits.prompt_for(k, r['name'], r.get('subtitle') or '', '')[:150]}…")
            return 0
        marker = Path("static/ref-portraits/.bulk-running")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"pid {__import__('os').getpid()}\n")
        print(f"bulk marker set: {marker} (the app's lazy kicks stand down)")
        try:
            done, failed = await run_reference(ref_kinds, args)
        finally:
            marker.unlink(missing_ok=True)
            print("bulk marker cleared")
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
        await _yield_to_interactive()
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
