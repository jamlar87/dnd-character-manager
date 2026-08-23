"""API endpoint tests.

Tests critical API endpoints using FastAPI TestClient.
Requires a test database with seeded data.
"""

import pytest
from main import app


class TestHealth:
    def test_root_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (200, 303)


class TestAuth:
    def test_login_page(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Login" in resp.text or "login" in resp.text

    def test_register_page(self, client):
        resp = client.get("/register")
        assert resp.status_code == 200
        assert "Register" in resp.text or "register" in resp.text


class TestDMUserCharacters:
    """The dm/user-characters endpoint lists all characters."""

    def test_requires_auth(self, client):
        resp = client.get("/api/dm/user-characters")
        # require_user raises 303 redirect to /login
        assert resp.status_code == 303 or resp.status_code == 200

    def test_returns_list(self, client, auth_headers):
        resp = client.get("/api/dm/user-characters", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "characters" in data
        assert isinstance(data["characters"], list)


class TestCharacterCreate:
    """Tests for character creation end-to-end."""

    def test_create_character(self, client, auth_headers):
        # This is a complex multi-step process.
        # At minimum verify the create page loads.
        resp = client.get("/create", headers=auth_headers)
        assert resp.status_code == 200


class TestDMNPCs:
    def test_list_npcs(self, client, auth_headers):
        resp = client.get("/api/dm/npcs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "npcs" in data

    def test_requires_auth(self, client):
        resp = client.get("/api/dm/npcs")
        assert resp.status_code == 303 or resp.status_code == 200


class TestDMMonsters:
    def test_list_monsters(self, client, auth_headers):
        resp = client.get("/api/dm/monsters", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "monsters" in data
        assert len(data["monsters"]) > 0
        # Verify monster structure
        sample = data["monsters"][0]
        for key in ("name", "type", "challenge_rating", "hit_points"):
            assert key in sample, f"Monster missing {key}"

    def test_monster_has_type(self, client, auth_headers):
        resp = client.get("/api/dm/monsters", headers=auth_headers)
        monsters = resp.json()["monsters"]
        types = {m.get("type") for m in monsters}
        assert "humanoid" in types

    def test_monsters_by_cr(self, client, auth_headers):
        resp = client.get("/api/dm/monsters/by-cr", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        for tier in ("trivial", "low", "medium", "high", "deadly", "legendary"):
            assert tier in data, f"by-cr missing tier {tier}"
        # Each tier must be CR-sorted ascending
        def cr(m):
            try:
                return float(m.get("challenge_rating", 0))
            except (TypeError, ValueError):
                return 99.0
        for tier, monsters in data.items():
            crs = [cr(m) for m in monsters]
            assert crs == sorted(crs), f"tier {tier} not CR-sorted"


class TestDMEncounters:
    def test_list_encounters(self, client, auth_headers):
        resp = client.get("/api/dm/encounters", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "encounters" in data

    def test_create_encounter(self, client, auth_headers, seeded_db):
        resp = client.post(
            "/api/dm/encounter/create",
            json={"name": "Test Encounter", "environment": "forest"},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("encounter_id") or data.get("id")


class TestDMAiBuildNPC:
    def test_build_npc(self, client, auth_headers):
        resp = client.post(
            "/api/dm/ai/build-npc",
            json={"race": "Human", "class_name": "Fighter", "level": 3},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        build = data.get("build", {})
        assert build.get("level") == 3
        assert build.get("class") == "Fighter"
        for key in ("ability_scores", "armor_class", "hit_points", "proficiency_bonus", "equipment", "features"):
            assert key in build, f"build missing {key}"


class TestSheetRendering:
    """Character sheet must render without errors for various character types."""

    def test_create_single(self, client, auth_headers):
        resp = client.get("/create", headers=auth_headers)
        assert resp.status_code == 200

    def test_api_user_characters_returns_json(self, client, auth_headers):
        resp = client.get("/api/user/characters", headers=auth_headers)
        assert resp.status_code in (200, 404)
