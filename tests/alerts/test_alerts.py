"""
Phase 7 remainder: owner alert tests.

Covers the durable replacement for log-only alerting (UNCERTAINTY U15):

- ``AlertService`` is idempotent per ``(tenant_id, dedupe_key)`` so an at-least-once
  outbox replay cannot raise two alerts for the same subject.
- The API is tenant-scoped: another tenant gets 404 on both list-filter and ack.
- The worker handler persists a CRITICAL alert and is replay-safe.
- Alerts never carry customer PII.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from apps.worker import main as worker
from packages.core.alerts import AlertService
from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.models.alert import AlertSeverity, AlertStatus, OwnerAlert
from packages.core.models.core import Tenant


@pytest.fixture
async def alerts_client(tenant, session_factory) -> AsyncGenerator[AsyncClient, None]:
    """Client with tenant + db overrides for the authenticated alerts routes."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# =============================================================================
# AlertService (unit-ish, real Postgres)
# =============================================================================


async def test_raise_alert_is_idempotent_per_dedupe_key(tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        service = AlertService(session)
        first = await service.raise_alert(
            tenant.id,
            kind="payment.collected_after_terminal",
            title="Money collected on a non-collectable order",
            severity=AlertSeverity.CRITICAL,
            dedupe_key="payment_collected_after_terminal:abc",
        )
        second = await service.raise_alert(
            tenant.id,
            kind="payment.collected_after_terminal",
            title="Money collected on a non-collectable order",
            severity=AlertSeverity.CRITICAL,
            dedupe_key="payment_collected_after_terminal:abc",
        )
    assert first.id == second.id


async def test_raise_alert_without_dedupe_key_always_inserts(tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        service = AlertService(session)
        a = await service.raise_alert(tenant.id, kind="ad_hoc", title="One")
        b = await service.raise_alert(tenant.id, kind="ad_hoc", title="Two")
    assert a.id != b.id


async def test_acknowledge_is_idempotent_and_persists(tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        service = AlertService(session)
        alert = await service.raise_alert(tenant.id, kind="k", title="t")
        acked = await service.acknowledge(tenant.id, alert.id, user_id=None)
        again = await service.acknowledge(tenant.id, alert.id, user_id=None)
    assert acked is not None and acked.status == AlertStatus.ACKNOWLEDGED.value
    assert again is not None and again.status == AlertStatus.ACKNOWLEDGED.value


# =============================================================================
# API: tenant-scoped list + ack
# =============================================================================


async def test_alerts_api_list_and_ack(alerts_client: AsyncClient, tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        service = AlertService(session)
        raised = await service.raise_alert(
            tenant.id,
            kind="payment.collected_after_terminal",
            title="Money collected on a non-collectable order",
            severity=AlertSeverity.CRITICAL,
            dedupe_key="api-test-1",
        )

    listing = await alerts_client.get("/api/v1/alerts")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "payment.collected_after_terminal"
    assert rows[0]["status"] == "open"
    assert rows[0]["severity"] == "critical"

    acked = await alerts_client.post(f"/api/v1/alerts/{raised.id}/ack")
    assert acked.status_code == 200, acked.text
    assert acked.json()["status"] == "acknowledged"

    # Filtering by status now hides it from the open list.
    open_only = await alerts_client.get("/api/v1/alerts", params={"status": "open"})
    assert open_only.json() == []


async def test_alerts_api_404_for_foreign_alert(
    alerts_client: AsyncClient, tenant, session_factory
) -> None:
    """An alert belonging to another tenant is invisible and unacknowledgeable."""
    other_tenant_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Tenant(
                id=other_tenant_id,
                name="Other Business",
                slug=f"other-{other_tenant_id.hex[:8]}",
                owner_phone="254700000009",
                owner_email="other@example.com",
                is_active=True,
                settings={},
            )
        )
        await session.flush()
        service = AlertService(session)
        foreign = await service.raise_alert(
            other_tenant_id, kind="k", title="Foreign alert", dedupe_key="foreign-1"
        )

    resp = await alerts_client.post(f"/api/v1/alerts/{foreign.id}/ack")
    assert resp.status_code == 404

    listing = await alerts_client.get("/api/v1/alerts")
    assert listing.json() == []


# =============================================================================
# Worker handler: persists + is replay-safe
# =============================================================================


async def test_worker_handler_raises_alert_and_is_replay_safe(tenant, session_factory) -> None:
    payload = {
        "payment_id": str(uuid4()),
        "order_id": str(uuid4()),
        "order_status": "cancelled",
        "checkout_request_id": "ws_CO_TEST",
        "mpesa_receipt_number": "SGH12345",
        "amount_cents": 25000,
    }
    await worker.handle_payment_collected_after_terminal(payload, tenant.id)
    # Replay the same event (at-least-once delivery).
    await worker.handle_payment_collected_after_terminal(payload, tenant.id)

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OwnerAlert).where(
                        OwnerAlert.tenant_id == tenant.id,
                        OwnerAlert.kind == "payment.collected_after_terminal",
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1, "a replayed outbox event must not raise a second alert"
    alert = rows[0]
    assert alert.severity == AlertSeverity.CRITICAL.value
    assert alert.status == AlertStatus.OPEN.value
    assert alert.resource_type == "order"
    assert str(alert.resource_id) == payload["order_id"]
    assert alert.dedupe_key == f"payment_collected_after_terminal:{payload['order_id']}"
    # Receipt and amount are merchant data, not customer PII.
    assert "2547" not in alert.body
    assert "2547" not in str(alert.metadata_json)


async def test_worker_alert_body_has_no_customer_phone(tenant, session_factory) -> None:
    """The alert body/metadata never copy a customer phone number."""
    payload = {
        "payment_id": str(uuid4()),
        "order_id": str(uuid4()),
        "order_status": "failed_delivery",
        "checkout_request_id": "ws_CO_TEST2",
        "mpesa_receipt_number": "SGH99999",
        "amount_cents": 10000,
    }
    await worker.handle_payment_collected_after_terminal(payload, tenant.id)

    async with session_factory() as session:
        alert = (
            (await session.execute(select(OwnerAlert).where(OwnerAlert.tenant_id == tenant.id)))
            .scalars()
            .one()
        )

    assert "2547" not in alert.body
    assert "2547" not in str(alert.metadata_json)
    assert "KES 100.00" in alert.body
