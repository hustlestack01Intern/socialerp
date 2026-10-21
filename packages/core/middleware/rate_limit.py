"""A rate limiter for the auth endpoints (M1.5), shared across workers (U78).

The constitution asks for rate limits and `packages/core/config.py` has
carried `RATE_LIMIT_REQUESTS_PER_MINUTE` since the beginning without
anything reading it. The invite gate made that gap concrete: the status code
of a registration tells an attacker whether a code is valid, so the practical
mitigation is to make probing expensive.

What this is
------------
A fixed-window counter, per client IP, per endpoint group, answering 429 with
a `Retry-After` header. The counter lives in a **store**:

* `RedisRateLimitStore` - the counter is in Redis, so every worker of the API
  shares one budget. This is the honest answer to "with N workers the limit
  was N times the limit".
* `InMemoryRateLimitStore` - the counter is in this process. It is the
  fallback when Redis is not configured or not answering, and the app logs
  which one it is using. It is **per process** and a restart clears it: it
  stops a script, not a distributed attack.

What this is NOT (say it out loud, do not pretend)
--------------------------------------------------
* It still only covers the paths it is given. The API is not rate limited
  everywhere yet.
* Redis failing *at request time* (not at startup) makes the limiter fail
  **open**: the request is allowed and the failure is logged. Failing closed
  would take the login down for every owner in the country because a cache
  is down; the residual window is in the uncertainty ledger.

The limit is passed in, not read from the environment here, so the middleware
never keeps its own copy of the settings (the settings object is cached per
import path, and a second copy is a second truth).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# The endpoints that are worth guessing at: a registration (an invite code)
# and a login (a password).
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "/api/v1/auth/register",
    "/api/v1/auth/login",
)


@dataclass(frozen=True)
class RateLimitResult:
    """What one hit did to the window."""

    count: int
    retry_after: int


class RateLimitStore(Protocol):
    """Where the hits are counted."""

    async def hit(self, key: str, window_seconds: int) -> RateLimitResult:
        """Count one hit for ``key`` and report the window it lands in.

        The store owns the window: it must forget the hits once the window has
        passed, and two callers of the same key must see the same count.
        """

    async def close(self) -> None:
        """Release whatever the store holds (a connection pool)."""


class InMemoryRateLimitStore:
    """Hits counted in this process. The fallback, and the honest baseline."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def hit(self, key: str, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        hits = [t for t in self._hits[key] if now - t < window_seconds]
        hits.append(now)
        self._hits[key] = hits
        return RateLimitResult(count=len(hits), retry_after=window_seconds)

    async def close(self) -> None:
        return None


