"""Tests for the active SQLite backup utility (scripts/backup_db.py)."""

import sqlite3

from scripts.backup_db import backup_db


def test_backup_creates_integrity_checked_copy(tmp_path):
    source = tmp_path / "source.db"
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE test (value TEXT)")
    db.execute("INSERT INTO test VALUES ('safe')")
    db.commit()
    db.close()

    destination = backup_db(source, tmp_path / "backups", keep=14)
    assert destination.is_file()
    copied = sqlite3.connect(destination)
    assert copied.execute("SELECT value FROM test").fetchone()[0] == "safe"
    assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copied.close()


def test_backup_rotation_keeps_newest(tmp_path):
    source = tmp_path / "source.db"
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE test (value TEXT)")
    db.execute("INSERT INTO test VALUES ('safe')")
    db.commit()
    db.close()

    # Create 3 backups keeping 2 — oldest should be removed
    backup_db(source, tmp_path / "backups", keep=2)
    backup_db(source, tmp_path / "backups", keep=2)
    backup_db(source, tmp_path / "backups", keep=2)

    remaining = sorted((tmp_path / "backups").glob("characters-*.db"))
    assert len(remaining) == 2
