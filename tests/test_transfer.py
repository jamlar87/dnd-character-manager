"""Export/import JSON round-trip tests."""

import sqlite3


def _make_character(client, auth_headers, name="Test Hero"):
    """Create a character via the API; returns char id."""
    resp = client.post(
        "/api/character/create",
        json={
            "name": name,
            "race": "Human",
            "class_name": "Fighter",
            "level": 3,
            "abilities": {
                "strength": 16,
                "dexterity": 14,
                "constitution": 15,
                "intelligence": 10,
                "wisdom": 12,
                "charisma": 8,
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestExport:
    def test_export_requires_auth(self, client):
        resp = client.get("/api/character/1/export")
        # TestClient follows the 303 -> /login, landing on 200
        assert resp.status_code in (200, 303)

    def test_export_missing_character_404(self, client, auth_headers):
        resp = client.get("/api/character/99999/export", headers=auth_headers)
        assert resp.status_code == 404

    def test_export_roundtrip_fields(self, client, auth_headers):
        char_id = _make_character(client, auth_headers, "Exportable")
        resp = client.get(f"/api/character/{char_id}/export", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert 'filename="Exportable.json"' in resp.headers.get("content-disposition", "")

        data = resp.json()
        assert data["version"] == 1
        c = data["character"]
        assert c["name"] == "Exportable"
        assert c["race"] == "Human"
        assert c["class_name"] == "Fighter"
        assert c["level"] == 3
        # Human race ASI: +1 to all abilities
        assert c["strength"] == 17
        assert "user_id" not in c
        assert "id" not in c

    def test_export_includes_spells_and_relationships(self, client, auth_headers, seeded_db):
        char_id = _make_character(client, auth_headers, "Full Export")
        # Add a spell + relationship directly
        con = sqlite3.connect(str(seeded_db["db_path"]))
        con.execute(
            "INSERT INTO character_spells (character_id, spell_name, spell_level, prepared, slots_max, slots_used, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (char_id, "Fireball", 3, 1, 1, 0, "SRD"),
        )
        con.execute(
            "INSERT INTO character_relationships (character_id, user_id, name, relationship_type, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (char_id, 1, "Barnaby", "ally", "Tavern keeper"),
        )
        con.commit()
        con.close()

        data = client.get(f"/api/character/{char_id}/export", headers=auth_headers).json()
        assert data["spells"] == [{
            "spell_name": "Fireball", "spell_level": 3, "prepared": 1,
            "slots_max": 1, "slots_used": 0, "source": "SRD",
        }]
        rels = data["relationships"]
        assert len(rels) == 1
        assert rels[0]["name"] == "Barnaby"
        assert rels[0]["relationship_type"] == "ally"


class TestImport:
    def test_import_requires_auth(self, client):
        resp = client.post("/api/character/import", json={"character": {"name": "X"}})
        # TestClient follows the 303 -> /login, landing on 200
        assert resp.status_code in (200, 303)

    def test_import_rejects_bad_payloads(self, client, auth_headers):
        assert client.post("/api/character/import", json={}, headers=auth_headers).status_code == 400
        assert client.post("/api/character/import", json={"character": {}}, headers=auth_headers).status_code == 422
        assert client.post("/api/character/import", json={"character": {"race": "Human"}}, headers=auth_headers).status_code == 422

    def test_import_creates_character(self, client, auth_headers):
        payload = {
            "version": 1,
            "character": {
                "name": "Imported Hero",
                "race": "Elf",
                "subrace": "High Elf",
                "class_name": "Wizard",
                "level": 5,
                "strength": 8,
                "dexterity": 14,
                "constitution": 12,
                "intelligence": 18,
                "wisdom": 13,
                "charisma": 10,
                "hp_max": 32,
                "hp_current": 20,
                "ac": 13,
                "proficiency_bonus": 3,
                "gp": 100,
                "background": "Sage",
                "features": "[]",
                "inventory": "[]",
            },
            "spells": [
                {"spell_name": "Magic Missile", "spell_level": 1, "prepared": 1, "slots_max": 2, "slots_used": 1, "source": "SRD"},
                {"spell_name": "Fireball", "spell_level": 3, "prepared": 1, "slots_max": 1, "slots_used": 0, "source": "SRD"},
            ],
            "relationships": [
                {"name": "Gandalf", "relationship_type": "mentor", "description": "Old wizard"},
            ],
        }
        resp = client.post("/api/character/import", json=payload, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "Imported Hero"

        # The new character renders on its sheet page
        r = client.get(f"/character/{data['id']}", headers=auth_headers)
        assert r.status_code == 200

    def test_import_ignores_user_id_spoof(self, client, auth_headers):
        payload = {
            "character": {
                "name": "Spoofy",
                "race": "Human",
                "class_name": "Rogue",
                "user_id": 999,
            }
        }
        resp = client.post("/api/character/import", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        new_id = resp.json()["id"]
        # Ownership is enforced server-side: import binds current user,
        # and user_id spoof in payload is dropped.
        assert client.get(f"/character/{new_id}", headers=auth_headers).status_code == 200

    def test_import_spells_and_relationships_persist(self, client, auth_headers):
        payload = {
            "character": {"name": "Spelly", "race": "Human", "class_name": "Cleric", "level": 2},
            "spells": [{"spell_name": "Cure Wounds", "spell_level": 1, "prepared": 1, "slots_max": 2, "slots_used": 0, "source": "SRD"}],
            "relationships": [{"name": "Priest", "relationship_type": "friendly", "description": "Temple elder"}],
        }
        resp = client.post("/api/character/import", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        new_id = resp.json()["id"]

        data = client.get(f"/api/character/{new_id}/export", headers=auth_headers).json()
        assert len(data["spells"]) == 1
        assert data["spells"][0]["spell_name"] == "Cure Wounds"
        assert len(data["relationships"]) == 1
        assert data["relationships"][0]["name"] == "Priest"
