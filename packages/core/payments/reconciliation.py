"""
Payment reconciliation (Phase 4).

The status-query fallback: callbacks can be late, duplicated or missing, so a
background job re-queries the provider for payments still ``initiated``/``pending``
after a timeout and applies the result. A "completed" query result is **not**
auto-credited (the Daraja query has no amount to match); it is flagged for human
review instead. Failures and cancellations are applied directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.audit.logger import AuditLogger
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


# ---------------------------------------------------------------------------
# Statement reconciliation (our ledger vs a provider settlement statement)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementEntry:
    """One line on a provider settlement statement (money the provider moved)."""

    receipt: str
    amount_cents: int


@dataclass(frozen=True)
class StatementMismatch:
    """A single discrepancy between our ledger and the provider statement."""

    kind: str  # missing_in_ledger | missing_at_provider | amount_mismatch | no_receipt
    receipt: str | None
    payment_id: str | None = None
    our_amount_cents: int | None = None
    provider_amount_cents: int | None = None


@dataclass(frozen=True)
class StatementReconciliationResult:
    matched: int
    mismatches: list[StatementMismatch]

    @property
    def ok(self) -> bool:
        return not self.mismatches


async def reconcile_statement(
    session: AsyncSession,
    tenant_id: UUID,
    statement: list[StatementEntry],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StatementReconciliationResult:
    """Compare completed payments against a provider settlement statement.

    The statement is provider-agnostic: a list of ``(receipt, amount_cents)``
    lines (from a Daraja portal export, an aggregator settlement file, or an
    operator upload). Mismatches are recorded as append-only audit entries so
    they can raise alerts; nothing is auto-adjusted.
    """
    stmt = select(Payment).where(
        Payment.tenant_id == tenant_id,
        Payment.status == PaymentRecordStatus.COMPLETED.value,
    )
    if since is not None:
        stmt = stmt.where(Payment.completed_at >= since)
    if until is not None:
        stmt = stmt.where(Payment.completed_at <= until)
    payments = list((await session.execute(stmt)).scalars().all())

    by_receipt: dict[str, Payment] = {}
    mismatches: list[StatementMismatch] = []
    for payment in payments:
        if not payment.mpesa_receipt_number:
            mismatches.append(
                StatementMismatch(kind="no_receipt", receipt=None, payment_id=str(payment.id))
            )
            continue
        by_receipt[payment.mpesa_receipt_number] = payment

    matched = 0
    seen: set[str] = set()
    for entry in statement:
        seen.add(entry.receipt)
        entry_payment = by_receipt.get(entry.receipt)
        if entry_payment is None:
            mismatches.append(
                StatementMismatch(
                    kind="missing_in_ledger",
                    receipt=entry.receipt,
                    provider_amount_cents=entry.amount_cents,
                )
            )
            continue
        if entry_payment.amount_cents != entry.amount_cents:
            mismatches.append(
                StatementMismatch(
                    kind="amount_mismatch",
                    receipt=entry.receipt,
                    payment_id=str(entry_payment.id),
                    our_amount_cents=entry_payment.amount_cents,
                    provider_amount_cents=entry.amount_cents,
                )
            )
            continue
        matched += 1

    for receipt, payment in by_receipt.items():
        if receipt not in seen:
            mismatches.append(
                StatementMismatch(
                    kind="missing_at_provider",
                    receipt=receipt,
                    payment_id=str(payment.id),
                    our_amount_cents=payment.amount_cents,
                )
            )

    if mismatches:
        audit = AuditLogger(session)
        for mismatch in mismatches:
            await audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="payment.reconciliation_mismatch",
                resource="payment",
                resource_id=UUID(mismatch.payment_id) if mismatch.payment_id else None,
                metadata={
                    "kind": mismatch.kind,
                    "receipt": mismatch.receipt,
                    "our_amount_cents": mismatch.our_amount_cents,
                    "provider_amount_cents": mismatch.provider_amount_cents,
                },
            )

    return StatementReconciliationResult(matched=matched, mismatches=mismatches)


__all__ = [
    "DEFAULT_STALE_SECONDS",
    "STALE_PAYMENT_STATUSES",
    "StatementEntry",
    "StatementMismatch",
    "StatementReconciliationResult",
    "reconcile_all",
    "reconcile_statement",
    "reconcile_tenant",
    "stale_payments",
]
