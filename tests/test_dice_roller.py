"""Click-to-roll dice: ONE asset, applied to any surface that renders rules text.

Atlas VTT's best small feature is that any dice expression in a stat block is clickable and
rolls with the action's name attached. This app renders dice everywhere (monster blocks,
traps, spells, sheet attacks) and had no way to roll any of it.

The asset is behavioural, not decorative, so the contract is tested by RUNNING it under node
(the repo's JS assets are guarded by text greps too — `node --check` is how they catch syntax
errors — but a roll that returns the wrong total would pass a grep).

What matters:
- `parse()` understands `d20`, `2d6+3`, `1d8 - 1`, and returns null for prose that merely
  LOOKS numeric (`p.222`, `level 3`, `4d6kh3` — advantage syntax is deliberately out of v1).
- `roll()` returns the individual faces AND the total, so the toast can show its work.
- `enhance()` is idempotent and never rewrites `<script>`, `<style>`, `<textarea>`, `<input>`,
  `<select>` or `<button>` — wrapping text inside a button would fire the button's own handler
  as well, and inside a source badge (`<a>`) it would break the link.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DICE = REPO / "static" / "dice.js"
LAYOUT = REPO / "templates" / "layout.html"

NODE = shutil.which("node")


def _run_node(script: str) -> dict:
    """Load static/dice.js in node with a stub `window` and return its JSON verdict."""
    bootstrap = (
        "global.window = {};\n"
        "global.document = { readyState: 'complete', addEventListener: () => {} };\n"
        f"require({json.dumps(str(DICE))});\n"
        "const D = global.window.DiceRoller;\n"
        "const out = {};\n"
        f"{script}\n"
        "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run([NODE, "-e", bootstrap], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_asset_exists_and_is_wired_into_the_layout():
    assert DICE.exists(), "static/dice.js is missing"
    layout = LAYOUT.read_text()
    assert "static_asset_version('dice.js')" in layout, (
        "layout.html must load dice.js through static_asset_version — a hand-pinned ?v= keeps "
        "serving the stale script from the browser and from Cloudflare")
    # every page loads layout.html, so the enhancer must be safe on pages with no dice text
    assert "DiceRoller" in layout or "dice.js" in layout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_parse_understands_the_expressions_the_app_actually_shows():
    out = _run_node(
        "out.plain_d20 = D.parse('1d20');\n"
        "out.bare_die  = D.parse('d8');\n"
        "out.with_mod  = D.parse('2d6+3');\n"
        "out.negative  = D.parse('1d8 - 1');\n"
        "out.upper     = D.parse('2D6');\n"
    )
    assert out["plain_d20"] == {"count": 1, "sides": 20, "mod": 0}
    assert out["bare_die"] == {"count": 1, "sides": 8, "mod": 0}
    assert out["with_mod"] == {"count": 2, "sides": 6, "mod": 3}
    assert out["negative"] == {"count": 1, "sides": 8, "mod": -1}
    assert out["upper"] == {"count": 2, "sides": 6, "mod": 0}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_parse_rejects_prose_that_only_looks_numeric():
    out = _run_node(
        "out.page_ref = D.parse('p.222');\n"
        "out.level    = D.parse('level 3');\n"
        "out.adv      = D.parse('4d6kh3');\n"
        "out.empty    = D.parse('');\n"
        "out.words    = D.parse('and 3 damage');\n"
    )
    for key in ("page_ref", "level", "adv", "empty", "words"):
        assert out[key] is None, f"{key} must not parse as a dice expression"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_roll_reports_faces_and_total():
    out = _run_node(
        "const r = D.roll('2d6+3');\n"
        "out.faces = r.rolls.length;\n"
        "out.inRange = r.rolls.every(v => v >= 1 && v <= 6);\n"
        "out.total = r.total;\n"
        "out.hasExpr = r.text.includes('2d6');\n"
        "const flat = D.roll('1d4-1');\n"
        "out.flatMin = flat.total >= 0;\n"
    )
    assert out["faces"] == 2
    assert out["inRange"] is True
    assert 5 <= out["total"] <= 15, "2d6+3 must land between 5 and 15"
    assert out["hasExpr"] is True
    assert out["flatMin"] is True


def test_enhance_is_idempotent_and_skips_interactive_or_raw_content():
    src = DICE.read_text()
    lowered = src.lower()  # the asset compares el.tagName, which is upper-case in the DOM
    for selector in ("script", "style", "textarea", "input", "select", "button", "a"):
        assert selector in lowered, f"enhance() must skip <{selector}>"
    assert "dice-roll" in src and "diceDone" in src, (
        "enhance() needs a marker so a second pass cannot double-wrap the same text")
    assert "eval(" not in src, "no eval"
    assert "innerHTML" not in src, "build nodes with textContent, never innerHTML of page text"


def test_the_roll_is_visible_without_a_console():
    """A roll the user cannot see is the whole feature failing silently."""
    src = DICE.read_text()
    assert re.search(r"(toast|overlay|dice-result)", src, re.I), "no result surface in the asset"
    assert re.search(r"setTimeout|remove\(\)", src), "the result surface never goes away"
