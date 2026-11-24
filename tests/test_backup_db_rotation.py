"""Regression: DB backups must be single-file and rotation must prune them.

The backup helper copies a WAL-mode database, so every snapshot used to come
with ``-wal``/``-shm`` sidecars that rotation (globbing ``characters-*.db``)
never deleted — the directory grew one stale triplet per run.
"""

import importlib.util
import sqlite3
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_db.py"
_spec = importlib.util.spec_from_file_location("backup_db", _SCRIPT)
backup_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_db)
backup_db = backup_db.backup_db


def test_backup_is_single_file_and_rotates(tmp_path):
    src = tmp_path / "src.db"
    con = sqlite3.connect(src)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (a INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()

    bdir = tmp_path / "backups"
    bdir.mkdir()
    for _ in range(4):
        backup_db(src, bdir, keep=2)

    files = sorted(p.name for p in bdir.iterdir())
    dbs = [f for f in files if f.endswith(".db")]
    sidecars = [f for f in files if f.endswith(("-wal", "-shm"))]
    assert len(dbs) == 2, files
    assert sidecars == [], files

    out = sqlite3.connect(str(bdir / dbs[-1]))
    assert out.execute("SELECT a FROM t").fetchone() == (1,)
    out.close()
