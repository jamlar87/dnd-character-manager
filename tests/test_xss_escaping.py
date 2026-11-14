"""Stage 0 regressions for the DM-panel XSS defects.

Pins three defects from docs/plans/2026-11-14-full-refactor.md §Stage 0:

* 0.4/0.5 A character or item name reached the page as markup — either inside a
  `<script>` block (`{{ characters_json|safe }}`) or through an `innerHTML` template.
* 0.6 A name was interpolated *into an inline handler* (`onclick="...('${name}')"`),
  where an apostrophe closed the string and turned the rest into code.

The page test is end-to-end (real TestClient request); the JS assertions are static
checks that the escaping helpers are still wired into every user-name sink, because
this repo has no headless-browser harness.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_JS = STATIC_DIR / "dm_tools.js"

EVIL = "</script><img src=x onerror=alert(1)>"


# ── 0.5 script-block breakout ─────────────────────────────────────────────────────────

def test_json_for_script_escapes_the_sequence_that_closes_the_block():
    from routes.dm import _json_for_script

    out = _json_for_script({"name": EVIL})

    assert "</script" not in out
    assert "<" not in out and ">" not in out
    # Still ordinary JSON: the browser decodes it back to the original name.
    assert json.loads(out)["name"] == EVIL


def test_json_for_script_escapes_line_separators():
    from routes.dm import _json_for_script

    out = _json_for_script({"name": "a\u2028b\u2029c"})

    assert "\\u2028" in out and "\\u2029" in out
    assert json.loads(out)["name"] == "a\u2028b\u2029c"


def test_dm_tools_page_does_not_emit_a_raw_closing_script_tag(client, seeded_db, admin_headers):
    """A character name is data; it must never terminate the page's <script> block."""
    db = sqlite3.connect(seeded_db["db_path"])
    db.execute(
        "INSERT INTO characters (user_id, name, race, class_name, inventory, gp, cp) "
        "VALUES (?,?,?,?,?,?,?)",
        (2, EVIL, "Human", "Fighter", "[]", 0, 0),
    )
    db.commit()
    db.close()

    resp = client.get("/dm-tools", headers=admin_headers)
    assert resp.status_code == 200, resp.text[:400]

    body = resp.text
    assert EVIL not in body
    assert "</script><img" not in body
    # The name survives as data, escaped for HTML.
    assert "\\u003c/script\\u003e" in body


# ── 0.4/0.6 escaping in the client templates ────────────────────────────────────────

@pytest.fixture(scope="module")
def js_source():
    return STATIC_JS.read_text(encoding="utf-8")


def test_shared_escape_html_covers_every_metacharacter():
    """escapeHtml is the one escaper in the app (static/ui/dom.js) — a missing
    character is an exploitable hole, and dm_tools.js calls this exact function."""
    src = (STATIC_DIR / "ui" / "dom.js").read_text(encoding="utf-8")
    m = re.search(r"function escapeHtml\(value\)\s*\{(.*?)\n\}", src, re.S)
    assert m, "the shared escapeHtml is gone; user names are no longer escaped"
    body = m.group(1)

    for char, entity in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                         ('"', "&quot;"), ("'", "&#39;")]:
        assert entity in body, f"escapeHtml does not escape {char!r}"

    assert "window.escapeHtml = escapeHtml" in src, "consumers cannot reach it"


def test_dm_tools_has_no_private_escaper_copy(js_source):
    """One escaper, not two: dm_tools must not grow a local dmEsc again."""
    assert not re.search(r"function\s+(dmEsc|escapeHtml|esc)\s*\(", js_source), \
        "dm_tools.js redeclared a local escaper instead of using the shared one"
    assert "escapeHtml" in js_source, "dm_tools.js stopped escaping user names"


def test_no_inline_handler_interpolates_a_user_name(js_source):
    """`onclick="f('${name}')"` is the 0.6 pattern: an apostrophe breaks out of the string.

    Only the *handler body* matters, so this looks inside each onclick="" attribute;
    a name rendered next to it as an escaped data-* attribute is the fix, not a bug.
    """
    offenders = []
    for n, line in enumerate(js_source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue  # a comment describing the pattern is not the pattern
        for handler in re.findall(r'onclick="([^"]*)"', line):
            if re.search(r"(?:\.name|npc_name)", handler) and "${" in handler:
                offenders.append((n, handler[:120]))
    assert not offenders, "inline handler interpolates a name: " + repr(offenders)


def test_team_item_and_character_names_are_escaped_where_rendered(js_source):
    """Every name that lands in innerHTML goes through dmEsc (0.4)."""
    for pattern in [
        r"\$\{dmEsc\(item\.name\)\}",          # team pool item card
        r"data-item-name=\"\$\{dmEsc\(item\.name\)\}\"",
        r"\$\{dmEsc\(ch\.name\)\}",            # character picker rows
        r"data-char-name=\"\$\{dmEsc\(ch\.name\)\}\"",
    ]:
        assert re.search(pattern, js_source), f"unescaped name sink: {pattern}"


def test_condition_badges_no_longer_travel_through_inline_handlers(js_source):
    """A custom condition name is free text typed by the DM, so it must not be in markup."""
    for handler in re.findall(r'onclick="([^"]*)"', js_source):
        assert "combatToggleCond" not in handler and "combatDismissCond" not in handler
    assert 'data-cond-name="${dmEsc(c.name)}"' in js_source
    assert re.search(r"closest\('\[data-cond-dismiss-en\]'\)", js_source), \
        "dismiss handler must be matched before the badge handler"


def test_character_preview_uses_data_attributes_and_a_delegated_listener(js_source):
    """0.6: previewCharSheet was called from onclick with the name inside the call."""
    assert "data-char-action=\"previewCharSheet\"" in js_source
    assert re.search(r"closest\('\[data-char-action\]'\)", js_source)
    # Comments above the delegated listener quote the old pattern; only real markup counts.
    offenders = [
        line for line in js_source.splitlines()
        if not line.strip().startswith(("//", "*"))
        and re.search(r'onclick="[^"]*previewCharSheet\(\s*\$\{', line)
    ]
    assert not offenders, offenders
