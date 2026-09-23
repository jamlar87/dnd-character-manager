"""Attunement: the sheet's card, its client state mirror, and the server contract.

Found Sept 2026: the ◆/◇ badge and the slot row disagreed after ANY equip or
unequip. toggleAttune only wrote the badge and the slot row into the DOM and never
updated ATTUNED_ITEMS — the client mirror of characters.attuned_items — so the next
renderEquipped() (every saveEquipped() calls it) redrew an attuned item as ◇ while
the slot row still counted it. Both halves of the card are now derived from
ATTUNED_ITEMS, and the flag map ships the whole library so an item typed into the
inventory and equipped in the same session still gets its badge.
"""

import json
import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHEET_JS = REPO / "static" / "sheet.js"

CFG_RE = re.compile(r"itemNeedsAttunement:\s*(\{.*?\})\s*,", re.S)
SLOT_RE = re.compile(r'font-weight:600">(\d)/3</span>')


def make_char(seeded_db, name, user_id=1, equipped=None, inventory=None, attuned=None):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute(
        "INSERT INTO characters (user_id, name, race, class_name, level, equipped, inventory,"
        " attuned_items) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, name, "Human", "Fighter", 5,
         json.dumps(equipped if equipped is not None else []),
         json.dumps(inventory if inventory is not None else []),
         json.dumps(attuned if attuned is not None else [])),
    )
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


def sheet_html(client, auth_headers, cid):
    r = client.get(f"/character/{cid}", headers=auth_headers)
    assert r.status_code == 200
    return r.text


def sheet_cfg(html):
    m = CFG_RE.search(html)
    assert m, "SHEETCFG.itemNeedsAttunement block missing"
    return json.loads(m.group(1))


def slots(html):
    m = SLOT_RE.search(html)
    assert m, "attunement slot counter missing"
    return int(m.group(1))


