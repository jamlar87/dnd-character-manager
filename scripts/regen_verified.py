"""Resume-safe driver for generate_verified over an explicit name list.

verify_portraits.py takes names as argv, which cannot express "Spider Drake" (argparse splits it)
and re-renders finished creatures on a rerun. This driver reads the list from a file and skips any
name already recorded in the results jsonl, so a long batch can be re-invoked after a timeout.

    python regen_run.py names_eyes.txt regen_eyes.jsonl [--attempts 3] [--limit 12]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.topology_verify import expected_from_clause, generate_verified  # noqa: E402
from services import ref_portraits as rp  # noqa: E402

#: cli name -> ref_portraits KINDS key. generate_verified only reads a clause for kind="creature";
#: passing anything else silently skips every name ("no countable anatomy in clause").
KIND = {"creature": "creature", "portrait": "creature", "npc": "npc", "item": "item"}


def done_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("name"):
            out.add(rec["name"])
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names_file")
    ap.add_argument("out_jsonl")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--kind", default="creature")
    ap.add_argument("--backup-dir", default="art_audit/art_backup")
    ap.add_argument("--limit", type=int, default=10_000)
    a = ap.parse_args()
    kind = KIND.get(a.kind, a.kind)

    names = [n.strip() for n in Path(a.names_file).read_text().splitlines() if n.strip()]
    out = Path(a.out_jsonl)
    seen = done_names(out)
    todo = [n for n in names if n not in seen][: a.limit]
    print(f"todo={len(todo)} already={len(seen)}", flush=True)

    # ref-portraits/ is gitignored, so a bad install has no VCS copy to fall back on. Snapshot the
    # live file first; `restore` puts it back.
    bdir = Path(a.backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)

    for i, name in enumerate(todo, 1):
        t0 = time.time()
        try:
            live = rp.path_for(kind, name)
            if live.exists() and not (bdir / live.name).exists():
                shutil.copy2(live, bdir / live.name)
            path, report = await generate_verified(kind, name, attempts=a.attempts,
                                                   subtitle="", snippet="")
            rec = {
                "name": name,
                "installed": report.get("installed"),
                "why": report.get("why"),
                "baseline": report.get("baseline"),
                "attempts": [{"counts": x.get("counts"), "score": x.get("score"), "error": x.get("error")}
                             for x in report.get("attempts", [])],
                "secs": round(time.time() - t0),
            }
        except Exception as e:  # noqa: BLE001 — one bad record must not end a 90-creature batch
            rec = {"name": name, "error": f"{type(e).__name__}: {e}", "secs": round(time.time() - t0)}
        with out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        tag = "INSTALL" if rec.get("installed") else ("ERR" if rec.get("error") else "keep")
        print(f"{i}/{len(todo)} {tag:7s} {name:34s} {rec.get('installed') or rec.get('why') or rec.get('error')}", flush=True)
    print("FINISHED", flush=True)
    return 0


sys.exit(asyncio.run(main()))
