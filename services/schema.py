"""Required SQLite schema validation."""

from __future__ import annotations

import sqlite3

REQUIRED_TABLES = {
    "users": {"id", "email", "password_hash"},
    "characters": {"id", "user_id", "name", "class_name"},
    "character_spells": {"id", "character_id", "spell_name"},
    "sessions": {"id", "user_id", "token", "expires_at"},
    "schema_migrations": {"version", "applied_at"},
}


def validate_schema(db: sqlite3.Connection, *, raise_on_error: bool = False) -> list[str]:
    errors: list[str] = []
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in REQUIRED_TABLES.items():
        if table not in tables:
            errors.append(f"missing table: {table}")
            continue
        found = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        for column in columns - found:
            errors.append(f"missing column: {table}.{column}")
    if raise_on_error and errors:
        raise RuntimeError("Schema validation failed: " + "; ".join(errors))
    return errors

__all__ = ["validate_schema"]
