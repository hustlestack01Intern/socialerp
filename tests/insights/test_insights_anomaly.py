"""Phase 8b: anomaly detection — pure truth table + service integration.

Covers plan acceptance criteria and failure modes:
- Spike fires when today > trailing median * factor; drop when today < median /
  factor; both require median >= min_baseline (baseline-quality guard that
  applies to BOTH branches — the drop branch's whole purpose is noise avoidance).
- Missing trailing days count as zeros (a day with no orders is real data).
- Defaults pinned: factor 2.0, min_baseline 5, window 14 days (derivations in
  packages/core/insights/service.py).
- Tenant scoping; window_days validation; no false spikes on quiet baselines
  (plan failure-mode table: "Anomaly false positive (small baseline)").
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain import TenantId
from packages.core.insights import (
    DEFAULT_FACTOR,
    DEFAULT_MIN_BASELINE,
    DEFAULT_WINDOW_DAYS,
    detect_anomaly_from_counts,
)
from packages.core.insights.service import InsightsService
from tests.insights.helpers import add_order, make_customer, make_tenant, utc_day

DAY = date(2026, 10, 10)


# =============================================================================
# Pure core: detect_anomaly_from_counts
# =============================================================================


def test_spike_fires_at_more_than_double_the_median() -> None:
    anomaly = detect_anomaly_from_counts(
        11, [5, 5, 5, 6, 5], factor=2.0, min_baseline=5, metric="orders", period=DAY,
    )
    assert anomaly is not None
    assert anomaly.direction == "spike"
    assert anomaly.today_value == 11
    assert anomaly.baseline_median == pytest.approx(5.0)
    assert anomaly.factor == pytest.approx(2.0)
    assert anomaly.metric == "orders"
    assert anomaly.period == DAY


def test_exact_double_is_not_a_spike() -> None:
    """Boundary is strict: today == median * factor is normal, not a spike."""
    assert (
        detect_anomaly_from_counts(
            10, [5, 5, 5, 5, 5], factor=2.0, min_baseline=5, metric="orders", period=DAY,
        )
        is None
    )


def test_drop_fires_below_half_the_median() -> None:
    anomaly = detect_anomaly_from_counts(
        2, [5, 6, 5, 5, 6], factor=2.0, min_baseline=5, metric="orders", period=DAY,
    )
    assert anomaly is not None
    assert anomaly.direction == "drop"
    assert anomaly.today_value == 2


def test_exact_half_is_not_a_drop() -> None:
    assert (
        detect_anomaly_from_counts(
            3, [6, 6, 6, 6, 6], factor=2.0, min_baseline=5, metric="orders", period=DAY,
        )
        is None
    )


def test_small_baseline_refuses_both_branches() -> None:
    """min_baseline guards noise: a spike AND a drop from a tiny baseline."""
    assert (
        detect_anomaly_from_counts(
            50, [1, 1, 1, 1], factor=2.0, min_baseline=5, metric="orders", period=DAY,
        )
        is None
    )
    assert (
        detect_anomaly_from_counts(
            0, [2, 2, 2, 2], factor=2.0, min_baseline=5, metric="orders", period=DAY,
        )
        is None
    )


def test_baseline_of_exactly_min_baseline_is_eligible() -> None:
    anomaly = detect_anomaly_from_counts(
        11,
        [5, 5, 5, 5, 5],
        factor=DEFAULT_FACTOR,
        min_baseline=DEFAULT_MIN_BASELINE,
        metric="orders",
        period=DAY,
    )
    assert anomaly is not None and anomaly.direction == "spike"


def test_empty_trailing_window_is_none() -> None:
    assert (
        detect_anomaly_from_counts(100, [], factor=2.0, min_baseline=5, metric="orders", period=DAY)
        is None
    )


def test_normal_day_is_none_including_zeros_in_baseline() -> None:
    # Trailing median 5 (zeros included as real data), today 5 => normal.
    assert (
        detect_anomaly_from_counts(
            5, [0, 5, 0, 10, 5], factor=2.0, min_baseline=5, metric="orders", period=DAY,
        )
        is None
    )


def test_defaults_match_the_documented_constants() -> None:
    assert DEFAULT_WINDOW_DAYS == 14
    assert pytest.approx(2.0) == DEFAULT_FACTOR
    assert DEFAULT_MIN_BASELINE == 5


# =============================================================================
# Service integration: real rows, tenant scope, validation
# =============================================================================


async def test_detect_anomaly_counts_trailing_days_from_orders(
    db_session: AsyncSession,
) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)

    # Trailing 3 days (Oct 7..9): 5 orders each => median 5.
    for day in (7, 8, 9):
        for _ in range(5):
            await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, day, 9))
    # Today (Oct 10): 12 orders => 12 > 5 * 2.0 => spike.
    for _ in range(12):
        await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9))

    anomaly = await InsightsService(db_session).detect_anomaly(
        TenantId(tenant.id), DAY, window_days=3,
    )
    assert anomaly is not None
    assert anomaly.direction == "spike"
    assert anomaly.today_value == 12
    assert anomaly.baseline_median == pytest.approx(5.0)


async def test_detect_anomaly_missing_trailing_days_count_as_zero(
    db_session: AsyncSession,
) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)

    # Only ONE trailing day has orders (5 on Oct 9); Oct 6..8 are missing => 0.
    for _ in range(5):
        await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 9, 9))
    for _ in range(5):
        await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9))

    # Baseline [0, 0, 0, 0, 5] => median 0 < min_baseline => refused (noise guard).
    anomaly = await InsightsService(db_session).detect_anomaly(
        TenantId(tenant.id), DAY, window_days=4,
    )
    assert anomaly is None


async def test_detect_anomaly_is_tenant_scoped(db_session: AsyncSession) -> None:
    tenant_a = await make_tenant(db_session, slug_prefix="anom-a")
    tenant_b = await make_tenant(db_session, slug_prefix="anom-b")
    customer_a = await make_customer(db_session, tenant_a.id)
    customer_b = await make_customer(db_session, tenant_b.id)

    # Tenant B's trailing baseline only; tenant A has no orders at all.
    for day in (7, 8, 9):
        for _ in range(6):
            await add_order(
                db_session, tenant_b.id, customer_b, created_at=utc_day(2026, 10, day, 9),
            )
    for _ in range(20):
        await add_order(db_session, tenant_b.id, customer_b, created_at=utc_day(2026, 10, 10, 9))

    service = InsightsService(db_session)
    assert await service.detect_anomaly(TenantId(tenant_a.id), DAY, window_days=3) is None
    anomaly_b = await service.detect_anomaly(TenantId(tenant_b.id), DAY, window_days=3)
    assert anomaly_b is not None and anomaly_b.today_value == 20
    assert customer_a.id != customer_b.id


async def test_detect_anomaly_rejects_bad_window(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    with pytest.raises(ValueError, match="window_days"):
        await InsightsService(db_session).detect_anomaly(TenantId(tenant.id), DAY, window_days=0)


async def test_daily_report_embeds_the_anomaly(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    # The daily report uses the DEFAULT 14-day window: seed all 14 trailing
    # days at 5 orders each so the median clears DEFAULT_MIN_BASELINE.
    for offset in range(1, DEFAULT_WINDOW_DAYS + 1):
        trailing_day = DAY - timedelta(days=offset)
        for _ in range(5):
            await add_order(
                db_session,
                tenant.id,
                customer,
                created_at=utc_day(trailing_day.year, trailing_day.month, trailing_day.day, 9),
            )
    for _ in range(11):
        await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9))

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY)
    assert report.anomaly is not None
    assert report.anomaly.direction == "spike"
    assert report.period.orders_count == 11
