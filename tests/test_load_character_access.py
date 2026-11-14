"""Regression tests for ``load_character`` — the single character-access check.

The interesting bug: callers may pass ``columns=`` to dodge the multi-MB portrait
blob, and the save path passed ``columns="id"``. The access check then read a
missing ``user_id`` and denied the owner, which turned every sheet save into a
silent no-op. These tests pin the access decision to the row's real owner, not to
whichever columns the caller asked for.
"""

import sqlite3

import pytest

from routes.characters.helpers import load_character

OWNER = {"id": 1, "is_admin": 0, "username": "owner"}
OTHER = {"id": 2, "is_admin": 0, "username": "other"}
STRANGER = {"id": 3, "is_admin": 0, "username": "stranger"}
ADMIN = {"id": 9, "is_admin": 1, "username": "admin"}


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            shared INTEGER DEFAULT 0,
            portrait_url TEXT
        );
        CREATE TABLE dm_campaigns (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            characters TEXT DEFAULT '[]'
        );
        CREATE TABLE dm_campaign_characters (
            campaign_id INTEGER,
            character_id INTEGER
        );
        INSERT INTO characters (id, user_id, name, shared, portrait_url)
            VALUES (10, 1, 'Mine', 0, 'data:image/png;base64,AAA'),
                   (11, 2, 'Theirs', 1, NULL),
                   (12, 2, 'Private', 0, NULL);
        INSERT INTO dm_campaigns (id, user_id, characters)
            VALUES (1, 1, '[{"id": 12, "name": "Private"}]');
        INSERT INTO dm_campaign_characters (campaign_id, character_id) VALUES (1, 11);
        """
    )
    yield conn
    conn.close()


class TestOwnerMode:
    def test_owner_allowed_with_star_columns(self, db):
        assert load_character(db, OWNER, 10, "owner")["name"] == "Mine"

    def test_owner_allowed_when_caller_only_asked_for_id(self, db):
        # The save path's exact call. Before the fix this returned None and the
        # UPDATE never ran for a legitimate owner.
        row = load_character(db, OWNER, 10, "owner", columns="id")
        assert row is not None
        assert row["id"] == 10

    def test_owner_check_still_reads_real_owner_when_columns_are_narrow(self, db):
        row = load_character(db, OWNER, 10, "owner", columns="id")
        assert row["user_id"] == 1

    def test_non_owner_denied(self, db):
        assert load_character(db, OTHER, 10, "owner", columns="id") is None

    def test_non_owner_denied_via_star_columns(self, db):
        assert load_character(db, OTHER, 10, "owner") is None

    def test_admin_passes_owner_mode(self, db):
        assert load_character(db, ADMIN, 11, "owner", columns="id") is not None

    def test_missing_character_returns_none(self, db):
        assert load_character(db, OWNER, 999, "owner", columns="id") is None


class TestSharedMode:
    def test_owner_reads_own(self, db):
        assert load_character(db, OWNER, 10, "shared")["name"] == "Mine"

    def test_stranger_reads_shared_character(self, db):
        assert load_character(db, OWNER, 11, "shared")["name"] == "Theirs"

    def test_stranger_reads_shared_character_with_narrow_columns(self, db):
        row = load_character(db, OWNER, 11, "shared", columns="name, portrait_url")
        assert row["name"] == "Theirs"
        assert row["shared"] == 1     # required by the decision, added back in

    def test_stranger_denied_unshared_character(self, db):
        assert load_character(db, OWNER, 12, "shared", columns="name, portrait_url") is None


class TestDmMode:
    def test_dm_reads_character_on_roster(self, db):
        assert load_character(db, OWNER, 12, "dm")["name"] == "Private"

    def test_dm_reads_character_via_legacy_join_table(self, db):
        assert load_character(db, OWNER, 11, "dm")["name"] == "Theirs"

    def test_dm_also_gets_plain_shared_characters(self, db):
        # `dm` is a superset of `shared`, as the sheet page relies on.
        db.execute("UPDATE characters SET shared=1 WHERE id=12")
        assert load_character(db, STRANGER, 12, "dm") is not None

    def test_non_dm_denied(self, db):
        db.execute("UPDATE characters SET shared=0 WHERE id=12")
        assert load_character(db, STRANGER, 12, "dm") is None

    def test_owner_still_passes_dm_mode(self, db):
        assert load_character(db, OTHER, 12, "dm", columns="id") is not None


class TestModeValidation:
    def test_unknown_mode_raises(self, db):
        with pytest.raises(ValueError):
            load_character(db, OWNER, 10, "superuser")
