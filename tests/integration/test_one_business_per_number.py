"""One business per WhatsApp number (M1.6, U81).

The pricing page asks for one trial per business. Before M1.6 nothing stopped
a second account on the same number. Now:

* the register route refuses it in words (409, and nothing is created);
* the refusal is race-free: the number is claimed under a transaction-scoped
  advisory lock, so two registrations that arrive at the same time cannot both
  win. An application-level check alone would be a TOCTOU hole.

The rule is an application rule, not a unique index, because the shared
``tenants`` table is also written by the tenant-isolation test fixtures
(protected zone) with one placeholder number; see
``packages/core/auth/tenant_claims.py`` and migration 0020.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.models.core import Tenant

pytestmark = pytest.mark.integration

PHONE = "254712000222"


def _payload(**overrides) -> dict[str, str]:
    email = f"biz-{uuid4().hex[:6]}@example.com"
    payload = {
        "business_name": "Number Traders",
        "slug": f"number-biz-{uuid4().hex[:6]}",
        "email": email,
        "owner_email": email,
        "owner_password": "S3cret-pass!",
        "owner_name": "Amina",
        "phone": PHONE,
    }
    payload.update(overrides)
    return payload


async def test_the_second_business_on_a_number_is_refused(api_client, session_factory):
    await api_client.post("/api/v1/auth/register", json=_payload())
    resp = await api_client.post("/api/v1/auth/register", json=_payload())
    assert resp.status_code == 409, resp.text
    assert "WhatsApp number" in resp.json()["detail"]

    async with session_factory() as session:
        count = await session.execute(
            text("SELECT count(*) FROM tenants WHERE owner_phone = :phone"),
            {"phone": PHONE},
        )
        assert count.scalar_one() == 1


async def test_two_registrations_racing_on_one_number_produce_one_business(
    api_client, session_factory
):
    """The race the advisory lock closes: one wins, one is refused, one row.

    Without the lock both requests check "is this number taken?" before
    either has inserted, and both would create a business - two trials for one
    number, which is exactly what the pricing page says cannot happen.
    """
    payloads = [_payload(), _payload()]
    responses = await asyncio.gather(
        *(api_client.post("/api/v1/auth/register", json=p) for p in payloads)
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 409], f"expected one signup and one refusal, got {codes}"

    async with session_factory() as session:
        count = await session.execute(
            text("SELECT count(*) FROM tenants WHERE owner_phone = :phone"),
            {"phone": PHONE},
        )
        assert count.scalar_one() == 1


async def test_the_claim_helper_refuses_a_taken_number(session_factory):
    """The helper itself: lock, look, answer."""
    from packages.core.auth.tenant_claims import claim_business_number

    async with session_factory() as session, session.begin():
        session.add(
            Tenant(
                name="First",
                slug=f"first-{uuid4().hex[:6]}",
                owner_phone=PHONE,
                owner_email="first@example.com",
                is_active=True,
                settings={},
            )
        )

    async with session_factory() as session:
        assert await claim_business_number(session, PHONE) is False
        assert await claim_business_number(session, "254799999999") is True


async def test_two_claims_racing_on_one_number_produce_one_winner(
    session_factory: async_sessionmaker[AsyncSession],
):
    """Two sessions, one number: the lock serialises them."""
    from packages.core.auth.tenant_claims import claim_business_number

    phone = "254712000333"

    async def claim_and_write() -> bool:
        async with session_factory() as session:
            free = await claim_business_number(session, phone)
            if free:
                session.add(
                    Tenant(
                        name="Racer",
                        slug=f"racer-{uuid4().hex[:6]}",
                        owner_phone=phone,
                        owner_email=f"racer-{uuid4().hex[:6]}@example.com",
                        is_active=True,
                        settings={},
                    )
                )
            await session.commit()
            return free

    results = await asyncio.gather(claim_and_write(), claim_and_write())
    assert sorted(results) == [False, True], (
        f"the lock did not serialise the claims: {results}"
    )
