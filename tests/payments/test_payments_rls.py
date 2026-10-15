"""
RLS isolation test for the ``payments`` table (Phase 4).

Proves a tenant cannot read or write another tenant's payment attempts, using
the non-owner ``socialerp_app`` role (subject to the RLS policies).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from socialerp.tenancy import Tenant, TenantAwareSession
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from packages.core.models.order import Customer, Order, OrderStatus, PaymentMethod
from packages.core.models.payment import Payment, PaymentRecordStatus

PHONE_A = "254700000001"
PHONE_B = "254700000002"


async def _seed(session_factory, phone: str, slug: str) -> tuple[UUID, UUID]:
    """Seed a tenant, customer, order and payment (superuser, RLS bypassed)."""
    async with session_factory() as session, session.begin():
        tenant = Tenant(
            name=f"Biz {slug}",
            slug=slug,
            owner_phone=phone,
            owner_email=f"{slug}@example.com",
            is_active=True,
            settings={},
        )
        session.add(tenant)
        await session.flush()
        customer = Customer(tenant_id=tenant.id, phone=phone, name="Buyer")
        session.add(customer)
        await session.flush()
        order = Order(
            tenant_id=tenant.id,
            customer_id=customer.id,
            customer_phone=phone,
            total_cents=500_000,
            status=OrderStatus.AWAITING_PAYMENT,
            payment_method=PaymentMethod.MPESA_STK,
        )
        session.add(order)
        await session.flush()
        payment = Payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=phone,
            amount_cents=500_000,
            status=PaymentRecordStatus.INITIATED,
            checkout_request_id=f"ws_CO_{slug}",
        )
        session.add(payment)
        return tenant.id, order.id


async def test_payments_are_isolated_by_tenant(session_factory, rls_session_factory) -> None:
    tenant_a, _order_a = await _seed(session_factory, PHONE_A, "rls-a")
    tenant_b, _order_b = await _seed(session_factory, PHONE_B, "rls-b")

    async with TenantAwareSession(tenant_a, rls_session_factory) as session:
        rows = (await session.execute(select(Payment))).scalars().all()
        assert len(rows) == 1
        assert rows[0].tenant_id == tenant_a

    async with TenantAwareSession(tenant_b, rls_session_factory) as session:
        rows = (await session.execute(select(Payment))).scalars().all()
        assert len(rows) == 1
        assert rows[0].tenant_id == tenant_b


async def test_payments_insert_wrong_tenant_blocked(session_factory, rls_session_factory) -> None:
    tenant_a, order_a = await _seed(session_factory, PHONE_A, "rls-ins")
    tenant_b, _order_b = await _seed(session_factory, PHONE_B, "rls-ins-b")

    # Context is tenant B, but the row claims tenant A -> WITH CHECK must reject.
    with pytest.raises(DBAPIError):
        async with TenantAwareSession(tenant_b, rls_session_factory) as session:
            session.add(
                Payment(
                    tenant_id=tenant_a,
                    order_id=order_a,
                    phone_number=PHONE_A,
                    amount_cents=500_000,
                    status=PaymentRecordStatus.INITIATED,
                    checkout_request_id="ws_CO_forbidden",
                )
            )
            await session.flush()
