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
MAP_CONSUMERS = (("/dm-tools", "dm_tools.js"), ("/create", "openSourceRef"))


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
        assert len(html) < 230_000, f"/dm-tools is back to {len(html)} B"
