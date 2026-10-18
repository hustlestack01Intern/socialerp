"""Phase 8b: segment service — latest-version selection, determinism, tenant scope.

Covers plan acceptance criteria and failure modes:
- "Segment membership is tenant-scoped; cross-tenant test fails closed."
- Determinism: same DB state => same membership, stable (sorted) pages.
- Latest trait version wins (``record_trait`` appends; evaluation reads latest).
- N+1 guard: ``list_members`` issues exactly ONE ``customer_traits`` query for
  a whole tenant, regardless of customer count.
- Read-only: evaluation inserts/updates nothing.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.crm.segment_service import SegmentService
from packages.core.crm.segments import SegmentKey
from packages.core.domain import TenantId
from packages.core.models.crm import CustomerTrait
from tests.segments.helpers import add_trait, make_customer, make_tenant


async def test_latest_trait_version_wins(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    # v1 says first-time; a re-derivation appends v2 saying repeat. Evaluation
    # must read the highest version per (customer, name), not the first row.
    await add_trait(db_session, tenant.id, customer.id, "order_count", 1, version=1)
    await add_trait(db_session, tenant.id, customer.id, "order_count", 3, version=2)

    service = SegmentService(db_session)
    assert (
        await service.evaluate_segment(TenantId(tenant.id), SegmentKey.FIRST_TIME, customer.id)
        is False
    )
    assert (
        await service.evaluate_segment(TenantId(tenant.id), SegmentKey.REPEAT_BUYERS, customer.id)
        is True
    )


async def test_evaluate_segment_unknown_customer_is_false(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    service = SegmentService(db_session)
    unknown = await service.evaluate_segment(
        TenantId(tenant.id), SegmentKey.FIRST_TIME, customer_id=uuid4()
    )
    assert unknown is False, "unknown customer must not fabricate membership"


async def test_list_members_is_deterministic_and_sorted(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customers = []
    for i in range(5):
        customer = await make_customer(db_session, tenant.id)
        await add_trait(db_session, tenant.id, customer.id, "order_count", 2 + i % 2)
        customers.append(customer)

    service = SegmentService(db_session)
    first = await service.list_members(TenantId(tenant.id), SegmentKey.REPEAT_BUYERS)
    second = await service.list_members(TenantId(tenant.id), SegmentKey.REPEAT_BUYERS)

    assert first.total == 5
    assert first.customer_ids == second.customer_ids
    assert list(first.customer_ids) == sorted(first.customer_ids)
    assert set(first.customer_ids) == {c.id for c in customers}


async def test_list_members_is_tenant_scoped(db_session: AsyncSession) -> None:
    tenant_a = await make_tenant(db_session, slug_prefix="seg-a")
    tenant_b = await make_tenant(db_session, slug_prefix="seg-b")
    customer_a = await make_customer(db_session, tenant_a.id)
    customer_b = await make_customer(db_session, tenant_b.id)
    await add_trait(db_session, tenant_a.id, customer_a.id, "order_count", 1)
    await add_trait(db_session, tenant_b.id, customer_b.id, "order_count", 1)

    service = SegmentService(db_session)
    page_a = await service.list_members(TenantId(tenant_a.id), SegmentKey.FIRST_TIME)
    page_b = await service.list_members(TenantId(tenant_b.id), SegmentKey.FIRST_TIME)

    assert page_a.customer_ids == (customer_a.id,)
    assert page_b.customer_ids == (customer_b.id,)
    assert customer_b.id not in page_a.customer_ids
    assert customer_a.id not in page_b.customer_ids


async def test_list_members_pagination_slices_sorted_ids(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    ids = []
    for _ in range(3):
        customer = await make_customer(db_session, tenant.id)
        await add_trait(db_session, tenant.id, customer.id, "order_count", 1)
        ids.append(customer.id)

    service = SegmentService(db_session)
    all_members = await service.list_members(TenantId(tenant.id), SegmentKey.FIRST_TIME)
    page = await service.list_members(TenantId(tenant.id), SegmentKey.FIRST_TIME, limit=1, offset=1)
    assert all_members.total == 3
    assert page.total == 3
    assert page.customer_ids == (sorted(ids)[1],)


async def test_list_members_issues_a_single_query(
    test_engine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """N+1 guard (plan failure modes): one batch SELECT for any customer count."""
    async with session_factory() as session, session.begin():
        tenant = await make_tenant(session)
        for _ in range(5):
            customer = await make_customer(session, tenant.id)
            await add_trait(session, tenant.id, customer.id, "order_count", 2)
            await add_trait(session, tenant.id, customer.id, "churn_risk", 0.4)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", _record)
    try:
        async with session_factory() as session, session.begin():
            page = await SegmentService(session).list_members(
                TenantId(tenant.id), SegmentKey.REPEAT_BUYERS
            )
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _record)

    assert page.total == 5
    trait_selects = [s for s in statements if "customer_traits" in s]
    assert len(trait_selects) == 1, f"expected one batch query, saw {trait_selects!r}"


async def test_segment_evaluation_is_read_only(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    await add_trait(db_session, tenant.id, customer.id, "order_count", 1)

    service = SegmentService(db_session)
    before = await _trait_count(db_session, tenant.id)
    await service.evaluate_segment(TenantId(tenant.id), SegmentKey.FIRST_TIME, customer.id)
    await service.list_members(TenantId(tenant.id), SegmentKey.FIRST_TIME)
    after = await _trait_count(db_session, tenant.id)

    assert before == after == 1, "evaluation must not insert or update trait rows"


async def _trait_count(session: AsyncSession, tenant_id: UUID) -> int:
    stmt = (
        select(func.count()).select_from(CustomerTrait).where(CustomerTrait.tenant_id == tenant_id)
    )
    return int(await session.scalar(stmt) or 0)
