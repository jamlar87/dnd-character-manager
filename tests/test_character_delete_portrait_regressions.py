"""Regressions for the Aug-2026 audit fixes:

1. `toggle_magic_initiate_use` 500'd (UnboundLocalError) for any character
   without a Magic Initiate feat entry — the loop never bound `mi`.
2. `delete_character` deleted only the `characters` row, orphaning
   character_spells / character_relationships / dm_campaign_characters rows and
   leaving the id in DM campaign rosters; it also reported ok:true for a
   character the caller does not own.
3. `apply_level_up` raised TypeError (500) on a malformed ASI payload instead
   of rejecting it with 400.
4. Sheet HTML inlined the stored base64 portrait (multi-MB) twice per view; it
   now points data: portraits at the cacheable /portrait-image route.
"""

import base64
import json
import secrets
import sqlite3

import pytest

from main import _hash

PORTRAIT_MARKER = "AUDITPORTRAITMARKER"
DATA_URL = "data:image/png;base64," + base64.b64encode(PORTRAIT_MARKER.encode()).decode()


def _con(seeded_db):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.row_factory = sqlite3.Row
    return con


def make_char(seeded_db, name, user_id=1, **cols):
    cols.setdefault("race", "Human")
    cols.setdefault("class_name", "Fighter")
    keys = list(cols)
    con = _con(seeded_db)
    con.execute(
        f"INSERT INTO characters (user_id, name{''.join(', ' + k for k in keys)}) "
        f"VALUES (?, ?{', ?' * len(keys)})",
        [user_id, name, *[cols[k] for k in keys]],
    )
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


def add_user(seeded_db, email, is_admin=0):
    token = secrets.token_hex(32)
    con = _con(seeded_db)
    con.execute("INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
                (email, _hash("Test1234!"), is_admin))
    uid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (uid, token))
    con.commit()
    con.close()
    return uid, token


def headers_for(token):
    return {"Cookie": f"dnd_token={token}; csrf_token=test-csrf-token",
            "X-CSRF-Token": "test-csrf-token"}


def scalar(db_path, sql, params=()):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


# ── 1. Magic Initiate toggle ───────────────────────────────────────────────

class TestMagicInitiateToggle:
    def test_without_feat_is_400_not_500(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "No Feat", asi_history="[]")
        r = client.post(f"/api/character/{cid}/magic-initiate/use", json={},
                        headers=auth_headers)
        assert r.status_code == 400, f"{r.status_code}: {r.text[:200]}"
        assert "Magic Initiate" in r.text
        # nothing written
        assert scalar(seeded_db["db_path"],
                      "SELECT asi_history FROM characters WHERE id=?", (cid,)) == "[]"

    def test_with_empty_asi_history_columns(self, client, seeded_db, auth_headers):
        """NULL / junk history must not crash either (json.loads(None) → 500)."""
        cid = make_char(seeded_db, "Null Hist")
        con = _con(seeded_db)
        con.execute("UPDATE characters SET asi_history=NULL WHERE id=?", (cid,))
        con.commit()
        con.close()
        r = client.post(f"/api/character/{cid}/magic-initiate/use", json={},
                        headers=auth_headers)
        assert r.status_code == 400, f"{r.status_code}: {r.text[:200]}"

    def test_null_history_on_get_and_reset_routes(self, client, seeded_db, auth_headers):
        """Same NULL trap on the sibling magic-initiate routes."""
        cid = make_char(seeded_db, "Null Hist 2")
        con = _con(seeded_db)
        con.execute("UPDATE characters SET asi_history=NULL WHERE id=?", (cid,))
        con.commit()
        con.close()
        assert client.get(f"/api/character/{cid}/magic-initiate",
                          headers=auth_headers).status_code == 200
        r = client.post(f"/api/character/{cid}/magic-initiate/reset", json={},
                        headers=auth_headers)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"

    def test_with_feat_toggles_used_flag(self, client, seeded_db, auth_headers):
        hist = [{"type": "feat", "feat": "magic_initiate",
                 "magic_initiate": {"class": "Wizard", "used": False}}]
        cid = make_char(seeded_db, "Has Feat", asi_history=json.dumps(hist))
        r = client.post(f"/api/character/{cid}/magic-initiate/use", json={},
                        headers=auth_headers)
        assert r.status_code == 200, r.text[:200]
        assert r.json()["used"] is True
        stored = json.loads(scalar(seeded_db["db_path"],
                                   "SELECT asi_history FROM characters WHERE id=?", (cid,)))
        assert stored[0]["magic_initiate"]["used"] is True
        # and back
        r = client.post(f"/api/character/{cid}/magic-initiate/use", json={},
                        headers=auth_headers)
        assert r.json()["used"] is False

    def test_other_users_character_is_404(self, client, seeded_db):
        cid = make_char(seeded_db, "Someone Else", user_id=1,
                        asi_history=json.dumps([{"type": "feat", "feat": "magic_initiate"}]))
        _, token = add_user(seeded_db, "stranger-mi@test.com")
        r = client.post(f"/api/character/{cid}/magic-initiate/use", json={},
                        headers=headers_for(token))
        assert r.status_code == 404