class RedisRateLimitStore:
    """Hits counted in Redis, so every worker shares one budget.

    The counting is a Lua script, so "count, set the expiry, read the time
    left" happens in one round trip and cannot interleave with another
    worker's call. The key expires by itself: no cleanup job, no unbounded
    keyspace.
    """

    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    local ttl = redis.call('TTL', KEYS[1])
    return {count, ttl}
    """

    def __init__(self, client: Any, prefix: str = "socialerp:ratelimit") -> None:
        self._client = client
        self._prefix = prefix
        self._script = client.register_script(self._SCRIPT)

    async def hit(self, key: str, window_seconds: int) -> RateLimitResult:
        raw = await self._script(keys=[f"{self._prefix}:{key}"], args=[window_seconds])
        count, ttl = int(raw[0]), int(raw[1])
        retry_after = max(1, ttl) if ttl and ttl > 0 else window_seconds
        return RateLimitResult(count=count, retry_after=retry_after)

    async def close(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()
        else:
            await self._client.close()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window, per-IP rate limit for a set of paths."""

    def __init__(
        self,
        app: object,
        *,
        limit: int,
        window_seconds: int = 60,
        paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS,
        store: RateLimitStore | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = limit
        self.window_seconds = window_seconds
        self.paths = paths
        self.store: RateLimitStore = store or InMemoryRateLimitStore()

    def _client_key(self, request: Request) -> str:
        client = request.client
        return client.host if client is not None else "unknown"

    def _store_for(self, request: Request) -> RateLimitStore:
        """The store the app built at startup, or the one the middleware has.

        The app builds the store in its lifespan, so in production the Redis
        client is created in the serving event loop and closed on shutdown.
        """
        built = getattr(request.app.state, "rate_limit_store", None)
        return built if built is not None else self.store

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if self.limit <= 0 or path not in self.paths:
            return await call_next(request)

        key = f"{path}|{self._client_key(request)}"
        try:
            result = await self._store_for(request).hit(key, self.window_seconds)
        except Exception as exc:  # pragma: no cover - the store failing
            # Fail open: a cache being down must not take the login down.
            logger.warning("rate limit store unavailable, allowing: %s", exc)
            return await call_next(request)

        if result.count > self.limit:
            retry_after = result.retry_after
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        "Too many attempts from this address. "
                        f"Try again in {retry_after} seconds."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


async def build_rate_limit_store(redis_url: str | None) -> RateLimitStore:
    """Build the store the app should use, and say which one it is.

    Redis when it is configured *and* answers; the in-process counter
    otherwise. The choice is logged, so an operator reading the log knows
    whether the limit is shared.
    """
    if not redis_url:
        logger.warning(
            "rate limit: no REDIS_URL configured - using the per-process "
            "counter, so the limit is per worker, not per platform"
        )
        return InMemoryRateLimitStore()

    try:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        await client.ping()
    except Exception as exc:
        logger.warning(
            "rate limit: Redis at %s is not answering (%s) - using the "
            "per-process counter, so the limit is per worker, not per platform",
            redis_url,
            exc,
        )
        return InMemoryRateLimitStore()

    logger.info("rate limit: shared counters in Redis at %s", redis_url)
    return RedisRateLimitStore(client)


class LazySharedStore:
    """The shared store, connected on first use, with a permanent fallback.

    The middleware is built when the app is created, before the lifespan runs,
    so the store cannot be connected at startup without reaching into
    Starlette's middleware stack. Instead the connection is made on the first
    request, once.

    If the shared store cannot be reached - Redis is down, or was never
    configured - the limiter falls back to the in-process counter **for good**
    and says so in the log. A request is never failed because a cache is
    down: the worst case is that the limit is per worker again, which is what
    it was before this change.
    """

    def __init__(self, redis_url: str | None) -> None:
        self._redis_url = redis_url
        self._store: RateLimitStore | None = None
        self._fallback = InMemoryRateLimitStore()
        self._lock = asyncio.Lock()

    @property
    def is_shared(self) -> bool:
        """True when the counters are shared between workers."""
        return isinstance(self._store, RedisRateLimitStore)

    async def _resolve(self) -> RateLimitStore:
        if self._store is not None:
            return self._store
        async with self._lock:
            if self._store is not None:
                return self._store
            self._store = await build_rate_limit_store(self._redis_url)
            return self._store

    async def hit(self, key: str, window_seconds: int) -> RateLimitResult:
        store = await self._resolve()
        try:
            return await store.hit(key, window_seconds)
        except Exception as exc:  # pragma: no cover - Redis failing mid-flight
            logger.warning(
                "rate limit: the shared store failed (%s) - falling back to "
                "the per-process counter",
                exc,
            )
            self._store = self._fallback
            return await self._fallback.hit(key, window_seconds)

    async def close(self) -> None:
        if self._store is not None:
            await self._store.close()


__all__ = [
    "DEFAULT_PROTECTED_PATHS",
    "InMemoryRateLimitStore",
    "LazySharedStore",
    "RateLimitMiddleware",
    "RateLimitResult",
    "RateLimitStore",
    "RedisRateLimitStore",
    "build_rate_limit_store",
]
