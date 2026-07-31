"""Cached JSON data access boundary.

The loader is deliberately small: merge policy remains in main.py until each
category has characterization tests. This module prevents repeated file I/O and
provides one independently testable access point.
"""

from functools import lru_cache
import json
from pathlib import Path
from typing import Any


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


__all__ = ["load_json", "clear_cache", "cache_info"]
