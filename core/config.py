"""Paths, environment and process-wide configuration.

Loaded first by main.py and imported by every other core module, so it must not
import anything from main.py or from services/*.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERE = REPO_ROOT  # repo root, same meaning it had in main.py

# ── Load .env (local config, not committed) ──────────────────────
_env_path = HERE / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
# ─────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("DND_DATA_DIR", str(HERE / "data")))
PAGE_MAP_DIR = DATA_DIR / "page_maps"
DB_PATH = DATA_DIR / "characters.db"
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
SECRET_KEY = os.environ.get("SECRET_KEY", "dnd-dev-secret-change-me")
APP_ENV = os.environ.get("APP_ENV", "development").lower()
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
if APP_ENV in {"production", "prod"} and SECRET_KEY == "dnd-dev-secret-change-me":
    raise RuntimeError("SECRET_KEY must be set to a unique value in production")
SRD_CACHE = DATA_DIR / "srd_cache"
MANUAL_DATA = HERE / "data" / "manual_data"

_CE_PATH = Path(os.environ.get("DND_CAMPAIGN_EXPERT_PATH", str(HERE.parent / "dnd-campaign-expert")))
if str(_CE_PATH) not in sys.path:
    sys.path.insert(0, str(_CE_PATH))
