"""In-process fan-out for the table channel (shared roll feed + live push).

The app is served by a single uvicorn process (verified: one pid, no reloader child), so a
queue per subscriber reaches every connected client. Correctness deliberately does not depend
on that: `roll_log` is the source of truth and a reconnecting client replays the gap from it,
so if the service ever runs multiple workers the feed goes stale, never wrong. Move this to
the DB or Redis if that day comes.

`publish` is synchronous and non-blocking on purpose — it is called from the request that just
committed a roll, and a stalled reader must never be able to hold up the dice.
"""

from __future__ import annotations

import asyncio

# Deep enough for a burst of rolls, shallow enough that a client which stopped reading is
# recognised quickly instead of growing a queue for the rest of the session.
QUEUE_MAX = 200

_subs: dict[int, set[asyncio.Queue]] = {}


def subscribe(campaign_id: int) -> asyncio.Queue:
    """Register a listener and return its queue. The caller owns unsubscribe()."""
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _subs.setdefault(int(campaign_id), set()).add(q)
    return q


def unsubscribe(campaign_id: int, q: asyncio.Queue) -> None:
    """Remove a listener. Safe to call twice, and after a drop."""
    bucket = _subs.get(int(campaign_id))
    if not bucket:
        return
    bucket.discard(q)
    if not bucket:
        _subs.pop(int(campaign_id), None)


def subscriber_count(campaign_id: int) -> int:
    return len(_subs.get(int(campaign_id), ()))


def publish(campaign_id: int, event: dict) -> None:
    """Fan an event out. A full queue means that client stalled: drop it, not the event."""
    for q in list(_subs.get(int(campaign_id), ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            unsubscribe(campaign_id, q)


__all__ = ["QUEUE_MAX", "subscribe", "unsubscribe", "subscriber_count", "publish"]
