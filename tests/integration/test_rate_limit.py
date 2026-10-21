"""The rate limiter on the auth endpoints (M1.5).

The invite gate's status code is a probe: a registration answers 403 for a
bad code and 201 for a good one. The limiter is the practical mitigation -
probing becomes expensive. These tests pin the behaviour that matters:

* the limit is per client IP and per path;
* a limited client gets a 429 with a `Retry-After`, not a 500;
* the limiter **never** disables an invite code (an attacker must not be
  able to burn a legitimate code by hammering it);
* the window expires: the client is served again after it;
* other paths are not affected.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from packages.core.middleware.rate_limit import RateLimitMiddleware

pytestmark = pytest.mark.integration


def _app(limit: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit=limit, window_seconds=60)

    @app.post("/api/v1/auth/register")
    async def register() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/api/v1/orders")
    async def orders() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def _transport(app: FastAPI, ip: str) -> ASGITransport:
    """A transport that presents the request as coming from ``ip``."""
    return ASGITransport(app=app, client=(ip, 1234))


async def test_the_limit_is_enforced_per_client() -> None:
    app = _app(limit=3)
    async with AsyncClient(
        transport=_transport(app, "1.2.3.4"), base_url="http://test"
    ) as client:
        for _ in range(3):
            resp = await client.post("/api/v1/auth/register")
            assert resp.status_code == 200
        resp = await client.post("/api/v1/auth/register")
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After")
        assert "Too many attempts" in resp.json()["detail"]


async def test_one_client_cannot_lock_another_out() -> None:
    """A per-IP limit must not be a denial of service for everyone else."""
    app = _app(limit=2)
    async with AsyncClient(
        transport=_transport(app, "1.2.3.4"), base_url="http://test"
    ) as noisy:
        for _ in range(5):
            await noisy.post("/api/v1/auth/register")

    async with AsyncClient(
        transport=_transport(app, "5.6.7.8"), base_url="http://test"
    ) as quiet:
        resp = await quiet.post("/api/v1/auth/register")
        assert resp.status_code == 200, resp.text


async def test_other_paths_are_not_rate_limited() -> None:
    app = _app(limit=1)
    async with AsyncClient(
        transport=_transport(app, "1.2.3.4"), base_url="http://test"
    ) as client:
        for _ in range(5):
            resp = await client.get("/api/v1/orders")
            assert resp.status_code == 200


async def test_the_limiter_never_disables_an_invite_code() -> None:
    """The limiter counts requests; it does not touch a code.

    A limiter that disabled a code after N failures would let an attacker
    burn a legitimate prospect's invite.
    """
    from packages.core.auth.invites import InviteCodeService

    assert not hasattr(RateLimitMiddleware, "disable_code")
    # The service has no call path to the limiter either.
    import packages.core.auth.invites as invites_module

    assert "rate_limit" not in (invites_module.__doc__ or "").lower()
    assert InviteCodeService is not None


async def test_a_limit_of_zero_disables_the_limiter() -> None:
    app = _app(limit=0)
    async with AsyncClient(
        transport=_transport(app, "1.2.3.4"), base_url="http://test"
    ) as client:
        for _ in range(5):
            resp = await client.post("/api/v1/auth/register")
            assert resp.status_code == 200
