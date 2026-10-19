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

import asyncio
import re
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from apps.worker import main as worker
from packages.core.alerts import AlertService
from packages.core.auth.dependencies import (
    get_current_tenant_id,
    get_current_user_id,
    get_db,
)
from packages.core.models.alert import AlertSeverity, AlertStatus, OwnerAlert
from packages.core.models.core import Tenant, User, UserRole
from packages.core.models.order import Customer, Order

ACTING_USER = uuid4()


async def _seed_user(session_factory, tenant, user_id: UUID) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            User(
                id=user_id,
                tenant_id=tenant.id,
                email=f"actor-{user_id.hex[:8]}@example.com",
                password_hash="x",
                full_name="Actor",
                role=UserRole.STAFF,
                is_active=True,
            ),
        )


@pytest.fixture
async def alerts_client(tenant, session_factory) -> AsyncGenerator[AsyncClient]:
    """Client with tenant + db overrides for the authenticated alerts routes."""

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            yield session

    # A real user row, so the audit_log user_id FK is satisfied on ack.
    await _seed_user(session_factory, tenant, ACTING_USER)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id
    app.dependency_overrides[get_current_user_id] = lambda: ACTING_USER

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
    actor = uuid4()
    await _seed_user(session_factory, tenant, actor)
    async with session_factory() as session, session.begin():
        service = AlertService(session)
        alert = await service.raise_alert(tenant.id, kind="k", title="t")
        acked = await service.acknowledge(tenant.id, alert.id, user_id=actor)
        first_at = acked.acknowledged_at if acked else None
        again = await service.acknowledge(tenant.id, alert.id, user_id=actor)
    assert acked is not None and acked.status == AlertStatus.ACKNOWLEDGED.value
    assert acked.acknowledged_at is not None, "acknowledged_at must be recorded"
    assert acked.acknowledged_by == actor
    assert again is not None and again.status == AlertStatus.ACKNOWLEDGED.value
    # A second ack does not move the original timestamp.
    assert again.acknowledged_at == first_at


async def test_raise_alert_backstop_unique_constraint(tenant, session_factory) -> None:
    """The unique constraint is the real replay/race backstop, not the check-then-insert."""
    async with session_factory() as session, session.begin():
        service = AlertService(session)
        await service.raise_alert(tenant.id, kind="k", title="first", dedupe_key="dupe")
        # Bypass the service pre-check and hit the DB constraint directly. The
        # savepoint keeps the outer transaction usable after the expected error.
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO owner_alerts "
                        "(tenant_id, kind, severity, title, body, metadata_json, dedupe_key) "
                        "VALUES (:t, 'k', 'warning', 'second', '', '{}'::jsonb, 'dupe')",
                    ),
                    {"t": str(tenant.id)},
                )


async def test_raise_alert_is_race_safe(tenant, session_factory) -> None:
    """Two concurrent raises with the same dedupe_key yield exactly one alert."""

    async def _raise() -> None:
        async with session_factory() as session, session.begin():
            await AlertService(session).raise_alert(
                tenant.id, kind="k", title="race", dedupe_key="race-key",
            )

    await asyncio.gather(_raise(), _raise())

    async with session_factory() as session:
        rows = (
            (await session.execute(select(OwnerAlert).where(OwnerAlert.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1, "concurrent duplicate raises must collapse to one alert"


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
    body = acked.json()
    assert body["status"] == "acknowledged"
    assert body["acknowledged_at"] is not None

    # Filtering by status now hides it from the open list.
    open_only = await alerts_client.get("/api/v1/alerts", params={"status": "open"})
    assert open_only.json() == []


async def test_alerts_api_404_for_foreign_alert(
    alerts_client: AsyncClient, tenant, session_factory,
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
            ),
        )
        await session.flush()
        service = AlertService(session)
        foreign = await service.raise_alert(
            other_tenant_id, kind="k", title="Foreign alert", dedupe_key="foreign-1",
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
                    ),
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
    # (PII-freedom is asserted separately, against a real seeded customer.)


async def test_worker_alert_body_has_no_customer_phone(tenant, session_factory) -> None:
    """The alert body/metadata never copy a customer phone number.

    We seed a REAL order + customer (with a phone) so that any future change
    which enriches the alert from the orders/customers tables would have
    something to leak — the test would then catch it.
    """
    customer_phone = "254711111111"
    async with session_factory() as session, session.begin():
        customer = Customer(tenant_id=tenant.id, phone=customer_phone, name="Jane Confidential")
        session.add(customer)
        await session.flush()
        order = Order(
            tenant_id=tenant.id,
            customer_id=customer.id,
            customer_phone=customer_phone,
            customer_name="Jane Confidential",
            total_cents=10000,
            status="failed_delivery",
            payment_method="mpesa_stk",
        )
        session.add(order)
        await session.flush()
        order_id = str(order.id)

    payload = {
        "payment_id": str(uuid4()),
        "order_id": order_id,
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

    blob = f"{alert.body} {alert.title} {alert.metadata_json}"
    assert customer_phone not in blob
    # Normalised-phone regex catches a reformatted leak too ("+254 711 111 111").
    assert re.search(r"254\s*7\d{2}\s*\d{3}\s*\d{3}", blob) is None
    assert "KES 100.00" in alert.body
