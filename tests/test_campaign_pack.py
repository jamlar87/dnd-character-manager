"""Campaign packs: export a whole campaign to one file, import it as a new one.

The rules these tests exist for: a pack is self-contained, it never carries a share secret or an
owner, and importing remaps every id — so a pack cannot overwrite or attach to anything that
already exists, and importing the same pack twice gives two independent campaigns.
"""

from __future__ import annotations

import base64
import json
import pathlib
import sqlite3

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
MAPS_DIR = REPO / "static" / "maps"
PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
           "q842iQAAAABJRU5ErkJggg==")


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit()
    db.close()
    return row_id


def _scalar(db_path, sql, params=()):
    db = sqlite3.connect(db_path)
    out = db.execute(sql, params).fetchone()
    db.close()
    return out[0] if out else None


@pytest.fixture
def no_stray_map_files():
    before = {p.name for p in MAPS_DIR.glob("*")} if MAPS_DIR.exists() else set()
    yield
    if MAPS_DIR.exists():
        for p in MAPS_DIR.glob("*"):
            if p.name not in before:
                p.unlink()


@pytest.fixture
def full_campaign(client, seeded_db, auth_headers, no_stray_map_files):
    """A campaign with a character (spells + a relationship), an NPC, a map with tokens and a
    setup, and the encounter one of those tokens came from."""
    db_path = seeded_db["db_path"]
    char = _insert(db_path, "INSERT INTO characters (user_id, name, race, class_name, level) "
                            "VALUES (?,?,?,?,?)", (1, "Garim", "Dragonborn", "Cleric", 8))
    _insert(db_path, "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared) "
                     "VALUES (?,?,?,?)", (char, "Guiding Bolt", 1, 1))
    _insert(db_path, "INSERT INTO character_relationships (character_id, user_id, name, "
                     "relationship_type) VALUES (?,?,?,?)", (char, 1, "Sister Mira", "ally"))
    npc = _insert(db_path, "INSERT INTO dm_npcs (user_id, name, role, hp_max) VALUES (?,?,?,?)",
                  (1, "Captain Vane", "questgiver", 22))
    enc = _insert(db_path, "INSERT INTO dm_encounters (user_id, name, shared) VALUES (?,?,?)",
                  (1, "Ambush at the ford", 1))
    part = _insert(db_path,
                   "INSERT INTO dm_encounter_npcs (encounter_id, npc_id, initiative, hp_current, "
                   "hp_max, creature_data) VALUES (?,?,?,?,?,?)",
                   (enc, -1, 12, 7, 7, json.dumps({"name": "Goblin", "role": "Small humanoid"})))

    camp = client.post("/api/dm/campaign/create", json={"name": "Windrun"}, headers=auth_headers).json()["id"]
    client.post(f"/api/dm/campaign/{camp}/update", json={}, headers=auth_headers)
    mid = client.post("/api/dm/map/create",
                      json={"name": "The ford", "campaign_id": camp, "grid_size": 50},
                      headers=auth_headers).json()["id"]
    client.post(f"/api/dm/map/{mid}/image", json={"image": f"data:image/png;base64,{PNG_1PX}"},
                headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/token/add",
                json={"kind": "creature", "ref_name": "Goblin", "label": "Goblin", "x": 25, "y": 25,
                      "hp_max": 7, "hp_current": 7, "encounter_en_id": part}, headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/token/add",
                json={"kind": "character", "character_id": char, "ref_name": "Garim",
                      "x": 75, "y": 25}, headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/layer", json={"fog": ["0,0"], "fog_on": 1}, headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/snapshot", json={"name": "Before the ford"}, headers=auth_headers)
    client.post(f"/api/dm/map/{mid}/player-key", json={"key": "share-secret-must-not-travel"},
                headers=auth_headers)
    # put both the character and the NPC on the campaign's rosters
    client.post(f"/api/dm/campaign/{camp}/add-character", json={"character_id": char},
                headers=auth_headers)
    client.post(f"/api/dm/campaign/{camp}/add-npc", json={"npc_id": npc}, headers=auth_headers)
    return {"camp": camp, "char": char, "npc": npc, "enc": enc, "part": part, "map": mid}


def _export(client, headers, camp):
    r = client.get(f"/api/dm/campaign/{camp}/export", headers=headers)
    assert r.status_code == 200, r.text
    return json.loads(r.content)


