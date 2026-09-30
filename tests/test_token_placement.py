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


def test_palette_place_buttons_carry_no_inline_json_spec():
    """Characters and NPCs stopped placing entirely: JSON.stringify put double quotes inside a
    double-quoted onclick, so the attribute closed early and the handler became `{kind:'character',
    character_id:78,label:` - a syntax error that fired nothing and reported nothing. Creatures escaped
    theirs with &quot; and kept working, which is exactly why the bug looked partial.

    No inline JSON spec anywhere: specs travel as escaped data attributes instead."""
    assert 'onclick="VTT.addToken(' not in JS, "an inline JSON spec is back - it only survives if escaped"
    assert "function placeRow(" in JS, "rows must be built through one helper, so branches cannot drift"
    body = JS.split("function placeRow(")[1].split("\n  }")[0]
    assert "esc(data[k])" in body, "attribute values must be escaped"
    assert "data-place" in body
    for kind in ("'creature'", "'npc'", "'character'"):
        assert kind in JS.split("function searchPalette(")[1], f"the {kind} branch vanished"


def test_the_palette_uses_one_delegated_listener():
    """Rows are replaced on every search, so a listener per button would be thrown away with them."""
    body = JS.split("function wirePalette(")[1].split("\n  }")[0]
    assert "closest('[data-place]')" in body, "the delegated handler must find the row's button"
    assert "addToken(spec)" in body
    search = JS.split("function searchPalette(")[1].split("\n  }")[0]
    assert "wirePalette();" in search, "the listener must be wired when the palette renders"


def test_addToken_does_not_assert_a_footprint_it_does_not_mean():
    """Large and Huge creatures landed as single squares because addToken's base object always sent
    w:1 h:1, and the server prefers an explicit w/h over the creature's size. The size travelled all
    the way to the server and was then overridden by a default nobody asked for.

    Omit w/h unless a caller means them - the server derives the footprint from the size the palette
    sends, and returns it on the token."""
    body = JS.split("function addToken(")[1].split("\n  }")[0]
    base = body.split("Object.assign(")[1].split("},")[0]
    assert "w:" not in base, "a hardcoded w in the base object beats the creature's size"
    assert "h:" not in base, "a hardcoded h in the base object beats the creature's size"
    assert "Object.assign(" in body and "spec || {}" in body, "an explicit spec must still win"


def test_the_palette_sends_the_creatures_real_size():
    """The server can only size the token if it is told the size (or a cells count)."""
    palette = JS.split("function searchPalette(")[1]
    assert "kind: 'creature', name: m.name, size: m.size" in palette, (
        "the creature branch must pass the reference library's size")
    handler = JS.split("function wirePalette(")[1].split("\n  }")[0]
    assert "size: d.size" in handler, "the delegated handler must carry the size into the spec"


def test_a_refused_placement_is_visible():
    """The whole reason this took two rounds: a placement that never happened looked identical to a
    button that did nothing."""
    body = JS.split("function addToken(")[1].split("\n  }")[0]
    assert "Could not place that token" in body, "a refusal must say so on the page"
    assert ".catch(" in body, "a network failure must say so too"


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
