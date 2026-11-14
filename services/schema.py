"""Required SQLite schema validation."""

from __future__ import annotations

import json
import sqlite3

REQUIRED_TABLES = {
    "users": {"id", "email", "password_hash"},
    "characters": {"id", "user_id", "name", "class_name"},
    "character_spells": {"id", "character_id", "spell_name"},
    "sessions": {"id", "user_id", "token", "expires_at"},
    "schema_migrations": {"version", "applied_at"},
    # The map layer: a bad migration here would only show up as a 500 on the map page.
    "dm_maps": {"id", "user_id", "name", "image_path", "grid_type", "grid_size"},
    "dm_map_tokens": {"id", "map_id", "x", "y", "kind", "ref_name"},
    "dm_map_scenes": {"id", "map_id", "name", "snapshot"},
    # The table channel: without this a bad roll_log migration shows up as a broken feed.
    "roll_log": {"id", "campaign_id", "who", "expression", "results", "total", "hidden"},
}


# ── Shared column hydration ────────────────────────────────────────────
# `characters` rows (and `dm_npcs` rows, which share the shape) keep their
# list-ish columns as JSON text. Eight call sites each parsed their own subset
# with their own fallback, so a column parsed on one page arrived as a raw
# string on the next. One union list, one function.
HYDRATE_JSON_FIELDS = (
    "skills", "features", "inventory", "equipped", "languages",
    "tool_proficiencies", "weapon_proficiencies", "armor_proficiencies",
    "save_proficiencies", "damage_resistances", "damage_immunities",
    "damage_vulnerabilities", "condition_immunities", "expertise_skills",
    "asi_history", "metamagic_history", "metamagic", "invocations",
    "maneuvers", "magical_secrets", "infusions", "attuned_items", "summons",
    "conditions", "favored_enemies", "favored_terrains",
    "feature_data", "attacks_data",
)
# Columns that hydrate to a dict instead of a list.
JSON_DICT_FIELDS = ("spell_slot_data",)

# Portraits are multi-MB base64 data URLs; list/roster queries only need to know
# whether one exists. Used verbatim in the SELECT so every page agrees.
HAS_PORTRAIT_SQL = "(portrait_url IS NOT NULL AND portrait_url != '') AS has_portrait"


def _hydrate_value(value, default):
    if isinstance(value, (list, dict)):
        return value                      # already hydrated by an earlier call
    if isinstance(value, str):
        if not value.strip():
            return default                # NULL-ish empty string
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return default                # malformed row, never a 500
    if value is None:
        return default
    return default                        # numbers/bytes where JSON text was expected


def hydrate_json_columns(row, fields=HYDRATE_JSON_FIELDS, dict_fields=JSON_DICT_FIELDS):
    """Parse the JSON-text columns of a character/NPC row in place; return the row.

    Tolerates rows already hydrated (dict/list values are left alone), NULL or
    empty values (become ``[]``/``{}``) and malformed JSON (becomes the same
    default the old per-site try/except produced). Keys the row does not have
    are skipped, never invented, so npc rows are safe to pass through.
    """
    for field in tuple(fields) + tuple(dict_fields):
        if field not in row:
            continue
        default = {} if field in dict_fields else []
        row[field] = _hydrate_value(row[field], default)
    return row


def validate_schema(db: sqlite3.Connection, *, raise_on_error: bool = False) -> list[str]:
    errors: list[str] = []
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in REQUIRED_TABLES.items():
        if table not in tables:
            errors.append(f"missing table: {table}")
            continue
        found = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        for column in columns - found:
            errors.append(f"missing column: {table}.{column}")
    if raise_on_error and errors:
        raise RuntimeError("Schema validation failed: " + "; ".join(errors))
    return errors

__all__ = [
    "validate_schema",
    "hydrate_json_columns",
    "HYDRATE_JSON_FIELDS",
    "JSON_DICT_FIELDS",
    "HAS_PORTRAIT_SQL",
]
