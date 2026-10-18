"""Phase 8a: trait derivation — allow-list, evidence, versioning (plan invariants 1, 2, 7).

Covers the plan's Slice 8a acceptance list:

- ``derive_traits`` produces only allow-listed traits (RFM, LTV, reorder cycle,
  typical order hour, preferred size/colour), each with evidence and a version;
  re-derivation appends a new version and keeps the old one.
- Any trait name outside the allow-list (sensitive attributes) is refused with
  ``TraitNotAllowedError`` — plan invariant 1.
- A trait with no evidence is refused — plan invariant 2.
- Trait arithmetic is deterministic plain code — plan invariant 7.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from socialerp.settings import get_settings
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.crm import (
    ALLOWED_TRAITS,
    SENSITIVE_TRAIT_NAMES,
    CRMService,
    TraitNotAllowedError,
)
from packages.core.crm.repository import CRMRepository
from packages.core.domain import TenantId
from packages.core.models.core import Tenant
from packages.core.models.crm import CustomerTrait
from tests.crm.helpers import make_customer, make_order, seed_paid_history

# The documented allow-list (plan Slice 8a). A regression that widens the set
# without a plan change must fail here too.
EXPECTED_ALLOWED_TRAITS = frozenset({
    "order_count",
    "lifetime_value_cents",
    "typical_order_hour",
    "reorder_cycle_days",
    "rfm_score",
    "churn_risk",
    "preferred_size",
    "preferred_color",
})


async def _derive(service: CRMService, tenant_id: UUID, customer_id: UUID) -> list[CustomerTrait]:
    return await service.derive_traits(TenantId(tenant_id), customer_id)


async def test_allowed_trait_set_matches_plan() -> None:
    assert ALLOWED_TRAITS == EXPECTED_ALLOWED_TRAITS


async def test_derive_traits_produces_allow_listed_traits_with_evidence(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer, orders, _product = await seed_paid_history(session, tenant.id)
        customer_id = customer.id
        service = CRMService(session)
        derived = await _derive(service, tenant.id, customer_id)

        names = {t.name for t in derived}
        assert names <= ALLOWED_TRAITS, "derive_traits may only emit allow-listed traits"

        by_name = {t.name: t for t in derived}
        order_ids_ascending = [str(o.id) for o in sorted(orders, key=lambda o: o.created_at)]
        assert by_name["order_count"].value_json == 3
        assert by_name["order_count"].evidence_json["order_ids"] == order_ids_ascending

        assert by_name["lifetime_value_cents"].value_json == 20_000
        assert by_name["lifetime_value_cents"].evidence_json["paid_count"] == 3
        assert len(by_name["lifetime_value_cents"].evidence_json["paid_order_ids"]) == 3

        # Orders are evenly spaced (3-day gaps); all share the seed hour.
        assert by_name["reorder_cycle_days"].value_json == 3
        assert by_name["reorder_cycle_days"].evidence_json["gaps_days"] == [3, 3]

        # U34: the derived hour is tenant-local (Africa/Nairobi, EAT = UTC+3).
        local_tz = ZoneInfo(get_settings().TENANT_TIMEZONE)
        hours = [
            o.created_at.astimezone(local_tz).hour
            for o in sorted(orders, key=lambda o: o.created_at)
        ]
        assert by_name["typical_order_hour"].value_json == hours[0]
        assert by_name["typical_order_hour"].evidence_json["hours"] == hours

        # Deterministic RFM: recency 5 (<=7d), frequency 3 (3 orders), monetary 1 (<100k).
        assert by_name["rfm_score"].value_json == {"r": 5, "f": 3, "m": 1}
        rfm_evidence = by_name["rfm_score"].evidence_json
        assert rfm_evidence["days_since_last_order"] == 3
        assert rfm_evidence["order_count"] == 3
        assert rfm_evidence["lifetime_value_cents"] == 20_000

        # Last order is exactly the expected cycle away -> not overdue -> zero churn.
        assert by_name["churn_risk"].value_json == pytest.approx(0.0)
        assert by_name["churn_risk"].evidence_json["expected_cycle_days"] == 3

        # Modal variant attributes, weighted by quantity, from order items.
        assert by_name["preferred_size"].value_json == "M"
        assert by_name["preferred_size"].evidence_json["occurrences"] == 2
        assert by_name["preferred_size"].evidence_json["candidates"] == {"M": 2, "L": 1}
        assert by_name["preferred_color"].value_json == "black"
        assert by_name["preferred_color"].evidence_json["occurrences"] == 3

        for trait in derived:
            assert trait.evidence_json, f"trait {trait.name} must carry evidence"
            assert trait.version == 1
            assert trait.confidence > 0.0


async def test_derive_traits_typical_hour_is_the_mode(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The typical order hour is the most frequent hour, not the first or last."""
    base = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        for day, hour in ((1, 15), (2, 10), (3, 10)):
            await make_order(
                session,
                tenant.id,
                customer.id,
                created_at=base + timedelta(days=day, hours=hour),
                payment_status="completed",
                status="paid",
            )
        derived = await _derive(CRMService(session), tenant.id, customer.id)

    by_name = {t.name: t for t in derived}
    # U34: seeded hours are UTC (15, 10, 10); in tenant-local Nairobi time
    # (EAT = UTC+3) they are 18, 13, 13 — the mode is still the 13h pair.
    assert by_name["typical_order_hour"].value_json == 13
    assert sorted(by_name["typical_order_hour"].evidence_json["hours"]) == [13, 13, 18]