# ── 2. Delete cascade ──────────────────────────────────────────────────────

class TestDeleteCascade:
    def test_children_and_campaign_roster_cleaned(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Doomed", user_id=1)
        con = _con(seeded_db)
        con.execute("INSERT INTO character_spells (character_id, spell_name, spell_level) "
                    "VALUES (?, 'Fire Bolt', 0)", (cid,))
        con.execute("INSERT INTO character_relationships (character_id, user_id, name) "
                    "VALUES (?, 1, 'Old Friend')", (cid,))
        con.execute("INSERT INTO dm_campaigns (user_id, name, characters) VALUES (1, 'Camp A', ?)",
                    (json.dumps([cid]),))
        camp_a = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO dm_campaigns (user_id, name, characters) VALUES (1, 'Camp B', ?)",
                    (json.dumps([{"id": cid, "name": "Doomed"}, {"id": 999, "name": "Other"}]),))
        camp_b = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO dm_campaign_characters (campaign_id, character_id) VALUES (?, ?)",
                    (camp_a, cid))
        con.commit()
        con.close()

        r = client.post(f"/api/character/{cid}/delete", json={}, headers=auth_headers)
        assert r.status_code == 200, r.text[:200]

        db = seeded_db["db_path"]
        assert scalar(db, "SELECT COUNT(*) FROM characters WHERE id=?", (cid,)) == 0
        assert scalar(db, "SELECT COUNT(*) FROM character_spells WHERE character_id=?", (cid,)) == 0
        assert scalar(db, "SELECT COUNT(*) FROM character_relationships WHERE character_id=?", (cid,)) == 0
        assert scalar(db, "SELECT COUNT(*) FROM dm_campaign_characters WHERE character_id=?", (cid,)) == 0
        roster_a = json.loads(scalar(db, "SELECT characters FROM dm_campaigns WHERE id=?", (camp_a,)))
        assert roster_a == []
        roster_b = json.loads(scalar(db, "SELECT characters FROM dm_campaigns WHERE id=?", (camp_b,)))
        assert roster_b == [{"id": 999, "name": "Other"}], "other entries must survive"

    def test_non_owner_gets_404_and_row_survives(self, client, seeded_db):
        cid = make_char(seeded_db, "Protected", user_id=1)
        _, token = add_user(seeded_db, "stranger-del@test.com")
        r = client.post(f"/api/character/{cid}/delete", json={}, headers=headers_for(token))
        assert r.status_code == 404
        assert scalar(seeded_db["db_path"], "SELECT COUNT(*) FROM characters WHERE id=?", (cid,)) == 1

    def test_missing_character_gets_404(self, client, seeded_db, auth_headers):
        r = client.post("/api/character/424242/delete", json={}, headers=auth_headers)
        assert r.status_code == 404

    def test_admin_may_delete_any(self, client, seeded_db, admin_headers):
        cid = make_char(seeded_db, "Admin Target", user_id=1)
        r = client.post(f"/api/character/{cid}/delete", json={}, headers=admin_headers)
        assert r.status_code == 200
        assert scalar(seeded_db["db_path"], "SELECT COUNT(*) FROM characters WHERE id=?", (cid,)) == 0