# ── export ─────────────────────────────────────────────────────────────────────────────

def test_a_pack_carries_the_whole_campaign(client, seeded_db, auth_headers, full_campaign):
    pack = _export(client, auth_headers, full_campaign["camp"])
    assert pack["format"] == "dnd-campaign-pack" and pack["version"] == 1
    assert pack["campaign"]["name"] == "Windrun"
    assert len(pack["characters"]) == 1 and len(pack["npcs"]) == 1
    assert len(pack["maps"]) == 1 and len(pack["encounters"]) == 1

    char_entry = pack["characters"][0]
    assert char_entry["character"]["name"] == "Garim"
    assert [s["spell_name"] for s in char_entry["spells"]] == ["Guiding Bolt"]
    assert char_entry["relationships"][0]["name"] == "Sister Mira"
    assert pack["encounters"][0]["participants"][0]["creature_data"]

    m = pack["maps"][0]
    assert len(m["tokens"]) == 2 and len(m["scenes"]) == 1
    assert m["tokens"][0]["encounter_en_id"] == full_campaign["part"], (
        "the token keeps the tracker link so the pack can rebuild it")
    assert pack["images"], "the map image must be embedded for a self-contained pack"


def test_a_pack_carries_no_secret_and_no_owner(client, seeded_db, auth_headers, full_campaign):
    """user_id, the player key and the shared flag must not travel in a file."""
    pack = _export(client, auth_headers, full_campaign["camp"])
    blob = json.dumps(pack)
    assert "share-secret-must-not-travel" not in blob, "the player key leaked into a pack"
    assert "player_key" not in blob
    assert "_drop_me" not in blob
    for table_key in ("campaign",):
        assert "user_id" not in pack[table_key]
    for m in pack["maps"]:
        assert "user_id" not in m and "player_key" not in m
    for n in pack["npcs"]:
        assert "user_id" not in n
    for e in pack["encounters"]:
        assert "user_id" not in e and "shared" not in e


def test_exporting_is_scoped(client, seeded_db, auth_headers):
    theirs = _insert(seeded_db["db_path"], "INSERT INTO dm_campaigns (user_id, name) VALUES (?,?)",
                     (2, "Theirs"))
    assert client.get(f"/api/dm/campaign/{theirs}/export", headers=auth_headers).status_code == 404


def test_images_can_be_left_out(client, seeded_db, auth_headers, full_campaign):
    r = client.get(f"/api/dm/campaign/{full_campaign['camp']}/export?images=skip",
                   headers=auth_headers)
    pack = json.loads(r.content)
    assert pack["images"] == {}
    assert any("not embedded" in w for w in pack.get("warnings", []))


def test_the_download_is_named_after_the_campaign(client, seeded_db, auth_headers, full_campaign):
    r = client.get(f"/api/dm/campaign/{full_campaign['camp']}/export", headers=auth_headers)
    assert 'filename="Windrun-pack.json"' in r.headers.get("content-disposition", "")


# ── import ─────────────────────────────────────────────────────────────────────────────

def test_importing_creates_an_independent_campaign(client, seeded_db, auth_headers, full_campaign):
    pack = _export(client, auth_headers, full_campaign["camp"])
    r = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["counts"]["maps"] == 1
    assert body["campaign_id"] != full_campaign["camp"], "an import must not touch the original"

    new_camp = body["campaign_id"]
    db_path = seeded_db["db_path"]
    assert _scalar(db_path, "SELECT name FROM dm_campaigns WHERE id=?", (new_camp,)) == "Windrun"
    assert _scalar(db_path, "SELECT user_id FROM dm_campaigns WHERE id=?", (new_camp,)) == 1

    # the characters existed twice now, and the copy is a real character with its spells
    names = _scalar(db_path, "SELECT COUNT(*) FROM characters WHERE name='Garim'")
    assert names == 2
    new_char = _scalar(db_path,
                       "SELECT id FROM characters WHERE name='Garim' AND id != ?",
                       (full_campaign["char"],))
    assert _scalar(db_path, "SELECT COUNT(*) FROM character_spells WHERE character_id=?",
                   (new_char,)) == 1
    assert _scalar(db_path, "SELECT COUNT(*) FROM character_relationships WHERE character_id=?",
                   (new_char,)) == 1

    # the campaign rosters point at the NEW rows, not the originals
    roster = json.loads(_scalar(db_path, "SELECT characters FROM dm_campaigns WHERE id=?",
                                (new_camp,)))
    assert [c["id"] for c in roster] == [new_char]
    npc_roster = json.loads(_scalar(db_path, "SELECT npcs FROM dm_campaigns WHERE id=?",
                                    (new_camp,)))
    assert npc_roster and npc_roster[0]["id"] != full_campaign["npc"]


