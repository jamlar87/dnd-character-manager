"""The watchdog must judge progress, not just liveness, and must resume the right chain step.

The old version checked only that a pid existed, so a run hung for 103 minutes looked healthy: it
sat in do_epoll_wait with 7:45 of CPU across 32 hours, writing nothing. These assertions pin the
signal it should have used, that it can never signal itself, and that a restart picks up the chain
where it left off rather than reverting to the default command.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import sys
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "scripts"))

import portrait_backfill_watchdog as w  # noqa: E402


def test_progress_uses_the_newest_of_art_and_log(monkeypatch, tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.webp").write_bytes(b"x")
    log = tmp_path / "run.log"
    log.write_text("")
    monkeypatch.setattr(w, "ART", art)
    monkeypatch.setattr(w, "RUN_LOGS", (log,))

    now = datetime.datetime.now()
    os.utime(art / "a.webp", (time.time() - 7200, time.time() - 7200))   # two hours ago
    log.write_text(f"{now:%Y-%m-%d %H:%M:%S} [INFO] HTTP Request: GET https://example/status\n")
    assert w.stall_minutes() < 1, "a fresh provider request is work even when no art changed"

    log.write_text(f"{now - datetime.timedelta(hours=2):%Y-%m-%d %H:%M:%S} [INFO] HTTP Request: GET x\n")
    assert w.stall_minutes() > 100, "neither source moving means the run is stalled"


def test_polling_alone_does_not_count_as_progress(monkeypatch, tmp_path):
    """The bug this replaced: the log's mtime moves every few seconds while polling an idle queue,
    so a run that produced nothing for 67 minutes reported "last progress 0 min ago"."""
    art = tmp_path / "art"
    art.mkdir()
    (art / "a.webp").write_bytes(b"x")
    log = tmp_path / "run.log"
    monkeypatch.setattr(w, "ART", art)
    monkeypatch.setattr(w, "RUN_LOGS", (log,))

    os.utime(art / "a.webp", (time.time() - 7200, time.time() - 7200))
    log.write_text(f"{datetime.datetime.now() - datetime.timedelta(hours=2):%Y-%m-%d %H:%M:%S}"
                   " [INFO] HTTP Request: GET x\n")
    os.utime(log, None)          # the file was just touched, but it holds no recent work
    assert w.stall_minutes() > 100, "a freshly-touched log with stale content is not progress"


def test_no_evidence_is_not_a_stall(monkeypatch, tmp_path):
    """An empty or missing source is unknown, not stalled — restarting on it would thrash."""
    monkeypatch.setattr(w, "ART", tmp_path / "missing")
    monkeypatch.setattr(w, "RUN_LOGS", (tmp_path / "missing.log",))
    assert w.stall_minutes() is None


def test_a_fresh_run_is_not_judged(monkeypatch, tmp_path):
    """A just-started run has had no chance to produce anything yet.

    Judging one by output alone killed a healthy three-minute-old run: the app takes ~20s to import
    before its first request, and a rate-limited first image can sit in backoff for minutes.
    """
    marker = tmp_path / ".bulk-running"
    marker.write_text("pid 123\n")
    monkeypatch.setattr(w, "MARKER", marker)
    assert w.run_age_minutes() < 1

    os.utime(marker, (time.time() - 7200, time.time() - 7200))
    assert w.run_age_minutes() > 100

    monkeypatch.setattr(w, "MARKER", tmp_path / "absent")
    assert w.run_age_minutes() is None


def test_stop_runs_never_signals_itself():
    assert w.stop_runs([os.getpid()]) == [], "the watchdog must never kill itself"


def test_stop_runs_ignores_dead_and_empty_pids():
    assert w.stop_runs([0, 999999999]) == []


def test_chain_starts_with_the_constructs_then_the_backfill(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "CONSTRUCTS_REPORT", tmp_path / "constructs.json")
    monkeypatch.setattr(w, "CONSTRUCTS_REPORT_TMP", tmp_path / "legacy-constructs.json")
    monkeypatch.setattr(w, "BACKFILL_REPORT", tmp_path / "backfill.json")

    what, extra, report = w.chain_step()
    assert what == "constructs pass"
    assert "--force" in extra and "--constructs" in extra, (
        "a restart before the constructs finish must keep the redo flags — the old watchdog "
        "restarted the default command and dropped the constructs from the queue")
    assert report == tmp_path / "constructs.json"

    (tmp_path / "constructs.json").write_text("{}")
    what, extra, report = w.chain_step()
    assert what == "remaining backfill"
    assert "--force" not in extra, "the backfill must not re-generate finished art"
    assert report == tmp_path / "backfill.json"


@pytest.mark.parametrize("attr", ["CONSTRUCTS_REPORT", "CONSTRUCTS_REPORT_TMP"])
def test_either_report_path_marks_the_constructs_done(monkeypatch, tmp_path, attr):
    """The run in flight writes to /tmp; a watchdog-started one writes to scratch. Both count."""
    monkeypatch.setattr(w, "CONSTRUCTS_REPORT", tmp_path / "a.json")
    monkeypatch.setattr(w, "CONSTRUCTS_REPORT_TMP", tmp_path / "b.json")
    monkeypatch.setattr(w, "BACKFILL_REPORT", tmp_path / "c.json")
    getattr(w, attr).write_text("{}")
    assert w.chain_step()[0] == "remaining backfill"
