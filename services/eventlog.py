"""The audit trail: one row per meaningful write, written so it cannot break the write.

Every write path in the app that changes a character, a campaign roster, the XP
ledger, or the undo stack calls `log_event` on its way out. The row goes into
`event_log` with the before/after state of the change, so "who changed this
sheet, and what did it look like before" has an answer that is not a memory.

Two rules make this safe to call from anywhere:

1. It opens its own connection. A write path already holds a transaction, and
   sharing that connection would put the audit row inside the write it is
   supposed to describe.
2. It never raises. A failed audit is a lost row, not a lost character. Every
   call site can therefore treat logging as fire-and-forget.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

EMPTY_OBJECT = "{}"


def _dumps(value) -> str:
    """JSON for a dict, or the empty JSON object for None."""
    if value is None:
        return EMPTY_OBJECT
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return EMPTY_OBJECT


def log_event(kind, campaign_id=0, actor="", target="", before=None, after=None, ts=None):
    """Append one row to `event_log`. Never raises."""
    try:
        from core.db import get_db

        db = get_db()
        try:
            if ts is None:
                from datetime import datetime, timezone

                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.execute(
                "INSERT INTO event_log (campaign_id, kind, actor, target, before, after, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    int(campaign_id or 0),
                    str(kind or ""),
                    str(actor or ""),
                    str(target or ""),
                    _dumps(before),
                    _dumps(after),
                    str(ts),
                ),
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - an audit row must never cost a write
        logger.warning("event log write failed: %s", exc)


def character_campaign_id(db, character_id: int) -> int:
    """The campaign a character belongs to, or 0 when it belongs to none.

    A character is owned by a player, not by a campaign; the roster join table
    (`dm_campaign_characters`) is what ties the two together, and a character
    can sit on more than one roster. The first roster is the campaign the audit
    trail files the character's writes under.
    """
    try:
        row = db.execute(
            "SELECT campaign_id FROM dm_campaign_characters WHERE character_id=? "
            "ORDER BY id LIMIT 1",
            (int(character_id),),
        ).fetchone()
        return int(row["campaign_id"]) if row else 0
    except Exception:  # noqa: BLE001 - the audit trail is not worth a failed lookup
        return 0
