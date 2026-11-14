"""Data and helpers shared by more than one DM Tools route module."""

import re
import sys


def _dm_package():
    """The ``routes.dm`` package, resolved at call time.

    Routes live in sub-modules now, but callers (and tests) still monkeypatch
    ``routes.dm._call_ai`` / ``routes.dm._user_where``. Binding those helpers directly
    into each sub-module would freeze the original function there and silently ignore
    the patch, so the two wrappers below go through the package attribute instead.
    """
    return sys.modules[__package__]


def user_where(user):
    """``core.security._user_where``, honouring a patch of ``routes.dm._user_where``."""
    return _dm_package()._user_where(user)


async def call_ai(prompt, label="gen", **kwargs):
    """``routes.characters._call_ai``, honouring a patch of ``routes.dm._call_ai``."""
    return await _dm_package()._call_ai(prompt=prompt, label=label, **kwargs)


# Columns every NPC list/detail read needs — everything except the portrait blob itself.
# portrait_url can be a base64 data: URL of several megabytes (the pack portraits are), and a
# DM Tools page load parses every NPC to build the palette; selecting and discarding that much
# text per request is what made the page heavy. External URLs still come through so an <img>
# can use them; `has_portrait` answers the thumbnail question without shipping the bytes —
# the image itself is fetched from /api/dm/npc/{id}/portrait-image.
NPC_LIST_COLS = """
    id, user_id, name, race, class_name, subclass, level, is_enemy, is_party_npc,
    strength, dexterity, constitution, intelligence, wisdom, charisma, hp_max, hp_current,
    temp_hp, ac, speed, proficiency_bonus, hit_dice, hit_dice_used, skills, features,
    inventory, notes, alignment, role, faction, xp_reward, created_at, source, source_book,
    source_page,
    CASE WHEN portrait_url LIKE 'data:%' THEN '' ELSE COALESCE(portrait_url, '') END AS portrait_url,
    CASE WHEN COALESCE(portrait_url, '') <> '' THEN 1 ELSE 0 END AS has_portrait
"""

_MANUAL_HP_RE = re.compile(r"(\d+)")

def _manual_npc_hp(mn: dict):
    """Hit points for an extracted NPC, or None when the corpus gives none.

    Most of npcs.json is narrative — a shopkeeper, a guard captain — and carries no stat block.
    The old fallback invented 10 HP for every one of them, so the encounter roster shipped
    damageable shopkeepers that could be "killed" and looted. Absent data stays absent: the
    roster gets None, no HP bar is drawn, and the NPC cannot be added as a combatant with fake
    hit points. Values like "18 (2d8+4)" or "unknown" parse to 18 and None respectively.
    """
    raw = mn.get("hp_current") or mn.get("hit_points")
    if raw is None:
        return None
    m = _MANUAL_HP_RE.match(str(raw).strip())
    if not m:
        return None
    hp = int(m.group(1))
    return hp if hp > 0 else None
