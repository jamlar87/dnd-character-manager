"""Global reference tables are cached asset data, not inline JSON on every page.

SOURCE_SLUG_MAP (~31 KB) was inlined into /dm-tools and /create, NAMED_ITEM_TYPES (18 KB) and
SUMMON_TEMPLATES (38 KB) into /dm-tools — byte-identical JSON re-sent on every load and
uncacheable, because it rode inside the HTML. All three are shared book/rule data, so they
belong in a versioned /static asset. COMBAT_CHARACTERS is the DM's own roster and must stay
inline: user rows may never enter a cached, shared file.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Pages that used to inline the map, with an anchor that only exists after it is in scope.
#: /create's consumer (openSourceRef, the 📚 badge handler) moved into its own static asset, so
#: the anchor there is the wizard script tag itself.
MAP_CONSUMERS = (("/dm-tools", "dm_tools.js"), ("/create", "/static/create.js"))


def static_asset(name: str) -> str:
    return (ROOT / "static" / name).read_text()


def _json_from_asset(raw: str, var: str):
    tail = raw.split("window." + var + " = ", 1)[1]
    return json.loads(tail.split(";\n", 1)[0])


class TestTheSourceMapRidesInItsOwnAsset:
    def test_the_asset_defines_the_global_the_pages_read(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        data = _json_from_asset(static_asset("source-slug-map.js"), "SOURCE_SLUG_MAP")
        assert len(data) > 50, "the source map lost its entries"
        assert all("display" in v or "path" in v for v in list(data.values())[:5])

    def test_no_page_inlines_the_map_any_more(self, client, seeded_db, auth_headers):
        for path, consumer in MAP_CONSUMERS:
            html = client.get(path, headers=auth_headers).text
            assert "const SOURCE_SLUG_MAP" not in html, f"{path} still inlines the source map"
            assert "window.SOURCE_SLUG_MAP = " not in html
            assert re.search(r'/static/source-slug-map\.js\?v=\w+', html), f"{path} misses the tag"
            # the tag has to come before the page's own code that dereferences the map
            assert html.index("/static/source-slug-map.js?v=") < html.index(consumer)

    def test_the_asset_holds_no_user_rows(self, client, seeded_db, auth_headers):
        """Two different users must produce byte-identical reference data — the leak guard."""
        client.get("/dm-tools", headers=auth_headers)
        first = static_asset("source-slug-map.js")
        admin = {"Cookie": f"dnd_token={seeded_db['admin_token']}"}
        client.get("/dm-tools", headers=admin)
        client.get("/create", headers=admin)
        assert static_asset("source-slug-map.js") == first
        # and the library half (monsters/NPCs/traps/spells + the DM-only consts)
        assert "COMBAT_CHARACTERS" not in static_asset("dm-library.js")


class TestTheDmOnlyTablesMoveWithTheLibrary:
    def test_the_library_carries_named_item_types_and_summon_templates(
            self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)
        raw = static_asset("dm-library.js")
        item_types = _json_from_asset(raw, "NAMED_ITEM_TYPES")
        assert len(item_types) > 100, "NAMED_ITEM_TYPES did not make it into the asset"
        # dmTagItemBadges() looks up by name, so the values must survive as objects
        assert isinstance(next(iter(item_types.values())), (dict, str))
        assert len(_json_from_asset(raw, "SUMMON_TEMPLATES")) > 50

    def test_the_dm_tools_page_no_longer_inlines_them(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        for var in ("NAMED_ITEM_TYPES", "SUMMON_TEMPLATES", "SOURCE_SLUG_MAP"):
            assert f"const {var}" not in html, f"{var} is still inline"
        # the DM's own roster is user data and has to stay in the page
        assert "COMBAT_CHARACTERS" in html
        # and both assets must load before the script that reads them
        assert html.index("source-slug-map.js") < html.index("dm-library.js") < html.index("dm_tools.js")

    def test_the_page_budget_holds(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        assert len(html) < 150_000, f"/dm-tools is back to {len(html)} B"


class TestTheCreateWizardRidesInAssetsToo:
    """/create shipped 39 inline `const NAME = {{ x | tojson }}` lines — ~241 KB of the page,
    plus its own 94 KB of wizard JS — all of it global reference data re-sent per load."""

    def test_the_asset_carries_the_wizard_tables(self, client, seeded_db, auth_headers):
        client.get("/create", headers=auth_headers)
        raw = static_asset("create-consts.js")
        races = _json_from_asset(raw, "RACES")
        assert len(races) > 50, "the race table did not make it into the asset"
        # race records are objects the wizard reads field-by-field
        assert isinstance(next(iter(races.values())), dict)
        assert len(_json_from_asset(raw, "CLASSES")) >= 12
        assert len(_json_from_asset(raw, "ALL_SKILLS")) >= 18
        # the name pools the wizard merges into its own RACE_NAMES literal
        pools = _json_from_asset(raw, "RACE_NAME_POOLS")
        assert len(pools) > 50
        assert any("male" in v for v in list(pools.values())[:20])

    def test_the_membership_tables_stay_sets(self, client, seeded_db, auth_headers):
        """The wizard calls .has() on these; an array would silently never match."""
        client.get("/create", headers=auth_headers)
        raw = static_asset("create-consts.js")
        assert "window.FLEXIBLE_ASI = new Set([" in raw
        assert "window.MPMM_ASI = new Set([" in raw

    def test_the_page_no_longer_inlines_them(self, client, seeded_db, auth_headers):
        html = client.get("/create", headers=auth_headers).text
        for var in ("RACES", "CLASSES", "ALL_SKILLS", "ALIGNMENTS", "BACKGROUNDS",
                    "EXPERTISE_LEVELS", "MPMM_ASI", "FLEXIBLE_ASI", "RACE_NAME_POOLS"):
            assert f"const {var} = " not in html, f"{var} is still inline"
        assert not re.search(r"\{\{ *(races|classes|all_skills|mpmm_asi_races) *\| tojson", html)
        assert re.search(r'/static/create-consts\.js\?v=\w+', html)
        # the wizard JS is a static file now, loaded after the data it reads
        assert re.search(r'/static/create\.js\?v=\w+', html)
        assert html.index("/static/create-consts.js?v=") < html.index("/static/create.js?v=")
        # the wizard's own markup still ships (it is per-user HTML, not reference data)
        assert "const SKILL_DESCS" not in html

    def test_the_asi_picker_blocks_survive_the_move(self, client, seeded_db, auth_headers):
        """The regression this move caused once: the three picker blocks were gated with
        `{% if "Half-Elf" in races %}` / `{% if mpmm_asi_races %}`. Dropping those context
        vars made Jinja drop the markup silently, and updateAsiPreview() then threw on every
        race click (reading .style of a missing #halfelf-picks) — which also skipped the
        flexible-ASI and MPMM blocks further down the same function."""
        html = client.get("/create", headers=auth_headers).text
        for el in ("halfelf-picks", "halfelf-grid", "customlineage-picks", "customlineage-grid",
                   "flex-asi-label", "mpmm-picks", "mpmm-grids", "mpmm-hint"):
            assert f'id="{el}"' in html, f"#{el} vanished from the page"
        # the gates are booleans the route computes now, not the moved tables
        src = (ROOT / "routes" / "characters" / "creation.py").read_text()
        for flag in ("has_half_elf=", "has_custom_lineage=", "has_mpmm_races="):
            assert flag in src, f"the route must still pass {flag}"
        tpl = (ROOT / "templates" / "create.html").read_text()
        assert '{% if "Half-Elf" in races %}' not in tpl and "{% if mpmm_asi_races %}" not in tpl

    def test_two_users_get_the_same_wizard_data(self, client, seeded_db, auth_headers):
        client.get("/create", headers=auth_headers)
        first = static_asset("create-consts.js")
        client.get("/create", headers={"Cookie": f"dnd_token={seeded_db['admin_token']}"})
        assert static_asset("create-consts.js") == first
        # every global in here must be one of the reference tables — no user rows, no
        # per-request state (the class descriptions legitimately contain the word "characters")
        names = re.findall(r"^window\.([A-Za-z_]\w*) = ", first, re.M)
        assert len(names) == 40, names
        assert all(n.upper() == n for n in names), names
        for forbidden in ("portrait_url", "dnd_token", "dm_campaigns", "user_id", "password"):
            assert forbidden not in first

    def test_the_page_budget_holds(self, client, seeded_db, auth_headers):
        html = client.get("/create", headers=auth_headers).text
        assert len(html) < 60_000, f"/create is back to {len(html)} B"
