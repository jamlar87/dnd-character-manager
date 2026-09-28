"""The spell library is cached asset data, not 580 KB of server-rendered HTML.

/dm-tools measured 1,786,095 B with the traps panel at 948,957 B; the spell panel was the
last server-rendered library at 582,289 B / 700 cards. The rows now ride in
/static/dm-library.js and are drawn by renderSpellCards(), which is the same contract the
monsters, manual NPCs and traps already use: the card markup and its data-* attributes must
stay identical, because filterSpells() matches on them.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The page's byte budget. Tight on purpose: the four libraries are client-rendered now, so
#: a jump back over this line means a library is being inlined again (or a new one is).
PAGE_BUDGET = 400_000


def asset_payload() -> dict:
    raw = (ROOT / "static" / "dm-library.js").read_text()
    start = raw.index("window.DM_LIBRARY = ") + len("window.DM_LIBRARY = ")
    end = raw.index(";\nwindow.DM_MONSTERS", start)
    return json.loads(raw[start:end])


class TestTheSpellLibraryRidesInTheAsset:
    def test_the_asset_carries_the_spell_library(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)          # regenerates the asset
        payload = asset_payload()
        assert len(payload.get("spells") or []) > 300, "spell rows missing from the asset"
        assert "window.DM_SPELLS" in (ROOT / "static" / "dm-library.js").read_text()

    def test_spell_cards_are_not_server_rendered(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        assert 'id="spellGrid"' in html
        assert "renderSpellCards" in (ROOT / "static" / "dm_tools.js").read_text()
        # monsters, spells and traps are all client-rendered now: the HTML must carry no
        # .monster-card at all (spells reuse that class), and no reference trap card
        assert html.count('class="monster-card"') == 0
        assert html.count('class="trap-card"') == 0
        assert len(html) < PAGE_BUDGET, f"/dm-tools is back to {len(html)} B"

    def test_the_row_fields_match_what_filterSpells_reads(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        spell = next(s for s in asset_payload()["spells"] if s.get("i"))
        for key in ("i", "n", "lv", "sc", "scd", "ct", "rng", "dur", "cls"):
            assert key in spell, f"the spell payload lost {key!r}; renderSpellCards needs it"
        # data-school and data-classes are compared against the lowercased <option> values
        assert str(spell["sc"]) == str(spell["sc"]).lower()
        assert str(spell["cls"]) == str(spell["cls"]).lower()

    def test_the_initial_count_covers_the_library(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        payload = asset_payload()
        m = re.search(r'id="spellCount">([\d,]+) spells', html)
        assert m, "the spell count element vanished"
        assert int(m.group(1).replace(",", "")) >= len(payload["spells"])


class TestTheRendererKeepsTheFiltersWorking:
    def test_the_renderer_runs_before_the_source_filter_mounts(self):
        """SourceFilter.init() snapshots the sources present in the DOM, and filterSpells()
        reads the data-* attributes, so the cards have to exist first."""
        js = (ROOT / "static" / "dm_tools.js").read_text()
        assert "function renderSpellCards(" in js
        assert "window.DM_SPELLS" in js
        for attr in ("data-name=", "data-level=", "data-school=", "data-classes=", "data-source="):
            assert attr in js
        render = js.index("const renderedSpells = renderSpellCards();")
        assert render < js.index("SourceFilter.init(el,")
        assert render < js.index("if (renderedSpells) filterSpells();")

    def test_filter_spells_still_scopes_to_its_own_grid(self):
        js = (ROOT / "static" / "dm_tools.js").read_text()
        assert "querySelectorAll('#spellGrid .monster-card')" in js
        # the monster renderer must not adopt the spell grid (spells share .monster-card)
        assert "document.getElementById('monsterGrid')" in js
