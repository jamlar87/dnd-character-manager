#!/usr/bin/env python3
"""Audit installed reference portraits against the anatomy their own manual clause demands.

Why: a diffusion prior draws two legs, two arms, two eyes and one head. Any creature whose
clause says otherwise (3 legs, 8 legs, 6 wings, one eye) can silently ship wrong art and no
test notices, because the render succeeded. This tool re-reads the installed image with the
local VLM and scores the count against the clause, so wrong art becomes a work list.

    python scripts/creature_art_audit.py scope        # who is risky (writes risky.json)
    python scripts/creature_art_audit.py run          # count them, append jsonl per image
    python scripts/creature_art_audit.py report       # markdown ranked by score

`run` appends one line per portrait, so a timeout or a dead backend loses nothing and a
re-run skips names already scored. Mismatch is triage, not proof — the VLM still undercounts
crowded limbs, so eyeball the top of the list before regenerating anything.
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

OUT = Path(os.environ.get("AUDIT_DIR", ROOT / "art_audit"))
KEY = {"legs", "arms", "eyes", "heads", "wings", "tentacles", "eyestalks", "tails"}
# Counts a diffusion prior already draws without being asked. Anything else in the clause is
# exactly where wrong art ships unnoticed, so that is the audit scope. Four legs matters: a
# dragon's clause says "four legs" and the model gets that right, so it is not a risk.
PRIOR = {"legs": {2, 4}, "arms": {2}, "eyes": {2}, "heads": {1}, "wings": {0, 2},
         "tentacles": {0}, "eyestalks": {0}, "tails": {1}}


def scope() -> None:
    """Creatures whose clause asks for something a diffusion prior will not guess."""
    from services import ref_portraits as rp
    from services import topology_verify as tv

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, clause in rp.creature_clauses().items():
        expect = tv.expected_from_clause(clause)
        if not expect:
            continue
        exotic = any(v not in PRIOR.get(k, {0}) for k, v in expect.items() if k in KEY)
        if not exotic:
            continue
        path = rp.path_for_existing("creature", name)
        if not path:
            continue
        rows.append({"name": name, "expect": expect, "path": str(path), "clause": clause[:200]})
    (OUT / "risky.json").write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} risky portraits -> {OUT / 'risky.json'}")


async def _score_all(local_only: bool) -> None:
    from services import topology_verify as tv

    if local_only:
        tv.VLM_ORDER[:] = ["ollama"]     # the cloud fallback invents counts; a gate wants the local read
    rows = json.loads((OUT / "risky.json").read_text())
    log = OUT / "results.jsonl"
    done = {json.loads(l)["name"] for l in log.read_text().splitlines()} if log.exists() else set()
    todo = [r for r in rows if r["name"] not in done]
    print(f"{len(rows)} risky, {len(done)} scored, {len(todo)} to do", flush=True)
    with log.open("a") as fh:
        for i, r in enumerate(todo, 1):
            expect = {k: int(v) for k, v in r["expect"].items()}
            try:
                got, note = await tv.count_in_image(Path(r["path"]), expect, [])
            except Exception as e:                                   # one bad image must not end the run
                got, note = None, f"error: {e}"
            rec = {"name": r["name"], "expect": expect, "got": got, "note": note,
                   "score": tv.score(got or {}, expect) if got else None,
                   "clause": r["clause"], "path": r["path"]}
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{i}/{len(todo)} {r['name']:<28} expect={expect} got={got}", flush=True)


def report() -> None:
    from services.topology_verify import score

    log = OUT / "results.jsonl"
    recs = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    for r in recs:
        r["score"] = score(r["got"], r["expect"]) if r["got"] else None
    ok = [r for r in recs if r["score"] is not None and r["score"] >= 8]
    bad = [r for r in recs if r["score"] is not None and r["score"] < 8]
    dead = [r for r in recs if r["score"] is None]
    bad.sort(key=lambda r: r["score"])
    lines = ["# Creature portraits vs their own anatomy clause", "",
             f"{len(recs)} scored · {len(ok)} within tolerance · {len(bad)} off · {len(dead)} unreadable",
             "", "Mismatch is triage: the VLM undercounts crowded limbs, so confirm visually "
             "before regenerating.", "", "## Off", ""]
    for r in bad:
        lines.append(f"- **{r['score']:.0f}** {r['name']} — want {r['expect']} got {r['got']}")
    lines += ["", "## Unreadable", ""] + [f"- {r['name']}" for r in dead]
    dst = OUT / "report.md"
    dst.write_text("\n".join(lines) + "\n")
    print(f"{len(ok)} ok / {len(bad)} off / {len(dead)} unreadable -> {dst}")
    for r in bad[:20]:
        print(f"  {r['score']:>5.0f} {r['name']:<26} want={r['expect']} got={r['got']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["scope", "run", "report"])
    ap.add_argument("--remote", action="store_true", help="allow the cloud VLM fallback (invents counts)")
    a = ap.parse_args()
    if a.cmd == "scope":
        scope()
    elif a.cmd == "run":
        asyncio.run(_score_all(local_only=not a.remote))
    else:
        report()


if __name__ == "__main__":
    main()
