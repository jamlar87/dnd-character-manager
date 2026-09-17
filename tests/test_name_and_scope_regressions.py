"""Regression tests for undefined-name (NameError 500) bugs + cross-user write holes.

The monolith extraction repeatedly dropped imports and let route handlers read
character rows by id without an ownership scope. pytest stayed green through all
of it because these paths had no coverage.

The pyflakes sweep below is the standing guard: it is the ONLY detector that
catches the undefined-name class of bug (pyflakes flags it, pytest does not).
"""

import json
import re
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYFLAKES_TARGETS = ["main.py", "routes", "services"]


# ── helpers ────────────────────────────────────────────────────────────────

def _connect(seeded_db):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.row_factory = sqlite3.Row
    return con


def make_char(seeded_db, name, user_id=1, **cols):
    """Insert a character row and return its id. `race` defaults (NOT NULL)."""
    cols.setdefault("race", "Human")
    keys = list(cols)
    con = _connect(seeded_db)
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
    """Create a non-admin user (with session) and return (user_id, token)."""
    from main import _hash
    token = secrets.token_hex(32)
    con = _connect(seeded_db)
    con.execute(
        "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
        (email, _hash("Test1234!"), is_admin),
    )
    uid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (uid, token))
    con.commit()
    con.close()
    return uid, token


def headers_for(token):
    return {
        "Cookie": f"dnd_token={token}; csrf_token=test-csrf-token",
        "X-CSRF-Token": "test-csrf-token",
    }


def char_field(seeded_db, char_id, column):
    con = _connect(seeded_db)
    row = con.execute(f"SELECT {column} FROM characters WHERE id=?", (char_id,)).fetchone()
    con.close()
    return row[column] if row else None


# ── static guard ───────────────────────────────────────────────────────────

class TestNoUndefinedNames:
    """pyflakes over the app modules must stay clean of undefined names."""

    def test_no_undefined_names(self):
        pytest.importorskip("pyflakes")
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", *PYFLAKES_TARGETS],
            cwd=REPO, capture_output=True, text=True,
        )
        report = proc.stdout + proc.stderr
        bad = [ln for ln in report.splitlines() if "undefined name" in ln]
        assert bad == [], "undefined names are runtime NameError 500s:\n" + "\n".join(bad)


# ── /api/character/{id}/toggle-attune ──────────────────────────────────────

class TestToggleAttune:
    def test_attune_equipped_item_requiring_attunement(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Attune Test", class_name="Fighter", level=5,
            equipped=json.dumps([{"name": "Cloak of Protection"}]),
            attuned_items="[]",
        )
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Cloak of Protection"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["action"] == "attuned"
        assert body["attuned"] == ["Cloak of Protection"]
        assert body["slots_used"] == 1

    def test_rejects_item_without_attunement(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "No Attune", class_name="Fighter", level=5,
            equipped=json.dumps([{"name": "Bag of Holding"}]),
            attuned_items="[]",
        )
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Bag of Holding"}, headers=auth_headers)
        assert r.status_code == 400
        assert "does not require attunement" in r.json()["error"]

    def test_other_users_cannot_attune(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Victim Char", class_name="Fighter", level=5,
            equipped=json.dumps([{"name": "Cloak of Protection"}]),
            attuned_items="[]",
        )
        _uid, token = add_user(seeded_db, "attune-attacker@test.com")
        r = client.post(f"/api/character/{cid}/toggle-attune",
                        json={"item": "Cloak of Protection"}, headers=headers_for(token))
        assert r.status_code == 404
        assert json.loads(char_field(seeded_db, cid, "attuned_items")) == []


# ── /api/character/{id}/add-spell, toggle-prepared, unlearn-spell ──────────

