"""Tests for the SQLite backup utility."""

import sqlite3

from scripts.backup_database import backup_database


def test_backup_database_creates_integrity_checked_copy(tmp_path):
    source = tmp_path / "source.db"
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE test (value TEXT)")
    db.execute("INSERT INTO test VALUES ('safe')")
    db.commit()
    db.close()

    destination = backup_database(source, tmp_path / "backups")
    assert destination.is_file()
    copied = sqlite3.connect(destination)
    assert copied.execute("SELECT value FROM test").fetchone()[0] == "safe"
    assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copied.close()
