"""Schema validation service tests."""

import sqlite3

import pytest

from services.schema import validate_schema


def test_validate_schema_accepts_initialized_database(test_db):
    db = sqlite3.connect(test_db)
    assert validate_schema(db) == []
    db.close()


def test_validate_schema_reports_missing_table(tmp_path):
    db = sqlite3.connect(tmp_path / "empty.db")
    errors = validate_schema(db)
    assert any("users" in error for error in errors)
    db.close()


def test_validate_schema_reports_missing_required_column(tmp_path):
    db = sqlite3.connect(tmp_path / "partial.db")
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    db.commit()
    errors = validate_schema(db)
    assert any("users.email" in error for error in errors)
    db.close()


def test_validate_schema_can_raise_for_invalid_schema(tmp_path):
    db = sqlite3.connect(tmp_path / "empty.db")
    with pytest.raises(RuntimeError, match="Schema validation failed"):
        validate_schema(db, raise_on_error=True)
    db.close()