class TestSpellWriteEndpointsScope:
    def test_add_spell_then_toggle_prepared_then_unlearn(self, client, auth_headers, seeded_db):
        cid = make_char(seeded_db, "Caster", class_name="Wizard", level=5)
        r = client.post(f"/api/character/{cid}/add-spell",
                        json={"name": "Magic Missile", "level": 1, "prepared": True},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        spell_id = r.json()["id"]

        r = client.post(f"/api/character/{cid}/toggle-prepared",
                        json={"id": spell_id, "prepared": False}, headers=auth_headers)
        assert r.status_code == 200, r.text

        r = client.post(f"/api/character/{cid}/unlearn-spell",
                        json={"id": spell_id}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["spell_name"] == "Magic Missile"

        con = _connect(seeded_db)
        left = con.execute("SELECT COUNT(*) FROM character_spells WHERE character_id=?",
                           (cid,)).fetchone()[0]
        con.close()
        assert left == 0

    def test_add_spell_rejects_other_users_character(self, client, seeded_db):
        cid = make_char(seeded_db, "Wizard Victim", class_name="Wizard", level=5)
        _uid, token = add_user(seeded_db, "spell-attacker@test.com")
        r = client.post(f"/api/character/{cid}/add-spell",
                        json={"name": "Fireball", "level": 3, "prepared": True},
                        headers=headers_for(token))
        assert r.status_code == 404
        con = _connect(seeded_db)
        n = con.execute("SELECT COUNT(*) FROM character_spells WHERE character_id=?",
                        (cid,)).fetchone()[0]
        con.close()
        assert n == 0

    def test_toggle_prepared_and_unlearn_reject_other_users_character(self, client, auth_headers, seeded_db):
        cid = make_char(seeded_db, "Owner Casts", class_name="Wizard", level=5)
        r = client.post(f"/api/character/{cid}/add-spell",
                        json={"name": "Shield", "level": 1, "prepared": True},
                        headers=auth_headers)
        spell_id = r.json()["id"]
        _uid, token = add_user(seeded_db, "spell-attacker2@test.com")
        h = headers_for(token)
        assert client.post(f"/api/character/{cid}/toggle-prepared",
                           json={"id": spell_id, "prepared": False}, headers=h).status_code == 404
        assert client.post(f"/api/character/{cid}/unlearn-spell",
                           json={"id": spell_id}, headers=h).status_code == 404
        con = _connect(seeded_db)
        row = con.execute("SELECT prepared FROM character_spells WHERE id=?", (spell_id,)).fetchone()
        con.close()
        assert row is not None and row[0] == 1


# ── /api/character/{id}/summons (was NameError: time) ──────────────────────
class TestCreateSummon:
    def test_create_summon_returns_generated_id(self, client, auth_headers, seeded_db):
        cid = make_char(seeded_db, "Summoner", class_name="Wizard", level=5, summons="[]")
        r = client.post(f"/api/character/{cid}/summons",
                        json={"name": "Wolf", "form": "Wolf", "hp_max": 11},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        summon = r.json()["summon"]
        assert re.fullmatch(r"summon_\d+", summon["id"]), summon
        assert summon["name"] == "Wolf"

        listed = client.get(f"/api/character/{cid}/summons", headers=auth_headers).json()
        assert [s["id"] for s in listed["summons"]] == [summon["id"]]


# ── /api/character/{id}/apply-level-up (was NameError: class_level) ─────────

class TestLegacyFlatMetamagicLevelUp:
    """Legacy frontends send metamagic as a flat list, not a per-level dict."""

    def test_flat_metamagic_list_levels_up(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Sorcerer Legacy", class_name="Sorcerer", level=2,
            class_levels='{"Sorcerer": 2}', constitution=12, charisma=16,
            hp_max=14, hp_current=14, ac=12, skills="[]",
            metamagic="[]", metamagic_history="[]", feature_data="[]",
            asi_history="[]", summons="[]",
        )
        r = client.post(
            f"/api/character/{cid}/apply-level-up",
            json={
                "target_level": 3,
                "class_to_level": "Sorcerer",
                "metamagic": ["careful_spell", "twinned_spell"],
                "hp_choices": {"3": "average"},
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        stored = json.loads(char_field(seeded_db, cid, "metamagic"))
        assert sorted(stored) == ["careful_spell", "twinned_spell"]
        history = json.loads(char_field(seeded_db, cid, "metamagic_history"))
        assert history and history[0]["level"] == 3
        assert sorted(history[0]["choices"]) == ["careful_spell", "twinned_spell"]


# ── manual search / AI summary endpoints must not be anonymous ─────────────

class TestManualSearchRequiresAuth:
    """These consume LLM quota and expose manual text; the nav JS already
    expects a non-JSON reply and tells the user to log in."""

    ENDPOINTS = [
        ("/api/dm/search-manuals", {"query": "goblin"}),
        ("/api/dm/search-manuals/summarize", {"query": "goblin"}),
        ("/api/ai/summary/pdf", {"query": "x", "summary": "y"}),
    ]

    @pytest.mark.parametrize("path,payload", ENDPOINTS)
    def test_anonymous_post_is_not_served(self, client, path, payload):
        r = client.post(path, json=payload, follow_redirects=False)
        assert r.status_code in (303, 401, 403), f"{path} served an anonymous request: {r.status_code}"

    @pytest.mark.parametrize("path,payload", ENDPOINTS)
    def test_authenticated_post_is_served(self, client, auth_headers, path, payload):
        r = client.post(path, json=payload, headers=auth_headers)
        assert r.status_code == 200, r.text
