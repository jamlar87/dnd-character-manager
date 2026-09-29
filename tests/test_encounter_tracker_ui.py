"""Combat tracker: keyboard navigation and click-to-locate.

The tracker listed combatants and nothing else — no way to move through them, and no visual
"you are here" during a fight. Atlas VTT does both (its initiative entries are clickable and
keyboard-driven, and clicking one locates that creature), and the same anchor is what the map
layer will use later to centre a token.

The renderer lives in static/dm_tools.js as template-literal HTML, so these are text guards
(the repo's convention for JS assets) plus a live browser pass for the real proof: the guards
below cannot tell whether the rows render at all.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
JS = (REPO / "static" / "dm_tools.js").read_text()
HTML = (REPO / "templates" / "dm_tools.html").read_text()


def _fn(name: str) -> str:
    """The body of a top-level function declaration, up to the next top-level declaration."""
    m = re.search(rf"^function {name}\(", JS, re.M)
    assert m, f"{name}() is gone from static/dm_tools.js"
    rest = JS[m.end():]
    nxt = re.search(r"^(?:function |// ──|async function )", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_every_tracker_row_carries_a_stable_anchor():
    """The map layer will locate a token from a tracker row, so the row needs an identity
    that survives a re-render — the index in the list does not."""
    assert 'id="participant-row-${p.id}"' in JS, "participant rows lost their stable id"
    assert 'data-name="${dmEsc(' in JS, "participant rows lost their data-name anchor"


def test_the_locate_button_exists_and_targets_the_row_not_the_modal():
    assert "p-locate" in JS, "no locate control on the tracker rows"
    locate = re.search(r'class="btn btn-outline btn-sm p-locate"[^>]*onclick="([^"]+)"', JS)
    assert locate, "the locate control is not wired to anything"
    assert "focusTrackerRow(" in locate.group(1), "locate must focus the tracker row"
    assert "stopPropagation" in locate.group(1), (
        "without stopPropagation the click also toggles the row's stat panel")


def test_focus_marks_one_row_at_a_time():
    body = _fn("focusTrackerRow")
    assert "init-active" in body, "focusing a row must mark it"
    assert "classList.toggle('init-active', n === idx)" in body, (
        "the highlight must MOVE — toggling without clearing leaves a trail of highlighted rows")
    assert "scrollIntoView" in body, "a focused row must be brought into view"


def test_keys_do_not_hijack_typing_in_the_hp_and_initiative_inputs():
    body = _fn("trackerKeydown")
    for tag in ("INPUT", "TEXTAREA", "SELECT"):
        assert tag in body, f"tracker keys must stand down while typing in <{tag}>"
    assert "ArrowDown" in body and "ArrowUp" in body
    assert "preventDefault" in body, "arrow keys scroll the page unless the default is stopped"


def test_navigation_is_inert_while_the_encounter_modal_is_closed():
    """The listener is on document, so it must check visibility — otherwise it steals the
    arrow keys on every other page of the app."""
    body = _fn("trackerRows")
    assert "encounterModal" in body and "getComputedStyle" in body, (
        "trackerRows() must return nothing unless the encounter modal is actually open")


def test_enter_opens_the_focused_combatant():
    body = _fn("trackerKeydown")
    assert "toggleParticipantStats(_trackerFocus)" in body, (
        "Enter must open the focused combatant's stats, not do nothing")


def test_the_highlight_has_a_real_style():
    """A class with no CSS is an invisible feature."""
    assert ".participant-row.init-active" in HTML, "no style for the focused row"
    rule = re.search(r"\.participant-row\.init-active\s*\{([^}]*)\}", HTML)
    assert rule and "outline" in rule.group(1), (
        "the focused row needs a visible outline, not just a background tint")
