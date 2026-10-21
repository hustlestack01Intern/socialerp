"""
Insights API routes (Phase 8b).

Thin, owner-only, tenant-scoped HTTP layer over
:class:`packages.core.insights.InsightsService`:

- ``GET /api/v1/insights/daily?date=YYYY-MM-DD``       day report + order anomaly
- ``GET /api/v1/insights/weekly?week_start=YYYY-MM-DD`` 7-day report
- ``GET /api/v1/insights/stock-alerts``               variants below reorder level

AuthZ: ``require_owner`` on every route (plan 8b API section: insights are
owner-facing; AC "API authZ: owner-only for insights ... non-owner denied").
Read-only: reports are computed on the fly; no rows are written (plan
invariant 8 — no durable alerts are raised from here).

Defaults (derivation, constitution §2):
- ``date`` defaults to today's UTC calendar day.
- ``week_start`` defaults to the Monday of the current UTC week (ISO-8601
  weeks start on Monday; ``date.weekday()`` gives days since Monday).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes._gates import require_insights
from packages.core.auth.dependencies import get_current_tenant_id, get_db, require_owner
from packages.core.domain import TenantId
from packages.core.insights import Anomaly, InsightsService, PeriodReport, StockAlert

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])


# =============================================================================
# Schemas
# =============================================================================


class TopItemView(BaseModel):
    product_id: str
    name: str
    sku: str
    quantity: int


class AnomalyView(BaseModel):
    metric: str
    period: str  # ISO date
    today_value: int
    baseline_median: float
    factor: float
    direction: str


class ReportView(BaseModel):
    period_start: str  # ISO date, inclusive
    period_end: str  # ISO date, exclusive
    orders_count: int
    paid_orders_count: int
    revenue_cents: int
    aov_cents: int
    new_customers: int
    returning_customers: int
    top_items: list[TopItemView]
    anomaly: AnomalyView | None = None  # daily reports only; always None for weekly


class StockAlertView(BaseModel):
    variant_id: str
    product_id: str
    product_name: str
    sku: str
    quantity: int
    reorder_level: int


# =============================================================================
# Shaping
# =============================================================================


def _anomaly(a: Anomaly | None) -> AnomalyView | None:
    if a is None:
        return None
    return AnomalyView(
        metric=a.metric,
        period=a.period.isoformat(),
        today_value=a.today_value,
        baseline_median=a.baseline_median,
        factor=a.factor,
        direction=a.direction,
    )


def _report(period: PeriodReport, anomaly: Anomaly | None = None) -> ReportView:
    return ReportView(
        period_start=period.period_start.isoformat(),
        period_end=period.period_end.isoformat(),
        orders_count=period.orders_count,
        paid_orders_count=period.paid_orders_count,
        revenue_cents=period.revenue_cents,
        aov_cents=period.aov_cents,
        new_customers=period.new_customers,
        returning_customers=period.returning_customers,
        top_items=[
            TopItemView(
                product_id=str(item.product_id),
                name=item.name,
                sku=item.sku,
                quantity=item.quantity,
            )
            for item in period.top_items
        ],
        anomaly=_anomaly(anomaly),
    )


def _stock_alert(a: StockAlert) -> StockAlertView:
    return StockAlertView(
        variant_id=str(a.variant_id),
        product_id=str(a.product_id),
        product_name=a.product_name,
        sku=a.sku,
        quantity=a.quantity,
        reorder_level=a.reorder_level,
    )


# =============================================================================
# Routes (owner-only)
# =============================================================================


@router.get("/daily", response_model=ReportView)
async def daily_report(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    report_date: Annotated[date | None, Query(alias="date")] = None,
) -> ReportView:
    """One UTC day's report plus its order-count anomaly."""
    # The matrix grants insights on every plan today; the gate is what keeps
    # that true if a future plan does not (M1.6, U80). Insights are the
    # owner's own data, so this is not state-gated (I3).
    await require_insights(db, tenant_id)
    on_date = report_date if report_date is not None else datetime.now(UTC).date()
    service = InsightsService(db)
    report = await service.daily_report(TenantId(tenant_id), on_date)
    return _report(report.period, report.anomaly)


@router.get("/weekly", response_model=ReportView)
async def weekly_report(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    week_start: Annotated[date | None, Query()] = None,
) -> ReportView:
    """A 7-day report; defaults to the current UTC week (Monday-anchored)."""
    await require_insights(db, tenant_id)
    if week_start is None:
        today = datetime.now(UTC).date()
        week_start = today - timedelta(days=today.weekday())  # ISO Monday
    service = InsightsService(db)
    report = await service.weekly_report(TenantId(tenant_id), week_start)
    return _report(report)


@router.get("/stock-alerts", response_model=list[StockAlertView])
async def stock_alerts(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[StockAlertView]:
    """Active variants below their reorder level (read-only, no durable alert)."""
    await require_insights(db, tenant_id)
    alerts = await InsightsService(db).stock_alerts(TenantId(tenant_id))
    return [_stock_alert(a) for a in alerts]


__all__: list[str] = []
