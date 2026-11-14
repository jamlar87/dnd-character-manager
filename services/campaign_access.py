"""Who is allowed to touch a campaign or a character inside it.

Campaign membership is recorded in two places that both have to be honoured:
the legacy ``dm_campaign_characters`` join table and the ``dm_campaigns.characters``
JSON roster. Before this module every route re-implemented that lookup — five
copies of "admin reads `WHERE id=?`, everyone else `WHERE id=? AND user_id=?`"
and three slightly different versions of "is this character mine / my party's".

Rules, stated once:

* **dm** — the campaign's ``user_id`` is yours, or you are an admin (the old SQL
  for admins deliberately dropped the user filter, so an admin DMs *any existing*
  campaign; a campaign that does not exist still answers no).
* **member** — you own a character the campaign lists, in either table.
* **none** — anything else.
"""
from __future__ import annotations

import json
import logging

from fastapi import HTTPException

from services.users import is_admin

logger = logging.getLogger(__name__)


def parse_campaign_roster(raw, where: str = "campaign") -> list:
    """Decode a ``dm_campaigns.characters`` roster, failing loudly on corruption.

    A truncated or hand-edited value used to be swallowed and silently demoted
    every member to "not in this campaign". Surface it instead.
    """
    try:
        chars = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.exception("Corrupt campaign roster in %s: %r", where, raw)
        raise HTTPException(status_code=500, detail="Campaign roster data is corrupt")
    if not isinstance(chars, list):
        logger.error("Campaign roster in %s is %s, not a list: %r",
                     where, type(chars).__name__, raw)
        raise HTTPException(status_code=500, detail="Campaign roster data is corrupt")
    return [c for c in chars if isinstance(c, dict)]


def _campaign_row(db, campaign_id):
    return db.execute(
        "SELECT id, user_id, characters FROM dm_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()


def is_dm_of_campaign(db, user: dict, campaign_id) -> bool:
    """True when `user` may act as DM of `campaign_id` (owner or admin)."""
    row = _campaign_row(db, campaign_id)
    if not row:
        return False
    return is_admin(user) or row["user_id"] == user["id"]


def campaign_role(db, user: dict, campaign_id) -> str:
    """'dm' | 'member' | 'none' — the user's standing in one campaign."""
    row = _campaign_row(db, campaign_id)
    if not row:
        return "none"
    if is_admin(user) or row["user_id"] == user["id"]:
        return "dm"

    legacy = db.execute(
        """SELECT 1 FROM dm_campaign_characters cc
           JOIN characters ch ON ch.id = cc.character_id
           WHERE cc.campaign_id = ? AND ch.user_id = ? LIMIT 1""",
        (campaign_id, user["id"]),
    ).fetchone()
    if legacy:
        return "member"

    ids = [c.get("id") for c in parse_campaign_roster(
        row["characters"], f"campaign_role(campaign={campaign_id})")]
    ids = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
    if not ids:
        return "none"
    placeholders = ",".join("?" * len(ids))
    mine = db.execute(
        f"SELECT 1 FROM characters WHERE user_id = ? AND id IN ({placeholders}) LIMIT 1",
        [user["id"], *ids],
    ).fetchone()
    return "member" if mine else "none"


def user_dms_character(db, user: dict, char_id: int) -> bool:
    """True when `user` DMs some campaign that lists `char_id`.

    Unlike :func:`campaign_role` this scans the user's own campaigns, and a
    corrupt roster on one of them is skipped rather than fatal — the caller is
    asking about one character, not about that campaign's page.
    """
    row = db.execute(
        """SELECT 1 FROM dm_campaign_characters cc
           JOIN dm_campaigns c ON c.id = cc.campaign_id
           WHERE cc.character_id = ? AND c.user_id = ? LIMIT 1""",
        (char_id, user["id"]),
    ).fetchone()
    if row:
        return True
    for camp in db.execute("SELECT characters FROM dm_campaigns WHERE user_id = ?",
                           (user["id"],)):
        try:
            chars = json.loads(camp["characters"] or "[]")
        except (ValueError, TypeError):
            continue
        for ch in chars or []:
            if (isinstance(ch, dict) and ch.get("id") == char_id) or ch == char_id:
                return True
    return False
