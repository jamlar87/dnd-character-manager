"""Every portrait tile must be clickable, NPCs included.

The gap this closes: only the character sheet's own 80px portrait had a click handler
(openPortraitModal in sheet.js). Monsters, items and NPCs render through the shared
charPortraitTile()/refArtImg() builders and the Jinja macro in _char_portrait.html, none of which were
clickable — so there was no way to see NPC art at full size anywhere in the app.

The JavaScript side is asserted by reading the served file, since that is what the browser actually
executes. The Jinja side is asserted by rendering the macro for real, so a template edit that drops
the handler fails here rather than silently in the UI.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _macro_html(**kwargs):
    """Render the macro for real.

    Don't build template source with %-formatting: a '%s' placeholder in a string that also contains
    Jinja braces is a trap (that approach raised "must be real number, not str" here). Jinja can hand
    back the macro itself, which removes the string assembly entirely.
    """
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")))
    module = env.get_template("_char_portrait.html").make_module()
    return str(module.char_portrait(**kwargs))


def test_ref_art_tile_is_clickable():
    """The NPC/monster/item case — the one that had no way to enlarge."""
    out = _macro_html(char_id=0, name="Adranach", has_portrait=1, size=32,
                      src_override="/api/ref-image/creature/Adranach?size=64")
    assert "openPortraitFull" in out, "reference tiles must open the full-size viewer"
    assert "data-full=" in out, "the viewer needs the source URL"
    assert "cp-clickable" in out, "the affordance class drives the zoom cursor"


def test_npc_kind_uses_the_npc_route_and_is_clickable():
    out = _macro_html(char_id=7, name="Ayo Jabe", has_portrait=1, size=40, kind="npc")
    assert "/api/dm/npc/7/portrait-image" in out
    assert "openPortraitFull" in out, "NPC portraits must be clickable (the reported gap)"


def test_character_portrait_is_clickable():
    out = _macro_html(char_id=12, name="Skyla", has_portrait=1, size=56)
    assert "/api/character/12/portrait-image" in out
    assert "openPortraitFull" in out


def test_the_initial_tile_is_not_clickable():
    """No portrait stored: the letter tile must not offer a viewer for nothing."""
    out = _macro_html(char_id=0, name="Nobody", has_portrait=0, size=32)
    assert "char-portrait-empty" in out
    assert "openPortraitFull" not in out


def test_the_shared_js_builders_are_wired():
    """charPortraitTile/refArtImg cover every list built in the browser."""
    js = (ROOT / "static" / "char-portrait.js").read_text()
    assert "function openPortraitFull(" in js
    assert js.count("openPortraitFull") >= 3, "tile, ref art and the definition all wire the viewer"
    # The viewer must ask for a large render, not re-show the thumbnail it was clicked from.
    assert "size=1024" in js or "1024" in js
    # Tiles live inside clickable rows and pickers; without this a click would do both things.
    assert "stopPropagation" in js
