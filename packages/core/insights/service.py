"""
Owner insights (Phase 8b): daily/weekly reports, stock alerts, anomalies.

What: deterministic, read-only numbers an owner can act on — orders, revenue,
AOV, new vs returning customers, top items, low-stock variants, and a simple
anomaly flag on today's order count.

Why (AGENTS.md §1): every figure must win/protect a sale (stock alerts prevent
stockouts; anomalies flag a dead or booming day), save the owner time, or make
the business smarter (who is new, what sells). The LLM never computes any of
it (AGENTS.md §5).

How: one service over the orders/order_items/catalog tables, tenant-scoped on
every query, computed on the fly — nothing is persisted and no side effects are
emitted (plan invariant 8; insights do NOT raise durable ``owner_alerts`` —
that is a write path and out of 8b's read-only slice).

Derivations (plan 8b "Insights" section; every figure is traceable to rows):

- ``orders_count``   = count of orders with ``created_at`` in the half-open
                       window [start, end) — ALL statuses (demand attempts,
                       plan wording: "count of orders created in [start,end)").
- ``revenue_cents``  = sum of ``total_cents`` over **paid, non-refunded**
                       orders in the window only, where paid = ``is_revenue_order``
                       (``is_order_paid`` — payment_status completed OR
                       ``paid_at`` set, packages/core/orders/service.py:92-102 —
                       AND payment_status != refunded). Plan 8b defines revenue
                       over paid orders; the refund exclusion is a correctness
                       refinement of that intent: refunds never clear ``paid_at``
                       (money-path invariant, packages/core/payments/service.py:
                       703-705), so ``is_order_paid`` alone would count a
                       refunded order at full value. Unpaid money is never
                       counted (financial guard).
- ``paid_orders_count`` = count of those revenue orders (refunded orders
                       excluded, same predicate as ``revenue_cents``).
- ``aov_cents``      = revenue_cents // paid_orders_count, integer floor;
                       0 when paid_orders_count == 0 (division-by-zero guard:
                       no paid orders means "no average", shown as 0).
- ``new_customers``  = customers whose earliest order ever falls in the window.
- ``returning_customers`` = customers with >= 1 order in the window whose
                       earliest order ever is strictly before the window start.
- ``top_items``      = top-k by quantity sold across items of paid,
                       non-refunded orders in the window (an unpaid
                       reservation is not a sale; a refunded order's items
                       are not sales either); ties broken by name for
                       determinism.
- ``stock_alerts``   = active variants with ``quantity < reorder_level`` —
                       the owner's own threshold (catalog model; read-only
                       snapshot of the cached quantity per plan wording).
- Anomaly            = today's order count vs the median of the trailing
                       ``window_days`` daily counts (excluding today):
                       fire when ``today > median * factor`` (spike) or
                       ``today < median / factor`` (drop), and only when
                       ``median >= min_baseline``. See
                       :func:`detect_anomaly_from_counts` for each constant's
                       derivation. No black-box scoring (plan invariant 7).

Units: all money is integer cents (KES 1 = 100 cents); all counts are ints.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain import TenantId
from packages.core.models.catalog import Product, ProductVariant
from packages.core.models.order import Order, OrderItem, PaymentStatus
from packages.core.orders.service import is_order_paid

# =============================================================================
# Thresholds — every one carries its derivation (constitution §2)
# =============================================================================

# Trailing window for the anomaly baseline: 14 days = two full weeks, so both
# weekday polarities appear twice and payday/month-end rhythm (constitution §5)
# has a chance to show, while staying recent enough to matter. Plan default.
DEFAULT_WINDOW_DAYS = 14

# Factor: a day must more than DOUBLE (or halve) the trailing median day to be
# worth interrupting the owner — 2.0x is the smallest round multiplier that
# beats ordinary day-to-day spread for a small shop and is trivial to explain
# ("twice your usual day"). Plan default; pinned by tests, caller-overridable.
DEFAULT_FACTOR = 2.0

# Minimum baseline: the trailing MEDIAN must be at least 5 orders/day. Below
# ~5, one extra order moves the median by more than 20% and every quiet week
# looks anomalous (small-count noise), so we refuse to fire at all — including
# on the drop branch, whose whole purpose is to avoid noise (plan failure-mode
# table: "Anomaly false positive (small baseline) | enforce min_baseline").
DEFAULT_MIN_BASELINE = 5

# Top-items list length: 5 items is the short list an owner can read in one
# glance (WhatsApp message length), and callers can pass a different k.
DEFAULT_TOP_K = 5


# =============================================================================
# Shared predicates
# =============================================================================


def is_revenue_order(order: Order) -> bool:
    """True when an order counts toward owner-facing revenue.

    Revenue = **paid AND NOT refunded** (plan 8b "revenue_cents: sum of
    total_cents over paid orders"; the refund exclusion is a correctness
    refinement of that intent — a refunded order is not a sale the owner
    keeps). Refunds never clear ``paid_at`` (deliberate money-path invariant,
    packages/core/payments/service.py:703-705 and :445), so ``is_order_paid``
    alone would count a refunded order at full value; the explicit
    ``payment_status != refunded`` check removes it without touching the
    protected payments code. A pay-on-delivery order paid then delivered
    (payment_status completed, paid_at set) still counts.
    """
    if not is_order_paid(order):
        return False
    status = order.payment_status
    status_value = getattr(status, "value", status)
    return status_value != PaymentStatus.REFUNDED.value


# =============================================================================
# Result types (frozen: reports are values, never mutated)
# =============================================================================


@dataclass(frozen=True)
class TopItem:
    """One row of "top items": quantity sold of paid orders in the window."""

    product_id: UUID
    name: str
    sku: str
    quantity: int


@dataclass(frozen=True)
class StockAlert:
    """An active variant below its owner-configured reorder level. No PII."""

    variant_id: UUID
    product_id: UUID
    product_name: str
    sku: str
    quantity: int
    reorder_level: int


@dataclass(frozen=True)
class Anomaly:
    """A fired anomaly for one metric on one day (simple, explainable)."""

    metric: str
    period: date
    today_value: int
    baseline_median: float
    factor: float
    direction: str  # "spike" (today > median*factor) or "drop" (today < median/factor)


@dataclass(frozen=True)
class PeriodReport:
    """Aggregate figures for one half-open window [period_start, period_end)."""

    period_start: date
    period_end: date  # exclusive
    orders_count: int
    paid_orders_count: int
    revenue_cents: int
    aov_cents: int
    new_customers: int
    returning_customers: int
    top_items: tuple[TopItem, ...]


@dataclass(frozen=True)
class DailyReport:
    """A day's report plus its order-count anomaly (None when normal)."""

    period: PeriodReport
    anomaly: Anomaly | None


