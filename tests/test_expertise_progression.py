"""Expertise progression regression tests.

EXPERTISE_LEVELS is consumed as {"levels": [...], "options": ...} by
services.leveling.get_expertise_count / get_expertise_options, the level-up
wizard, the sheet edit modal and templates/create.html JS. A stale second
definition in routes/characters/all.py (rich shape) was shadowed by an
int-keyed copy in data.py (wrong shape AND wrong levels), so:

  * get_expertise_count() always returned 0  -> sheet edit modal dead,
    de-level wiped every pick, level-up wizard never offered Expertise;
  * create.html's L1 Expertise picker never appeared for Rogues.

These tests pin the PHB 2014 progression and every render path.
"""

import json
import re
import sqlite3

import pytest

from main import EXPERTISE_LEVELS
from services.leveling import get_expertise_count, get_expertise_options


# ── helpers (mirrors tests/test_name_and_scope_regressions.py) ──────────────

def _connect(seeded_db):
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.row_factory = sqlite3.Row
    return con


def make_char(seeded_db, name, user_id=1, **cols):
    cols.setdefault("race", "Human")
    cols.setdefault("class_name", "Fighter")
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


def char_field(seeded_db, char_id, column):
    con = _connect(seeded_db)
    row = con.execute(f"SELECT {column} FROM characters WHERE id=?", (char_id,)).fetchone()
    con.close()
    return row[column] if row else None


class TestExpertiseData:
    def test_shape_is_the_rich_contract(self):
        for key, entry in EXPERTISE_LEVELS.items():
            assert isinstance(entry, dict), f"{key} must be a dict"
            assert "levels" in entry, f"{key} needs a levels list"
            assert isinstance(entry["levels"], list)
            assert all(isinstance(l, int) for l in entry["levels"])

    def test_phb_levels(self):
        assert EXPERTISE_LEVELS["Rogue"]["levels"] == [1, 6]    # PHB p.96
        assert EXPERTISE_LEVELS["Bard"]["levels"] == [3, 10]    # PHB p.54
        assert EXPERTISE_LEVELS["Knowledge Domain"]["levels"] == [1]  # PHB p.59

    def test_no_stale_shadow_definition_in_all_module(self):
        import routes.characters.all as all_module
        assert all_module.EXPERTISE_LEVELS is EXPERTISE_LEVELS


@pytest.mark.parametrize("cls,level,expected", [
    ("Rogue", 1, 2), ("Rogue", 5, 2), ("Rogue", 6, 4), ("Rogue", 20, 4),
    ("Bard", 2, 0), ("Bard", 3, 2), ("Bard", 9, 2), ("Bard", 10, 4),
    ("Fighter", 20, 0), ("Wizard", 10, 0),
])
def test_expertise_count_by_level(cls, level, expected):
    assert get_expertise_count(cls, level) == expected


def test_knowledge_domain_cleric_gets_expertise():
    assert get_expertise_count("Cleric", 1, "Knowledge Domain") == 2
    assert get_expertise_count("Cleric", 1, "Life Domain") == 0


def test_rogue_options_include_thieves_tools():
    opts = get_expertise_options("Rogue", "", ["Stealth", "Perception"])
    assert opts == ["Stealth", "Perception", "Thieves' Tools"]


def test_bard_options_are_character_skills_only():
    assert get_expertise_options("Bard", "", ["Stealth", "Perception"]) == ["Stealth", "Perception"]


def test_knowledge_domain_options_are_the_four_lore_skills():
    assert get_expertise_options("Cleric", "Knowledge Domain", ["Stealth"]) == [
        "Arcana", "History", "Nature", "Religion",
    ]