def test_the_imported_map_is_whole(client, seeded_db, auth_headers, full_campaign):
    pack = _export(client, auth_headers, full_campaign["camp"])
    new_camp = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers).json()["campaign_id"]
    db_path = seeded_db["db_path"]
    new_map = _scalar(db_path, "SELECT id FROM dm_maps WHERE campaign_id=?", (new_camp,))
    assert new_map and new_map != full_campaign["map"]

    tokens = client.get(f"/api/dm/map/{new_map}", headers=auth_headers).json()
    assert len(tokens["tokens"]) == 2
    assert {t["label"] or t["ref_name"] for t in tokens["tokens"]} == {"Goblin", "Garim"}
    assert tokens["map"]["fog"] == ["0,0"] and tokens["map"]["fog_on"] == 1
    assert len(tokens["scenes"]) == 1, "a prepared setup travels with the map"

    # the character token points at the IMPORTED character, the tracker link at the new row
    char_token = [t for t in tokens["tokens"] if t["kind"] == "character"][0]
    imported_char = _scalar(db_path, "SELECT id FROM characters WHERE name='Garim' AND id != ?",
                            (full_campaign["char"],))
    assert char_token["character_id"] == imported_char
    goblin = [t for t in tokens["tokens"] if t["kind"] == "creature"][0]
    assert goblin["encounter_en_id"] != full_campaign["part"], "the tracker link was remapped"
    assert _scalar(db_path, "SELECT npc_id FROM dm_encounter_npcs WHERE id=?",
                   (goblin["encounter_en_id"],)) is not None


def test_the_image_comes_back_on_disk(client, seeded_db, auth_headers, full_campaign):
    pack = _export(client, auth_headers, full_campaign["camp"])
    new_camp = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers).json()["campaign_id"]
    db_path = seeded_db["db_path"]
    path = _scalar(db_path, "SELECT image_path FROM dm_maps WHERE campaign_id=?", (new_camp,))
    assert path.startswith("/static/maps/")
    assert (MAPS_DIR / pathlib.Path(path).name).is_file(), "the embedded image was not written back"


def test_importing_twice_gives_two_campaigns(client, seeded_db, auth_headers, full_campaign):
    pack = _export(client, auth_headers, full_campaign["camp"])
    first = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers).json()
    second = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers).json()
    assert first["campaign_id"] != second["campaign_id"]
    assert _scalar(seeded_db["db_path"], "SELECT COUNT(*) FROM dm_campaigns WHERE name='Windrun'") == 3


def test_an_import_belongs_to_the_importer(client, seeded_db, auth_headers, admin_headers, full_campaign):
    """A pack from someone else's machine becomes YOUR campaign, not theirs."""
    pack = _export(client, auth_headers, full_campaign["camp"])
    r = client.post("/api/dm/campaign/import", json=pack, headers=admin_headers)
    assert r.status_code == 200, r.text
    new_camp = r.json()["campaign_id"]
    assert _scalar(seeded_db["db_path"], "SELECT user_id FROM dm_campaigns WHERE id=?",
                   (new_camp,)) == 2
    assert _scalar(seeded_db["db_path"],
                   "SELECT COUNT(*) FROM dm_maps WHERE campaign_id=? AND user_id=2",
                   (new_camp,)) == 1


def test_the_share_key_never_survives_an_import(client, seeded_db, auth_headers, full_campaign):
    """A share link is a secret for THIS table; importing must not recreate it on the copy."""
    pack = _export(client, auth_headers, full_campaign["camp"])
    new_camp = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers).json()["campaign_id"]
    db_path = seeded_db["db_path"]
    imported = _scalar(db_path, "SELECT id FROM dm_maps WHERE campaign_id=?", (new_camp,))
    assert imported and imported != full_campaign["map"], "the import must be a new row"
    assert _scalar(db_path, "SELECT player_key FROM dm_maps WHERE id=?", (imported,)) in ("", None)
    assert _scalar(db_path, "SELECT COUNT(*) FROM dm_maps WHERE player_key != ''") == 1, (
        "only the ORIGINAL map may still hold its key")


