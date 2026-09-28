"""The manual library tree is cached asset data, not 70 KB of server-rendered HTML.

The panel listed ~87 manuals with long names and repeated inline styles — the biggest single
panel left on /dm-tools after the monster/NPC/trap/spell moves. It is the server's global
manual directory, so it belongs in /static/dm-library.js; renderManualGroups() rebuilds the
same markup, because toggleCollapse() and the group counts hang off those classes.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def asset_payload() -> dict:
    raw = (ROOT / "static" / "dm-library.js").read_text()
    start = raw.index("window.DM_LIBRARY = ") + len("window.DM_LIBRARY = ")
    end = raw.index(";\nwindow.DM_MONSTERS", start)
    return json.loads(raw[start:end])


class TestTheManualTreeRidesInTheAsset:
    def test_the_asset_carries_the_tree(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        groups = asset_payload().get("manuals")
        assert groups, "the manual tree is missing from the asset"
        assert all("g" in g and isinstance(g["items"], list) for g in groups)
        assert sum(len(g["items"]) for g in groups) > 20
        assert "window.DM_MANUALS" in (ROOT / "static" / "dm-library.js").read_text()

    def test_the_page_does_not_server_render_the_rows(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        assert 'id="manualGroups"' in html
        panel = html[html.index('id="panel-manuals"'):html.index('id="panel-combat"')]
        assert "📄" not in panel, "manual rows are still server-rendered"
        assert "collapse-header" not in panel
        assert "renderManualGroups" in (ROOT / "static" / "dm_tools.js").read_text()

    def test_every_row_keeps_its_open_link(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        rows = [m for g in asset_payload()["manuals"] for m in g["items"]]
        assert rows, "no manual rows in the asset"
        for m in rows:
            # the template chose the slug route first, the raw file route otherwise — never
            # both, and at least one, or the row loses its Open button
            assert bool(m.get("s")) != bool(m.get("p")), f"row carries both/neither route: {m}"
            assert m.get("s") or m.get("p"), f"row has no link target: {m}"
        assert any(m.get("mb") for m in rows), "sizes were dropped, so MB labels vanish"

    def test_the_group_and_row_counts_still_render(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        groups = asset_payload()["manuals"]
        total = sum(len(g["items"]) for g in groups)
        m = re.search(r"(\d+) groups · ([\d,]+) manuals", html)
        assert m, "the manual count line vanished (it stays server-rendered)"
        assert int(m.group(1)) == len(groups)
        assert int(m.group(2).replace(",", "")) == total

    def test_the_renderer_keeps_toggle_collapse_hooked_up(self):
        js = (ROOT / "static" / "dm_tools.js").read_text()
        assert "function renderManualGroups(" in js
        assert 'onclick="toggleCollapse(this)"' in js
        assert 'class="collapse-arrow"' in js and 'class="collapse-body"' in js
        # and it must run while the page initialises, before the source filters mount
        assert js.index("renderManualGroups();") < js.index("SourceFilter.init(el,")
