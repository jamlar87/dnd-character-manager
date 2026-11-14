"""Disk-side JSON access: cached reads of SRD cache and manual-data files.

Every read of a JSON file happens here, and a missing/malformed file yields an
empty value so callers can merge unconditionally. No merge policy lives here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "load_json", "clear_cache", "cache_info", "_load_srd_class_data",
    "_load_spell_dice", "_load_json_cache", "_load_manual_json",
]


@lru_cache(maxsize=128)
def load_json(data_dir: str, filename: str) -> Any:
    path = Path(data_dir) / filename
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def clear_cache() -> None:
    load_json.cache_clear()


def cache_info():
    return load_json.cache_info()


def _load_srd_class_data() -> tuple[dict, dict]:
    """Load cached SRD class levels and metadata."""
    from core.config import SRD_CACHE
    try:
        with open(SRD_CACHE / "class_levels.json") as f:
            levels = json.load(f)
        with open(SRD_CACHE / "class_meta.json") as f:
            meta = json.load(f)
        return levels, meta
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}


def _load_spell_dice() -> dict[str, dict]:
    from core.config import DATA_DIR
    try:
        with open(DATA_DIR / "spell_dice.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_json_cache(filename: str) -> list[dict]:
    from core.config import SRD_CACHE
    try:
        with open(SRD_CACHE / filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _load_manual_json(filename: str) -> list[dict]:
    from core.config import MANUAL_DATA
    result = load_json(str(MANUAL_DATA), filename)
    return result if isinstance(result, list) else result