# ── 3. ASI payload validation ──────────────────────────────────────────────

class TestApplyLevelUpAsiValidation:
    def _mk(self, client, headers):
        r = client.post("/api/character/create", json={
            "name": "ASI Probe", "race": "Human", "class_name": "Fighter", "level": 1,
            "hp_max": 12, "abilities": {"strength": 16, "dexterity": 14, "constitution": 15,
                                        "intelligence": 10, "wisdom": 12, "charisma": 8},
        }, headers=headers)
        assert r.status_code == 200, r.text[:300]
        return r.json()["id"]

    def test_non_integer_increase_is_400(self, client, seeded_db, auth_headers):
        cid = self._mk(client, auth_headers)
        r = client.post(f"/api/character/{cid}/apply-level-up",
                        json={"target_level": 4, "asi_choices": {"4": {"strength": "two"}}},
                        headers=auth_headers)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"

    def test_unknown_ability_is_400(self, client, seeded_db, auth_headers):
        cid = self._mk(client, auth_headers)
        r = client.post(f"/api/character/{cid}/apply-level-up",
                        json={"target_level": 4, "asi_choices": {"4": {"luck": 2}}},
                        headers=auth_headers)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"

    def test_valid_asi_still_applies(self, client, seeded_db, auth_headers):
        cid = self._mk(client, auth_headers)
        # Human gets +1 to every score at creation, so compare against the
        # stored value rather than the requested one.
        before = scalar(seeded_db["db_path"], "SELECT strength FROM characters WHERE id=?", (cid,))
        r = client.post(f"/api/character/{cid}/apply-level-up",
                        json={"target_level": 4, "subclass": "Champion",
                              "asi_choices": {"4": {"strength": 2}}},
                        headers=auth_headers)
        assert r.status_code == 200, r.text[:300]
        after = scalar(seeded_db["db_path"], "SELECT strength FROM characters WHERE id=?", (cid,))
        assert after == before + 2, f"{before} -> {after}"


# ── 4. Portrait route + sheet no longer inlines the data URL ───────────────

class TestPortraitImageRoute:
    def test_data_url_served_as_image(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Painted", user_id=1, portrait_url=DATA_URL)
        r = client.get(f"/api/character/{cid}/portrait-image", headers=auth_headers)
        assert r.status_code == 200, r.text[:200]
        assert r.content.decode() == PORTRAIT_MARKER
        assert r.headers["content-type"].startswith("image/png")
        assert "max-age" in r.headers.get("cache-control", "")

    def test_non_owner_is_404(self, client, seeded_db):
        cid = make_char(seeded_db, "Private Art", user_id=1, portrait_url=DATA_URL)
        _, token = add_user(seeded_db, "stranger-art@test.com")
        r = client.get(f"/api/character/{cid}/portrait-image", headers=headers_for(token))
        assert r.status_code == 404

    def test_admin_allowed(self, client, seeded_db, admin_headers):
        cid = make_char(seeded_db, "Admin Art", user_id=1, portrait_url=DATA_URL)
        r = client.get(f"/api/character/{cid}/portrait-image", headers=admin_headers)
        assert r.status_code == 200

    def test_no_portrait_is_404(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Unpainted", user_id=1)
        r = client.get(f"/api/character/{cid}/portrait-image", headers=auth_headers)
        assert r.status_code == 404

    def test_sheet_html_does_not_inline_the_base64(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Rendered", user_id=1, portrait_url=DATA_URL)
        r = client.get(f"/character/{cid}", headers=auth_headers)
        assert r.status_code == 200, r.text[:200]
        assert PORTRAIT_MARKER not in r.text, "base64 portrait must not be inlined"
        assert f"/api/character/{cid}/portrait-image" in r.text


class TestPortraitTemplateWiring:
    """The live sheet template is sheet.html — _sheet_tabs.html is an orphan."""

    def test_sheet_template_uses_the_route(self):
        from pathlib import Path
        tpl = (Path(__file__).resolve().parent.parent / "templates" / "sheet.html").read_text()
        assert "portrait_src" in tpl
        assert 'src="{{ character.portrait_url }}"' not in tpl