class TestLevelUpWizardOffersExpertise:
    def test_rogue_5_to_6_offers_two_picks(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Rogue Six", class_name="Rogue", level=5,
            class_levels='{"Rogue": 5}', skills=json.dumps(["Stealth", "Perception"]),
        )
        r = client.get(f"/api/character/{cid}/level-up-info?target=6", headers=auth_headers)
        assert r.status_code == 200, r.text
        exp = r.json().get("expertise")
        assert exp is not None, "Expertise step missing from the level-up wizard"
        assert exp["picks_gained"] == 2
        assert exp["level_count"] == 4
        assert exp["levels"] == [6]
        assert "Thieves' Tools" in exp["options"]

    def test_bard_2_to_3_offers_expertise(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Bard Three", class_name="Bard", level=2,
            class_levels='{"Bard": 2}', skills=json.dumps(["Stealth", "Perception"]),
        )
        exp = client.get(
            f"/api/character/{cid}/level-up-info?target=3", headers=auth_headers
        ).json().get("expertise")
        assert exp is not None
        assert exp["picks_gained"] == 2

    def test_rogue_5_to_6_still_offers_other_steps(self, client, auth_headers, seeded_db):
        """Guard against the expertise block aborting the rest of the payload."""
        cid = make_char(
            seeded_db, "Rogue Payload", class_name="Rogue", level=5,
            class_levels='{"Rogue": 5}', skills=json.dumps(["Stealth"]),
        )
        body = client.get(f"/api/character/{cid}/level-up-info?target=6", headers=auth_headers).json()
        for key in ("target_level", "current_level", "class_name", "class_level",
                    "hp", "all_features", "expertise"):
            assert key in body, f"{key} missing from level-up-info"


class TestSheetExpertiseEditor:
    def test_rogue_sheet_ships_count_and_options(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Rogue Sheet", class_name="Rogue", level=5,
            class_levels='{"Rogue": 5}', skills=json.dumps(["Stealth", "Perception"]),
            expertise_skills=json.dumps(["Stealth", "Perception"]),
        )
        html = client.get(f"/character/{cid}", headers=auth_headers).text
        m = re.search(r"const EXPERTISE_COUNT\s*=\s*(\d+)", html)
        assert m, "expertise_count never reached the sheet template"
        assert int(m.group(1)) == 2, "Rogue 5 should have 2 expertise picks"
        assert "Thieves" in html, "Rogue expertise options missing Thieves' Tools"

    def test_edit_expertise_accepts_thieves_tools(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Rogue Edit", class_name="Rogue", level=5,
            class_levels='{"Rogue": 5}', skills=json.dumps(["Stealth", "Perception"]),
            expertise_skills="[]",
        )
        r = client.post(f"/api/character/{cid}/edit-expertise",
                        json={"expertise_skills": ["Stealth", "Thieves' Tools"]},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        stored = json.loads(char_field(seeded_db, cid, "expertise_skills"))
        assert stored == ["Stealth", "Thieves' Tools"]

    def test_edit_expertise_rejects_invalid_pick(self, client, auth_headers, seeded_db):
        cid = make_char(
            seeded_db, "Rogue Bad Pick", class_name="Rogue", level=5,
            class_levels='{"Rogue": 5}', skills=json.dumps(["Stealth"]),
            expertise_skills="[]",
        )
        r = client.post(f"/api/character/{cid}/edit-expertise",
                        json={"expertise_skills": ["Smith's Tools"]},
                        headers=auth_headers)
        assert r.status_code == 400


class TestDeLevelKeepsExpertise:
    def test_rogue_6_to_5_keeps_two_picks(self, client, auth_headers, seeded_db):
        picks = ["Stealth", "Perception", "Insight", "Investigation"]
        cid = make_char(
            seeded_db, "Rogue Down", class_name="Rogue", level=6,
            class_levels='{"Rogue": 6}', skills=json.dumps(picks),
            expertise_skills=json.dumps(picks), hp_max=45, hp_current=45,
            asi_history="[]", feature_data="[]",
        )
        r = client.post(f"/api/character/{cid}/de-level",
                        json={"target_level": 5, "class_to_level": "Rogue"},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        stored = json.loads(char_field(seeded_db, cid, "expertise_skills"))
        assert stored == ["Stealth", "Perception"], f"expertise wiped by de-level: {stored}"

    def test_rogue_6_to_1_keeps_first_two(self, client, auth_headers, seeded_db):
        picks = ["Stealth", "Perception", "Insight", "Investigation"]
        cid = make_char(
            seeded_db, "Rogue Way Down", class_name="Rogue", level=6,
            class_levels='{"Rogue": 6}', skills=json.dumps(picks),
            expertise_skills=json.dumps(picks), hp_max=45, hp_current=45,
            asi_history="[]", feature_data="[]",
        )
        r = client.post(f"/api/character/{cid}/de-level",
                        json={"target_level": 1, "class_to_level": "Rogue"},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        stored = json.loads(char_field(seeded_db, cid, "expertise_skills"))
        assert stored == ["Stealth", "Perception"]
