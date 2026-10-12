"""In-process fan-out for the table channel (shared roll feed + live push).

The app is served by a single uvicorn process (verified: one pid, no reloader child), so a
queue per subscriber reaches every connected client. Correctness deliberately does not depend
on that: `roll_log` is the source of truth and a reconnecting client replays the gap from it,
so if the service ever runs multiple workers the feed goes stale, never wrong. Move this to
the DB or Redis if that day comes.

`publish` is synchronous and non-blocking on purpose — it is called from the request that just
committed a roll, and a stalled reader must never be able to hold up the dice.

Two scopes share one implementation:

- **campaign** (`subscribe`/`publish`) — the roll feed and `hp` state events.
- **map** (`subscribe_map`/`publish_map`) — a poke that the 3 s map poll is replaced by. The map
  event carries no state: the player view refetches `/state`, so there is exactly one projection
  and no second copy of the rules for who may see what.

Keys are namespaced (`c:12`, `m:7`) so a campaign and a map can never collide on the id space.
"""

from __future__ import annotations

import asyncio

# Deep enough for a burst of rolls, shallow enough that a client which stopped reading is
# recognised quickly instead of growing a queue for the rest of the session.
QUEUE_MAX = 200

_subs: dict[str, set[asyncio.Queue]] = {}


def _subscribe(key: str) -> asyncio.Queue:
    """Register a listener and return its queue. The caller owns the unsubscribe."""
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _subs.setdefault(key, set()).add(q)
    return q


def _unsubscribe(key: str, q: asyncio.Queue) -> None:
    """Remove a listener. Safe to call twice, and after a drop."""
    bucket = _subs.get(key)
    if not bucket:
        return
    bucket.discard(q)
    if not bucket:
        _subs.pop(key, None)


def _subscriber_count(key: str) -> int:
    return len(_subs.get(key, ()))


def _publish(key: str, event: dict) -> None:
    """Fan an event out. A full queue means that client stalled: drop it, not the event."""
    for q in list(_subs.get(key, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            _unsubscribe(key, q)


# ── campaigns ───────────────────────────────────────────────────────────────────────

def subscribe(campaign_id: int) -> asyncio.Queue:
    return _subscribe(f"c:{int(campaign_id)}")


def unsubscribe(campaign_id: int, q: asyncio.Queue) -> None:
    _unsubscribe(f"c:{int(campaign_id)}", q)


def subscriber_count(campaign_id: int) -> int:
    return _subscriber_count(f"c:{int(campaign_id)}")


def publish(campaign_id: int, event: dict) -> None:
    _publish(f"c:{int(campaign_id)}", event)


# ── maps ────────────────────────────────────────────────────────────────────────────

def subscribe_map(map_id: int) -> asyncio.Queue:
    return _subscribe(f"m:{int(map_id)}")


def unsubscribe_map(map_id: int, q: asyncio.Queue) -> None:
    _unsubscribe(f"m:{int(map_id)}", q)


def map_subscriber_count(map_id: int) -> int:
    return _subscriber_count(f"m:{int(map_id)}")


def publish_map(map_id: int, event: dict) -> None:
    _publish(f"m:{int(map_id)}", event)


__all__ = [
    "QUEUE_MAX",
    "subscribe", "unsubscribe", "subscriber_count", "publish",
    "subscribe_map", "unsubscribe_map", "map_subscriber_count", "publish_map",
]