# =============================================================================
# Pure anomaly core (no I/O — fully unit-testable)
# =============================================================================


def detect_anomaly_from_counts(
    today_count: int,
    trailing_counts: Sequence[int],
    *,
    factor: float,
    min_baseline: int,
    metric: str,
    period: date,
) -> Anomaly | None:
    """Compare one day's count against the trailing baseline (pure).

    ``trailing_counts`` are the daily counts for the ``window_days`` days
    BEFORE ``period`` (zeros included — a day with no orders is real data).
    Derivation (plan 8b "Anomaly detection"):

        median m = median(trailing_counts)
        fire spike if  today > m * factor  AND  m >= min_baseline
        fire drop  if  today < m / factor  AND  m >= min_baseline   (symmetric)
        otherwise   None

    The ``m >= min_baseline`` guard applies to BOTH branches: its purpose is
    baseline quality (avoid noise), which a drop from a tiny baseline violates
    just as much as a spike. ``min_baseline`` and ``factor`` come in as
    parameters with :data:`DEFAULT_FACTOR` / :data:`DEFAULT_MIN_BASELINE`
    defaults so tests can pin the plan defaults AND probe other values.
    """
    if not trailing_counts:
        return None
    median = float(statistics.median(trailing_counts))
    if median < min_baseline:
        return None  # baseline too small to distinguish signal from noise
    if today_count > median * factor:
        return Anomaly(
            metric=metric,
            period=period,
            today_value=today_count,
            baseline_median=median,
            factor=factor,
            direction="spike",
        )
    if today_count < median / factor:
        return Anomaly(
            metric=metric,
            period=period,
            today_value=today_count,
            baseline_median=median,
            factor=factor,
            direction="drop",
        )
    return None


