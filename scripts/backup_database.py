#!/usr/bin/env python3
"""Create and integrity-check a consistent SQLite backup."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(source: Path, backup_dir: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{source.name}.{datetime.now():%Y%m%d%H%M%S}"
    source_db = sqlite3.connect(str(source))
    backup_db = sqlite3.connect(str(destination))
    try:
        source_db.backup(backup_db)
        result = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {result}")
    finally:
        backup_db.close()
        source_db.close()
    return destination


if __name__ == "__main__":
    source = Path(os.environ.get("DND_DB_PATH", Path(__file__).parent.parent / "data" / "characters.db"))
    target = Path(os.environ.get("DND_BACKUP_DIR", Path(__file__).parent.parent / "data" / "backups"))
    print(backup_database(source, target))
