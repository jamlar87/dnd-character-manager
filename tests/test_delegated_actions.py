"""Contracts for the delegated data-action layer.

Inline on* attributes are gone from the DM templates. These tests keep them gone
and keep markup, dispatcher and handler registry in sync, because a typo in a
data-action name fails silently in the browser (a console.warn nobody reads).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "static"
CONVERTED = [REPO / "templates" / "dm_tools.html", REPO / "templates" / "campaign_detail.html"]
ACTION_SPECS = STATIC / "dm" / "actions.js"


def _text(path):
    return Path(path).read_text()


def _actions_in(html):
    return sorted(set(re.findall(r'data-action="([A-Za-z0-9_]+)"', html)))


def _all_js_definitions():
    """Every name a page could reach on window, scanned permissively.

    Permissive on purpose: the point is to catch a rename or a typo, not to
    re-implement JS scoping.
    """
    names = set()
    for path in STATIC.rglob("*.js"):
        src = path.read_text(errors="ignore")
        for pattern in (
            r"(?:async\s+)?function\s+([A-Za-z0-9_]+)",
            r"window\.([A-Za-z0-9_]+)\s*=",
            r"(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=",
            r"^\s*([A-Za-z0-9_]+)\s*:\s*(?:async\s*)?function",
        ):
            names |= set(re.findall(pattern, src, re.M))
    return names


def _inline_template_functions():
    names = set()
    for path in CONVERTED:
        names |= set(re.findall(r"function\s+([A-Za-z0-9_]+)", _text(path)))
    return names


@pytest.mark.parametrize("path", CONVERTED, ids=lambda p: Path(p).name)
def test_converted_templates_carry_no_inline_event_handlers(path):
    html = _text(path)
    found = re.findall(r"\son(?:click|change|input|submit|dragover|drop|keydown|keyup|blur|focus)=", html)
    assert not found, "inline handlers block a CSP without 'unsafe-inline': %s" % found[:5]


@pytest.mark.parametrize("path", CONVERTED, ids=lambda p: Path(p).name)
def test_every_data_action_has_a_handler(path):
    known = _all_js_definitions() | _inline_template_functions()
    missing = [a for a in _actions_in(_text(path)) if a not in known]
    assert not missing, "no handler on window for: %s" % missing


def test_actions_reading_markup_have_an_argument_spec():
    """A data-* attribute on a data-action element must be consumed by a spec.

    Without this, `data-id="{{ x }}"` silently becomes an undefined argument.
    """
    specs = _text(ACTION_SPECS)
    unregistered = set()
    for path in CONVERTED:
        for tag in re.findall(r"<[^>]*data-action=\"[^\"]+\"[^>]*>", _text(path)):
            action = re.search(r'data-action="([^"]+)"', tag).group(1)
            extra = re.findall(r"data-(?!action[\"=])([a-z0-9-]+)=", tag)
            if extra and action not in specs:
                unregistered.add(action)
    assert not unregistered, "these actions read data-* but register no spec: %s" % sorted(unregistered)


def test_the_dispatcher_runs_only_the_closest_action_and_passes_spec_args(tmp_path):
    """Behavioural check of static/ui/events.js in plain node, no DOM library."""
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
window.outer = function () { calls.push(['outer', Array.from(arguments)]); };
window.inner = function () { calls.push(['inner', Array.from(arguments)]); };
window.needsArgs = function () { calls.push(['needsArgs', Array.from(arguments)]); };
window.cancels = function () { calls.push(['cancels', Array.from(arguments)]); };

ctx.window.Actions.register({
  needsArgs: function (el) { return [el.dataset.id, el.dataset.flag === 'true']; },
  cancels: function () { return null; },
});

function el(action, dataset) {
  return {
    dataset: dataset || {},
    getAttribute: function (name) { return name === 'data-action' ? action : null; },
    hasAttribute: function (name) { return name === 'data-action'; },
  };
}

function fire(closestEl) {
  listeners.click({ target: { closest: function () { return closestEl; } } });
}

fire(el('inner'));                       // closest wins, outer must not run
fire(el('outer'));
fire(el('needsArgs', {id: '7', flag: 'true'}));
fire(el('cancels'));
fire(el('missingHandler'));              // must warn, not throw
fire(null);                              // no action ancestor

console.log(JSON.stringify(calls));
"""
    )

    out = subprocess.run(
        [node, str(harness), str(STATIC / "ui" / "events.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    calls = json.loads(out.stdout.strip().splitlines()[-1])
    assert calls == [
        ["inner", []],
        ["outer", []],
        ["needsArgs", ["7", True]],  # markup values arrive as strings, as with inline handlers
    ], calls
    # 'cancels' returns null from its spec, so its handler must not run at all.
    assert all(c[0] != "cancels" for c in calls)
    # 'missingHandler' has no window function: no throw (returncode 0), no call.


def test_the_action_layer_loads_once_and_in_order():
    """layout.html owns the dispatcher; pages only add their own specs.

    Loading events.js per-page would double-register listeners, so the global
    load must stay the single source and every page's scripts block runs after
    it (Jinja puts {% block scripts %} at the end of the body).
    """
    layout = _text(REPO / "templates" / "layout.html")
    dispatcher = layout.find("ui/events.js")
    assert dispatcher != -1, "layout.html must load the dispatcher"
    assert layout.find("ui/shell.js") > dispatcher, "shell.js adapters need window.Actions first"
    block = re.search(r"\{%\s*block\s+scripts\s*%\}", layout)
    assert block, "layout.html has no scripts block"
    assert block.start() > dispatcher, "page scripts must run after the dispatcher loads"

    for path in CONVERTED:
        html = _text(path)
        assert "ui/events.js" not in html, (
            "%s must not reload the global dispatcher" % Path(path).name)
        specs = html.find("dm/actions.js")
        assert specs != -1, "%s must load its action specs" % Path(path).name
        assert re.search(r"\{%\s*block\s+scripts\s*%\}", html), (
            "%s: a script tag outside {% block scripts %} is dropped by Jinja inheritance"
            % Path(path).name)
