"""Unit tests for the shared rules/access primitives.

These cover the three things Stage 2 of the 2026-11-14 refactor centralised:
5e arithmetic (``services/rules.py``), JSON column hydration
(``services/schema.py``) and campaign standing
(``services/campaign_access.py``).
"""

import json
import sqlite3

import pytest

from services.rules import PROFICIENCY_BONUS, ability_mod, proficiency_bonus
from services.schema import (
    HAS_PORTRAIT_SQL, HYDRATE_JSON_FIELDS, JSON_DICT_FIELDS, hydrate_json_columns,
)
from services.campaign_access import campaign_role


class TestAbilityMod:
    @pytest.mark.parametrize("score,expected", [
        (1, -5), (3, -4), (8, -1), (9, -1), (10, 0), (11, 0),
        (13, 1), (18, 4), (20, 5), (30, 10),
    ])
    def test_phb_table(self, score, expected):
        assert ability_mod(score) == expected

    @pytest.mark.parametrize("score,expected", [
        (-1, -6), (-2, -6), (-5, -8), (0, -5),
    ])
    def test_negative_scores_floor_not_truncate(self, score, expected):
        # int(-0.5) == 0 is the classic bug; // keeps the floor direction.
        assert ability_mod(score) == expected
        assert ability_mod(score) == (score - 10) // 2

    def test_odd_scores_floor(self):
        assert ability_mod(7) == -2       # -1.5 -> -2
        assert ability_mod(15) == 2

    def test_none_and_garbage_read_as_ten(self):
        assert ability_mod(None) == 0
        assert ability_mod("") == 0
        assert ability_mod("nope") == 0

    def test_numeric_strings(self):
        assert ability_mod("18") == 4


class TestProficiencyBonus:
    def test_full_table(self):
        for level, bonus in PROFICIENCY_BONUS.items():
            assert proficiency_bonus(level) == bonus

    def test_levels_one_to_twenty(self):
        expected = [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [6] * 4
        assert [proficiency_bonus(l) for l in range(1, 21)] == expected

    @pytest.mark.parametrize("level,expected", [
        (0, 2), (-3, 2), (21, 6), (99, 6), (None, 2), ("7", 3),
    ])
    def test_clamped_not_extrapolated(self, level, expected):
        assert proficiency_bonus(level) == expected


class TestHydrateJsonColumns:
    def test_valid_json_strings(self):
        row = {"skills": '["Stealth"]', "spell_slot_data": '{"1": 4}'}
        out = hydrate_json_columns(row)
        assert out["skills"] == ["Stealth"]
        assert out["spell_slot_data"] == {"1": 4}

    def test_already_parsed_values_untouched(self):
        skills, slots = ["Athletics"], {"2": 3}
        row = {"skills": skills, "spell_slot_data": slots}
        out = hydrate_json_columns(row)
        assert out["skills"] is skills
        assert out["spell_slot_data"] is slots

    def test_invalid_json_falls_back_to_default(self):
        row = {"inventory": "{not json", "spell_slot_data": "[1,2]"}
        out = hydrate_json_columns(row)
        assert out["inventory"] == []            # malformed -> list default
        assert out["spell_slot_data"] == [1, 2]  # parsed to the wrong shape, unchanged

    def test_null_and_empty_and_missing(self):
        row = {"skills": None, "inventory": "", "spell_slot_data": None}
        out = hydrate_json_columns(row)
        assert out["skills"] == []
        assert out["inventory"] == []
        assert out["spell_slot_data"] == {}
        assert "features" not in out             # absent keys are not invented

    def test_union_covers_every_shared_field(self):
        row = {f: "[]" for f in HYDRATE_JSON_FIELDS}
        row.update({f: "{}" for f in JSON_DICT_FIELDS})
        out = hydrate_json_columns(row)
        assert all(out[f] == [] for f in HYDRATE_JSON_FIELDS)
        assert all(out[f] == {} for f in JSON_DICT_FIELDS)

    def test_returns_same_object(self):
        row = {"skills": "[]"}
        assert hydrate_json_columns(row) is row

    def test_has_portrait_sql_constant(self):
        assert HAS_PORTRAIT_SQL == "(portrait_url IS NOT NULL AND portrait_url != '') AS has_portrait"
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE t (portrait_url TEXT)")
        db.execute("INSERT INTO t VALUES ('data:image/png;base64,xx'), (''), (NULL)")
        got = [r[0] for r in db.execute(f"SELECT {HAS_PORTRAIT_SQL} FROM t")]
        assert got == [1, 0, 0]
        db.close()


@pytest.fixture()
def campaign_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE characters (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)")
    db.execute("""CREATE TABLE dm_campaigns (
        id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, characters TEXT)""")
    db.execute("""CREATE TABLE dm_campaign_characters (
        campaign_id INTEGER, character_id INTEGER)""")
    # campaign 1 owned by user 1; campaign 2 owned by user 2
    db.execute("INSERT INTO dm_campaigns VALUES (1, 1, 'Mine', ?)", (json.dumps([{"id": 10}]),))
    db.execute("INSERT INTO dm_campaigns VALUES (2, 2, 'Theirs', '[]')")
    db.execute("INSERT INTO characters VALUES (10, 3, 'Roster PC')")
    db.execute("INSERT INTO characters VALUES (11, 3, 'Legacy PC')")
    db.execute("INSERT INTO characters VALUES (12, 9, 'Stranger')")
    db.execute("INSERT INTO dm_campaign_characters VALUES (2, 11)")
    db.commit()
    yield db
    db.close()


class TestCampaignRole:
    def test_missing_campaign_is_none(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 1, "is_admin": 0}, 999) == "none"

    def test_owner_is_dm(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 1, "is_admin": 0}, 1) == "dm"

    def test_non_owner_non_member_is_none(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 42, "is_admin": 0}, 1) == "none"

    def test_admin_dms_any_existing_campaign(self, campaign_db):
        # The old admin SQL dropped the user_id filter entirely.
        assert campaign_role(campaign_db, {"id": 7, "is_admin": 1}, 2) == "dm"

    def test_admin_still_none_on_missing_campaign(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 7, "is_admin": 1}, 999) == "none"

    def test_member_via_roster_json(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 3, "is_admin": 0}, 1) == "member"

    def test_member_via_legacy_join_table(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 3, "is_admin": 0}, 2) == "member"

    def test_member_flag_not_granted_by_other_users_character(self, campaign_db):
        assert campaign_role(campaign_db, {"id": 9, "is_admin": 0}, 1) == "none"

    def test_corrupt_roster_raises(self, campaign_db):
        campaign_db.execute("UPDATE dm_campaigns SET characters='{oops' WHERE id=1")
        campaign_db.commit()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            campaign_role(campaign_db, {"id": 3, "is_admin": 0}, 1)
        assert exc.value.status_code == 500
