"""Field bounds for numbers that a client can post.

The write paths take JSON straight from the request. `UpdateCharacter` declares only a handful of
fields and then sets `extra="allow"`, so `{"hp_max": 100000000}` reached the column untouched, and
one bad cell in a sheet form is enough to make a character unusable (HP bar at 0%, a dice roller
dividing by an absurd max, a sort order that never settles). The schema was the wrong place to
stop this: the same columns are written from the sheet route, from character creation, and from the
DM's NPC editor, and only the last of those used a Pydantic model at all.

So the bounds live in one table here, keyed by column name, and every route that writes those
columns clamps through it. Clamping — not rejecting — is deliberate: these are form fields, the
player's intent is the nearest legal value, and a 422 mid-session loses the rest of the payload
(including the HP change that was the point of the request). Bounds are set generous enough that
ordinary play never touches them; they only catch values no table could mean.

Non-numeric values are left alone: type checking belongs to the schema and to SQLite, and silently
turning "abc" into a number here would hide a real client bug.
"""

from __future__ import annotations

# column name -> (min, max). Characters and dm_npcs share most column names, so one table serves
# both; the money columns exist only on characters and never arrive from the NPC editor.
FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "level": (1, 20),
    "hp_max": (1, 100_000),
    "hp_current": (0, 100_000),
    "temp_hp": (0, 100_000),
    "ac": (0, 100),
    "speed": (0, 1_000),
    "strength": (1, 30),
    "dexterity": (1, 30),
    "constitution": (1, 30),
    "intelligence": (1, 30),
    "wisdom": (1, 30),
    "charisma": (1, 30),
    "proficiency_bonus": (1, 20),
    "passive_perception": (0, 100),
    "inspiration": (0, 1),
    "exhaustion": (0, 6),  # 6 levels of exhaustion is death
    "death_saves_success": (0, 3),
    "death_saves_fail": (0, 3),
    "hit_dice_used": (0, 20),
    "gp": (0, 10_000_000),
    "cp": (0, 10_000_000),
    "xp_reward": (0, 1_000_000_000),
}


def clamp_int(field: str, value):
    """Clamp one value to its field's bounds; anything non-numeric passes through untouched."""
    bounds = FIELD_BOUNDS.get(field)
    if not bounds or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    lo, hi = bounds
    try:
        n = int(value)
    except (ValueError, OverflowError):  # NaN / inf from a hand-written JSON body
        return value
    return min(max(n, lo), hi)


def clamp_fields(data: dict) -> dict:
    """Return `data` with every bounded numeric field clamped. Keys not in the table are copied."""
    return {k: clamp_int(k, v) for k, v in data.items()}
