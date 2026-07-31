"""Cross-user access regression tests for owned character data."""

import sqlite3


def test_user_cannot_view_another_users_private_character(client, seeded_db, auth_headers):
    db = sqlite3.connect(seeded_db["db_path"])
    db.execute(
        "INSERT INTO characters (user_id, name, race, class_name) VALUES (?, ?, ?, ?)",
        (2, "Private NPC", "Human", "Fighter"),
    )
    character_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.close()

    response = client.get(f"/character/{character_id}", headers=auth_headers)
    assert response.status_code in (403, 404)


def test_user_cannot_update_another_users_character(client, seeded_db, auth_headers):
    db = sqlite3.connect(seeded_db["db_path"])
    db.execute(
        "INSERT INTO characters (user_id, name, race, class_name) VALUES (?, ?, ?, ?)",
        (2, "Private NPC", "Human", "Fighter"),
    )
    character_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.close()

    response = client.post(
        f"/api/character/{character_id}/update",
        json={"name": "stolen"},
        headers=auth_headers,
    )
    assert response.status_code in (403, 404, 405)

    db = sqlite3.connect(seeded_db["db_path"])
    assert db.execute("SELECT name FROM characters WHERE id = ?", (character_id,)).fetchone()[0] == "Private NPC"
    db.close()
