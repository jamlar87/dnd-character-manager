"""The map canvas: the page renders for its owner only, and the asset keeps its contract.

The page is server-rendered and then drawn by static/vtt.js, so there are two failure modes
that tests must cover separately: a template error (which returns 500 — this happened while
building it, from a single un-renamed context variable) and a broken JS contract (which looks
fine to Python and silently does nothing in the browser).
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
VTT = REPO / "static" / "vtt.js"
TEMPLATE = REPO / "templates" / "map.html"


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


def test_the_page_renders_for_its_owner(client, seeded_db, auth_headers):
    """A 500 here means the template and the route disagree about a context variable."""
    mid = _insert(seeded_db["db_path"],
                  "INSERT INTO dm_maps (user_id, name, grid_size) VALUES (?,?,?)",
                  (1, "Test mine", 40))
    r = client.get(f"/dm-map/{mid}", headers=auth_headers)
    assert r.status_code == 200, r.text[:400]
    assert "vttCanvas" in r.text, "the canvas element is gone"
    assert "vtt.js" in r.text, "the renderer is not loaded"
    assert "Test mine" in r.text
    assert 'id="vttGridSize" value="40"' in r.text, (
        "the grid size from the row is not on the page (an aligned grid starts from it)")


def test_the_page_is_not_served_to_other_users_or_anonymous(client, seeded_db, auth_headers):
    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_maps (user_id, name) VALUES (?,?)",
                     (2, "Theirs"))
    assert client.get(f"/dm-map/{theirs}", headers=auth_headers).status_code == 404
    anon = client.get(f"/dm-map/{theirs}", follow_redirects=False)
    assert anon.status_code in (303, 401, 403), (
        "an anonymous visitor must be sent to login, not shown the page")


def test_the_template_uses_only_context_it_is_given():
    """`map` is a Jinja builtin filter — passing a map row AS `map` shadows it, so the page
    context uses `the_map`. A leftover `{{ map.… }}` fails only at render time."""
    src = TEMPLATE.read_text()
    assert "the_map" in src
    assert not re.search(r"\{\{\s*map\.", src), "a context reference was left as `map.`"
    assert not re.search(r"\{\{\s*map_json\.", src)


def test_the_renderer_keeps_its_public_contract():
    src = VTT.read_text()
    for name in ("init", "redraw", "zoomBy", "fit", "toggleGrid", "setGridType", "nudgeSize",
                 "toggleSnap", "addToken", "removeToken", "updateToken", "saveNow",
                 "screenToWorld", "worldToScreen", "snapPoint", "snapshot", "restoreScene"):
        assert f"{name}:" in src or f"function {name}" in src, f"VTT.{name} is missing"
    assert "window.VTT" in src, "the renderer no longer exposes itself"


def test_the_renderer_saves_like_the_rest_of_the_app():
    """Same traps as the widget bar: a debounced save must flush on unload with keepalive,
    otherwise a drag during a fight is lost on refresh."""
    src = VTT.read_text()
    assert "keepalive" in src, "the placement save must survive the page it was sent from"
    assert "pagehide" in src and "visibilitychange" in src
    assert "SAVE_DELAY" in src, "no debounce: every mousemove would POST"
    assert "/api/dm/map/" in src and "/tokens" in src


def test_the_default_grid_and_hex_maths_are_present():
    src = VTT.read_text()
    assert "hex" in src and "square" in src
    # the axial conversion has to use the hex radius, not the cell size, or snapping lands off-grid
    assert "Math.sqrt(3)" in src, "pointy-top hex geometry is missing"
    assert "cube round" in src or "var cx = q" in src, "hex rounding is missing"


def test_token_art_comes_from_the_existing_routes():
    """No second art pipeline: creatures/NPCs use /api/ref-image, PCs use their portrait route."""
    src = VTT.read_text()
    assert "/api/ref-image/" in src
    assert "/api/character/" in src and "portrait-image" in src


def test_the_page_stays_lightweight():
    """Everything the canvas needs is its own cached asset, not inline page weight."""
    page = TEMPLATE.read_text()
    assert "static_asset_version('vtt.js')" in page, "the renderer would be served stale"
    # markup is fine; LOGIC in a template is not. The only inline script is the config line.
    inline = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", page, re.S)
    logic = " ".join(inline).strip()
    assert len(logic) < 200, f"inline script logic belongs in the asset: {logic[:100]}"
    toolbar = (TEMPLATE.parent / "_map_toolbar.html").read_text()
    assert "vttCanvas" not in toolbar, "the toolbar partial should hold the toolbar, nothing else"
    # counted together: splitting a file must not be a way to grow the page unnoticed
    assert len(page) + len(toolbar) < 10000, "the map page has outgrown 'markup only'"
    css = re.search(r"<style>(.*?)</style>", page, re.S)
    assert css and len(css.group(1)) < 2500, "inline CSS is growing; move it to a stylesheet"


def test_no_two_functions_share_a_name_in_the_renderer():
    """Two `function foo` declarations in one scope: the LATER one wins everywhere.

    This is not hypothetical — the canvas sizing function was named `resize`, a token-sizing
    function was also named `resize`, and the canvas silently stayed at its 300x150 default
    because the window-resize listener was wired to the token version. Python cannot see this
    (pyflakes' `redefinition of unused` has no JS equivalent), so the guard lives here.
    """
    src = VTT.read_text()
    names = re.findall(r"^\s*function\s+([A-Za-z_$][\w$]*)\s*\(", src, re.M)
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate function name(s) in static/vtt.js: {sorted(dupes)} — the last wins"


def test_the_canvas_is_sized_from_its_host():
    """A canvas left at its 300x150 default draws the map outside the visible area, which
    looks like 'the feature does nothing'."""
    src = VTT.read_text()
    assert "function resizeCanvas" in src, "no canvas sizing function"
    assert "canvas.width =" in src and "canvas.height =" in src
    assert "host.clientWidth" in src and "host.clientHeight" in src
    # the window-resize listener must be wired to the CANVAS sizing function, not the token one
    listener = re.search(r"addEventListener\('resize',\s*([A-Za-z_$][\w$]*)\)", src)
    assert listener and listener.group(1) == "resizeCanvas", (
        "the resize listener is not wired to resizeCanvas")
    assert "requestAnimationFrame(resizeCanvas)" in src, (
        "a flex parent can measure 0 on the first pass — size again on the next frame")


def test_the_dm_tools_tab_lists_and_opens_maps():
    """A canvas nobody can find is not a feature: the tab is the only entry point."""
    page = (REPO / "templates" / "dm_tools.html").read_text()
    jsrc = (REPO / "static" / "dm_tools.js").read_text()
    assert 'data-tab="maps"' in page and 'id="panel-maps"' in page
    assert "renderMaps" in jsrc and "createMap" in jsrc and "deleteMap" in jsrc
    valid = jsrc.split("const validTabs")[1][:260]
    assert "'maps'" in valid, "the maps tab would be forgotten when the last tab is restored"
    # Loading belongs to activateTab, not to the click handler. When it lived in the click handler a
    # direct load restored the tab, showed the static "Loading maps…" placeholder and never fetched.
    activate = jsrc.split("function activateTab(")[1].split("\n}")[0]
    assert "if (tabName === 'maps') renderMaps();" in activate, (
        "the list is never fetched — the panel would say 'Loading maps…' forever")
    assert "'/dm-map/'" in jsrc or "/dm-map/" in jsrc, "nothing links to the canvas page"
