"""Undo: put back the last thing you changed, if nothing has moved it since.

The shape of the problem at a table is narrow. A DM misclicks a tracker row, a player's HP jumps to
0, and the app has no memory of it. What solves that is not a versioned history of every field — it
is the *last* action, restorable in one tap, and a refusal instead of a wrong answer when the world
has moved on.

So:
- A write records what it changed: the raw column values before and after, per character.
- Undo takes the newest un-undone record *for that user*, checks the row still holds the `after`
  values, and only then writes `before` back. If anything has changed since, it refuses and says so.
  Clobbering a later edit to restore an earlier one is data loss, not an undo.
- One user's record is never another's: this is scoped by user_id, and the characters table is
  written by route code that already authorised the caller.

Values are stored exactly as the database holds them (a JSON blob stays its text), so the staleness
comparison is an equality check rather than a re-serialisation guess.
"""

from __future__ import annotations

import json

# How many actions back a user can reach. Deep enough for a bad minute at the table, shallow enough
# that this never becomes an audit log with all the obligations that implies.
UNDO_DEPTH = 10

# Columns this service will restore. An undo writes columns a route already writes; it must never
# become a way to write something no route would.
RESTORABLE = {
    "hp_current", "hp_max", "temp_hp", "conditions", "summons", "xp", "level",
    "death_saves_success", "death_saves_fail", "exhaustion", "inspiration", "notes",
    "spell_slots_used", "hit_dice_used",
}


def _db_value(value):
    """What the database will actually hold for a Python value we were handed."""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _same(a, b) -> bool:
    if a == b:
        return True
    if a is None or b is None:
        return (a in (None, "")) and (b in (None, ""))
    return str(a) == str(b)


def record(db, user_id: int, kind: str, label: str, entities: list, meta: dict | None = None) -> None:
    """Remember what a write changed. Callers pass raw DB values; failures here must never break
    the write that is already committed, so route code wraps this."""
    clean = []
    for ent in entities or []:
        cid = ent.get("character_id")
        if not cid:
            continue
        before = {k: _db_value(v) for k, v in (ent.get("before") or {}).items() if k in RESTORABLE}
        after = {k: _db_value(v) for k, v in (ent.get("after") or {}).items() if k in RESTORABLE}
        if not before and not after:
            continue
        clean.append({"character_id": int(cid), "before": before, "after": after})
    if not clean:
        return

    db.execute("INSERT INTO action_undo (user_id, kind, label, entities, meta) "
               "VALUES (?, ?, ?, ?, ?)",
               (int(user_id), str(kind or "")[:40], str(label or "")[:160], json.dumps(clean),
                json.dumps(meta or {})))
    # keep the stack shallow: everything older than the last UNDO_DEPTH entries goes
    db.execute(
        "DELETE FROM action_undo WHERE user_id = ? AND id NOT IN ("
        "  SELECT id FROM action_undo WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
        (int(user_id), int(user_id), UNDO_DEPTH))
    db.commit()


def peek(db, user_id: int) -> dict | None:
    """What an undo would put back — for the UI to name before the user commits to it."""
    row = db.execute(
        "SELECT id, kind, label, entities, created_at FROM action_undo "
        "WHERE user_id = ? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (int(user_id),)).fetchone()
    if not row:
        return None
    entities = _load_entities(row["entities"])
    return {"id": row["id"], "kind": row["kind"], "label": row["label"],
            "characters": len(entities), "created_at": row["created_at"]}


def undo(db, user_id: int) -> dict:
    """Restore the newest action, or explain why not.

    Returns {"ok": True, "kind", "label", "restored": [{"character_id", "fields": [...]}]} or
    {"ok": False, "reason": "empty"|"stale", ...}.
    """
    row = db.execute(
        "SELECT id, kind, label, entities, meta FROM action_undo "
        "WHERE user_id = ? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (int(user_id),)).fetchone()
    if not row:
        return {"ok": False, "reason": "empty", "error": "Nothing to undo"}

    entities = _load_entities(row["entities"])
    if not entities:
        db.execute("UPDATE action_undo SET undone_at = datetime('now') WHERE id = ?", (row["id"],))
        db.commit()
        return {"ok": False, "reason": "empty", "error": "Nothing to undo"}

    # 1. Refuse if the row has moved on: every field we set must still hold the value we set.
    stale = []
    for ent in entities:
        cid = ent["character_id"]
        after = ent.get("after") or {}
        if not after:
            continue
        cols = list(after.keys())
        current = db.execute(
            f"SELECT {', '.join(cols)} FROM characters WHERE id = ?", (cid,)).fetchone()
        if current is None:
            stale.append({"character_id": cid, "why": "gone"})
            continue
        for i, col in enumerate(cols):
            if not _same(current[i], after[col]):
                stale.append({"character_id": cid, "field": col,
                              "now": current[i], "expected": after[col]})
    if stale:
        return {"ok": False, "reason": "stale", "detail": stale, "kind": row["kind"],
                "label": row["label"],
                "error": "That action has been changed since — undo would overwrite newer work"}

    # 2. Put the before values back, and report what moved so the caller can tell every screen.
    restored = []
    for ent in entities:
        cid = ent["character_id"]
        before = ent.get("before") or {}
        cols = [k for k in before if k in RESTORABLE]
        if cols:
            db.execute(
                f"UPDATE characters SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                tuple([before[c] for c in cols] + [cid]))
        restored.append({"character_id": cid, "fields": cols,
                         "values": {c: before[c] for c in cols}})

    db.execute("UPDATE action_undo SET undone_at = datetime('now') WHERE id = ?", (row["id"],))
    try:
        meta = json.loads(row["meta"] or "{}")
    except (json.JSONDecodeError, TypeError, IndexError):
        meta = {}
    db.commit()
    return {"ok": True, "kind": row["kind"], "label": row["label"], "restored": restored,
            "meta": meta}


def _load_entities(raw) -> list:
    try:
        loaded = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return loaded if isinstance(loaded, list) else []


def record_safe(db, user_id: int, kind: str, label: str, entities: list, meta: dict | None = None) -> None:
    """Record an action without ever costing the write it describes — and without hiding a failure.

    Every caller used to wrap record() in its own bare `except Exception: pass`, which is how a
    missing column silently stopped every undo record on the live database while the tests (fresh
    schema) all passed. The write still wins; the log gets the truth.
    """
    try:
        record(db, user_id, kind, label, entities, meta=meta)
    except Exception as exc:                       # noqa: BLE001 - the save must not fail here
        try:
            import logging

            logging.getLogger(__name__).warning(
                "undo record failed (kind=%s): %s: %s", kind, type(exc).__name__, exc)
        except Exception:
            pass
