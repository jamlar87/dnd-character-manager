"""Portrait tiles on every surface that lists characters (Sept 2026).

One shared renderer per side:
  server  → templates/_char_portrait.html (macro `char_portrait`)
  browser → static/char-portrait.js   (charPortraitTile / charPortraitFallback)

Both must point at /api/character/{id}/portrait-image with a thumbnail size and
never inline the stored base64 blob (`characters.portrait_url` is 1.9-3.4 MB).
"""

import base64
import io
import json
import re
import sqlite3

import pytest


def make_char(seeded_db, name, user_id=1, **cols):
    cols.setdefault("race", "Human")
    cols.setdefault("class_name", "Fighter")
    cols.setdefault("level", 3)
    cols.setdefault("hp_current", 10)
    cols.setdefault("hp_max", 12)
    keys = list(cols)
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute(
        f"INSERT INTO characters (user_id, name{''.join(', ' + k for k in keys)}) "
        f"VALUES (?, ?{', ?' * len(keys)})",
        [user_id, name, *[cols[k] for k in keys]],
    )
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


def png_data_url(w=400, h=300, color=(120, 60, 200)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def make_campaign(seeded_db, name, char_ids, user_id=1):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute(
        "INSERT INTO dm_campaigns (user_id, name, party_level, party_size, quests, locations, characters, npcs) "
        "VALUES (?, ?, 1, 1, '[]', '[]', ?, '[]')",
        (user_id, name, json.dumps([{"id": c, "status": "active"} for c in char_ids])),
    )
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


class TestSharedMacro:
    def test_layout_ships_the_browser_tile_helper(self, client):
        html = client.get("/login").text
        assert "/static/char-portrait.js" in html

    def test_helper_uses_the_image_route_with_a_thumbnail(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "static" / "char-portrait.js").read_text()
        assert "function charPortraitTile(" in js
        assert "function charPortraitFallback(" in js
        assert "/portrait-image?size=" in js
        assert "data:image" not in js, "the tile must never render a stored data: URL"

    def test_macro_has_no_inline_blob_path(self):
        from pathlib import Path
        tpl = (Path(__file__).resolve().parent.parent / "templates" / "_char_portrait.html").read_text()
        assert "portrait-image?size=" in tpl
        assert "portrait_url }}" not in tpl and "{{ portrait_url }}" not in tpl


class TestDmToolsCampaignCards:
    def test_party_chips_show_portraits_and_live_hp(self, client, seeded_db, auth_headers):
        with_p = make_char(seeded_db, "Has Portrait", portrait_url=png_data_url(),
                           hp_current=7, hp_max=20)
        without_p = make_char(seeded_db, "No Portrait", hp_current=3, hp_max=9)
        camp = make_campaign(seeded_db, "Portrait Campaign", [with_p, without_p])
        html = client.get("/dm-tools", headers=auth_headers).text
        assert "char-portrait-chip" in html
        assert f"/api/character/{with_p}/portrait-image?size=52" in html   # 26px tile -> 2x
        assert "char-portrait-empty" in html
        # live HP from the characters table, not the (HP-less) roster snapshot
        assert re.search(r"Has Portrait\s*<span[^>]*>\(7/20\)", html)
        assert re.search(r"No Portrait\s*<span[^>]*>\(3/9\)", html)

    def test_dm_tools_page_carries_no_portrait_blobs(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Blob Guard", portrait_url=png_data_url())
        make_campaign(seeded_db, "Blob Campaign", [cid])
        html = client.get("/dm-tools", headers=auth_headers).text
        assert "data:image/png;base64" not in html

    def test_deleted_character_does_not_break_the_card(self, client, seeded_db, auth_headers):
        """Roster entry whose character row is gone (shouldn't happen) still lists."""
        cid = make_char(seeded_db, "Ghost", hp_current=1, hp_max=1)
        camp = make_campaign(seeded_db, "Ghost Campaign", [cid, 999999])
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("DELETE FROM characters WHERE id=?", (cid,))
        con.commit()
        con.close()
        r = client.get("/dm-tools", headers=auth_headers)
        assert r.status_code == 200


class TestCampaignDetailRows:
    def test_party_row_has_a_portrait_tile(self, client, seeded_db, auth_headers):
        with_p = make_char(seeded_db, "Row Portrait", portrait_url=png_data_url())
        camp = make_campaign(seeded_db, "Detail Campaign", [with_p])
        html = client.get(f"/campaign/{camp}", headers=auth_headers).text
        assert f"/api/character/{with_p}/portrait-image?size=72" in html   # 36px tile -> 2x
        assert "char-portrait" in html
        assert "data:image/png;base64" not in html

    def test_row_without_portrait_gets_the_initial(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Quill")
        camp = make_campaign(seeded_db, "Plain Campaign", [cid])
        html = client.get(f"/campaign/{camp}", headers=auth_headers).text
        assert "char-portrait-empty" in html
        assert f"/api/character/{cid}/portrait-image" not in html


class TestCampaignApiExposesTheFlag:
    def test_campaigns_api_returns_has_portrait(self, client, seeded_db, auth_headers):
        with_p = make_char(seeded_db, "Api Portrait", portrait_url=png_data_url())
        without_p = make_char(seeded_db, "Api Plain")
        camp = make_campaign(seeded_db, "Api Campaign", [with_p, without_p])
        data = client.get("/api/dm/campaigns", headers=auth_headers).json()
        row = next(c for c in data["campaigns"] if c["id"] == camp)
        flags = {c["id"]: bool(c["has_portrait"]) for c in row["characters"]}
        assert flags[with_p] is True
        assert flags[without_p] is False
        # the blob itself must never ride along in the JSON
        assert "data:image" not in json.dumps(row)


class TestBrowserRenderedListsUseTheSharedTile:
    def test_dm_tools_js_wires_the_tile_into_every_character_list(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "static" / "dm_tools.js").read_text()
        # players panel, encounter participant rows, initiative track, picker, campaign list
        assert js.count("charPortraitTile(") >= 5
        assert "hasPortrait: p.has_portrait" in js
        assert "hasPortrait: c.has_portrait" in js
        assert "data:image" not in js

    def test_dm_tools_js_is_syntactically_consistent(self):
        """A stray backtick/brace in a template literal would kill the whole file."""
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "static" / "dm_tools.js").read_text()
        assert js.count("`") % 2 == 0, "unbalanced backticks in dm_tools.js"
        # every call must sit inside a template literal (${ ... })
        in_literal = len(re.findall(r"\$\{[^}]*charPortraitTile\(", js))
        assert in_literal == js.count("charPortraitTile("), \
            "a tile call escaped its template literal"
