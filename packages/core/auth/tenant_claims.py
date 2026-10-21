"""
One business per WhatsApp number (U81, M1.6).

The product rule: a WhatsApp number is the business identity. One number, one
business - so a second account on the same number would be a second trial for
the same business, which is exactly what the pricing page promises not to
happen.

**Why this is an application rule and not a unique index.** A unique index on
``tenants.owner_phone`` is the obvious database answer, and it was built
(migration 0020) - but it makes the *shared* tenant table unforgiving for
every other writer of that table, including the tenant-isolation test
fixtures in ``tests/billing/test_billing_service.py`` (protected zone: they
create two tenants with one placeholder number). Editing a protected test to
fit a schema change is exactly what the constitution forbids, so the rule is
enforced where the business is created instead, and made race-free with a
Postgres transaction-scoped advisory lock:

    lock(number) -> check -> insert -> commit

Two registrations for the same number serialise on the lock, so the second
one sees the first one's committed row and is refused. The lock is held to the
end of the transaction (``pg_advisory_xact_lock``), which is what makes the
check-then-insert atomic against other registrations.

The same helper covers the owner email, which had the same race: two
concurrent signups with one email created two tenants, and login is by email,
so the second signup made the login ambiguous. That hole is closed here too.

If the unique index is wanted at the database level, the change is: rewrite
migration 0020 to create a unique index, set ``Tenant.owner_phone`` back to
``unique=True``, and update the protected fixtures to use distinct numbers -
which needs the human's approval.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from packages.core.models.core import Tenant


async def claim_business_number(session: AsyncSession, phone: str) -> bool:
    """Try to claim a WhatsApp number for one business.

    Returns True when the number was free (the caller must create the tenant
    in the *same* transaction, or the claim is lost), False when another
    business already holds it.

    The caller keeps the claim until the transaction ends, so the check and
    the insert cannot be interleaved by another registration.
    """
    return await _claim(session, "tenant-phone", phone, Tenant.owner_phone)


async def claim_owner_email(session: AsyncSession, email: str) -> bool:
    """Try to claim an owner email for one account (login is by email)."""
    return await _claim(session, "owner-email", email, Tenant.owner_email)


async def _claim(
    session: AsyncSession,
    namespace: str,
    value: str,
    column: InstrumentedAttribute[Any],
) -> bool:
    """Serialise on the value, then look it up. Same transaction, no race."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{namespace}:{value.strip().lower()}"},
    )
    found = await session.execute(select(Tenant.id).where(column == value))
    return found.first() is None


__all__ = ["claim_business_number", "claim_owner_email"]
