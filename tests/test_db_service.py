"""Tests for the extracted database connection boundary."""

import sqlite3

from services.db import connect


def test_connect_configures_sqlite_invariants(tmp_path):
    db = connect(tmp_path / "test.db")
    db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    assert isinstance(db.row_factory, type(sqlite3.Row))
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    db.close()