def test_a_pack_is_validated_before_anything_is_written(client, seeded_db, auth_headers, full_campaign):
    before = _scalar(seeded_db["db_path"], "SELECT COUNT(*) FROM dm_campaigns")
    pack = _export(client, auth_headers, full_campaign["camp"])

    wrong_format = dict(pack, format="something-else")
    r = client.post("/api/dm/campaign/import", json=wrong_format, headers=auth_headers)
    assert r.status_code == 400 and "not a campaign pack" in r.json()["error"]

    wrong_version = dict(pack, version=99)
    r = client.post("/api/dm/campaign/import", json=wrong_version, headers=auth_headers)
    assert r.status_code == 400 and "version" in r.json()["error"]

    nameless = dict(pack, campaign={"description": "no name"})
    assert client.post("/api/dm/campaign/import", json=nameless, headers=auth_headers).status_code == 400

    assert client.post("/api/dm/campaign/import", content=b"not json",
                       headers={**auth_headers, "Content-Type": "application/json"}).status_code == 400
    assert _scalar(seeded_db["db_path"], "SELECT COUNT(*) FROM dm_campaigns") == before, (
        "a rejected pack must not leave a half-imported campaign behind")


def test_a_junk_pack_degrades_instead_of_500ing(client, seeded_db, auth_headers, full_campaign):
    """Clients send junk: entries that are not dicts, no ids, unusable characters."""
    pack = _export(client, auth_headers, full_campaign["camp"])
    pack["characters"] = [None, {"character": {}}, "nonsense"]       # all unusable
    pack["npcs"] = [{"name": "Kept"}, {"no_name": True}, 42]
    pack["maps"] = [{"name": "Empty map", "tokens": [None, "x"], "scenes": [None]}]
    pack["encounters"] = [{"name": "Empty fight", "participants": [None]}]
    r = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["npcs"] == 1, "only the usable NPC landed"
    assert body["counts"]["characters"] == 0
    assert body["counts"]["maps"] == 1 and body["counts"]["tokens"] == 0
    assert body["warnings"], "the caller should be told what was skipped"


def test_creature_only_combatants_get_the_sentinel_npc(client, seeded_db, auth_headers, full_campaign):
    """`npc_id = -1` is the app's creature-only sentinel and the FK to dm_npcs is enforced, so an
    import into a database without that placeholder must create it instead of failing."""
    db_path = seeded_db["db_path"]
    assert _scalar(db_path, "SELECT COUNT(*) FROM dm_npcs WHERE id = -1") == 0
    pack = _export(client, auth_headers, full_campaign["camp"])
    r = client.post("/api/dm/campaign/import", json=pack, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _scalar(db_path, "SELECT COUNT(*) FROM dm_npcs WHERE id = -1") == 1
    assert _scalar(db_path, "SELECT name FROM dm_npcs WHERE id = -1") == "__sentinel__"


def test_the_pack_shares_the_character_import_path():
    """Two inserters with the same whitelist drift; the pack must call the real one."""
    src = (REPO / "routes" / "pack.py").read_text()
    assert "insert_character" in src and "build_character_payload" in src
    assert "from routes.characters.transfer import" in src, (
        "the pack must reuse the character import, not re-implement it")


def test_the_export_is_a_plain_download_link():
    """A GET link lets the browser handle the file; doing it in JS would have to synthesise the
    download and the CSRF-less GET is already exempt from the token check."""
    page = (REPO / "templates" / "campaign_detail.html").read_text()
    assert 'href="/api/dm/campaign/{{ camp.id }}/export"' in page
    assert "download" in page.split('href="/api/dm/campaign/{{ camp.id }}/export"')[1][:200]


def test_the_import_control_is_on_both_pages_and_defined_once():
    detail = (REPO / "templates" / "campaign_detail.html").read_text()
    tools = (REPO / "templates" / "dm_tools.html").read_text()
    layout = (REPO / "static" / "layout.js").read_text()
    assert "importCampaignPack(this)" in detail, "a campaign page must offer the import"
    assert "importCampaignPack(this)" in tools, (
        "a DM with no campaigns yet still needs somewhere to import one")
    assert layout.count("window.importCampaignPack =") == 1, (
        "the import helper must be defined exactly once")
    assert "include the CSRF" in layout or "CSRF" in layout
