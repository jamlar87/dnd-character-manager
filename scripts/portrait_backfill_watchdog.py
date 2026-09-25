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
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ART = REPO / "static" / "ref-portraits"
MARKER = ART / ".bulk-running"
GENERATOR = REPO / "scripts" / "generate_portraits.py"
SCRATCH = Path.home() / ".hermes" / "cache" / "scratch"
LOG = SCRATCH / "portrait-backfill.log"
STATE = SCRATCH / "portrait-backfill.state"
LOG_CAP = 20 * 1024 * 1024          # truncate rather than grow without bound
KINDS = ("npc", "item", "creature")

#: The chain, in order. The constructs pass comes first because that art is *wrong* rather than
#: missing: the prompt used to call every creature a living thing, so a ram came out fleshy. Its
#: report existing is what marks the pass done — the generator writes it only at the end.
CONSTRUCTS_REPORT = SCRATCH / "portrait-constructs-report.json"
CONSTRUCTS_REPORT_TMP = Path("/tmp/portrait_constructs_report.json")   # a run already in flight
BACKFILL_REPORT = SCRATCH / "portrait-backfill-report.json"

#: A live run that has produced nothing for this long is stuck, not slow. The retry backoff can
#: spend 15 minutes on one rate-limited image (60s+ per attempt), so this must clear that by a wide
#: margin. The old watchdog tested liveness only, so a hung run looked healthy for 103 minutes.
STALL_MIN_DEFAULT = 60.0

#: Where a run's output might be. The watchdog's own launches write to LOG; a run started by hand
#: usually writes somewhere else, and checking only LOG declared such a run stalled.
RUN_LOGS = (LOG, Path("/tmp/portrait_run.log"))


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


def last_art_activity() -> float | None:
    """Newest mtime under static/ref-portraits, or None when there is none."""
    newest = None
    for path in ART.rglob("*.webp"):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def last_activity() -> float | None:
    """The newest evidence of *work*: a rewritten portrait, or a request to the provider.

    The log's plain mtime is deliberately NOT used. It was, and it made the stall test useless: a
    run polling a queue it will wait an hour in writes a status line every 6 seconds, so "progress 0
    minutes ago" stayed true while nothing was produced for 67 minutes.

    Requests are the right signal because they separate working-from-waiting, and because they
    catch the failure this watchdog exists for: the run that hung on a vanished pipe made no
    requests at all, while one queued at position 215 keeps making them.
    """
    stamps = [s for s in (last_art_activity(),) if s]
    newest_request = None
    for path in RUN_LOGS:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for match in re.finditer(
                r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[INFO\] HTTP Request", text, re.M):
            try:
                when = datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            newest_request = when if newest_request is None else max(newest_request, when)
    if newest_request is not None:
        stamps.append(newest_request)
    return max(stamps) if stamps else None


def run_age_minutes() -> float | None:
    """Minutes since the current run started — the marker is written at launch."""
    try:
        return (time.time() - MARKER.stat().st_mtime) / 60
    except OSError:
        return None


def stall_minutes() -> float | None:
    """Minutes since the last sign of work, or None when there is no evidence either way."""
    last = last_activity()
    return None if last is None else (time.time() - last) / 60


def stop_runs(pids: list[int]) -> list[int]:
    """Signal each pid directly, by explicit pid.

    The marker holds the generator's own pid and the watchdog starts the generator directly, so
    there is no wrapper shell in between. That matters: killing a *wrapper* once left the python
    orphaned, still running pre-fix code and competing for the same image quota. Never signal
    ourselves.
    """
    stopped = []
    for pid in pids:
        if pid and pid != os.getpid() and alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                stopped.append(pid)
            except OSError:
                continue
    if stopped:
        time.sleep(5)
    for pid in stopped:
        if alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
    return stopped


def chain_step() -> tuple[str, list[str], Path]:
    """(what, argv-tail, report path) for the next step of the chain.

    Constructs first, because that art is wrong rather than missing. The pass is done when its report
    exists — the generator writes one only after the whole pass finishes — so a restart resumes the
    right step instead of redoing finished work.
    """
    if CONSTRUCTS_REPORT.exists() or CONSTRUCTS_REPORT_TMP.exists():
        return "remaining backfill", [], BACKFILL_REPORT
    return "constructs pass", ["--constructs", "--force"], CONSTRUCTS_REPORT


def announced_complete() -> bool:
    try:
        return STATE.read_text().strip() == "complete"
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, never start")
    ap.add_argument("--verbose", action="store_true", help="print the full report")
    ap.add_argument("--stall-min", type=float, default=STALL_MIN_DEFAULT,
                    help=f"treat a live run with no new art or log output for this many "
                         f"minutes as stuck (default {STALL_MIN_DEFAULT:.0f})")
    args = ap.parse_args()

    live = running_generators()
    if live:
        age = stall_minutes()
        started = run_age_minutes()
        if started is not None and started < args.stall_min:
            # A run that has only just started has had no chance to produce anything yet. Judging it
            # now killed a healthy three-minute-old run: the app takes ~20s to import before its
            # first request, and a rate-limited first image can spend minutes in backoff.
            say(f"bulk run alive (pid {marker_pid() or '?'}), started {started:.0f} min ago — "
                f"warming up, nothing to do", args.verbose or args.dry_run)
            return 0
        if age is None or age < args.stall_min:
            where = f", last progress {age:.0f} min ago" if age is not None else " (no progress data)"
            say(f"bulk run alive (pid {marker_pid() or '?'}){where} — nothing to do",
                args.verbose or args.dry_run)
            return 0
        pids = {marker_pid() or 0}
        pids |= {int(ln.split()[0]) for ln in live if ln.split() and ln.split()[0].isdigit()}
        if args.dry_run:
            say(f"stalled {age:.0f} min — would stop {sorted(p for p in pids if p)} and restart",
                notify=True)
            return 0
        stopped = stop_runs(sorted(p for p in pids if p))
        say(f"⚠️ portrait backfill stalled ({age:.0f} min without a new image or log line) — "
            f"stopped {stopped}, restarting", notify=True)
        time.sleep(3)

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
    # The next step of the chain, never the default: the constructs pass must keep its place, and
    # --delay 6 is the pacing that drew 429s. A run started here IS the python — no wrapper shell —
    # so the pid recorded in the marker can be signalled directly.
    what, extra, report = chain_step()
    step = [sys.executable, str(GENERATOR), "--kind", "all",
            "--delay", "20", "--timeout", "180", "--retries", "4"] + extra \
        + ["--report", str(report)]

    with open(LOG, "ab") as fh:
        fh.write(f"\n=== watchdog restart {datetime.datetime.now().isoformat()} "
                 f"({what}; missing before start: {missing}) ===\n".encode())
        proc = subprocess.Popen(
            step, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,   # survives this process
        )
    say(f"🎨 Portrait backfill was not running — started the {what} (pid {proc.pid}). "
        f"{summary}, missing {missing}", notify=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