class TestAttunementFlagMap:
    """The sheet's JS decides ◇/◆ from this map, so it must be the whole library."""

    def test_map_covers_items_the_character_does_not_own(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Empty Pockets")
        flags = sheet_cfg(sheet_html(client, auth_headers, cid))
        # nothing equipped/inventoried, yet the canonical names are all there
        assert len(flags) > 100
        for name in ("Ring of Protection", "Amulet of Health", "Boots of Levitation"):
            assert flags.get(name) is True, name

    def test_map_matches_item_attunement_truth(self, client, seeded_db, auth_headers):
        from routes.characters.sheet import ITEM_ATTUNEMENT

        cid = make_char(seeded_db, "Truth Probe")
        flags = sheet_cfg(sheet_html(client, auth_headers, cid))
        for name in flags:
            assert ITEM_ATTUNEMENT.get(name.lower()) is True, f"false entry: {name}"
        expected = {n for n in ITEM_ATTUNEMENT if ITEM_ATTUNEMENT[n]}
        assert len(flags) == len(expected)

    def test_goggles_of_night_is_not_an_attunement_item(self, client, seeded_db, auth_headers):
        """2014 DMG p.172 — no attunement. Pinned so a 2024-rules 'fix' can't slip in."""
        cid = make_char(seeded_db, "Goggle Wearer",
                        equipped=[{"name": "Goggles of Night", "qty": 1}])
        html = sheet_html(client, auth_headers, cid)
        assert "Goggles of Night" in html
        flags = sheet_cfg(html)
        assert "Goggles of Night" not in flags
        # and the item card shows no ◇ badge at all
        assert html.count("item-attune-badge") == 0


class TestToggleAttuneRoute:
    def test_toggle_fills_a_slot_and_renders_it(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Ring Bearer",
                        equipped=[{"name": "Ring of Protection", "qty": 1}])
        assert slots(sheet_html(client, auth_headers, cid)) == 0

        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Ring of Protection"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["slots_used"] == 1

        html = sheet_html(client, auth_headers, cid)
        assert slots(html) == 1
        assert "item-attune-badge attuned" in html
        assert "🔮 Attunement" in html, "attunement card vanished"

    def test_breaking_attunement_frees_the_slot(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Breaker",
                        equipped=[{"name": "Ring of Protection", "qty": 1}],
                        attuned=["Ring of Protection"])
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Ring of Protection"}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["action"] == "broken"
        assert slots(sheet_html(client, auth_headers, cid)) == 0

    def test_item_that_does_not_require_attunement_is_rejected(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Goggle Wearer",
                        equipped=[{"name": "Goggles of Night", "qty": 1}])
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Goggles of Night"}, headers=auth_headers)
        assert r.status_code == 400
        assert "attunement" in r.json()["error"].lower()

    def test_unequipped_item_is_rejected(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Chest Item",
                        inventory=[{"name": "Ring of Protection", "qty": 1}])
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Ring of Protection"}, headers=auth_headers)
        assert r.status_code == 400
        assert r.json()["error"] == "Item is not equipped"

    def test_fourth_attunement_is_refused(self, client, seeded_db, auth_headers):
        names = ["Ring of Protection", "Amulet of Health", "Boots of Levitation", "Gauntlets of Ogre Power"]
        cid = make_char(seeded_db, "Slot Hog", equipped=[{"name": n, "qty": 1} for n in names])
        for n in names[:3]:
            r = client.post(f"/api/character/{cid}/toggle-attune", json={"item": n}, headers=auth_headers)
            assert r.status_code == 200, (n, r.text)
        r = client.post(f"/api/character/{cid}/toggle-attune", json={"item": names[3]}, headers=auth_headers)
        assert r.status_code == 400 and "full" in r.json()["error"].lower()
        assert slots(sheet_html(client, auth_headers, cid)) == 3

    def test_attuned_item_stops_counting_when_unequipped(self, client, seeded_db, auth_headers):
        """The slot count is equipped ∩ attuned — an unequipped attuned item frees its slot."""
        cid = make_char(seeded_db, "Pocket Ring",
                        equipped=[{"name": "Ring of Protection", "qty": 1}],
                        attuned=["Ring of Protection"])
        assert slots(sheet_html(client, auth_headers, cid)) == 1
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("UPDATE characters SET equipped='[]' WHERE id=?", (cid,))
        con.commit()
        con.close()
        assert slots(sheet_html(client, auth_headers, cid)) == 0

    def test_non_owner_cannot_attune(self, client, seeded_db, auth_headers):
        import secrets
        from main import _hash

        cid = make_char(seeded_db, "Somebody Elses Ring",
                        equipped=[{"name": "Ring of Protection", "qty": 1}])
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute("INSERT INTO users (email, password_hash, is_admin) VALUES (?,?,0)",
                    ("other@test.com", _hash("x")))
        other_uid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        token = secrets.token_hex(32)
        con.execute("INSERT INTO sessions (user_id, token, expires_at) VALUES (?,?,datetime('now','+1 day'))",
                    (other_uid, token))
        con.commit()
        con.close()
        headers = {"Cookie": f"dnd_token={token}; csrf_token=test-csrf-token",
                   "X-CSRF-Token": "test-csrf-token"}
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Ring of Protection"}, headers=headers)
        assert r.status_code == 404
        con = sqlite3.connect(str(seeded_db["db_path"]))
        assert con.execute("SELECT attuned_items FROM characters WHERE id=?", (cid,)).fetchone()[0] == "[]"
        con.close()


class TestClientMirror:
    """Sheet JS guards — the badge and the slots must both derive from ATTUNED_ITEMS."""

    def setup_method(self):
        self.src = SHEET_JS.read_text()

    def test_toggle_updates_the_client_mirror(self):
        body = self.src.split("async function toggleAttune(", 1)[1].split("\n}", 1)[0]
        assert "setAttuned(" in body, "toggleAttune must update ATTUNED_ITEMS"
        assert "renderAttunementSlots(" in body, "toggleAttune must redraw the slot row"

    def test_mirror_helper_mutates_the_shared_array(self):
        body = self.src.split("function setAttuned(", 1)[1].split("\n}", 1)[0]
        assert "ATTUNED_ITEMS.push(" in body
        assert "ATTUNED_ITEMS.splice(" in body

    def test_slots_are_derived_not_read_back_from_the_dom(self):
        body = self.src.split("function renderAttunementSlots(", 1)[1].split("\n}", 1)[0]
        assert "attunedEquipped()" in body
        # the old duplicated loop keyed off the response payload inside toggleAttune
        assert "if (i < d.slots_used)" not in self.src

    def test_render_equipped_resyncs_the_slot_row(self):
        body = self.src.split("function renderEquipped(", 1)[1].split("\n}", 1)[0]
        assert "renderAttunementSlots(" in body, (
            "renderEquipped() redraws the ◆/◇ badges, so it must redraw the slots too"
        )
