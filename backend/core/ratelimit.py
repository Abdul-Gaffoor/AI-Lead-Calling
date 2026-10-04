"""API rate limiting (MVP section 32).

A fixed window counter, in Redis when it is configured and in process
memory otherwise. Redis matters as soon as there is more than one worker:
an in-memory counter lets an attacker get N times the limit by spreading
requests across N processes, which is exactly what a load balancer does
for them.

Two limits rather than one. The login endpoint is the valuable target —
it is where passwords are guessed and where one-time codes could be
brute-forced — so it gets a much tighter allowance, keyed on client
address. Everything else gets a general per-caller limit, keyed on the
authenticated user when there is one so that a busy office behind a single
NAT address is not throttled as though it were one person.

A fixed window is a deliberate simplification. It allows a burst across a
window boundary, which a sliding window would not. That is an acceptable
trade for something with no dependencies and no background sweeping, and
the account lockout in `auth/router.py` is the real defence against
password guessing — this is the defence against being flooded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class _MemoryCounter:
    """Per-process counters. Correct for one worker, approximate for many."""

    windows: dict[str, tuple[int, int]] = field(default_factory=dict)

    def hit(self, key: str, window_s: int) -> int:
        now = int(time.time())
        bucket = now // window_s
        count, stored_bucket = self.windows.get(key, (0, bucket))
        if stored_bucket != bucket:
            count = 0
        count += 1
        self.windows[key] = (count, bucket)

        # Drop expired entries occasionally so a long-running process does
        # not accumulate a key per address forever.
        if len(self.windows) > 10_000:
            self.windows = {
                k: v for k, v in self.windows.items() if v[1] >= bucket - 1
            }
        return count


_memory = _MemoryCounter()
_redis_client = None
_redis_unavailable = False


def _redis():
    """The shared Redis client, or None when it is not usable."""
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is not None:
        return _redis_client
    url = getattr(settings, "redis_url", None)
    if not url:
        _redis_unavailable = True
        return None
    try:
        import redis

        _redis_client = redis.Redis.from_url(url, socket_timeout=0.25)
        _redis_client.ping()
        return _redis_client
    except Exception:
        # Rate limiting must never take the API down. Fall back to the
        # in-process counter and say so once.
        logger.warning("Rate limiter falling back to in-process counters", exc_info=True)
        _redis_unavailable = True
        _redis_client = None
        return None


def hit(key: str, window_s: int) -> int:
    """Count one request against `key`; return the count in this window."""
    client = _redis()
    if client is None:
        return _memory.hit(key, window_s)
    try:
        bucket = int(time.time()) // window_s
        redis_key = f"ratelimit:{key}:{bucket}"
        pipe = client.pipeline()
        pipe.incr(redis_key)
        # Expire a window later, so the key cannot outlive its usefulness
        # even if the process handling it dies.
        pipe.expire(redis_key, window_s * 2)
        count, _ = pipe.execute()
        return int(count)
    except Exception:
        logger.warning("Redis rate limit failed; using memory", exc_info=True)
        return _memory.hit(key, window_s)


def caller_key(request: Request) -> str:
    """Who is this request from?

    The authenticated user when we know them, so one office behind one
    address is not throttled collectively. The client address otherwise,
    taken from X-Forwarded-For because the app sits behind Traefik.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # The left-most entry is the original client; the rest are proxies.
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def enforce(request: Request, *, limit: int, window_s: int, scope: str) -> None:
    """Raise 429 when `request` is over the limit for `scope`."""
    if limit <= 0:
        return  # disabled
    key = f"{scope}:{caller_key(request)}"
    count = hit(key, window_s)
    if count > limit:
        logger.info("Rate limited %s on %s (%s in %ss)", key, scope, count, window_s)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Please slow down and try again shortly.",
            headers={"Retry-After": str(window_s)},
        )


def reset() -> None:
    """Clear counters. For tests, so one does not rate-limit the next."""
    global _redis_client, _redis_unavailable
    _memory.windows.clear()
    # Only touch Redis if a client was already opened. Probing here would
    # make every test that resets the limiter pay a connection timeout.
    client = _redis_client
    if client is not None:
        try:
            for key in client.scan_iter("ratelimit:*"):
                client.delete(key)
        except Exception:
            logger.debug("Could not clear Redis rate limit keys", exc_info=True)
    _redis_client = None
    _redis_unavailable = False