# =============================================================================
# Service
# =============================================================================


class InsightsService:
    """Tenant-scoped, read-only owner reports."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- reports
    async def daily_report(
        self, tenant_id: TenantId, on_date: date, *, top_k: int = DEFAULT_TOP_K
    ) -> DailyReport:
        """The report for one UTC calendar day, plus its order anomaly."""
        period = await self._report_for_window(
            tenant_id, on_date, on_date + timedelta(days=1), top_k=top_k
        )
        anomaly = await self.detect_anomaly(tenant_id, on_date)
        return DailyReport(period=period, anomaly=anomaly)

    async def weekly_report(
        self, tenant_id: TenantId, week_start: date, *, top_k: int = DEFAULT_TOP_K
    ) -> PeriodReport:
        """The report for the 7-day window [week_start, week_start + 7)."""
        return await self._report_for_window(
            tenant_id, week_start, week_start + timedelta(days=7), top_k=top_k
        )

    async def _report_for_window(
        self, tenant_id: TenantId, start: date, end: date, *, top_k: int
    ) -> PeriodReport:
        start_dt = datetime.combine(start, time.min, tzinfo=UTC)
        end_dt = datetime.combine(end, time.min, tzinfo=UTC)

        orders = list(
            (
                await self.session.execute(
                    select(Order).where(
                        Order.tenant_id == tenant_id,
                        Order.created_at >= start_dt,
                        Order.created_at < end_dt,
                    )
                )
            )
            .scalars()
            .all()
        )

        # Financial guard: only paid, NON-refunded orders count as revenue
        # (is_revenue_order; refunds keep paid_at, so is_order_paid alone
        # would overstate).
        paid = [o for o in orders if is_revenue_order(o)]
        paid_count = len(paid)
        revenue_cents = sum(int(o.total_cents) for o in paid)
        # Integer floor division; no paid orders => 0 (division-by-zero guard).
        aov_cents = revenue_cents // paid_count if paid_count else 0

        first_order_by_customer = await self._first_orders(tenant_id)
        period_customers = {o.customer_id for o in orders}
        new_customers = sum(
            1
            for cid in period_customers
            if cid in first_order_by_customer and start_dt <= first_order_by_customer[cid] < end_dt
        )
        returning_customers = sum(
            1
            for cid in period_customers
            if cid in first_order_by_customer and first_order_by_customer[cid] < start_dt
        )

        top_items = await self._top_items(tenant_id, [o.id for o in paid], top_k=top_k)

        return PeriodReport(
            period_start=start,
            period_end=end,
            orders_count=len(orders),
            paid_orders_count=paid_count,
            revenue_cents=revenue_cents,
            aov_cents=aov_cents,
            new_customers=new_customers,
            returning_customers=returning_customers,
            top_items=top_items,
        )

    async def _first_orders(self, tenant_id: TenantId) -> dict[UUID, datetime]:
        """Earliest order timestamp per customer for this tenant (all-time)."""
        stmt = (
            select(Order.customer_id, func.min(Order.created_at))
            .where(Order.tenant_id == tenant_id)
            .group_by(Order.customer_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def _top_items(
        self, tenant_id: TenantId, paid_order_ids: Sequence[UUID], *, top_k: int
    ) -> tuple[TopItem, ...]:
        """Top-k items by quantity sold across the given paid, non-refunded orders."""
        if not paid_order_ids or top_k <= 0:
            return ()
        items = list(
            (
                await self.session.execute(
                    select(OrderItem).where(
                        OrderItem.tenant_id == tenant_id,
                        OrderItem.order_id.in_(list(paid_order_ids)),
                    )
                )
            )
            .scalars()
            .all()
        )
        quantity: dict[tuple[UUID, str, str], int] = {}
        for item in items:
            key = (item.product_id, item.name, item.sku)
            quantity[key] = quantity.get(key, 0) + int(item.quantity)
        # Sort by quantity desc, then name/sku for a deterministic tie-break.
        ranked = sorted(quantity.items(), key=lambda kv: (-kv[1], kv[0][1], kv[0][2]))
        return tuple(
            TopItem(product_id=product_id, name=name, sku=sku, quantity=qty)
            for (product_id, name, sku), qty in ranked[:top_k]
        )

    # --------------------------------------------------------- stock alerts
    async def stock_alerts(self, tenant_id: TenantId) -> list[StockAlert]:
        """Active variants whose stock is below their reorder level.

        Read-only: computes the list at request time and raises no durable
        ``owner_alerts`` row (plan invariant 8 keeps 8b free of side effects).
        ``ProductVariant.quantity`` is the cached catalog field the plan points
        at ("from inventory/catalog models"); the event-sourced ledger remains
        the availability source of truth (ADR 0013) and is out of 8b's scope.
        Inactive variants are skipped: stock on an item you cannot sell is not
        an actionable restock signal.
        """
        stmt = (
            select(ProductVariant, Product.name)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                ProductVariant.tenant_id == tenant_id,
                ProductVariant.is_active.is_(True),
                ProductVariant.quantity < ProductVariant.reorder_level,
            )
            .order_by(ProductVariant.sku.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            StockAlert(
                variant_id=variant.id,
                product_id=variant.product_id,
                product_name=product_name,
                sku=variant.sku,
                quantity=int(variant.quantity),
                reorder_level=int(variant.reorder_level),
            )
            for variant, product_name in rows
        ]

    # ------------------------------------------------------------- anomaly
    async def detect_anomaly(
        self,
        tenant_id: TenantId,
        on_date: date,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        factor: float = DEFAULT_FACTOR,
        min_baseline: int = DEFAULT_MIN_BASELINE,
    ) -> Anomaly | None:
        """Today's order count vs the trailing ``window_days``-day median.

        One grouped read of ``orders.created_at`` over the window (today
        included), bucketed by UTC calendar day in Python; missing days count
        as 0. The comparison itself is the pure
        :func:`detect_anomaly_from_counts`.
        """
        if window_days < 1:
            raise ValueError("window_days must be >= 1")
        window_start = datetime.combine(on_date - timedelta(days=window_days), time.min, tzinfo=UTC)
        window_end = datetime.combine(on_date + timedelta(days=1), time.min, tzinfo=UTC)
        rows = (
            await self.session.execute(
                select(Order.created_at).where(
                    Order.tenant_id == tenant_id,
                    Order.created_at >= window_start,
                    Order.created_at < window_end,
                )
            )
        ).all()

        # Index 0..window_days-1 are the trailing days; index window_days is
        # `on_date` itself.
        counts = [0] * (window_days + 1)
        first_day = on_date - timedelta(days=window_days)
        for (created_at,) in rows:
            idx = (created_at.date() - first_day).days
            if 0 <= idx <= window_days:  # defensive: rows are window-bounded already
                counts[idx] += 1
        return detect_anomaly_from_counts(
            counts[window_days],
            counts[:window_days],
            factor=factor,
            min_baseline=min_baseline,
            metric="orders",
            period=on_date,
        )


__all__ = [
    "DEFAULT_FACTOR",
    "DEFAULT_MIN_BASELINE",
    "DEFAULT_TOP_K",
    "DEFAULT_WINDOW_DAYS",
    "Anomaly",
    "DailyReport",
    "InsightsService",
    "PeriodReport",
    "StockAlert",
    "TopItem",
    "detect_anomaly_from_counts",
    "is_revenue_order",
]
