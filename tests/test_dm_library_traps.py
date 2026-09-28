"""Reference traps are cached asset data, not server-rendered HTML (Sept 2026).

/dm-tools measured 1,786,095 B with the traps panel alone at 948,957 B — bigger than
the spells panel that prompted the first asset pass, and the reason the page had grown
from the 1,025,563 B the monster/NPC pass left behind. The reference traps now ride in
/static/dm-library.js and are drawn by renderTrapCards(); the DM's own traps stay in the
HTML, because user-owned rows must never enter a shared, cacheable file.
"""

import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def asset_payload() -> dict:
    raw = (ROOT / "static" / "dm-library.js").read_text()
    start = raw.index("window.DM_LIBRARY = ") + len("window.DM_LIBRARY = ")
    end = raw.index(";\nwindow.DM_MONSTERS", start)
    return json.loads(raw[start:end])


class TestReferenceTrapsRideInTheAsset:
    def test_the_asset_carries_the_reference_trap_library(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)          # regenerates the asset
        payload = asset_payload()
        assert len(payload.get("traps") or []) > 100, "reference traps missing from the asset"
        assert "window.DM_TRAPS" in (ROOT / "static" / "dm-library.js").read_text()

    def test_trap_cards_are_not_server_rendered(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        assert 'id="trapGrid"' in html
        assert "renderTrapCards" in (ROOT / "static" / "dm_tools.js").read_text()
        # a card is the .trap-card div; with no custom traps the HTML must carry none
        assert html.count('class="trap-card"') == 0
        # The libraries and constant tables are cached-asset data now; a jump back over this
        # line means one is being inlined again. See tests/test_dm_library_spells.py.
        assert len(html) < 150_000, f"/dm-tools is back to {len(html)} B"

    def test_trap_row_fields_match_the_template_markup(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        trap = next(t for t in asset_payload()["traps"] if t.get("src"))
        for key in ("n", "t", "d", "src", "trig", "detdc", "detsk", "detd", "dism", "eff"):
            assert key in trap, f"the trap payload lost {key!r}; renderTrapCards needs it"


class TestASplashOfUserDataStaysServerRendered:
    def test_a_dms_own_traps_stay_in_the_html(self, client, seeded_db, auth_headers):
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute(
            "INSERT INTO dm_custom_traps (user_id, name, type, danger, effect) "
            "VALUES (1, 'My Homebrew Trap', 'magical', 'deadly', 'boom')"
        )
        con.commit()
        con.close()
        html = client.get("/dm-tools", headers=auth_headers).text
        assert "My Homebrew Trap" in html, "a custom trap must still render for its owner"
        assert "My Homebrew Trap" not in (ROOT / "static" / "dm-library.js").read_text()

    def test_the_initial_count_includes_the_reference_library(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        payload = asset_payload()
        m = re.search(r'id="trapCount">([\d,]+) traps', html)
        assert m, "the trap count element vanished"
        assert int(m.group(1).replace(",", "")) >= len(payload["traps"])
        assert "filterTraps();" in (ROOT / "static" / "dm_tools.js").read_text()

    def test_the_js_renderer_runs_before_the_source_filter_mounts(self):
        """SourceFilter.init() snapshots the source options in the DOM, so the trap
        cards (and their data-source values) must exist first — same rule as monsters."""
        js = (ROOT / "static" / "dm_tools.js").read_text()
        assert "function renderTrapCards(" in js
        assert "window.DM_TRAPS" in js
        for attr in ("data-name=", "data-type=", "data-danger=", "data-source="):
            assert attr in js
        # Compare the CALL positions, not the first mention anywhere in the file: both
        # names also appear in earlier definitions/comments.
        render = js.index("const renderedTraps = renderTrapCards();")
        assert render < js.index("SourceFilter.init(el,"), \
            "the cards must exist before SourceFilter.init() snapshots the sources"
        assert render < js.index("if (renderedTraps) filterTraps();"), \
            "render first, then apply the restored filter so the count is right"
