"""
Segment service (Phase 8b): tenant-scoped, read-only, batch evaluation.

Turns the pure rules in :mod:`packages.core.crm.segments` into answers for one
tenant, reading ``customer_traits`` rows and resolving the LATEST version per
``(customer, name)`` in Python (mirroring ``CRMRepository.latest_traits``, but
batched for a whole tenant so member listing never degenerates to N+1 — plan
failure-mode table: "N+1 on member listing | count queries").

Invariants (plan 8b):
- **Read-only**: no inserts, no updates, no audit writes; segments are
  evaluated at read time and never persisted (no ``segments`` table — plan
  "Isolation" section; no migration).
- **Tenant-scoped**: every query filters ``tenant_id``; Postgres RLS is the
  second line of defence (tests/segments/test_segments_rls.py proves the
  fail-closed behaviour).
- **Deterministic**: member lists are sorted by customer id, so pagination and
  repeated calls are stable for identical trait state.
- **Safe defaults**: a customer with no traits (or an unknown customer id)
  simply does not match any segment.

Derivation of membership (recorded per plan invariant 2):
  membership = evaluate_predicate(rule.predicate,
                                  {trait.name: trait.value_json
                                   for latest trait versions of the customer})
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.crm.segments import SEGMENT_RULES, SegmentKey, evaluate_predicate
from packages.core.domain import TenantId
from packages.core.models.crm import CustomerTrait


@dataclass(frozen=True)
class SegmentMemberPage:
    """One page of segment members (ids only — no PII beyond the CRM read path)."""

    segment: SegmentKey
    customer_ids: tuple[UUID, ...]
    total: int
    limit: int
    offset: int


class SegmentService:
    """Evaluate fixed segment rules for one tenant (read-only)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_traits_by_customer(
        self, tenant_id: TenantId, *, customer_id: UUID | None = None
    ) -> dict[UUID, dict[str, Any]]:
        """Batch-load latest trait values, grouped per customer.

        One SELECT for the whole tenant (optionally narrowed to one customer);
        the highest ``version`` per ``(customer, name)`` wins, exactly like
        ``CRMRepository.latest_traits`` does per customer. This is the only
        query :meth:`list_members` issues — the N+1 guard.
        """
        stmt = select(CustomerTrait).where(CustomerTrait.tenant_id == tenant_id)
        if customer_id is not None:
            stmt = stmt.where(CustomerTrait.customer_id == customer_id)
        rows = list((await self.session.execute(stmt)).scalars().all())

        latest: dict[tuple[UUID, str], CustomerTrait] = {}
        for trait in rows:
            key = (trait.customer_id, trait.name)
            current = latest.get(key)
            if current is None or trait.version > current.version:
                latest[key] = trait

        by_customer: dict[UUID, dict[str, Any]] = {}
        for (cid, name), trait in latest.items():
            by_customer.setdefault(cid, {})[name] = trait.value_json
        return by_customer

    async def evaluate_segment(
        self, tenant_id: TenantId, segment_key: SegmentKey, customer_id: UUID
    ) -> bool:
        """Whether one customer currently belongs to a segment.

        An unknown customer (or one with no traits) yields ``False`` — missing
        data never fabricates membership (plan invariant 6).
        """
        rule = SEGMENT_RULES[segment_key]
        by_customer = await self.latest_traits_by_customer(tenant_id, customer_id=customer_id)
        traits = by_customer.get(customer_id)
        if traits is None:
            return False
        return evaluate_predicate(rule.predicate, traits)

    async def list_members(
        self,
        tenant_id: TenantId,
        segment_key: SegmentKey,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> SegmentMemberPage:
        """All member customer ids for a segment, sorted, paginated.

        Deterministic: ids are sorted ascending, so the same trait state always
        produces the same page at the same offset.
        """
        rule = SEGMENT_RULES[segment_key]
        by_customer = await self.latest_traits_by_customer(tenant_id)
        members = sorted(
            cid for cid, traits in by_customer.items() if evaluate_predicate(rule.predicate, traits)
        )
        return SegmentMemberPage(
            segment=segment_key,
            customer_ids=tuple(members[offset : offset + limit]),
            total=len(members),
            limit=limit,
            offset=offset,
        )


def rule_for(segment_key: SegmentKey) -> Mapping[str, Any]:
    """The raw predicate tree for a segment (API copy / explainability)."""
    return SEGMENT_RULES[segment_key].predicate


__all__ = ["SegmentMemberPage", "SegmentService", "rule_for"]
