"""
Payment reconciliation (Phase 4).

The status-query fallback: callbacks can be late, duplicated or missing, so a
background job re-queries the provider for payments still ``initiated``/``pending``
after a timeout and applies the result. A "completed" query result is **not**
auto-credited (the Daraja query has no amount to match); it is flagged for human
review instead. Failures and cancellations are applied directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.domain.payment import PaymentProvider, PaymentProviderError
from packages.core.models.payment import Payment, PaymentRecordStatus
from packages.core.payments.service import PaymentService

# Statuses that mean "we haven't heard a terminal outcome yet".
STALE_PAYMENT_STATUSES = (
    PaymentRecordStatus.INITIATED.value,
    PaymentRecordStatus.PENDING.value,
)

DEFAULT_STALE_SECONDS = 15 * 60


async def stale_payments(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    older_than_seconds: int = DEFAULT_STALE_SECONDS,
    now: datetime | None = None,
) -> list[Payment]:
    """Payments still awaiting a terminal outcome past the timeout."""
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=older_than_seconds)
    stmt = (
        select(Payment)
        .where(
            Payment.tenant_id == tenant_id,
            Payment.status.in_(STALE_PAYMENT_STATUSES),
            Payment.created_at < cutoff,
        )
        .order_by(Payment.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def reconcile_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    provider: PaymentProvider,
    *,
    older_than_seconds: int = DEFAULT_STALE_SECONDS,
    now: datetime | None = None,
) -> int:
    """Query the provider for each stale payment and apply the result.

    Returns the number of payments that were queried.
    """
    service = PaymentService(session, provider)
    payments = await stale_payments(
        session, tenant_id, older_than_seconds=older_than_seconds, now=now
    )
    queried = 0
    for payment in payments:
        if not payment.checkout_request_id:
            continue
        try:
            result = await provider.query_status(payment.checkout_request_id)
        except PaymentProviderError:
            # Provider hiccup: leave it stale for the next run.
            continue
        await service.apply_query_result(tenant_id=tenant_id, result=result)
        queried += 1
    return queried


async def reconcile_all(
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    *,
    older_than_seconds: int = DEFAULT_STALE_SECONDS,
) -> int:
    """Run the status-query fallback across every tenant that has payments."""
    async with session_factory() as session:
        tenant_ids = list(
            (await session.execute(select(Payment.tenant_id).distinct())).scalars().all()
        )

    total = 0
    for tenant_id in tenant_ids:
        async with session_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            total += await reconcile_tenant(
                session, tenant_id, provider, older_than_seconds=older_than_seconds
            )
    return total


__all__ = [
    "DEFAULT_STALE_SECONDS",
    "STALE_PAYMENT_STATUSES",
    "reconcile_all",
    "reconcile_tenant",
    "stale_payments",
]