async def test_derive_traits_counts_only_paid_orders_in_ltv(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        await make_order(
            session,
            tenant.id,
            customer.id,
            total_cents=3000,
            created_at=now - timedelta(days=2),
            payment_status="completed",
            status="paid",
        )
        await make_order(
            session,
            tenant.id,
            customer.id,
            total_cents=4000,
            created_at=now - timedelta(days=1),
            paid_at=now - timedelta(days=1),
            status="paid",
        )
        # Unpaid: neither payment_status completed nor paid_at set.
        await make_order(
            session,
            tenant.id,
            customer.id,
            total_cents=9000,
            created_at=now,
            payment_status="pending",
        )
        derived = await _derive(CRMService(session), tenant.id, customer.id)

    by_name = {t.name: t for t in derived}
    assert by_name["order_count"].value_json == 3
    assert by_name["lifetime_value_cents"].value_json == 7000
    assert by_name["lifetime_value_cents"].evidence_json["paid_count"] == 2
    assert len(by_name["lifetime_value_cents"].evidence_json["paid_order_ids"]) == 2


async def test_derive_traits_customer_with_no_orders(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A customer with no history still gets count/LTV zero traits, with evidence."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        derived = await _derive(CRMService(session), tenant.id, customer.id)

    by_name = {t.name: t for t in derived}
    assert set(by_name) == {"order_count", "lifetime_value_cents"}
    assert by_name["order_count"].value_json == 0
    assert by_name["order_count"].evidence_json == {"order_ids": []}
    assert by_name["lifetime_value_cents"].value_json == 0
    assert by_name["lifetime_value_cents"].evidence_json["paid_order_ids"] == []
    for trait in derived:
        assert trait.evidence_json


async def test_rederivation_appends_version_and_keeps_old(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Re-deriving appends a new version per trait; prior versions are kept."""
    async with session_factory() as session, session.begin():
        customer, _orders, _product = await seed_paid_history(session, tenant.id)
        customer_id = customer.id
        service = CRMService(session)
        tid = TenantId(tenant.id)

        first = await _derive(service, tenant.id, customer_id)
        assert first and all(t.version == 1 for t in first)

        second = await _derive(service, tenant.id, customer_id)
        assert second and all(t.version == 2 for t in second)

        repo = CRMRepository(session)
        all_versions = await repo.list_traits(tid, customer_id)
        assert len(all_versions) == len(first) + len(second)

        latest = await repo.latest_traits(tid, customer_id)
        assert {t.name for t in latest} == {t.name for t in first}
        assert all(t.version == 2 for t in latest)

        # Deterministic re-derivation: same inputs, same values.
        assert {t.name: t.value_json for t in first} == {t.name: t.value_json for t in second}


async def test_derived_trait_names_stay_allow_listed_after_rederive(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer, _orders, _product = await seed_paid_history(session, tenant.id)
        service = CRMService(session)
        await _derive(service, tenant.id, customer.id)
        again = await _derive(service, tenant.id, customer.id)

    assert again
    for trait in again:
        assert trait.name in ALLOWED_TRAITS
        assert trait.name not in SENSITIVE_TRAIT_NAMES
        assert trait.evidence_json


async def test_record_trait_refuses_every_sensitive_name(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Plan invariant 1: no inferred sensitive attribute can ever be stored."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)
        for name in sorted(SENSITIVE_TRAIT_NAMES):
            with pytest.raises(TraitNotAllowedError):
                await service.record_trait(
                    tid, customer.id, name, "value", evidence={"source": "synthetic"},
                )

        stored = await service.repo.list_traits(tid, customer.id)
        assert stored == []


async def test_record_trait_refuses_any_non_allow_listed_name(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)
        for name in ("favorite_religion", "shoe_size", "invented_trait", "", "Health"):
            with pytest.raises(TraitNotAllowedError):
                await service.record_trait(tid, customer.id, name, 1, evidence={"x": 1})

        stored = await service.repo.list_traits(tid, customer.id)
        assert stored == [], "a refused trait must not be persisted"


async def test_record_trait_refuses_empty_evidence(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Plan invariant 2: a trait with no evidence is refused."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        with pytest.raises(TraitNotAllowedError, match="must carry evidence"):
            await service.record_trait(
                TenantId(tenant.id), customer.id, "order_count", 5, evidence={},
            )


async def test_record_trait_versions_increment_per_name(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)
        t1 = await service.record_trait(tid, customer.id, "order_count", 1, evidence={"n": 1})
        t2 = await service.record_trait(tid, customer.id, "order_count", 2, evidence={"n": 2})
        o1 = await service.record_trait(
            tid, customer.id, "lifetime_value_cents", 100, evidence={"n": 1},
        )
    assert t1.version == 1
    assert t2.version == 2
    assert o1.version == 1, "versions are tracked per trait name, not globally"


async def test_record_trait_requires_existing_customer(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Trait storage fails closed on a foreign/unknown customer id (FK)."""
    async with session_factory() as session, session.begin():
        service = CRMService(session)
        with pytest.raises(IntegrityError):
            await service.record_trait(
                TenantId(tenant.id), uuid4(), "order_count", 1, evidence={"x": 1},
            )


async def test_typical_order_hour_is_tenant_local_nairobi_time(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U34: the hour must be tenant-local (Nairobi/EAT), not UTC.

    An order placed at 19:00 in Nairobi (16:00 UTC) must derive
    ``typical_order_hour == 19`` — AGENTS.md targets Nairobi SMEs and the
    owner reads "typical order hour" as their local wall-clock hour.
    """
    # 2026-06-01T19:00+03:00 == 2026-06-01T16:00Z; Kenya has no DST.
    order_at_1900_eat = datetime(2026, 6, 1, 16, 0, 0, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        await make_order(
            session,
            tenant.id,
            customer.id,
            created_at=order_at_1900_eat,
            payment_status="completed",
            status="paid",
        )
        derived = await _derive(CRMService(session), tenant.id, customer.id)

    by_name = {t.name: t for t in derived}
    assert by_name["typical_order_hour"].value_json == 19, (
        "U34: 16:00 UTC must derive as hour 19 in tenant-local Nairobi time, "
        f"got {by_name['typical_order_hour'].value_json}"
    )
    assert by_name["typical_order_hour"].evidence_json["hours"] == [19]
