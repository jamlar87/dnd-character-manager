"""Scope and escaping regressions found by the security audit.

Three distinct holes, each reachable by any registered account (registration is open):

1. `POST /api/dm/campaign/{id}/add-character` accepted *any* character id after checking only
   that the row existed. Campaign membership is what `_user_dms_character` reads, and that is
   what unlocks `GET /character/{id}?dm_preview=1` — which selects the whole row. So a
   sequential id plus a campaign you own was a full read of a stranger's sheet, with no DM role
   and no notice to the owner. The gate is the owner's own "Public" toggle (`characters.shared`),
   which already means "other accounts may read this sheet"; an unshared character now answers
   404 exactly like a nonexistent one.

2. Jinja was built without `autoescape`, which is Jinja's default and a trap: the dashboard
   renders *other users'* character names (the `shared = 1` block), so a character called
   `"><img src=x onerror=...>` executed in every visitor's session. `autoescape=True` fixes the
   whole family at once; templates that need markup already say `|safe`.

3. Campaign-pack import took the file extension from the pack's own `image_path` and wrote the
   blob into `static/maps/`, which StaticFiles serves by suffix — a pack could plant
   `x.html` (or `.svg`) on the app's own origin.
"""

import base64
import json
import pathlib
import sqlite3

REPO = pathlib.Path(__file__).resolve().parent.parent
MAPS_DIR = REPO / "static" / "maps"


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


def _scalar(db_path, sql, params=()):
    db = sqlite3.connect(db_path)
    row = db.execute(sql, params).fetchone()
    db.close()
    return row[0] if row else None


# ── 1. campaign membership is not a grant on a stranger's character ─────────────────────────

def test_private_character_from_another_account_cannot_be_pulled_into_a_campaign(
        client, seeded_db, auth_headers):
    camp_id = _insert(seeded_db["db_path"],
                      "INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?, ?, '[]')",
                      (1, "My table"))
    victim = _insert(seeded_db["db_path"],
                     "INSERT INTO characters (user_id, name, race, class_name, shared)"
                     " VALUES (?, ?, ?, ?, 0)",
                     (2, "Victim", "Human", "Rogue"))

    r = client.post(f"/api/dm/campaign/{camp_id}/add-character",
                    json={"character_id": victim}, headers=auth_headers)

    assert r.status_code == 404, r.text
    assert "Victim" not in (_scalar(seeded_db["db_path"],
                                    "SELECT characters FROM dm_campaigns WHERE id=?", (camp_id,)) or "")
    # And the sheet itself stays closed even with the DM-preview switch.
    assert client.get(f"/character/{victim}?dm_preview=1",
                      headers=auth_headers).status_code == 404


def test_public_character_from_another_account_can_join_a_campaign(
        client, seeded_db, auth_headers):
    camp_id = _insert(seeded_db["db_path"],
                      "INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?, ?, '[]')",
                      (1, "My table"))
    friendly = _insert(seeded_db["db_path"],
                       "INSERT INTO characters (user_id, name, race, class_name, shared)"
                       " VALUES (?, ?, ?, ?, 1)",
                       (2, "Friendly", "Elf", "Cleric"))

    r = client.post(f"/api/dm/campaign/{camp_id}/add-character",
                    json={"character_id": friendly}, headers=auth_headers)

    assert r.status_code == 200, r.text
    listed = json.loads(_scalar(seeded_db["db_path"],
                                "SELECT characters FROM dm_campaigns WHERE id=?", (camp_id,)) or "[]")
    assert [c["id"] for c in listed] == [friendly]
    assert client.get(f"/character/{friendly}?dm_preview=1",
                      headers=auth_headers).status_code == 200


def test_own_character_can_always_join_own_campaign(client, seeded_db, auth_headers):
    camp_id = _insert(seeded_db["db_path"],
                      "INSERT INTO dm_campaigns (user_id, name, characters) VALUES (?, ?, '[]')",
                      (1, "My table"))
    mine = _insert(seeded_db["db_path"],
                   "INSERT INTO characters (user_id, name, race, class_name, shared)"
                   " VALUES (?, ?, ?, ?, 0)",
                   (1, "Mine", "Dwarf", "Paladin"))
    r = client.post(f"/api/dm/campaign/{camp_id}/add-character",
                    json={"character_id": mine}, headers=auth_headers)
    assert r.status_code == 200, r.text


# ── 2. autoescape: another account's character name is data, not markup ─────────────────────

def test_shared_character_name_is_escaped_on_the_dashboard(client, seeded_db, auth_headers):
    payload = '"><img src=x onerror=alert(1)>'
    _insert(seeded_db["db_path"],
            "INSERT INTO characters (user_id, name, race, class_name, shared) VALUES (?, ?, ?, ?, 1)",
            (2, payload, "Human", "Wizard"))

    body = client.get("/dashboard", headers=auth_headers).text

    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;img" in body or "&#34;&gt;&lt;img" in body


def test_homebrew_text_with_markup_is_escaped_on_the_sheet(client, seeded_db, auth_headers):
    char_id = _insert(seeded_db["db_path"],
                      "INSERT INTO characters (user_id, name, race, class_name, notes)"
                      " VALUES (?, ?, ?, ?, ?)",
                      (1, "Escaper", "Human", "Bard", "<script>alert(1)</script>"))

    body = client.get(f"/character/{char_id}", headers=auth_headers).text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# ── 3. pack import cannot choose the extension of a file served by StaticFiles ──────────────

def test_pack_import_refuses_to_write_a_markup_file_into_static(client, seeded_db, auth_headers,
                                                               monkeypatch):
    before = {p.name for p in MAPS_DIR.glob("*")} if MAPS_DIR.exists() else set()
    html = b"<html><body><script>alert(document.cookie)</script></body></html>"
    pack = {
        "format": "dnd-campaign-pack",
        "version": 1,
        "campaign": {"name": "Malicious pack"},
        "maps": [{"name": "Trap map", "image_path": "/static/maps/trap.html"}],
        "images": {"trap.html": base64.b64encode(html).decode()},
    }

    r = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert any("unsupported" in w for w in body.get("warnings", [])), body
    assert not (MAPS_DIR / "trap.html").exists()
    added = {p.name for p in MAPS_DIR.glob("*")} - before if MAPS_DIR.exists() else set()
    assert not added, f"pack wrote {added}"
