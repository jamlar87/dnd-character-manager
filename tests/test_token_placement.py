"""Placing a token has to be visible.

Reported: "I'm trying to place a token but nothing happens when I click." Nothing was broken — the
token was created and saved — but it was placed at the centre of the view every time, so a second one
landed exactly underneath the first and the click looked inert. The guard here is that placement walks
to a free cell.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
JS = (APP / "static" / "vtt.js").read_text()


def test_placement_does_not_stack_on_an_existing_token():
    assert "function freeSpotNear(" in JS, "placement has no free-cell search"
    body = JS.split("function freeSpotNear(")[1].split("\n  }")[0]
    assert "taken[" in body, "it must know which cells are occupied"
    assert "ring" in body and "dx" in body and "dy" in body, (
        "it must search outward, not just test the centre")
    add = JS.split("function addToken(")[1].split("\n  }")[0]
    assert "freeSpotNear(" in add, "addToken must use the free-cell search, not the raw view centre"
    assert "snapPoint(centre[0], centre[1])" not in add, "placing still uses the bare view centre"


def test_the_search_prefers_the_centre_and_only_moves_when_busy():
    """A free centre cell must be taken as-is: nudging a token off the DM's target for no reason is
    its own bug, and this is the behaviour that used to work."""
    body = JS.split("function freeSpotNear(")[1].split("\n  }")[0]
    assert "if (isFree(start)) return start;" in body, (
        "the view centre is tried first, and returned when free")
    assert body.index("isFree(start)") < body.index("for (var ring"), (
        "the centre is tested before any ring search")


def test_the_map_lists_its_tokens_and_each_row_jumps_to_that_token():
    """Asked for: a list of every token on the map, and clicking one centres the view on it."""
    page = (APP / "templates" / "map.html").read_text()
    assert 'id="vttTokenList"' in page, "the panel has nowhere to show the roster"
    assert "function renderTokenList(" in JS, "there is no roster renderer"
    body = JS.split("function renderTokenList(")[1].split("\n  }")[0]
    assert "VTT.centreOn(" in body, "a roster row must jump to its token"
    assert "esc(" in body, "labels come from creature names and must be escaped, never interpolated raw"
    # the roster is rebuilt only when the set changes: renderSelected runs on every drag
    assert "_tokenListSig" in body, "the roster must not rebuild the DOM on every selection"
    # ...and it is reachable from the place every token change already passes through
    sel = JS.split("function renderSelected(")[1].split("\n  }")[0]
    assert "renderTokenList();" in sel, "adding or removing a token must refresh the roster"


def test_duplicate_names_are_numbered_and_unique_ones_are_not():
    """Three goblins on one map must be tellable apart in the roster, and a lone creature must keep its
    plain name — numbering everything would be noise."""
    body = JS.split("function renderTokenList(")[1].split("\n  }")[0]
    assert "total[label] > 1" in body, "numbering must apply only to labels that collide"
    assert "seen[label] = (seen[label] || 0) + 1" in body, "duplicates need an index"
    assert "label = label + ' ' + seen[label]" in body, "the index must land on the displayed label"
    assert "esc(label)" in body, "the numbered label still has to be escaped"


def test_centring_uses_the_exact_inverse_of_the_camera_transform():
    """worldToScreen is (world + cam) * zoom, so centring must be cam = centre / zoom - world. Getting
    a sign or the zoom placement wrong puts the token somewhere plausible but wrong, which is the
    failure this codebase keeps paying for."""
    assert "function worldToScreen(x, y)" in JS and "(x + state.camera.x) * state.camera.zoom" in JS, (
        "the transform changed - re-derive the inverse below")
    body = JS.split("function centreOn(")[1].split("\n  }")[0]
    assert "canvas.clientWidth / (2 * state.camera.zoom) - t.x" in body, (
        "the x centring is not the inverse of worldToScreen")
    assert "canvas.clientHeight / (2 * state.camera.zoom) - t.y" in body, (
        "the y centring is not the inverse of worldToScreen")
    assert "state.selected = t.id" in body, "jumping to a token should also select it"
    assert "centreOn: centreOn" in JS, "not exported, so nothing can call it"


def test_occupied_cells_are_keyed_on_rounded_coordinates():
    """Tokens store float world coordinates; the occupancy test must round them, or two tokens a
    fraction of a pixel apart count as different cells and still stack visually."""
    body = JS.split("function freeSpotNear(")[1].split("\n  }")[0]
    assert re.search(r"Math\.round\(t\.x\)", body) and re.search(r"Math\.round\(t\.y\)", body), (
        "occupancy must be keyed on rounded coordinates")
