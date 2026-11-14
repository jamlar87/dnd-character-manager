"""In-memory sliding-window throttle for credential endpoints.

Why this exists: `POST /login` verified a bcrypt hash on every request with no
cap, so an attacker could guess passwords as fast as the CPU allowed. This
module keeps a small, bounded table of recent failure timestamps per key
(e.g. `login:account:someone@example.com`) and tells the caller when a key has
used up its budget.

Design constraints:
  * stdlib only, no database — the check happens before any DB or bcrypt work,
    so a blocked attempt costs almost nothing;
  * thread-safe — the auth routes are plain `def` handlers and therefore run in
    Starlette's threadpool, so every read/write goes through one Lock;
  * monotonic clock — wall-clock jumps (NTP, DST) must not reset a window;
  * bounded memory — timestamps older than the key's window are pruned on read
    and idle keys are swept, so a flood of distinct emails cannot grow the dict
    without bound.

Keys are opaque strings owned by the caller; this module never parses them.
"""

from __future__ import annotations

import threading
import time

# Hard ceiling on distinct keys held at once. Anything above this is evicted
# oldest-first after the routine sweep, which keeps a hostile client from
# exhausting memory by spraying unique emails/IPs at the login form.
MAX_KEYS = 4096

# How often (in monotonic seconds) the opportunistic idle-key sweep runs.
_SWEEP_INTERVAL_S = 60.0

# Fallbacks used by record_failure() when a key was never checked first, so a
# stray record call can never leave an unbounded bucket behind.
_DEFAULT_LIMIT = 5
_DEFAULT_WINDOW_S = 15 * 60

_lock = threading.Lock()
_buckets: dict[str, "_Bucket"] = {}
_last_sweep = 0.0


def _now() -> float:
    """Clock indirection: tests patch this, production uses the monotonic clock."""
    return time.monotonic()


class _Bucket:
    """Failure timestamps for one key, plus the policy they were recorded under."""

    __slots__ = ("times", "limit", "window_s")

    def __init__(self, limit: int, window_s: int) -> None:
        self.times: list[float] = []
        self.limit = limit
        self.window_s = window_s

    def prune(self, now: float, window_s: int | None = None) -> None:
        cutoff = now - (self.window_s if window_s is None else window_s)
        self.times = [t for t in self.times if t > cutoff]

    def is_idle(self, now: float) -> bool:
        return not self.times or self.times[-1] <= now - self.window_s


def _sweep_locked(now: float, force: bool = False) -> None:
    """Drop idle keys. Called with `_lock` held.

    Kept cheap: runs at most once per `_SWEEP_INTERVAL_S` unless memory is over
    budget, at which point the oldest keys are evicted until it fits.
    """
    global _last_sweep
    if not force and now - _last_sweep < _SWEEP_INTERVAL_S:
        return
    _last_sweep = now
    for key in [k for k, b in _buckets.items() if b.is_idle(now)]:
        del _buckets[key]
    if len(_buckets) > MAX_KEYS:
        # Still over budget (a burst of live keys): evict the least recently
        # touched first. The evicted key simply gets a fresh budget.
        ordered = sorted(_buckets.items(), key=lambda kv: kv[1].times[-1] if kv[1].times else 0.0)
        for key, _ in ordered[: len(_buckets) - MAX_KEYS]:
            del _buckets[key]


def check(key: str, limit: int, window_s: int) -> float:
    """Return 0.0 if `key` may try again, else seconds until its oldest failure expires.

    A successful (0.0) result does not record anything; callers record only the
    outcomes they consider failures.
    """
    now = _now()
    with _lock:
        _sweep_locked(now)
        bucket = _buckets.get(key)
        if bucket is None:
            return 0.0
        bucket.limit = limit
        bucket.window_s = window_s
        bucket.prune(now, window_s)
        if not bucket.times:
            del _buckets[key]
            return 0.0
        if len(bucket.times) < limit:
            return 0.0
        # Blocked: the key regains capacity when its oldest failure ages out.
        # Never return exactly 0.0 here — that means "allowed".
        return max(bucket.times[0] + window_s - now, 0.001)


def record_failure(key: str, limit: int = _DEFAULT_LIMIT, window_s: int = _DEFAULT_WINDOW_S) -> None:
    """Record one failed attempt for `key`, keeping at most limit+1 timestamps."""
    now = _now()
    with _lock:
        _sweep_locked(now)
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = _buckets[key] = _Bucket(limit, window_s)
        else:
            bucket.limit = limit
            bucket.window_s = window_s
        bucket.prune(now, window_s)
        bucket.times.append(now)
        if len(bucket.times) > bucket.limit + 1:
            # No amount of history can change the answer past limit+1 entries.
            del bucket.times[: len(bucket.times) - (bucket.limit + 1)]
        _sweep_locked(now, force=len(_buckets) > MAX_KEYS)


def clear(key: str) -> None:
    """Forget every failure for `key` (call on a successful login)."""
    with _lock:
        _buckets.pop(key, None)


def reset() -> None:
    """Forget all keys. For tests; production never needs it."""
    global _last_sweep
    with _lock:
        _buckets.clear()
        _last_sweep = 0.0
