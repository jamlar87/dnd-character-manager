#!/usr/bin/env python3
"""Automated SQLite backup with rotation for D&D Character Manager.

Uses the SQLite backup API (Connection.backup) so it is safe against
WAL-mode writes and concurrent connections — unlike a plain file copy.

Usage:
    .venv/bin/python3 scripts/backup_db.py            # one backup + rotate
    .venv/bin/python3 scripts/backup_db.py --keep 14  # override retention

Backups land in data/backups/characters-YYYYMMDD-HHMMSS-ffffff.db.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEFAULT_DB = HERE / "data" / "characters.db"
BACKUP_DIR = HERE / "data" / "backups"
DEFAULT_KEEP = 14


def backup_db(db_path: Path, backup_dir: Path, keep: int) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = backup_dir / f"characters-{ts}.db"

    # SQLite backup API — consistent snapshot, safe with WAL + live writes
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # Rotation: keep the newest N backups
    backups = sorted(backup_dir.glob("characters-*.db"), key=lambda p: p.name)
    stale = backups[:-keep] if keep > 0 else backups
    for old in stale:
        old.unlink(missing_ok=True)

    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup D&D Character Manager DB")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"number of backups to keep (default {DEFAULT_KEEP})")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"database path (default {DEFAULT_DB})")
    args = parser.parse_args()

    try:
        dest = backup_db(args.db, BACKUP_DIR, args.keep)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    size = dest.stat().st_size
    print(f"Backup OK: {dest} ({size:,} bytes); keeping {args.keep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
