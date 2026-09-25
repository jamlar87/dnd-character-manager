"""The portrait-backfill watchdog's output policy.

It is wired to a `no_agent` cron job, so this process's stdout IS the delivered
message: noise here means a Telegram message every 30 minutes. The policy is
therefore load-bearing, and it is what these tests pin:

  * a live bulk run           -> completely silent
  * a dead run, work left     -> exactly one restart line (and a detached process)
  * library complete          -> announced ONCE, silent on every later tick
  * --verbose                 -> always reports (manual use)

Also pinned: the app's data layer prints a page of load chatter at import, which
must never reach stdout (it would arrive glued to a notification).
"""

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "portrait_backfill_watchdog.py"


def load(monkeypatch, tmp_path, **attrs):
    """Fresh module per test, with the marker/state files pointed at tmp_path."""
    spec = importlib.util.spec_from_file_location("wd", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["wd"])
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "MARKER", tmp_path / ".bulk-running")
    monkeypatch.setattr(mod, "STATE", tmp_path / ".state")
    monkeypatch.setattr(mod, "LOG", tmp_path / "backfill.log")
    monkeypatch.setattr(mod, "SCRATCH", tmp_path)
    for k, v in attrs.items():
        monkeypatch.setattr(mod, k, v)
    return mod


def capture(mod, argv=()):
    sys.argv = ["wd", *argv]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue()


class TestSilenceWhileRunning:
    def test_cron_mode_is_silent_when_a_run_is_live(self, monkeypatch, tmp_path):
        mod = load(monkeypatch, tmp_path, running_generators=lambda: ["1234 python3 generate_portraits.py"])
        rc, out = capture(mod)
        assert rc == 0
        assert out == "", f"a live run must produce no cron message, got {out!r}"

    def test_verbose_still_reports_while_running(self, monkeypatch, tmp_path):
        mod = load(monkeypatch, tmp_path, running_generators=lambda: ["1234 python3 generate_portraits.py"],
                   marker_pid=lambda: 1234)
        rc, out = capture(mod, ["--verbose"])
        assert rc == 0 and "nothing to do" in out


class TestRestartPath:
    def _stub(self, monkeypatch, tmp_path, spawned):
        def fake_popen(args, **kwargs):
            spawned["args"] = args
            spawned["kwargs"] = kwargs

            class P:
                pid = 4242
            return P()

        mod = load(monkeypatch, tmp_path,
                   running_generators=lambda: [],
                   missing_counts=lambda: {"npc": (414, 414), "item": (10, 100), "creature": (1, 50)})
        monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
        return mod

    def test_restart_is_announced_and_detached(self, monkeypatch, tmp_path):
        spawned = {}
        mod = self._stub(monkeypatch, tmp_path, spawned)
        rc, out = capture(mod)
        assert rc == 0
        # Assert the RESULT the reader needs, not one phrasing of it: an explicit statement that
        # something was started, plus the pid to act on. The message reads "was not running —
        # started the constructs pass (pid 4242)".
        assert "started" in out and "4242" in out
        assert "missing {'npc': 0, 'item': 90, 'creature': 49}" in out
        # detached, so it outlives the cron run, and it runs the real generator
        assert spawned["kwargs"].get("start_new_session") is True
        assert spawned["kwargs"].get("cwd") == str(REPO)
        assert str(SCRIPT.parent / "generate_portraits.py") in spawned["args"]
        assert "--kind" in spawned["args"] and "all" in spawned["args"]

    def test_no_restart_when_nothing_is_missing(self, monkeypatch, tmp_path):
        spawned = {}
        mod = self._stub(monkeypatch, tmp_path, spawned)
        monkeypatch.setattr(mod, "missing_counts",
                            lambda: {"npc": (414, 414), "item": (100, 100), "creature": (50, 50)})
        rc, out = capture(mod)
        assert rc == 0 and spawned == {}
        assert "complete" in out, "completion is worth one notification"

    def test_completion_is_announced_only_once(self, monkeypatch, tmp_path):
        spawned = {}
        mod = self._stub(monkeypatch, tmp_path, spawned)
        monkeypatch.setattr(mod, "missing_counts",
                            lambda: {"npc": (414, 414), "item": (100, 100), "creature": (50, 50)})
        rc1, out1 = capture(mod)
        rc2, out2 = capture(mod)
        assert "complete" in out1
        assert out2 == "", f"the completion notice must not repeat, got {out2!r}"

    def test_a_dead_runs_marker_is_cleared_on_completion(self, monkeypatch, tmp_path):
        spawned = {}
        mod = self._stub(monkeypatch, tmp_path, spawned)
        monkeypatch.setattr(mod, "missing_counts",
                            lambda: {"npc": (414, 414), "item": (100, 100), "creature": (50, 50)})
        mod.MARKER.write_text("pid 999999\n")
        capture(mod)
        assert not mod.MARKER.exists(), "a stale marker would keep the web app's lazy art off"


class TestNoiseLeak:
    def test_app_import_chatter_never_reaches_stdout(self, monkeypatch, tmp_path):
        """missing_counts() imports the app; its load chatter must go to stderr."""
        mod = load(monkeypatch, tmp_path, running_generators=lambda: [])
        counts = mod.missing_counts()
        assert set(counts) == {"npc", "item", "creature"}
        assert all(h <= t for h, t in counts.values())
