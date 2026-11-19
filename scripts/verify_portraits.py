"""Resample-and-verify portraits for creatures whose clause states exotic anatomy.

    python scripts/verify_portraits.py --hard                  # curated exotic set
    python scripts/verify_portraits.py Xorn Sleipnir Drider    # named records
    python scripts/verify_portraits.py --all-verifiable        # every clause with a count

Each creature is rolled N times, the vision model counts the parts, and the best match is installed.
Progress goes to stdout one JSON line per creature so a long run can be tailed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services import ref_portraits                      # noqa: E402
from services.topology_verify import expected_from_clause, generate_verified  # noqa: E402

#: Creatures audited by eye on 2026-11-18 as rendering the wrong topology. Not a re-derivation of
#: the clause — these are the ones a human looked at and said "that is not the monster".
HARD = [
    "Xorn", "Sleipnir", "Demogorgon", "Drider", "Duodrone", "Tlincalli", "Retriever",
    "Giant Fly", "Froghemoth", "Spectator", "Chimera", "Azi Dahaka", "Kraken",
]


def verifiable() -> list[str]:
    return [n for n, c in ref_portraits.creature_clauses().items() if expected_from_clause(c)]


async def run(names, attempts: int, kind: str) -> int:
    ok = bad = skipped = 0
    for i, name in enumerate(names, 1):
        try:
            path, report = await generate_verified(kind, name, attempts=attempts)
        except Exception as e:                          # noqa: BLE001 — one bad record must not end the run
            print(json.dumps({"n": f"{i}/{len(names)}", "name": name, "error": f"{type(e).__name__}: {e}"}), flush=True)
            bad += 1
            continue
        report["n"] = f"{i}/{len(names)}"
        print(json.dumps(report), flush=True)
        if report.get("installed"):
            ok += 1
        elif report.get("skipped"):
            skipped += 1
        else:
            bad += 1
    print(json.dumps({"FINISHED": True, "ok": ok, "bad": bad, "skipped": skipped}), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--hard", action="store_true", help="curated set of known-wrong topologies")
    ap.add_argument("--all-verifiable", action="store_true", help="every creature whose clause counts anatomy")
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--kind", default="creature")
    a = ap.parse_args()

    names = list(a.names)
    if a.hard:
        names += [n for n in HARD if n not in names]
    if a.all_verifiable:
        names += [n for n in verifiable() if n not in names]
    if not names:
        ap.error("give names, --hard or --all-verifiable")
    return asyncio.run(run(names, a.attempts, a.kind))


if __name__ == "__main__":
    raise SystemExit(main())
