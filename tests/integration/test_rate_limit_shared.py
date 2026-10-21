"""The rate limiter's counters are shared, not per process (M1.6, U78).

Before U78 the counters lived in the API process, so N workers meant N times
the limit, and a restart cleared the budget. The counters now live in Redis
when it is configured and answering.

These tests use the real Redis (the compose stack runs one; the test suite
uses database 1), and they prove the thing that matters: **two clients - two
"workers" - share one budget**. The in-process fallback is tested too,
because it is what runs when Redis is down and it must still answer 429.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from packages.core.middleware.rate_limit import (
    InMemoryRateLimitStore,
    LazySharedStore,
    RateLimitMiddleware,
    RedisRateLimitStore,
)

pytestmark = pytest.mark.integration

REDIS_URL = "redis://localhost:6379/1"


async def _redis_store(prefix: str | None = None) -> RedisRateLimitStore:
    """A store. Two stores with the same prefix are two workers of one app."""
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL, decode_responses=True)
    await client.ping()
    return RedisRateLimitStore(
        client, prefix=prefix or f"test:ratelimit:{uuid.uuid4().hex[:8]}"
    )


async def test_two_workers_share_one_budget() -> None:
    """The heart of U78: the limit is per platform, not per process."""
    prefix = f"test:ratelimit:{uuid.uuid4().hex[:8]}"
    store_a = await _redis_store(prefix)
    store_b = await _redis_store(prefix)  # another worker, same keyspace

    first = await store_a.hit("login|1.2.3.4", 60)
    assert first.count == 1

    # A different store (a different worker) sees the same counter.
    second = await store_b.hit("login|1.2.3.4", 60)
    assert second.count == 2, "the second worker did not see the first hit"

    # A different client has its own budget.
    other = await store_b.hit("login|5.6.7.8", 60)
    assert other.count == 1

    await store_a.close()


async def test_the_window_expires_itself() -> None:
    """The key must not live forever: Redis expires it."""
    from redis.asyncio import Redis

    store = await _redis_store()
    await store.hit("login|9.9.9.9", 60)

    client = Redis.from_url(REDIS_URL, decode_responses=True)
    keys = [k async for k in client.scan_iter(match=f"{store._prefix}:*")]
    assert keys, "the counter left no key behind"
    ttl = await client.ttl(keys[0])
    assert 0 < ttl <= 60, f"the counter expires in {ttl}s, expected 1..60"
    await client.aclose()
    await store.close()


async def test_the_in_process_store_counts_and_forgets() -> None:
    """The fallback: it counts, and it forgets after the window."""
    import time

    store = InMemoryRateLimitStore()
    first = await store.hit("login|1.1.1.1", 1)
    assert first.count == 1
    second = await store.hit("login|1.1.1.1", 1)
    assert second.count == 2
    time.sleep(1.05)
    third = await store.hit("login|1.1.1.1", 1)
    assert third.count == 1, "the window did not expire"


async def test_the_middleware_answers_429_with_retry_after() -> None:
    """The contract the middleware keeps: 429 and a Retry-After header."""

    async def ok(request):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/v1/auth/login", ok)])
    app.add_middleware(
        RateLimitMiddleware,
        limit=2,
        window_seconds=60,
        store=InMemoryRateLimitStore(),
    )

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/v1/auth/login")).status_code == 200
        assert (await c.get("/api/v1/auth/login")).status_code == 200
        third = await c.get("/api/v1/auth/login")
        assert third.status_code == 429
        assert third.headers["Retry-After"]
        # A path that is not protected is untouched.
        assert (await c.get("/api/v1/other")).status_code == 404


async def test_the_lazy_store_falls_back_when_redis_is_down() -> None:
    """A cache being down must not take the login down."""
    store = LazySharedStore("redis://localhost:6399/1")  # nothing listens there
    result = await store.hit("login|2.2.2.2", 60)
    assert result.count == 1
    assert store.is_shared is False
    await store.close()


async def test_the_lazy_store_shares_when_redis_answers() -> None:
    store = LazySharedStore(REDIS_URL)
    try:
        first = await store.hit(f"login|3.3.3.3|{uuid.uuid4().hex[:6]}", 60)
        assert first.count == 1
        assert store.is_shared is True
    finally:
        await store.close()
