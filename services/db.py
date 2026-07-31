"""Small database connection boundary shared by application code."""

from pathlib import Path
import sqlite3


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection with the application's invariants."""
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db
