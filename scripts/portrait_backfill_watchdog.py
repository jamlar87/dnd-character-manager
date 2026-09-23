#!/usr/bin/env python3
"""Keep the reference-art backfill alive across a Hermes or host restart.

Why this exists: finishing the art library is ~39h of sequential work on one
shared free quota (items ~10h, creatures ~29h at ~54s an image). If the process
that launched it dies — a Hermes restart, a reboot, a stray kill — the library
silently stops growing and nobody notices until someone opens a page and sees
letter tiles.

Safe to run on a timer: it starts a run ONLY when no run is live AND images are
still missing, so two bulk runs can never race for the same quota.

OUTPUT POLICY (it is wired to a no_agent cron job whose stdout is delivered
verbatim): silent while a run is live, because that is the normal case and a
message every 30 minutes is noise. It speaks only when it starts a run, and once
when the library first completes. `--verbose` restores the full report for a
manual check.

Liveness is the PID the bulk run writes into static/ref-portraits/.bulk-running,
with a pgrep fallback for a run whose marker was lost or overwritten. That marker
is also what makes the web app stand down: with a bulk run live, the
/api/ref-image route stops kicking its own lazy generations.

    python3 scripts/portrait_backfill_watchdog.py --verbose   # manual check
    python3 scripts/portrait_backfill_watchdog.py --dry-run    # report only

Exit codes: 0 = fine (running, restarted, or nothing left), 1 = could not start.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARKER = REPO / "static" / "ref-portraits" / ".bulk-running"
GENERATOR = REPO / "scripts" / "generate_portraits.py"
SCRATCH = Path.home() / ".hermes" / "cache" / "scratch"
LOG = SCRATCH / "portrait-backfill.log"
STATE = SCRATCH / "portrait-backfill.state"
LOG_CAP = 20 * 1024 * 1024          # truncate rather than grow without bound
KINDS = ("npc", "item", "creature")


def say(msg: str, verbose: bool = False, *, notify: bool = False) -> None:
    """Cron contract: routine states are silent; only `notify` sends a message."""
    if verbose or notify:
        print(msg)


def marker_pid() -> int | None:
    """PID the bulk run recorded, or None when the marker is missing/garbled."""
    try:
        text = MARKER.read_text().strip()
    except OSError:
        return None
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else None


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)          # signal 0 = liveness probe, sends nothing
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_generators() -> list[str]:
    """Any live generator, marker or not — the race guard. Never matches itself."""
    try:
        out = subprocess.run(["pgrep", "-af", "generate_portraits[.]py"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    me = str(os.getpid())
    return [ln for ln in out.splitlines() if ln.strip() and f" {me} " not in ln]


def missing_counts() -> dict[str, tuple[int, int]]:
    """kind -> (have, indexed). Uses the app's own index, not a second copy.

    Importing the app's data layer prints a page of load chatter ("+ Spells: 324",
    timings, source validation). On a no_agent cron job this process's stdout IS
    the delivered message, so the chatter is parked on stderr where it cannot
    pollute a notification.
    """
    sys.path.insert(0, str(REPO))
    os.chdir(REPO)
    with contextlib.redirect_stdout(sys.stderr):
        from services import ref_portraits
        from services.entity_search import iter_entities

        out = {}
        for kind in KINDS:
            total = 0
            have = 0
            for row in iter_entities(kind):
                total += 1
                if ref_portraits.have(kind, row["name"]):
                    have += 1
            out[kind] = (have, total)
    return out


def announced_complete() -> bool:
    try:
        return STATE.read_text().strip() == "complete"
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, never start")
    ap.add_argument("--verbose", action="store_true", help="print the full report")
    args = ap.parse_args()

    live = running_generators()
    if live:
        say(f"bulk run alive (pid {marker_pid() or '?'}) — nothing to do",
            args.verbose or args.dry_run)
        return 0

    counts = missing_counts()
    summary = " | ".join(f"{k} {h}/{t}" for k, (h, t) in counts.items())
    missing = {k: t - h for k, (h, t) in counts.items()}

    if not any(missing.values()):
        MARKER.unlink(missing_ok=True)   # a dead run's marker would hold the app back
        if not announced_complete():
            say(f"🎨 Reference-art backfill complete — {summary}", notify=True)
            try:
                STATE.parent.mkdir(parents=True, exist_ok=True)
                STATE.write_text("complete")
            except OSError:
                pass
        else:
            say(f"backfill complete — {summary}", args.verbose)
        return 0

    if args.dry_run:
        say(f"would start the backfill — {summary} (missing {missing})", notify=True)
        return 0

    SCRATCH.mkdir(parents=True, exist_ok=True)
    try:
        if LOG.exists() and LOG.stat().st_size > LOG_CAP:
            LOG.unlink()
    except OSError:
        pass
    with open(LOG, "ab") as fh:
        fh.write(f"\n=== watchdog restart {datetime.datetime.now().isoformat()} "
                 f"(missing before start: {missing}) ===\n".encode())
        proc = subprocess.Popen(
            [sys.executable, str(GENERATOR), "--kind", "all", "--delay", "6"],
            cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,   # survives this process
        )
    say(f"🎨 Portrait backfill was not running — restarted it (pid {proc.pid}). "
        f"{summary}, missing {missing}", notify=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
