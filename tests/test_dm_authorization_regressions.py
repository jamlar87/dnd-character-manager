"""Ownership regressions for DM resources."""

import sqlite3


def _insert(db_path, sql, values):
    db = sqlite3.connect(db_path)
    cur = db.execute(sql, values)
    row_id = cur.lastrowid
    db.commit(); db.close()
    return row_id


def test_user_cannot_update_another_users_campaign(client, seeded_db, auth_headers):
    campaign_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_campaigns (user_id, name) VALUES (?, ?)",
        (2, "Private campaign"),
    )
    response = client.post(
        f"/api/dm/campaign/{campaign_id}/update",
        json={"name": "stolen"}, headers=auth_headers,
    )
    assert response.status_code in (403, 404)
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT name FROM dm_campaigns WHERE id=?", (campaign_id,)).fetchone()[0] == "Private campaign"
    db.close()


def test_user_cannot_delete_another_users_campaign(client, seeded_db, auth_headers):
    campaign_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_campaigns (user_id, name) VALUES (?, ?)",
        (2, "Private campaign"),
    )
    response = client.post(f"/api/dm/campaign/{campaign_id}/delete", headers=auth_headers)
    assert response.status_code in (403, 404)
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT 1 FROM dm_campaigns WHERE id=?", (campaign_id,)).fetchone() is not None
    db.close()


def test_user_cannot_update_another_users_npc(client, seeded_db, auth_headers):
    npc_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_npcs (user_id, name) VALUES (?, ?)",
        (2, "Private NPC"),
    )
    response = client.post(
        f"/api/dm/npc/{npc_id}/update",
        json={"name": "stolen"}, headers=auth_headers,
    )
    assert response.status_code in (403, 404)
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT name FROM dm_npcs WHERE id=?", (npc_id,)).fetchone()[0] == "Private NPC"
    db.close()


def test_user_cannot_delete_another_users_npc(client, seeded_db, auth_headers):
    npc_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_npcs (user_id, name) VALUES (?, ?)",
        (2, "Private NPC"),
    )
    response = client.post(f"/api/dm/npc/{npc_id}/delete", headers=auth_headers)
    assert response.status_code in (403, 404)
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT 1 FROM dm_npcs WHERE id=?", (npc_id,)).fetchone() is not None
    db.close()


def test_user_cannot_update_another_users_encounter(client, seeded_db, auth_headers):
    encounter_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_encounters (user_id, name) VALUES (?, ?)",
        (2, "Private encounter"),
    )
    response = client.post(
        f"/api/dm/encounter/{encounter_id}/update",
        json={"name": "stolen"}, headers=auth_headers,
    )
    assert response.status_code in (403, 404)
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT name FROM dm_encounters WHERE id=?", (encounter_id,)).fetchone()[0] == "Private encounter"
    db.close()


def test_user_cannot_delete_another_users_encounter(client, seeded_db, auth_headers):
    encounter_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_encounters (user_id, name) VALUES (?, ?)",
        (2, "Private encounter"),
    )
    response = client.post(f"/api/dm/encounter/{encounter_id}/delete", headers=auth_headers)
    assert response.status_code in (403, 404)
    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT 1 FROM dm_encounters WHERE id=?", (encounter_id,)).fetchone() is not None
    db.close()


def test_user_character_picker_only_returns_owned_characters(client, seeded_db, auth_headers):
    _insert(
        seeded_db["db_path"],
        "INSERT INTO characters (user_id, name, race, class_name) VALUES (?, ?, ?, ?)",
        (2, "Other character", "Human", "Fighter"),
    )
    response = client.get("/api/dm/user-characters", headers=auth_headers)
    assert response.status_code == 200
    assert all(row["name"] != "Other character" for row in response.json()["characters"])


def test_combat_character_picker_only_returns_owned_characters(client, seeded_db, auth_headers):
    _insert(
        seeded_db["db_path"],
        "INSERT INTO characters (user_id, name, race, class_name) VALUES (?, ?, ?, ?)",
        (2, "Other combat character", "Human", "Fighter"),
    )
    response = client.get("/api/dm/characters-for-combat", headers=auth_headers)
    assert response.status_code == 200
    assert all(row["name"] != "Other combat character" for row in response.json()["characters"])


def test_campaign_detail_rejects_other_users_campaign(client, seeded_db, auth_headers):
    campaign_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_campaigns (user_id, name) VALUES (?, ?)",
        (2, "Private campaign"),
    )
    response = client.get(f"/campaign/{campaign_id}", headers=auth_headers)
    assert response.status_code in (403, 404)


def test_shared_encounter_can_be_viewed_but_not_updated(client, seeded_db, auth_headers):
    encounter_id = _insert(
        seeded_db["db_path"],
        "INSERT INTO dm_encounters (user_id, name, shared) VALUES (?, ?, 1)",
        (2, "Shared encounter"),
    )
    view = client.get(f"/api/dm/encounter/{encounter_id}", headers=auth_headers)
    assert view.status_code in (200, 404)
    update = client.post(
        f"/api/dm/encounter/{encounter_id}/update",
        json={"name": "stolen"}, headers=auth_headers,
    )
    assert update.status_code in (403, 404)
