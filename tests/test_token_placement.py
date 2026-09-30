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


def test_occupied_cells_are_keyed_on_rounded_coordinates():
    """Tokens store float world coordinates; the occupancy test must round them, or two tokens a
    fraction of a pixel apart count as different cells and still stack visually."""
    body = JS.split("function freeSpotNear(")[1].split("\n  }")[0]
    assert re.search(r"Math\.round\(t\.x\)", body) and re.search(r"Math\.round\(t\.y\)", body), (
        "occupancy must be keyed on rounded coordinates")
