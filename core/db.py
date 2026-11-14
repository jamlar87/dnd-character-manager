"""SQLite access: the FastAPI dependency, the DB path, JSON-column helper."""
from __future__ import annotations

import json
import sqlite3

from core import config  # DB_PATH is read live: tests repoint it

# ── DB ──────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    from services.db import connect
    return connect(config.DB_PATH)

def _json_list(value, default=None):
    """Parse a JSON TEXT column that may be SQL NULL or junk without raising.

    `json.loads(row.get("col", "[]"))` looks safe but the default only applies
    when the COLUMN IS MISSING — a NULL value reaches json.loads and raises
    TypeError (500). Use this for stored JSON columns instead.
    """
    if value is None or value == "":
        return list(default) if default is not None else []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return list(default) if default is not None else []
    return parsed
