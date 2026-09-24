"""Sheet item art: the item popout and the item picker must show reference art.

Reported as "I'm not seeing the item thumbnails when I select the item popout on
the inventory tab". The art route was healthy (200, image/webp) and the inventory
ROWS had tiles — but `static/sheet.js`'s two item surfaces were never wired:

  * `showItemInfo()`  — the ℹ️ popout (#detail-popup) built its body from text only
  * `renderPickerResults()` — the Add-item picker rows were text only

Their DM-tools twins (`showItemDetail`, `renderPickerResults` in dm_tools.js) both
had art, and `references/art_surfaces.md` listed only the DM-tools item popup as a
detail view — so the sheet's surfaces were missed by the "art everywhere" pass.

These are contract tests over the served JS (the same style as
tests/test_entity_search.py): the fix is in a 300 KB static file that no Python
import can see, and the failure mode is silent (no art, no error).
"""

import re
from pathlib import Path

import pytest

SHEET_JS = Path(__file__).resolve().parent.parent / "static" / "sheet.js"
DM_TOOLS_JS = Path(__file__).resolve().parent.parent / "static" / "dm_tools.js"
PORTRAIT_JS = Path(__file__).resolve().parent.parent / "static" / "char-portrait.js"


def _fn_body(src: str, name: str) -> str:
    """Source of `function <name>(...)` including its braces (${} stays balanced)."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    assert m, f"function {name} not found"
    start = src.index("{", m.end() - 1)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    pytest.fail(f"unterminated body for {name}")


@pytest.fixture(scope="module")
def sheet_js() -> str:
    return SHEET_JS.read_text()


class TestItemPopoutArt:
    """The ℹ️ popout on an inventory row."""

    def test_popout_requests_item_reference_art(self, sheet_js):
        body = _fn_body(sheet_js, "showItemInfo")
        assert "refArtImg(" in body, (
            "showItemInfo() builds #popup-body without reference art — that is the "
            "reported bug: no thumbnail in the item popout")
        assert re.search(r"refArtImg\(\s*'item'", body), "must ask for the item kind"

    def test_popout_uses_the_shared_detail_renderer(self, sheet_js):
        """Never hand-write the <img>: refArtImg owns sizing + the hide-on-404 rule."""
        body = _fn_body(sheet_js, "showItemInfo")
        assert "/api/ref-image/" not in body, (
            "the popup must go through refArtImg(), not its own image URL/markup")
        assert "charPortraitTile(" not in body, "a tile is the wrong renderer in a detail view"

    def test_popout_art_uses_the_canonical_item_name(self, sheet_js):
        """The art library is keyed by canonical name; the typed inventory name may differ."""
        body = _fn_body(sheet_js, "showItemInfo")
        call = re.search(r"refArtImg\(\s*'item'\s*,\s*([^,]+),", body)
        assert call, "refArtImg call not parsed"
        assert "data.name" in call.group(1), (
            "fall back to the API's canonical name (like dm_tools showItemDetail), "
            "otherwise a differently-cased inventory entry misses its art")


class TestItemPickerArt:
    """The Add-item picker rows."""

    def test_picker_rows_show_an_art_tile(self, sheet_js):
        body = _fn_body(sheet_js, "renderPickerResults")
        assert "charPortraitTile(" in body, (
            "picker rows render no image — the other half of the reported bug")
        assert "/api/ref-image/item/" in body, "the tile must point at item reference art"

    def test_picker_tile_is_a_thumbnail_not_a_full_image(self, sheet_js):
        body = _fn_body(sheet_js, "renderPickerResults")
        tile = re.search(r"charPortraitTile\([^)]*\)", body)
        assert tile and "size: 28" in tile.group(0), "rows want a small tile"
        assert "?size=56" in body, "request 2x the display size, like the other pickers"

    def test_picker_tile_sits_inside_the_click_target(self, sheet_js):
        """Clicking the thumbnail must select the item, not do nothing."""
        body = _fn_body(sheet_js, "renderPickerResults")
        select_at = body.index("selectPickerItem(")
        tile_at = body.index("charPortraitTile(")
        assert select_at < tile_at, (
            "the tile must be inside the element carrying the selectPickerItem() "
            "handler; as a sibling of it a click on the art is a dead zone")


class TestNoDriftFromWorkingExample:
    """The DM-tools twins are the reference implementation — they must keep art."""

    def test_dm_tools_item_detail_still_has_art(self):
        body = _fn_body(DM_TOOLS_JS.read_text(), "showItemDetail")
        assert "refArtImg('item'" in body

    def test_dm_tools_picker_still_has_art(self):
        body = _fn_body(DM_TOOLS_JS.read_text(), "renderPickerResults")
        assert "charPortraitTile(" in body and "/api/ref-image/item/" in body

    def test_both_renderers_still_exist_in_char_portrait_js(self):
        src = PORTRAIT_JS.read_text()
        assert _fn_body(src, "refArtImg")
        assert _fn_body(src, "charPortraitTile")
