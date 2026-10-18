"""
CRM memory service (Phase 8).

Turns a customer's history into remembered facts, with hard privacy guardrails:

- **Append-only events**: :meth:`CRMService.record_event` only inserts and is
  idempotent per ``dedupe_key`` (savepoint absorbs a lost race). No update/delete
  path is exposed; migration 0012 adds a DB trigger as a backstop.
- **Consent is explicit**: :meth:`has_consent` reads the latest record per
  (customer, purpose) and defaults to DENIED. Marketing/win-back sends must gate
  on it. :meth:`opt_out` records a denial.
- **Traits are allow-listed, versioned and evidence-backed**: only names in
  :data:`ALLOWED_TRAITS` can be stored, so no inferred sensitive attribute
  (health/religion/ethnicity/sexual orientation/…) is ever recorded; a stored
  trait always carries its evidence and a monotonically increasing version.
- **Arithmetic is deterministic** (RFM, LTV, reorder cycle). The LLM never
  computes these.
- **Erasure is human-approved** (``approved_by`` required) and fully audited; it
  removes the customer's CRM memory, anonymises the customer row and scrubs the
  customer's personal identifiers from their orders (email, address, location
  and free-text identity columns) and their payments (M-Pesa MSISDN and any
  metadata ``phone`` key), keeping the order/payment records themselves
  (financial record) without personal identifiers. The audit entry stores only
  an opaque tombstone and a boolean ``phone_present`` flag, never plaintext PII.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from socialerp.settings import get_settings
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.crm.repository import CRMRepository
from packages.core.domain import TenantId
from packages.core.models.crm import ConsentRecord, CustomerEvent, CustomerTrait
from packages.core.models.whatsapp import WhatsAppContact, WhatsAppMessage

# Names a trait may take. Anything else is refused (no sensitive attributes).
ALLOWED_TRAITS: frozenset[str] = frozenset({
    "order_count",
    "lifetime_value_cents",
    "typical_order_hour",
    "reorder_cycle_days",
    "rfm_score",
    "churn_risk",
    "preferred_size",
    "preferred_color",
})

# Explicitly refused (defence in depth / documentation of intent).
SENSITIVE_TRAIT_NAMES: frozenset[str] = frozenset({
    "religion",
    "ethnicity",
    "race",
    "health",
    "sexual_orientation",
    "gender",
    "political_affiliation",
    "disability",
})

PURPOSE_MARKETING = "marketing"


class TraitNotAllowedError(ValueError):
    """Raised when a non-allow-listed (e.g. sensitive) trait name is used."""


class CustomerNotFoundError(Exception):
    """Raised when a customer does not exist for the tenant."""


def _is_paid(order: Any) -> bool:
    status = getattr(order.payment_status, "value", order.payment_status)
    return status == "completed" or getattr(order, "paid_at", None) is not None


def _median_int(values: list[int]) -> int:
    return int(statistics.median(values))


class CRMService:
    """Customer memory for one tenant."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CRMRepository(session)
        self.audit = AuditLogger(session)

    # ---------------------------------------------------------------- events
    async def record_event(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        event_type: str,
        *,
        channel: str | None = None,
        summary: str = "",
        data: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CustomerEvent:
        """Append a summarised, PII-minimal event (idempotent on dedupe_key)."""
        if dedupe_key is not None:
            existing = await self.repo.get_event_by_dedupe(tenant_id, dedupe_key)
            if existing is not None:
                return existing

        event = CustomerEvent(
            tenant_id=tenant_id,
            customer_id=customer_id,
            event_type=event_type,
            channel=channel,
            summary=summary[:300],
            data_json=data or {},
            dedupe_key=dedupe_key,
            occurred_at=occurred_at or datetime.now(UTC),
        )
        try:
            async with self.session.begin_nested():
                await self.repo.add_event(event)
        except IntegrityError:
            if dedupe_key is None:
                raise
            existing = await self.repo.get_event_by_dedupe(tenant_id, dedupe_key)
            if existing is None:
                raise
            return existing
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="crm.event_recorded",
            resource="customer_event",
            resource_id=event.id,
            after={"event_type": event_type, "channel": channel},
        )
        return event

    # --------------------------------------------------------------- consent
    async def record_consent(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        *,
        purpose: str,
        granted: bool,
        source: str,
        evidence: str | None = None,
    ) -> ConsentRecord:
        record = ConsentRecord(
            tenant_id=tenant_id,
            customer_id=customer_id,
            purpose=purpose,
            granted=granted,
            source=source,
            evidence=evidence,
        )
        await self.repo.add_consent(record)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="crm.consent_recorded",
            resource="consent_record",
            resource_id=record.id,
            after={"purpose": purpose, "granted": granted, "source": source},
        )
        return record

    async def has_consent(self, tenant_id: TenantId, customer_id: UUID, purpose: str) -> bool:
        """Latest decision wins; absence of any record means DENIED."""
        record = await self.repo.latest_consent(tenant_id, customer_id, purpose)
        return bool(record is not None and record.granted)

    async def opt_out(self, tenant_id: TenantId, customer_id: UUID) -> ConsentRecord:
        """Record a marketing opt-out (denial)."""
        return await self.record_consent(
            tenant_id, customer_id, purpose=PURPOSE_MARKETING, granted=False, source="opt_out"
        )

    # ---------------------------------------------------------------- traits
    async def record_trait(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        name: str,
        value: Any,
        *,
        evidence: dict[str, Any],
        confidence: float = 1.0,
    ) -> CustomerTrait:
        """Store a new version of an allow-listed trait, with its evidence."""
        if name in SENSITIVE_TRAIT_NAMES or name not in ALLOWED_TRAITS:
            raise TraitNotAllowedError(f"trait not allowed: {name!r}")
        if not evidence:
            raise TraitNotAllowedError(f"trait {name!r} must carry evidence")
        version = await self.repo.max_trait_version(tenant_id, customer_id, name) + 1
        trait = CustomerTrait(
            tenant_id=tenant_id,
            customer_id=customer_id,
            name=name,
            value_json=value,
            confidence=confidence,
            evidence_json=evidence,
            version=version,
        )
        await self.repo.add_trait(trait)
        return trait

    async def derive_traits(self, tenant_id: TenantId, customer_id: UUID) -> list[CustomerTrait]:
        """Recompute the allow-listed traits from the customer's orders."""
        orders = await self.repo.orders_for_customer(tenant_id, customer_id)
        order_ids = [o.id for o in orders]
        items = await self.repo.items_for_orders(tenant_id, order_ids)

        paid = [o for o in orders if _is_paid(o)]
        ltv_cents = sum(int(o.total_cents) for o in paid)
        order_count = len(orders)

        derived: list[CustomerTrait] = []

        derived.append(
            await self.record_trait(
                tenant_id,
                customer_id,
                "order_count",
                order_count,
                evidence={"order_ids": [str(o.id) for o in orders]},
                confidence=1.0,
            )
        )
        derived.append(
            await self.record_trait(
                tenant_id,
                customer_id,
                "lifetime_value_cents",
                ltv_cents,
                evidence={
                    "paid_order_ids": [str(o.id) for o in paid],
                    "paid_count": len(paid),
                },
                confidence=1.0,
            )
        )

        if orders:
            # AGENTS.md targets Nairobi SMEs; Kenya has no DST, EAT = UTC+3.
            # created_at is stored UTC, so convert to the tenant-local zone
            # (TENANT_TIMEZONE, default Africa/Nairobi) before reading .hour.
            local_tz = ZoneInfo(get_settings().TENANT_TIMEZONE)
            hours = [o.created_at.astimezone(local_tz).hour for o in orders]
            typical_hour = Counter(hours).most_common(1)[0][0]
            derived.append(
                await self.record_trait(
                    tenant_id,
                    customer_id,
                    "typical_order_hour",
                    int(typical_hour),
                    evidence={"hours": hours},
                    confidence=min(1.0, order_count / 3),
                )
            )

            if order_count >= 2:
                dates = sorted(o.created_at for o in orders)
                gaps = [
                    (dates[i + 1] - dates[i]).days
                    for i in range(len(dates) - 1)
                    if dates[i + 1] > dates[i]
                ]
                if gaps:
                    median_gap = _median_int(gaps)
                    derived.append(
                        await self.record_trait(
                            tenant_id,
                            customer_id,
                            "reorder_cycle_days",
                            median_gap,
                            evidence={"gaps_days": gaps},
                            confidence=min(1.0, len(gaps) / 3),
                        )
                    )

            last_order = max(o.created_at for o in orders)
            days_since = (datetime.now(UTC) - last_order).days
            r = _recency_bucket(days_since)
            f = _frequency_bucket(order_count)
            m = _monetary_bucket(ltv_cents)
            derived.append(
                await self.record_trait(
                    tenant_id,
                    customer_id,
                    "rfm_score",
                    {"r": r, "f": f, "m": m},
                    evidence={
                        "days_since_last_order": days_since,
                        "order_count": order_count,
                        "lifetime_value_cents": ltv_cents,
                    },
                    confidence=min(1.0, order_count / 3),
                )
            )

            expected_cycle = 30
            cycle_trait = next((t for t in derived if t.name == "reorder_cycle_days"), None)
            if cycle_trait is not None:
                expected_cycle = max(1, int(cycle_trait.value_json))
            over = days_since - expected_cycle
            churn = 0.0 if over <= 0 else min(1.0, over / expected_cycle)
            derived.append(
                await self.record_trait(
                    tenant_id,
                    customer_id,
                    "churn_risk",
                    round(churn, 3),
                    evidence={
                        "days_since_last_order": days_since,
                        "expected_cycle_days": expected_cycle,
                    },
                    confidence=min(1.0, order_count / 3),
                )
            )

        size, size_evidence = _modal_variant_attribute(items, {"size"})
        if size is not None:
            derived.append(
                await self.record_trait(
                    tenant_id,
                    customer_id,
                    "preferred_size",
                    size,
                    evidence=size_evidence,
                    confidence=min(1.0, size_evidence["occurrences"] / 3),
                )
            )
        color, color_evidence = _modal_variant_attribute(items, {"color", "colour"})
        if color is not None:
            derived.append(
                await self.record_trait(
                    tenant_id,
                    customer_id,
                    "preferred_color",
                    color,
                    evidence=color_evidence,
                    confidence=min(1.0, color_evidence["occurrences"] / 3),
                )
            )

        return derived

    # ------------------------------------------------- export / erasure (DSR)
    async def export_customer(self, tenant_id: TenantId, customer_id: UUID) -> dict[str, Any]:
        """Assemble the customer's full record (data-subject access)."""
        customer = await self.repo.get_customer(tenant_id, customer_id)
        if customer is None:
            raise CustomerNotFoundError(str(customer_id))
        # U33: a DSR export must be COMPLETE — no 200-row cap, no truncation.
        events = await self.repo.all_events(tenant_id, customer_id)
        traits = await self.repo.list_traits(tenant_id, customer_id)
        consents = await self.repo.list_consents(tenant_id, customer_id)
        orders = await self.repo.orders_for_customer(tenant_id, customer_id)
        return {
            "customer": {
                "id": str(customer.id),
                "phone": customer.phone,
                "name": customer.name,
                "email": customer.email,
                "default_area": customer.default_area,
                "is_active": customer.is_active,
                "created_at": customer.created_at.isoformat(),
            },
            "events": [
                {
                    "id": str(e.id),
                    "event_type": e.event_type,
                    "channel": e.channel,
                    "summary": e.summary,
                    "occurred_at": e.occurred_at.isoformat(),
                }
                for e in events
            ],
            "traits": [
                {
                    "name": t.name,
                    "value": t.value_json,
                    "confidence": t.confidence,
                    "version": t.version,
                    "evidence": t.evidence_json,
                }
                for t in traits
            ],
            "consents": [
                {
                    "purpose": c.purpose,
                    "granted": c.granted,
                    "source": c.source,
                    "recorded_at": c.recorded_at.isoformat(),
                }
                for c in consents
            ],
            "orders": [
                {
                    "id": str(o.id),
                    "status": str(getattr(o.status, "value", o.status)),
                    "total_cents": int(o.total_cents),
                    "created_at": o.created_at.isoformat(),
                }
                for o in orders
            ],
        }

    async def delete_customer(
        self, tenant_id: TenantId, customer_id: UUID, *, approved_by: UUID
    ) -> None:
        """Erase a customer's CRM memory (human-approved, audited).

        Sets the erasure session flag so the append-only trigger permits the
        event deletion, removes events/traits/consents, anonymises the customer
        row and scrubs the customer's personal identifiers from their orders
        (email/address/location/free text) and payments (MSISDN + metadata
        phone key). Order and payment records themselves are retained
        (financial record) without personal identifiers; the audit entry is
        PII-free (boolean ``phone_present`` + opaque tombstone only).
        """
        # U36: the CRM/tenant tables carry RLS keyed on
        # current_tenant_id(); without this transaction-local GUC the reads
        # find nothing and the DELETE/UPDATEs below silently match 0 rows
        # once TenantContextMiddleware enforces the policies.
        await self.session.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        customer = await self.repo.get_customer(tenant_id, customer_id)
        if customer is None:
            raise CustomerNotFoundError(str(customer_id))

        await self.session.execute(text("SELECT set_config('app.allow_event_erasure', 'on', true)"))
        # Capture the original phone before we overwrite it with the tombstone.
        original_phone = customer.phone
        await self.session.execute(
            text("DELETE FROM customer_events WHERE tenant_id = :t AND customer_id = :c"),
            {"t": str(tenant_id), "c": str(customer_id)},
        )
        await self.session.execute(
            text("DELETE FROM customer_traits WHERE tenant_id = :t AND customer_id = :c"),
            {"t": str(tenant_id), "c": str(customer_id)},
        )
        await self.session.execute(
            text("DELETE FROM consent_records WHERE tenant_id = :t AND customer_id = :c"),
            {"t": str(tenant_id), "c": str(customer_id)},
        )
        tombstone = erasure_tombstone(customer_id)
        await self.session.execute(
            text(
                "UPDATE customers SET phone = :p, name = NULL, email = NULL, "
                "default_address = NULL, default_area = NULL, is_active = false "
                "WHERE tenant_id = :t AND id = :c"
            ),
            {"p": tombstone, "t": str(tenant_id), "c": str(customer_id)},
        )
        # U31: identity + location text on the retained order rows; money
        # columns (totals, payment status/reference) are a financial record
        # and are never touched.
        await self.session.execute(
            text(
                "UPDATE orders SET customer_phone = :p, customer_name = NULL, "
                "customer_email = NULL, delivery_address = NULL, "
                "delivery_area = NULL, delivery_lat = NULL, delivery_lng = NULL, "
                "delivery_instructions = NULL, notes = NULL "
                "WHERE tenant_id = :t AND customer_id = :c"
            ),
            {"p": tombstone, "t": str(tenant_id), "c": str(customer_id)},
        )
        # U30: the M-Pesa MSISDN (and any top-level metadata phone key) on
        # their payments; amount/status/receipt/provider ids stay untouched.
        # Payments link to the customer only through their orders.
        await self.session.execute(
            text(
                "UPDATE payments SET phone_number = :p, "
                "metadata_json = metadata_json - 'phone' "
                "WHERE tenant_id = :t "
                "AND order_id IN ("
                "SELECT id FROM orders WHERE tenant_id = :t AND customer_id = :c"
                ")"
            ),
            {"p": tombstone, "t": str(tenant_id), "c": str(customer_id)},
        )
        # U51: scrub the phone key from outbox payment payloads.
        # The outbox stores the phone in its JSONB payload for payment.initiated
        # events. Erasure runs long after the payment lifecycle, so removing the
        # key is safe. Use JSONB deletion operator (-) to remove the key.
        await self.session.execute(
            text(
                "UPDATE outbox SET payload = payload - 'phone' "
                "WHERE tenant_id = :t "
                "AND payload ? 'phone' "
                "AND payload->>'phone' = :phone"
            ),
            {"t": str(tenant_id), "phone": original_phone},
        )
        # U52: HARD DELETE WhatsApp contacts and messages for this customer.
        # DSR erasure means "gone", not "redacted but linkable". These are
        # channel-infrastructure tables; no business ledger depends on them.
        # WhatsAppContact links to Customer via phone/wa_id, not a customer_id FK.
        # Delete messages first (FK to contact), then contacts.
        await self.session.execute(
            delete(WhatsAppMessage).where(
                WhatsAppMessage.contact_id.in_(
                    select(WhatsAppContact.id).where(
                        WhatsAppContact.phone == original_phone,
                        WhatsAppContact.tenant_id == tenant_id,
                    )
                )
            )
        )
        await self.session.execute(
            delete(WhatsAppContact).where(
                WhatsAppContact.phone == original_phone,
                WhatsAppContact.tenant_id == tenant_id,
            )
        )
        # U32: the audit_log is append-only for 2555 days — a plaintext phone
        # in `before` would survive the erasure. Record presence, not value.
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=approved_by,
            action="crm.customer_erased",
            resource="customer",
            resource_id=customer_id,
            before={"phone_present": customer.phone is not None},
            after={"phone": tombstone},
        )


