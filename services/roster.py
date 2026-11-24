"""Keep `dm_campaign_characters` in step with the roster JSON.

`dm_campaigns.characters` is a JSON array of roster entries and stays the write
target for the existing flows. The join table next to it was created but never
populated — on the live database it held zero rows while five campaigns had
rosters — so every reader had to fall back to parsing JSON and any new JOIN
would silently return nothing.

This module mirrors the JSON into the table whenever the roster is written, and
backfills once at startup. Rows carry `status` so the sheet can tell an active
party member from a retired one without opening the blob.
"""

from __future__ import annotations

import json
import sqlite3


def parse_entries(raw) -> list[dict]:
    """Roster entries as dicts. Tolerates junk: the column is free-form text."""
    if isinstance(raw, list):
        data = raw
    else:
        try:
            data = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for entry in data:
        if isinstance(entry, dict):
            out.append(entry)
        elif isinstance(entry, int) and not isinstance(entry, bool):
            # Ancient rows stored bare ids. Keep them addressable instead of
            # dropping a character off the roster.
            out.append({"id": entry})
    return out


def character_ids(raw) -> list[int]:
    """Character ids on a roster, in roster order, deduplicated."""
    seen: set[int] = set()
    ids: list[int] = []
    for entry in parse_entries(raw):
        try:
            cid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


def _character_exists(db: sqlite3.Connection, character_id: int) -> bool:
    """True when `characters` really has this id."""
    try:
        row = db.execute(
            "SELECT 1 FROM characters WHERE id = ? LIMIT 1", (int(character_id),)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def sync(db: sqlite3.Connection, campaign_id: int, raw) -> dict:
    """Rewrite the join rows for one campaign to match its roster JSON.

    Returns counts so callers (and tests) can assert what moved.
    """
    entries = parse_entries(raw)
    wanted: dict[int, str] = {}
    for entry in entries:
        try:
            cid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if not cid:
            continue
        status = entry.get("status") or "active"
        wanted[cid] = str(status)[:20]

    # A roster can name a character that no longer exists (the blob is free
    # -form text, and DM campaigns outlive the sheets they list). The join
    # table has a real FK to `characters`, so inserting such an id raises
    # IntegrityError and takes the whole write down. Keep only ids that
    # actually exist.
    wanted = {cid: status for cid, status in wanted.items() if _character_exists(db, cid)}

    existing = {
        int(row[0]): (row[1] or "active")
        for row in db.execute(
            "SELECT character_id, status FROM dm_campaign_characters WHERE campaign_id = ?",
            (int(campaign_id),),
        )
    }

    removed = 0
    for cid in existing:
        if cid not in wanted:
            db.execute(
                "DELETE FROM dm_campaign_characters WHERE campaign_id = ? AND character_id = ?",
                (int(campaign_id), cid),
            )
            removed += 1

    added = updated = 0
    for cid, status in wanted.items():
        if cid not in existing:
            db.execute(
                "INSERT INTO dm_campaign_characters (campaign_id, character_id, status, notes)"
                " VALUES (?,?,?,'')",
                (int(campaign_id), cid, status),
            )
            added += 1
        elif existing[cid] != status:
            db.execute(
                "UPDATE dm_campaign_characters SET status = ? WHERE campaign_id = ? AND character_id = ?",
                (status, int(campaign_id), cid),
            )
            updated += 1
    return {"added": added, "updated": updated, "removed": removed}


def sync_campaign(db: sqlite3.Connection, campaign_id: int) -> dict:
    """Sync one campaign from its stored roster JSON. No-op when it is gone."""
    row = db.execute(
        "SELECT characters FROM dm_campaigns WHERE id = ?", (int(campaign_id),)
    ).fetchone()
    if row is None:
        return {"added": 0, "updated": 0, "removed": 0}
    return sync(db, campaign_id, row[0])


def sync_all(db: sqlite3.Connection) -> int:
    """Backfill every campaign. Returns the number of roster rows added."""
    total = 0
    for camp_id, raw in db.execute("SELECT id, characters FROM dm_campaigns").fetchall():
        total += sync(db, int(camp_id), raw)["added"]
    return total
