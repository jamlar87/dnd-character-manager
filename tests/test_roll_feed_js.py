"""The roll feed's client asset, and the dice sink it depends on.

Both assets carry behaviour, so the contract is proven by RUNNING them under node (same
pattern as tests/test_dice_roller.py) rather than by grepping for strings. The two traps
worth a test:

* the sink must fire from inside dice.js's own roll(), because enhance()'s click handler
  calls the closure-local function — patching the exported DiceRoller.roll would look
  correct and silently publish nothing for every click-to-roll in the app;
* a row's total is the server's number. If the client recomputed it, the feed would show a
  different result from the one the log recorded.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DICE = REPO / "static" / "dice.js"
FEED = REPO / "static" / "rollfeed.js"
FEED_CSS = REPO / "static" / "rollfeed.css"
LAYOUT = REPO / "templates" / "layout.html"

NODE = shutil.which("node")


def _run_node(script: str) -> dict:
    """Load the dice asset and the feed asset in node with stubbed window/document."""
    bootstrap = (
        "global.window = {};\n"
        "const _nodes = [];\n"
        "function _mk(tag) {\n"
        "  const n = { tagName: tag, children: [], className: '', textContent: '',\n"
        "              appendChild(c) { this.children.push(c); return c; },\n"
        "              classList: { add() {}, remove() {}, contains() { return false; } } };\n"
        "  _nodes.push(n);\n"
        "  return n;\n"
        "}\n"
        "global.document = { readyState: 'complete', addEventListener: () => {},\n"
        "                    cookie: '', createElement: _mk, getElementById: () => null };\n"
        "global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });\n"
        f"require({json.dumps(str(DICE))});\n"
        f"require({json.dumps(str(FEED))});\n"
        "const D = global.window.DiceRoller;\n"
        "const R = global.window.RollFeed;\n"
        "const out = {};\n"
        f"{script}\n"
        "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run([NODE, "-e", bootstrap], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_sink_sees_a_roll_made_through_the_local_roll_function():
    """The point of the sink: enhance()'s click handler must reach it."""
    out = _run_node(
        "const seen = [];\n"
        "D.setSink(r => seen.push(r));\n"
        "const r = D.roll('2d6+3');\n"
        "out.count = seen.length;\n"
        "out.totalMatches = seen[0].total === r.total;\n"
        "out.facesMatch = JSON.stringify(seen[0].rolls) === JSON.stringify(r.rolls);\n"
        "out.expr = seen[0].expr;\n"
        "out.setSinkReturns = D.setSink(null);\n"
        "D.roll('1d20');\n"
        "out.afterDetach = seen.length;\n"
    )
    assert out["count"] == 1, "the sink must fire exactly once per roll"
    assert out["totalMatches"] is True
    assert out["facesMatch"] is True
    assert out["expr"] == "2d6+3"
    assert out["setSinkReturns"] == 0
    assert out["afterDetach"] == 1, "a detached sink must not keep receiving rolls"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_throwing_sink_never_loses_the_roll():
    """A dead feed must not stop the dice: the roll still returns its result."""
    out = _run_node(
        "D.setSink(() => { throw new Error('feed is down'); });\n"
        "const r = D.roll('1d20+5');\n"
        "out.total = r.total;\n"
        "out.returned = !!r;\n"
    )
    assert out["returned"] is True
    assert 6 <= out["total"] <= 25


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_parse_and_roll_are_unchanged_without_a_sink():
    """The existing contract is not allowed to shift because the sink exists."""
    out = _run_node(
        "out.parsed = D.parse('1d20');\n"
        "out.noSinkRoll = D.roll('1d6').total >= 1;\n"
    )
    assert out["parsed"] == {"count": 1, "sides": 20, "mod": 0}
    assert out["noSinkRoll"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_feed_keeps_the_servers_total_and_orders_newest_first():
    out = _run_node(
        "R.init({ listEl: null });\n"
        "R.onEvent({ data: JSON.stringify({ kind:'roll', id: 1, expression:'1d20',\n"
        "  results:[7], total: 7, who:'dm' }) });\n"
        "R.onEvent({ data: JSON.stringify({ kind:'roll', id: 2, expression:'1d20+5',\n"
        "  results:[17], total: 22, who:'player', character_name:'Garim', label:'Longsword' }) });\n"
        "const rows = R.feedRows();\n"
        "out.first = rows[0].id;\n"
        "out.total = rows[0].total;\n"
        "out.who = rows[0].character_name;\n"
        "out.count = rows.length;\n"
        "out.dupe = R.onEvent({ data: JSON.stringify({ kind:'roll', id: 2, total: 22 }) });\n"
        "out.afterDupe = R.feedRows().length;\n"
        "out.garbage = R.onEvent({ data: 'not json' });\n"
    )
    assert out["first"] == 2, "newest roll first"
    assert out["total"] == 22, "the total is rendered as the server sent it"
    assert out["who"] == "Garim"
    assert out["count"] == 2
    assert out["dupe"] is False and out["afterDupe"] == 2, "a replay overlap is not a new row"
    assert out["garbage"] is False


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_feed_posts_the_shape_the_route_validates():
    """expression + the actual dice, and nothing that would be a lie about the roll."""
    out = _run_node(
        "global._posts = [];\n"
        "global.fetch = (url, opts) => { _posts.push({ url, body: JSON.parse(opts.body),\n"
        "  headers: opts.headers }); return Promise.resolve({ ok: true,\n"
        "  json: () => Promise.resolve({ ok: true }) }); };\n"
        "R.init({ rollUrl: '/api/campaign/9/roll', characterId: 78, label: 'Attack',\n"
        "         publish: false });\n"
        "R.post({ expr: '2d6+3', rolls: [5, 2], mod: 3, total: 10, text: 'x' }, {});\n"
        "global._flush = () => {\n"
        "  out.url = _posts[0].url;\n"
        "  out.body = _posts[0].body;\n"
        "};\n"
        "_flush();\n"
    )
    assert out["url"] == "/api/campaign/9/roll"
    assert out["body"]["expression"] == "2d6+3"
    assert out["body"]["results"] == [5, 2]
    assert out["body"]["character_id"] == 78
    assert out["body"]["label"] == "Attack"
    # the client never sends a total: the route computes it from these dice
    assert "total" not in out["body"]


def test_the_assets_are_wired_through_static_asset_version():
    layout = LAYOUT.read_text()
    for asset in ("rollfeed.js", "rollfeed.css"):
        assert f"static_asset_version('{asset}')" in layout, (
            f"{asset} must load through static_asset_version — a hand-pinned ?v= keeps serving "
            "the stale asset from the browser and from Cloudflare")
    assert FEED_CSS.exists() and FEED.exists()


def test_the_feed_never_builds_markup_from_data():
    """A label is user text: it goes in through textContent or not at all.

    Comments are stripped first — a naive grep over the raw file fails on a comment that
    merely names the danger, which is how this test first failed.
    """
    import re

    src = FEED.read_text()
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)

    assert "innerHTML" not in code, "build rows with textContent, never innerHTML"
    assert "eval(" not in code, "no eval"
    assert "textContent" in code


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_non_roll_events_reach_hooks_and_not_the_feed():
    """The sheet's HP rides the same stream; it must not appear as a roll row."""
    out = _run_node(
        "R.init({ listEl: null });\n"
        "const seen = [];\n"
        "R.on('hp', p => seen.push(p.hp_current));\n"
        "R.onEvent({ data: JSON.stringify({ kind:'hp', character_id: 5, hp_current: 4 }) });\n"
        "out.hookCalls = seen.length;\n"
        "out.hpValue = seen[0];\n"
        "out.feedRows = R.feedRows().length;\n"
        "R.onEvent({ data: JSON.stringify({ kind:'roll', id: 1, total: 9 }) });\n"
        "out.feedAfterRoll = R.feedRows().length;\n"
        "out.hookStillOnce = seen.length;\n"
    )
    assert out["hookCalls"] == 1 and out["hpValue"] == 4
    assert out["feedRows"] == 0, "an HP event is not a feed row"
    assert out["feedAfterRoll"] == 1
    assert out["hookStillOnce"] == 1, "a roll must not be dispatched as an hp event"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_one_bad_listener_does_not_take_the_stream_down():
    out = _run_node(
        "R.init({ listEl: null });\n"
        "let second = 0;\n"
        "R.on('hp', () => { throw new Error('this listener is broken'); });\n"
        "R.on('hp', () => { second += 1; });\n"
        "out.returned = R.onEvent({ data: JSON.stringify({ kind:'hp', hp_current: 1 }) });\n"
        "out.secondRan = second;\n"
        "out.offWorks = (R.off('hp'), R.onEvent({ data: JSON.stringify({ kind:'hp' }) }), second);\n"
    )
    assert out["returned"] is False
    assert out["secondRan"] == 1, "later listeners must still run"
    assert out["offWorks"] == 1, "off() must detach the listener"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_connection_state_is_readable_and_starts_closed():
    out = _run_node(
        "R.init({ listEl: null });\n"
        "out.before = R.isConnected();\n"
    )
    assert out["before"] is False, "no EventSource in node: not connected"
