"""Schema migration and validation regression tests."""

import sqlite3


def test_schema_migrations_table_records_current_schema(test_db):
    db = sqlite3.connect(test_db)
    row = db.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] >= 1
    db.close()


def test_required_schema_objects_exist(test_db):
    db = sqlite3.connect(test_db)
    tables = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"users", "characters", "character_spells", "sessions", "schema_migrations"} <= tables
    columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    assert {"token", "expires_at"} <= columns
    db.close()


def test_schema_migration_is_idempotent(test_db):
    from main import init_db
    init_db()
    init_db()
    db = sqlite3.connect(test_db)
    versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == sorted(set(versions))
    db.close()