# ------------------------------------------------------------------ helpers


def _recency_bucket(days_since: int) -> int:
    if days_since <= 7:
        return 5
    if days_since <= 30:
        return 4
    if days_since <= 90:
        return 3
    if days_since <= 180:
        return 2
    return 1


def _frequency_bucket(order_count: int) -> int:
    if order_count >= 10:
        return 5
    if order_count >= 5:
        return 4
    if order_count >= 3:
        return 3
    if order_count >= 2:
        return 2
    return 1


def _monetary_bucket(ltv_cents: int) -> int:
    if ltv_cents >= 5_000_000:
        return 5
    if ltv_cents >= 2_000_000:
        return 4
    if ltv_cents >= 500_000:
        return 3
    if ltv_cents >= 100_000:
        return 2
    return 1


def _modal_variant_attribute(
    items: list[Any], names: set[str]
) -> tuple[str | None, dict[str, Any]]:
    """Most common variant attribute value whose attribute name is in ``names``."""
    counter: Counter[str] = Counter()
    for item in items:
        attrs = (item.attributes or {}).get("variant_attributes") or []
        for attr in attrs:
            attr_name = str(attr.get("name", "")).lower()
            if attr_name in names and attr.get("value") is not None:
                counter[str(attr["value"])] += item.quantity
    if not counter:
        return None, {}
    value, occurrences = counter.most_common(1)[0]
    return value, {"occurrences": occurrences, "candidates": dict(counter)}


def erasure_tombstone(customer_id: UUID) -> str:
    """A PII-free replacement for the customer's phone, fit for VARCHAR(20).

    Derived once so the service and its tests can never disagree on the
    format: ``erased-`` (7 chars) + 12 hex chars of the UUID = 19 chars,
    within the ``customers.phone`` / ``orders.customer_phone`` String(20)
    columns (the previous 43-char form overflowed them and broke erasure).
    """
    return f"erased-{customer_id.hex[:12]}"


__all__ = [
    "ALLOWED_TRAITS",
    "PURPOSE_MARKETING",
    "SENSITIVE_TRAIT_NAMES",
    "CRMService",
    "CustomerNotFoundError",
    "TraitNotAllowedError",
    "erasure_tombstone",
]
