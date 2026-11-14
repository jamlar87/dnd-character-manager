"""Contracts for the ES-module page structure.

campaign_detail.html was the last page carrying a large inline <script> (105
lines, 17 handlers). Inline script is exactly what a CSP without
'unsafe-inline' forbids, so the page now loads one type="module" entry point
that publishes its handlers through Actions.provide().

These tests keep that shape: no inline script, a module entry point, every
data-action on the page published by the module, and a dispatcher that really
resolves module-registered handlers (checked in plain node, no DOM library).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "templates" / "campaign_detail.html"
MODULE = REPO / "static" / "pages" / "campaign-detail.js"
EVENTS = REPO / "static" / "ui" / "events.js"


def _text(path):
    return Path(path).read_text()


def test_the_base_layout_loads_its_shared_behaviour_from_files():
    layout = (REPO / "templates" / "layout.html").read_text()
    inline = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", layout)
    assert not inline, "layout.html must not carry inline <script>: %s" % inline
    for asset in ("ui/nav-filter.js", "ui/wakelock.js"):
        assert "/static/" + asset in layout, "%s must be loaded by the layout" % asset
        assert (REPO / "static" / asset).exists()


def test_the_page_has_no_inline_script_left():
    html = _text(TEMPLATE)
    inline = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", html)
    assert not inline, "inline <script> blocks a CSP without 'unsafe-inline': %s" % inline
    assert "type=\"module\"" in html, "the page must load its behaviour as a module"
    assert "/static/pages/campaign-detail.js" in html


def test_the_page_hands_its_data_over_as_json_not_as_jinja_interpolation():
    """A module cannot read Jinja; a JSON block keeps the values out of JS source."""
    html = _text(TEMPLATE)
    assert 'id="campaign-detail-config"' in html
    assert "| tojson" in html.split('id="campaign-detail-config"')[1][:400]


def _js_definitions():
    """Every name a page could reach on window, scanned permissively."""
    names = set()
    for path in (REPO / "static").rglob("*.js"):
        src = path.read_text(errors="ignore")
        for pattern in (
            r"(?:async\s+)?function\s+([A-Za-z0-9_]+)",
            r"window\.([A-Za-z0-9_]+)\s*=",
            r"^\s*([A-Za-z0-9_]+)\s*:\s*(?:async\s*)?function",
        ):
            names |= set(re.findall(pattern, src, re.M))
    return names


def test_every_data_action_on_the_page_is_published_by_the_module():
    """Page-local handlers come from the module; shared ones stay in dm/actions.js."""
    html = _text(TEMPLATE)
    module = _text(MODULE)
    actions = set(re.findall(r'data-action="([A-Za-z0-9_]+)"', html))
    published = re.search(r"Actions\.provide\(\{(.*?)\}\);", module, re.S)
    assert published, "the module must publish its handlers with Actions.provide()"
    names = {n.strip() for n in published.group(1).split(",") if n.strip()}
    shared = _js_definitions()
    missing = sorted(a for a in actions if a not in names and a not in shared)
    assert not missing, "data-action with no handler anywhere: %s" % missing
    # the module must not silently re-implement a shared helper
    assert not (names - actions), "module publishes handlers the page never uses: %s" % sorted(names - actions)


def test_the_module_uses_the_shared_api_wrapper():
    """Raw fetch in a page module re-decides CSRF and error handling every time."""
    module = _text(MODULE)
    assert "window.api(" in module
    assert not re.search(r"(?<![\w.$])fetch\(", module), "use api(), not raw fetch()"


def test_actions_provide_is_the_documented_entry_point():
    src = _text(EVENTS)
    assert "function provide(map)" in src
    assert "provide: provide" in src, "Actions.provide must be part of the public API"
    # module handlers outrank window globals so a page can own a shared name
    assert "HANDLERS[name]" in src


def test_the_module_is_valid_es_syntax(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    tmp = tmp_path / "campaign-detail.mjs"
    tmp.write_text(_text(MODULE))
    res = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_the_dispatcher_resolves_module_handlers(tmp_path):
    """provide() must be reachable before any click, without window globals."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    harness = tmp_path / "harness.js"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');

const listeners = {};
const document = {
  readyState: 'complete',
  addEventListener: function (type, fn) { listeners[type] = fn; },
};
const window = { document: document };
const ctx = { window: window, document: document, console: console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);

const calls = [];
// a window global of the same name must lose to the module registration
window.pageHandler = function () { calls.push(['window version']); };
ctx.window.Actions.provide({
  pageHandler: function (el, ev) { calls.push(['module version', el.dataset.id]); },
  notAFunction: 'nope',
});

function el(action, dataset) {
  return {
    dataset: dataset || {},
    getAttribute: function (name) { return name === 'data-action' ? action : null; },
    hasAttribute: function (name) { return name === 'data-action'; },
  };
}
listeners.click({ target: { closest: function () { return el('pageHandler', {id: '9'}); } } });
listeners.click({ target: { closest: function () { return el('neverProvided'); } } });

if (typeof ctx.window.Actions.handler('pageHandler') !== 'function') {
  throw new Error('Actions.handler() did not resolve the module handler');
}
console.log(JSON.stringify(calls));
"""
    )
    res = subprocess.run(
        [node, str(harness), str(EVENTS)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    calls = json.loads(res.stdout.strip().splitlines()[-1])
    assert calls == [["module version", "9"]], calls

def test_the_page_renders_its_config_and_module(client, seeded_db, auth_headers):
    """End-to-end: the real template produces parseable config and a module tag."""
    import sqlite3

    db = sqlite3.connect(seeded_db["db_path"])
    cur = db.execute(
        "INSERT INTO dm_campaigns (user_id, name, party_level, party_size) VALUES (?, ?, ?, ?)",
        (1, "Module test campaign", 5, 4),
    )
    campaign_id = cur.lastrowid
    db.commit()
    db.close()

    page = client.get(f"/campaign/{campaign_id}", headers=auth_headers)
    assert page.status_code == 200, page.text
    html = page.text

    # layout.html used to ship two inline blocks (nav filter, wake lock); this
    # asserts the whole rendered page — base plus page — is inline-script free.
    inline = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", html)
    assert not inline, "rendered page still carries inline <script>: %s" % inline
    assert '/static/pages/campaign-detail.js' in html

    blob = re.search(
        r'<script type="application/json" id="campaign-detail-config">(.*?)</script>',
        html, re.S,
    )
    assert blob, "config JSON block missing from the rendered page"
    cfg = json.loads(blob.group(1))
    assert cfg["id"] == campaign_id
    assert cfg["name"] == "Module test campaign"
    assert cfg["party_level"] == 5
