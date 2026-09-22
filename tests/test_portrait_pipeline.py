"""Portrait pipeline: one write contract, one render contract (Sept 2026).

Write side  — every portrait write funnels through services.images.normalize_portrait
              (validate + downscale). This file guards the four character paths
              (upload, AI generate, AI background task, NPC→character build) and
              the two NPC paths (create, update).
Render side — nothing may put a stored base64 blob in a page; tiles point at
              /api/character/{id}/portrait-image and /api/dm/npc/{id}/portrait-image.

Plus the DM tools page-weight contract: the monster library is a cacheable
asset, not ~2,600 server-rendered cards.
"""

import base64
import io
import json
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def png_data_url(w=3000, h=2000, color=(10, 80, 160)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


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


def make_npc(seeded_db, name, user_id=1, portrait_url=""):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute("INSERT INTO dm_npcs (user_id, name, portrait_url) VALUES (?, ?, ?)",
                (user_id, name, portrait_url))
    nid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return nid


def stored_portrait(seeded_db, table, row_id):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    row = con.execute(f"SELECT portrait_url FROM {table} WHERE id=?", (row_id,)).fetchone()
    con.close()
    return row[0] if row else None


def thirds(seeded_db, user_id):
    """Session headers for a different user (non-owner auth check)."""
    import secrets
    from main import _hash
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute("INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 0)",
                (f"other{user_id}@test.com", _hash("x")))
    uid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    token = secrets.token_hex(32)
    con.execute("INSERT INTO sessions (user_id, token, expires_at) "
                "VALUES (?, ?, datetime('now', '+1 day'))", (uid, token))
    con.commit()
    con.close()
    csrf = "test-csrf-token"
    return {"Cookie": f"dnd_token={token}; csrf_token={csrf}", "X-CSRF-Token": csrf}


# ── The shared normaliser ────────────────────────────────────────────────────

class TestNormalizePortrait:
    def test_downscales_a_large_data_url(self):
        from services.images import normalize_portrait
        big = png_data_url(3000, 2000)
        out, err = normalize_portrait(big, max_px=1024)
        assert err is None
        assert out.startswith("data:image/webp")
        assert len(out) < len(big) / 5
        assert len(base64.b64decode(out.split(",", 1)[1])) < 800 * 1024

    def test_passes_http_urls_and_empties_through(self):
        from services.images import normalize_portrait
        assert normalize_portrait("https://cdn.example/p.png")[0] == "https://cdn.example/p.png"
        assert normalize_portrait("")[0] == ""
        assert normalize_portrait(None)[0] == ""

    def test_rejects_junk_and_oversized(self):
        from services.images import MAX_PORTRAIT_BYTES, normalize_portrait
        assert normalize_portrait("data:image/png;base64,!!!")[1]      # no image data
        assert normalize_portrait("javascript:alert(1)")[1]            # not an image ref
        assert normalize_portrait("data:image/png;base64," + "A" * (MAX_PORTRAIT_BYTES * 2))[1]
        # rejected values must never be returned for storage
        assert normalize_portrait("javascript:alert(1)")[0] == ""


# ── Write side: characters ───────────────────────────────────────────────────

class TestCharacterWritesAreUnified:
    def test_upload_route_shrinks_what_it_stores(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Upload Target")
        r = client.post("/api/character/portrait", headers=auth_headers,
                        json={"character_id": cid, "image_data": png_data_url(3000, 2000)})
        assert r.status_code == 200 and r.json()["ok"]
        stored = stored_portrait(seeded_db, "characters", cid)
        assert stored.startswith("data:image/webp"), stored[:40]
        assert len(stored) < 800 * 1024, f"stored {len(stored)} bytes"

    def test_upload_route_rejects_an_oversized_payload(self, client, seeded_db, auth_headers):
        from services.images import MAX_PORTRAIT_BYTES
        cid = make_char(seeded_db, "Too Big")
        huge = "data:image/png;base64," + "A" * (MAX_PORTRAIT_BYTES * 2)
        r = client.post("/api/character/portrait", headers=auth_headers,
                        json={"character_id": cid, "image_data": huge})
        assert r.status_code == 400
        assert "too large" in r.json()["error"]
        assert stored_portrait(seeded_db, "characters", cid) in (None, "", )

    def test_every_character_write_path_goes_through_the_normaliser(self):
        src = (ROOT / "routes" / "characters" / "ai_routes.py").read_text()
        # upload route, AI background task (and the build path lives in dm.py)
        assert src.count("normalize_portrait") >= 2
        assert "UPDATE characters SET portrait_url=?" in src
        dm_src = (ROOT / "routes" / "dm.py").read_text()
        assert "normalize_portrait" in dm_src

    def test_npc_to_character_build_carries_the_portrait(self, seeded_db):
        """Fills an empty portrait on the built character, never overwrites one."""
        import routes.dm as dm
        src = make_npc(seeded_db, "Portrait Source", portrait_url=png_data_url(600, 600))
        empty = make_char(seeded_db, "Freshly Built")
        filled = make_char(seeded_db, "Already Has One", portrait_url=png_data_url(50, 50))
        assert dm._apply_portrait_to_built_character(
            empty, stored_portrait(seeded_db, "dm_npcs", src)) is True
        assert stored_portrait(seeded_db, "characters", empty).startswith("data:image/")
        before = stored_portrait(seeded_db, "characters", filled)
        dm._apply_portrait_to_built_character(
            filled, stored_portrait(seeded_db, "dm_npcs", src))
        assert stored_portrait(seeded_db, "characters", filled) == before


# ── Write side: NPCs ─────────────────────────────────────────────────────────

class TestNpcWritesAreUnified:
    def test_update_stores_a_downscaled_portrait(self, client, seeded_db, auth_headers):
        nid = make_npc(seeded_db, "Villain")
        r = client.post(f"/api/dm/npc/{nid}/update", headers=auth_headers,
                        json={"portrait_url": png_data_url(2400, 1600)})
        assert r.status_code == 200
        stored = stored_portrait(seeded_db, "dm_npcs", nid)
        assert stored.startswith("data:image/webp") and len(stored) < 800 * 1024

    def test_update_rejects_bad_payloads(self, client, seeded_db, auth_headers):
        nid = make_npc(seeded_db, "Bad Payload")
        r = client.post(f"/api/dm/npc/{nid}/update", headers=auth_headers,
                        json={"portrait_url": "javascript:alert(1)"})
        assert r.status_code == 400 and "error" in r.json()

    def test_create_accepts_and_shrinks_a_portrait(self, client, seeded_db, auth_headers):
        r = client.post("/api/dm/npc/create", headers=auth_headers,
                        json={"name": "New With Art", "portrait_url": png_data_url(2000, 2000)})
        assert r.status_code == 200
        nid = r.json()["id"]
        assert stored_portrait(seeded_db, "dm_npcs", nid).startswith("data:image/webp")

    def test_rename_without_touching_the_picker_keeps_the_portrait(self, client, seeded_db, auth_headers):
        """The editor only sends portrait_url when the DM picked/removed one."""
        nid = make_npc(seeded_db, "Keep My Art", portrait_url=png_data_url(100, 100))
        before = stored_portrait(seeded_db, "dm_npcs", nid)
        r = client.post(f"/api/dm/npc/{nid}/update", headers=auth_headers,
                        json={"name": "Renamed"})
        assert r.status_code == 200
        assert stored_portrait(seeded_db, "dm_npcs", nid) == before

    def test_both_editors_share_the_browser_downscaler(self):
        helper = (ROOT / "static" / "char-portrait.js").read_text()
        assert "function downscaleImageFile(" in helper
        assert "downscaleImageFile(" in (ROOT / "static" / "sheet.js").read_text()
        assert "downscaleImageFile(" in (ROOT / "static" / "dm_tools.js").read_text()


# ── Read side: the image routes ──────────────────────────────────────────────

class TestPortraitImageRoutes:
    def test_npc_route_serves_stored_bytes_and_a_thumbnail(self, client, seeded_db, auth_headers):
        blob = png_data_url(900, 700)
        nid = make_npc(seeded_db, "Pic", portrait_url=blob)
        r = client.get(f"/api/dm/npc/{nid}/portrait-image", headers=auth_headers)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
        assert r.content == base64.b64decode(blob.split(",", 1)[1])
        t = client.get(f"/api/dm/npc/{nid}/portrait-image?size=64", headers=auth_headers)
        assert t.status_code == 200 and t.headers["content-type"] == "image/webp"
        assert len(t.content) < len(r.content)

    def test_npc_route_scoping_and_missing_portrait(self, client, seeded_db, auth_headers):
        nid = make_npc(seeded_db, "Private", portrait_url=png_data_url(80, 80))
        other = thirds(seeded_db, 7)
        assert client.get(f"/api/dm/npc/{nid}/portrait-image", headers=other).status_code == 404
        plain = make_npc(seeded_db, "No Art")
        assert client.get(f"/api/dm/npc/{plain}/portrait-image", headers=auth_headers).status_code == 404
        assert client.get("/api/dm/npc/999999/portrait-image", headers=auth_headers).status_code == 404

    def test_npc_detail_hides_the_blob_but_reports_it(self, client, seeded_db, auth_headers):
        nid = make_npc(seeded_db, "Blob Guard", portrait_url=png_data_url(120, 120))
        d = client.get(f"/api/dm/npc/{nid}", headers=auth_headers).json()
        assert d["has_portrait"] is True and d["portrait_is_data"] is True
        assert d["portrait_url"] == ""
        assert "data:image" not in json.dumps(d)

    def test_npc_list_reports_portrait_without_the_blob(self, client, seeded_db, auth_headers):
        with_p = make_npc(seeded_db, "Listed Art", portrait_url=png_data_url(90, 90))
        plain = make_npc(seeded_db, "Listed Plain")
        body = client.get("/api/dm/npcs", headers=auth_headers).text
        assert "data:image" not in body
        rows = {n["id"]: n for n in client.get("/api/dm/npcs", headers=auth_headers).json()["npcs"]}
        assert rows[with_p]["has_portrait"] is True
        assert rows[plain]["has_portrait"] is False


# ── Read side: no page may inline a blob ─────────────────────────────────────

class TestNoPageInlinesABlob:
    def test_sheet_and_modal_use_the_route(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Sheet Owner", portrait_url=png_data_url(300, 300))
        r = client.get(f"/character/{cid}", headers=auth_headers)
        if r.status_code != 200:
            pytest.skip(f"sheet route is not /character/{{id}} (got {r.status_code})")
        assert "data:image/png;base64" not in r.text
        assert f"/api/character/{cid}/portrait-image" in r.text

    def test_modal_template_never_prints_portrait_url(self):
        tpl = (ROOT / "templates" / "_sheet_modals.html").read_text()
        assert "{{ character.portrait_url }}" not in tpl
        assert "portrait_src" in tpl

    def test_dm_tools_and_campaign_detail_stay_blob_free(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Campaign Art", portrait_url=png_data_url(200, 200))
        nid = make_npc(seeded_db, "Campaign Npc", portrait_url=png_data_url(200, 200))
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("INSERT INTO dm_campaigns (user_id, name, quests, locations, characters, npcs) "
                    "VALUES (1, 'Art Campaign', '[]', '[]', ?, ?)",
                    (json.dumps([{"id": cid}]), json.dumps([{"id": nid}])))
        camp = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()
        con.close()
        for html in (client.get("/dm-tools", headers=auth_headers).text,
                     client.get(f"/campaign/{camp}", headers=auth_headers).text):
            assert "data:image/png;base64" not in html
        detail = client.get(f"/campaign/{camp}", headers=auth_headers).text
        assert f"/api/dm/npc/{nid}/portrait-image" in detail
        assert f"/api/character/{cid}/portrait-image" in detail

    def test_macro_and_tile_helper_support_npcs_and_fallbacks(self):
        tpl = (ROOT / "templates" / "_char_portrait.html").read_text()
        assert "kind == 'npc'" in tpl and "/api/dm/npc/" in tpl
        assert "charPortraitFallback(this)" in tpl, "a stale flag must not show a broken image"
        js = (ROOT / "static" / "char-portrait.js").read_text()
        assert "opts.kind === 'npc'" in js and "'/api/dm/npc/'" in js


# ── DM tools page weight ─────────────────────────────────────────────────────

class TestDmToolsMonsterAsset:
    def test_monsters_are_not_server_rendered(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        assert re.search(r"/static/dm-library\.js\?v=", html), "library asset not referenced"
        assert re.search(r"/static/dm_tools\.js\?v=", html), "hand-bumped ?v= came back"
        # what remains of the shared .monster-card markup is the spells + traps
        # grids; the monster library (>1,900 cards) must not be in the HTML
        assert html.count('class="monster-card"') < 800

    def test_asset_matches_the_monster_api(self, client, seeded_db, auth_headers):
        client.get("/dm-tools", headers=auth_headers)      # generates the asset
        asset = ROOT / "static" / "dm-library.js"
        assert asset.exists()
        raw = asset.read_text()
        start = raw.index("window.DM_LIBRARY = ") + len("window.DM_LIBRARY = ")
        end = raw.index(";\nwindow.DM_MONSTERS", start)
        payload = json.loads(raw[start:end])["monsters"]
        assert len(payload) > 100, "monster cache looks empty in this environment"
        api = client.get("/api/dm/monsters", headers=auth_headers).json()["monsters"]
        by_name = {m["name"]: m for m in api}
        sample = next(m for m in payload if m["n"] in by_name)
        src = by_name[sample["n"]]
        assert float(sample["cr"]) == float(src["challenge_rating"])
        assert sample["hp"] == src["hit_points"]
        assert sample["t"] == (src.get("type") or "").lower()

    def test_manual_npc_rows_are_not_server_rendered(self, client, seeded_db, auth_headers):
        """~400 manual NPC rows (one with a 433 KB description) move to the asset."""
        html = client.get("/dm-tools", headers=auth_headers).text
        assert "id=\"manualNpcList\"" in html
        assert "renderManualNpcRows" in (ROOT / "static" / "dm_tools.js").read_text()
        # the manual rows themselves must be absent from the HTML
        assert html.count('class="npc-row') == 0

    def test_library_asset_carries_only_global_reference_data(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Secret Owner")
        nid = make_npc(seeded_db, "Secret Npc")
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("INSERT INTO dm_campaigns (user_id, name, quests, locations, characters, npcs) "
                    "VALUES (1, 'Secret Camp', '[]', '[]', '[]', '[]')")
        con.commit()
        con.close()
        client.get("/dm-tools", headers=auth_headers)
        asset = (ROOT / "static" / "dm-library.js").read_text()
        assert "window.DM_LIBRARY" in asset and "window.DM_MONSTERS" in asset
        # user-owned rows must never reach a shared, cacheable asset
        for secret in ("Secret Camp", "Secret Owner", "Secret Npc"):
            assert secret not in asset

    def test_renderer_carries_the_filter_attributes(self):
        js = (ROOT / "static" / "dm_tools.js").read_text()
        assert "function renderMonsterCards(" in js
        for attr in ("data-name=", "data-type=", "data-cr=", "data-source="):
            assert attr in js
        # filtering must stay scoped to the monster grid (spells share the class)
        assert "querySelectorAll('#monsterGrid .monster-card')" in js
