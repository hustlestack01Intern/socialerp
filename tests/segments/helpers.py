"""Synthetic seeding helpers for the Phase 8b segment tests.

Mirrors the patterns of ``tests/crm/helpers.py`` (that file is shared and is
deliberately NOT edited for 8b). All data is synthetic; helpers flush (but do
not commit) so they can be used inside a caller-owned transaction.

Traits are inserted DIRECTLY as :class:`CustomerTrait` rows rather than via
``CRMService.record_trait``: it gives exact control over versions (for
latest-version tests) and keeps these tests independent of the CRM service's
own evolution.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.core import Tenant, User, UserRole
from packages.core.models.crm import CustomerTrait
from packages.core.models.order import Customer

PHONE_PREFIX = "254700"


async def make_tenant(session: AsyncSession, *, slug_prefix: str = "seg") -> Tenant:
    tenant = Tenant(
        id=uuid4(),
        name="Segments Test Biz",
        slug=f"{slug_prefix}-{uuid4().hex[:8]}",
        owner_phone="254700000011",
        owner_email=f"{slug_prefix}-{uuid4().hex[:6]}@example.com",
        is_active=True,
        settings={},
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def make_user(
    session: AsyncSession, tenant_id: UUID, *, role: UserRole, user_id: UUID | None = None
) -> User:
    user = User(
        id=user_id or uuid4(),
        tenant_id=tenant_id,
        email=f"user-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        full_name="Test Actor",
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def make_customer(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    phone: str | None = None,
    name: str | None = "Test Customer",
) -> Customer:
    customer = Customer(
        tenant_id=tenant_id,
        phone=phone or f"{PHONE_PREFIX}{uuid4().int % 10_000_000:07d}",
        name=name,
        email="customer@example.com",
        default_address="1 Test Lane",
        default_area="Kilimani",
        is_active=True,
    )
    session.add(customer)
    await session.flush()
    return customer


async def add_trait(
    session: AsyncSession,
    tenant_id: UUID,
    customer_id: UUID,
    name: str,
    value: Any,
    *,
    version: int = 1,
    confidence: float = 1.0,
    evidence: dict[str, Any] | None = None,
) -> CustomerTrait:
    """Insert one trait version directly (bypasses CRMService on purpose)."""
    trait = CustomerTrait(
        tenant_id=tenant_id,
        customer_id=customer_id,
        name=name,
        value_json=value,
        confidence=confidence,
        evidence_json=evidence if evidence is not None else {"synthetic": True},
        version=version,
    )
    session.add(trait)
    await session.flush()
    return trait
