"""Phase 8a: data-subject rights — export completeness and erasure cascade
(plan invariants 4, 5; Slice 8a acceptance "export returns the full subject
record; erasure removes it").

``export_customer`` must return events + traits + consent + orders summary for
the data subject. ``delete_customer`` is human-approved (``approved_by`` is a
required keyword-only parameter), tenant-scoped, audited, and must remove the
customer's CRM memory while retaining de-identified order records.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.crm import CRMService, CustomerNotFoundError, erasure_tombstone
from packages.core.domain import TenantId
from packages.core.domain.payment import (
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentResult,
)
from packages.core.domain.payment import (
    STKPushRequest as DomainSTKPushRequest,
)
from packages.core.domain.payment import (
    STKPushResponse as DomainSTKPushResponse,
)
from packages.core.models.core import Tenant, UserRole
from packages.core.models.crm import CustomerEvent
from packages.core.models.payment import Payment, PaymentRecordStatus
from tests.crm.helpers import PHONE, make_customer, make_order, make_tenant, make_user


async def _seed_full_record(session: AsyncSession, tenant_id: UUID) -> UUID:
    """Customer with events, derived traits, consents and two paid orders."""
    service = CRMService(session)
    tid = TenantId(tenant_id)
    customer = await make_customer(session, tenant_id)
    await service.record_event(
        tid, customer.id, "message.summarised", channel="whatsapp", dedupe_key="evt-1",
    )
    await service.record_event(
        tid, customer.id, "order.created", channel="whatsapp", dedupe_key="evt-2",
    )
    await service.record_consent(
        tid, customer.id, purpose="marketing", granted=True, source="whatsapp_opt_in",
    )
    await service.record_consent(
        tid, customer.id, purpose="marketing", granted=False, source="opt_out",
    )
    await service.derive_traits(tid, customer.id)
    await make_order(
        session,
        tenant_id,
        customer.id,
        total_cents=5000,
        payment_status="completed",
        status="paid",
    )
    await make_order(
        session,
        tenant_id,
        customer.id,
        total_cents=7000,
        payment_status="completed",
        status="paid",
    )
    return customer.id


async def test_export_returns_full_subject_record(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer_id = await _seed_full_record(session, tenant.id)
        payload = await CRMService(session).export_customer(TenantId(tenant.id), customer_id)

    assert set(payload) == {"customer", "events", "traits", "consents", "orders"}

    assert payload["customer"]["id"] == str(customer_id)
    assert payload["customer"]["phone"] == "254712345678"
    assert payload["customer"]["name"] == "Test Customer"
    assert payload["customer"]["email"] == "customer@example.com"
    assert payload["customer"]["default_area"] == "Kilimani"
    assert payload["customer"]["is_active"] is True
    assert payload["customer"]["created_at"]

    assert len(payload["events"]) == 2
    event_types = [e["event_type"] for e in payload["events"]]
    assert event_types == ["message.summarised", "order.created"]
    for event in payload["events"]:
        assert set(event) == {"id", "event_type", "channel", "summary", "occurred_at"}
        assert event["occurred_at"]

    trait_names = {t["name"] for t in payload["traits"]}
    assert "order_count" in trait_names
    assert "lifetime_value_cents" in trait_names
    for trait in payload["traits"]:
        assert set(trait) == {"name", "value", "confidence", "version", "evidence"}
        assert trait["version"] == 1
        assert trait["evidence"], "exported traits must carry their evidence"

    assert len(payload["consents"]) == 2
    assert [c["granted"] for c in payload["consents"]] == [True, False]
    assert {c["source"] for c in payload["consents"]} == {"whatsapp_opt_in", "opt_out"}
    for consent in payload["consents"]:
        assert consent["purpose"] == "marketing"
        assert consent["recorded_at"]

    assert len(payload["orders"]) == 2
    assert {o["total_cents"] for o in payload["orders"]} == {5000, 7000}
    for order in payload["orders"]:
        assert set(order) == {"id", "status", "total_cents", "created_at"}
        assert order["status"] == "paid"


async def test_export_unknown_customer_raises_not_found(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        with pytest.raises(CustomerNotFoundError):
            await CRMService(session).export_customer(TenantId(tenant.id), uuid4())


async def test_export_is_tenant_scoped(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Tenant A cannot export a data subject belonging to tenant B."""
    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="export-other")
        customer_b = await make_customer(session, tenant_b.id, phone="254755555555")
        with pytest.raises(CustomerNotFoundError):
            await CRMService(session).export_customer(TenantId(tenant.id), customer_b.id)


def test_delete_customer_requires_human_approval_parameter() -> None:
    """Plan invariant 5: erasure is human-approved — approver is mandatory."""
    sig = inspect.signature(CRMService.delete_customer)
    param = sig.parameters["approved_by"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


async def test_delete_unknown_customer_raises_not_found(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        with pytest.raises(CustomerNotFoundError):
            await CRMService(session).delete_customer(
                TenantId(tenant.id), uuid4(), approved_by=approver.id,
            )


async def test_delete_customer_is_tenant_scoped(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Erasing with the wrong tenant id fails closed and touches nothing."""
    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="erase-other")
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        customer_b = await make_customer(session, tenant_b.id, phone="254744444444")
        service = CRMService(session)
        await service.record_event(
            TenantId(tenant_b.id), customer_b.id, "message.summarised", dedupe_key="b-evt",
        )

        with pytest.raises(CustomerNotFoundError):
            await service.delete_customer(
                TenantId(tenant.id), customer_b.id, approved_by=approver.id,
            )

        # Tenant B's CRM data is untouched.
        still_there = await service.repo.list_events(TenantId(tenant_b.id), customer_b.id)
        assert len(still_there) == 1


async def test_erasure_cascade_removes_crm_data_scrubs_pii_and_audits(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The full erasure contract (plan invariant 5 + Slice 8a acceptance).

    Removes events/traits/consents for the customer only, anonymises the
    customer row, scrubs the personal identifiers on their orders while keeping
    the order records (financial record), and writes an audit entry naming the
    human approver. A bystander customer in the same tenant must be untouched.
    """
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        approver_id = approver.id
        service = CRMService(session)
        tid = TenantId(tenant.id)

        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
        bystander = await make_customer(session, tenant.id, phone="254788888888")
        bystander_id = bystander.id

        await service.record_event(tid, customer_id, "message.summarised", dedupe_key="e1")
        await service.record_event(tid, customer_id, "order.created", dedupe_key="e2")
        await service.record_consent(
            tid, customer_id, purpose="marketing", granted=True, source="whatsapp_opt_in",
        )
        await service.derive_traits(tid, customer_id)
        order = await make_order(
            session,
            tenant.id,
            customer_id,
            total_cents=1234,
            payment_status="completed",
            status="delivered",
        )
        order_id = order.id
        await service.record_event(tid, bystander_id, "message.summarised", dedupe_key="b1")

        await service.delete_customer(tid, customer_id, approved_by=approver_id)

        tombstone = erasure_tombstone(customer_id)
        assert len(tombstone) <= 20, "tombstone must fit customers.phone VARCHAR(20)"

        for table in ("customer_events", "customer_traits", "consent_records"):
            count = await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE customer_id = :c AND tenant_id = :t"),
                {"c": str(customer_id), "t": str(tenant.id)},
            )
            assert int(count.scalar() or 0) == 0, f"{table} rows must be erased"

        erased_customer = (
            await session.execute(
                text(
                    "SELECT phone, name, email, default_address, default_area, is_active "
                    "FROM customers WHERE id = :c",
                ),
                {"c": str(customer_id)},
            )
        ).one()
        assert erased_customer.phone == tombstone
        assert erased_customer.name is None
        assert erased_customer.email is None
        assert erased_customer.default_address is None
        assert erased_customer.default_area is None
        assert erased_customer.is_active is False

        # Order records are retained as the financial trace, without identifiers.
        kept_order = (
            await session.execute(
                text("SELECT customer_phone, customer_name FROM orders WHERE id = :o"),
                {"o": str(order_id)},
            )
        ).one()
        assert kept_order.customer_phone == tombstone
        assert kept_order.customer_name is None
        order_count = await session.execute(
            text("SELECT count(*) FROM orders WHERE customer_id = :c"),
            {"c": str(customer_id)},
        )
        assert int(order_count.scalar() or 0) == 1, "orders themselves must be retained"

        # The bystander customer in the same tenant is untouched.
        bystander_rows = (
            await session.execute(
                text("SELECT phone, name, is_active FROM customers WHERE id = :c"),
                {"c": str(bystander_id)},
            )
        ).one()
        assert bystander_rows.phone == "254788888888"
        assert bystander_rows.name == "Test Customer"
        assert bystander_rows.is_active is True
        bystander_events = (
            await session.execute(
                text("SELECT count(*) FROM customer_events WHERE customer_id = :c"),
                {"c": str(bystander_id)},
            )
        ).scalar()
        assert int(bystander_events or 0) == 1

        # Fully audited: who approved, what was erased.
        audit = (
            await session.execute(
                text(
                    "SELECT user_id, action, resource, resource_id FROM audit_log "
                    "WHERE action = 'crm.customer_erased'",
                ),
            )
        ).one()
        assert audit.user_id == approver_id
        assert audit.resource == "customer"
        assert str(audit.resource_id) == str(customer_id)


async def test_erasure_clears_all_crm_rows_checked_via_orm(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Cross-check the cascade with ORM selects on a fresh session."""
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        service = CRMService(session)
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
        tid = TenantId(tenant.id)
        await service.record_event(tid, customer_id, "message.summarised", dedupe_key="x1")
        await service.record_consent(
            tid, customer_id, purpose="marketing", granted=True, source="opt_in",
        )
        await service.derive_traits(tid, customer_id)

    async with session_factory() as session, session.begin():
        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver.id,
        )

    async with session_factory() as session:
        events = (
            await session.execute(
                text("SELECT count(*) FROM customer_events WHERE customer_id = :c"),
                {"c": str(customer_id)},
            )
        ).scalar()
        traits = (
            await session.execute(
                text("SELECT count(*) FROM customer_traits WHERE customer_id = :c"),
                {"c": str(customer_id)},
            )
        ).scalar()
        consents = (
            await session.execute(
                text("SELECT count(*) FROM consent_records WHERE customer_id = :c"),
                {"c": str(customer_id)},
            )
        ).scalar()

    assert int(events or 0) == 0
    assert int(traits or 0) == 0
    assert int(consents or 0) == 0


# =============================================================================
# U30-U33: privacy protected-zone fixes (human-approved 2026-10-18)
# =============================================================================


async def test_erasure_scrubs_payment_phone_and_metadata(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U30: erasure must remove the M-Pesa MSISDN from the payment ledger.

    ``payments.phone_number`` and any top-level ``phone`` key in
    ``payments.metadata_json`` are PII; the financial columns (amount, status,
    provider ids, receipt) are a retained financial record and must not move.
    """
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        customer = await make_customer(session, tenant.id, phone=PHONE)
        customer_id = customer.id
        order = await make_order(
            session,
            tenant.id,
            customer_id,
            total_cents=1234,
            payment_status="completed",
            status="delivered",
        )
        order_id = order.id
        approver_id = approver.id
        session.add(
            Payment(
                tenant_id=tenant.id,
                order_id=order_id,
                provider="daraja",
                phone_number=PHONE,
                amount_cents=1234,
                status=PaymentRecordStatus.COMPLETED,
                mpesa_receipt_number="QAZ123XYZ",
                metadata_json={
                    "idempotency_key": "ik-erase-1",
                    "customer_message": "Enter your M-Pesa PIN",
                    "phone": PHONE,
                },
            ),
        )
        await session.flush()

        pre = (
            await session.execute(
                text("SELECT phone_number FROM payments WHERE order_id = :o"),
                {"o": str(order_id)},
            )
        ).one()
        assert pre.phone_number == PHONE, "seed must start with the real MSISDN"

        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver_id,
        )

        row = (
            await session.execute(
                text(
                    "SELECT phone_number, amount_cents, status, mpesa_receipt_number, "
                    "metadata_json FROM payments WHERE order_id = :o",
                ),
                {"o": str(order_id)},
            )
        ).one()

    tombstone = erasure_tombstone(customer_id)
    assert row.phone_number == tombstone, "U30: MSISDN must be replaced by the tombstone"
    metadata = dict(row.metadata_json or {})
    assert PHONE not in str(metadata), "U30: no phone may survive in payments.metadata_json"
    assert "phone" not in metadata, "U30: the top-level metadata phone key must be removed"
    # Financial / provider record is untouched.
    assert row.amount_cents == 1234
    assert str(row.status) == "completed"
    assert row.mpesa_receipt_number == "QAZ123XYZ"
    assert metadata["idempotency_key"] == "ik-erase-1"


async def test_erasure_nulls_order_email_address_and_location_text(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U31: the retained order rows must lose all customer identity/location text.

    ``customer_email`` and ``delivery_address`` were never touched (contradicting
    ADR 0017 §6); ``delivery_instructions``/``delivery_area``/lat-lng/``notes``
    are the same class of free-text identity/location data. Money columns stay.
    """
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        customer = await make_customer(session, tenant.id, phone=PHONE)
        customer_id = customer.id
        approver_id = approver.id
        order = await make_order(
            session,
            tenant.id,
            customer_id,
            total_cents=4321,
            payment_status="completed",
            status="paid",
        )
        order_id = order.id
        order.customer_email = "jane@example.com"
        order.delivery_address = "12 Riverside Drive, Kilimani"
        order.delivery_area = "Kilimani"
        order.delivery_lat = -1.2921
        order.delivery_lng = 36.7842
        order.delivery_instructions = "Blue gate, call Jane on 0722 000 111"
        order.notes = "Prefers Jane at the office after 5pm"
        await session.flush()

        pre = (
            await session.execute(
                text(
                    "SELECT customer_email, delivery_address, delivery_instructions, notes "
                    "FROM orders WHERE id = :o",
                ),
                {"o": str(order_id)},
            )
        ).one()
        assert pre.customer_email == "jane@example.com"
        assert pre.delivery_address == "12 Riverside Drive, Kilimani"
        assert pre.delivery_instructions is not None
        assert pre.notes is not None

        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver_id,
        )

        post = (
            await session.execute(
                text(
                    "SELECT customer_email, delivery_address, delivery_area, delivery_lat, "
                    "delivery_lng, delivery_instructions, notes, customer_phone, "
                    "customer_name, total_cents FROM orders WHERE id = :o",
                ),
                {"o": str(order_id)},
            )
        ).one()

    assert post.customer_email is None, "U31: customer_email must be NULL after erasure"
    assert post.delivery_address is None, "U31: delivery_address must be NULL after erasure"
    assert post.delivery_area is None
    assert post.delivery_lat is None
    assert post.delivery_lng is None
    assert post.delivery_instructions is None
    assert post.notes is None
    assert post.customer_phone == erasure_tombstone(customer_id)
    assert post.customer_name is None
    # The order survives as a financial record.
    assert post.total_cents == 4321


async def test_erasure_audit_row_holds_no_plaintext_phone(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U32: the append-only audit row must not persist the pre-erasure phone.

    ``audit_log`` cannot be deleted or updated (retention 2555 days), so a
    plaintext ``before={"phone": ...}`` defeats the erasure. The row itself
    must still exist — the action stays audited, just PII-free.
    """
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        approver_id = approver.id
        customer = await make_customer(session, tenant.id, phone=PHONE)
        customer_id = customer.id
        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver_id,
        )

        leak_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE action = 'crm.customer_erased' "
                    "AND (coalesce(before_json::text, '') || coalesce(after_json::text, '')) "
                    "LIKE :needle",
                ),
                {"needle": f"%{PHONE}%"},
            )
        ).scalar()
        row_count = (
            await session.execute(
                text("SELECT count(*) FROM audit_log WHERE action = 'crm.customer_erased'"),
            )
        ).scalar()
        row = (
            await session.execute(
                text(
                    "SELECT user_id, before_json FROM audit_log "
                    "WHERE action = 'crm.customer_erased'",
                ),
            )
        ).one()

    assert int(leak_count or 0) == 0, "U32: no audit payload may contain the plaintext phone"
    assert int(row_count or 0) == 1, "U32: the erasure must still be audited"
    assert str(row.user_id) == str(approver_id)
    assert "phone_present" in (row.before_json or {}), "U32: before must carry a boolean flag"
    assert PHONE not in str(row.before_json)


async def test_export_returns_every_event_beyond_the_default_200_cap(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U33: a data-subject export must be complete — no silent 200-event cap."""
    total = 201
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        session.add_all([
            CustomerEvent(
                tenant_id=tenant.id,
                customer_id=customer_id,
                event_type="message.summarised",
                summary=f"event {i}",
                data_json={},
                dedupe_key=f"export-{i}",
                occurred_at=base + timedelta(minutes=i),
            )
            for i in range(total)
        ])
        await session.flush()

        payload = await CRMService(session).export_customer(TenantId(tenant.id), customer_id)

    assert len(payload["events"]) == total, (
        f"U33: export must return all {total} events, got {len(payload['events'])}"
    )


# =============================================================================
# U51-U52: additional protected-zone erasure fixes (2026-10-18)
# =============================================================================


class FakeOutboxProvider(PaymentProvider):
    """Fake provider for outbox erasure test (no network)."""

    def __init__(self) -> None:
        self.valid_callback = True
        self.result: ProviderPaymentResult | None = None
        self.push_error: PaymentProviderError | None = None
        self._seq = 0

    async def initiate_stk_push(self, request: DomainSTKPushRequest) -> DomainSTKPushResponse:
        if self.push_error is not None:
            raise self.push_error
        self._seq += 1
        return DomainSTKPushResponse(
            checkout_request_id=f"ws_CO_{self._seq}",
            merchant_request_id=f"mreq_{self._seq}",
            response_code="0",
            response_description="Success. Request accepted for processing",
            customer_message="Enter PIN",
        )

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        assert self.result is not None
        return self.result

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        return self.valid_callback

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        assert self.result is not None
        return self.result

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        return False


async def test_erasure_scrubs_outbox_payment_phone_payload(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U51: erasure must remove the customer's phone from outbox payment payloads.

    The `payment.initiated` outbox event stores the phone in its JSONB payload.
    `delete_customer` must scrub that key from historical outbox rows after the
    payment lifecycle is complete. The financial columns on `payments` are
    already scrubbed by U30; the outbox is an operational table that also
    carries PII and must be cleaned.
    """

    from packages.core.payments.service import PaymentService

    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        approver_id = approver.id
        customer = await make_customer(session, tenant.id, phone=PHONE)
        customer_id = customer.id
        order = await make_order(
            session,
            tenant.id,
            customer_id,
            total_cents=1234,
            payment_status="pending",
            status="awaiting_payment",
        )
        order_id = order.id

        # Create a real payment via PaymentService.initiate_stk_payment so the
        # outbox gets the real payload with "phone" key.
        provider = FakeOutboxProvider()
        payment_service = PaymentService(session, provider)
        payment = await payment_service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order_id,
            phone_number=PHONE,
            callback_url="http://test/callback",
            idempotency_key="ik-test-outbox-1",
        )
        await session.flush()

        # Verify the outbox has the phone in its payload
        result = await session.execute(
            text(
                "SELECT payload FROM outbox "
                "WHERE event_type = 'payment.initiated' AND tenant_id = :t",
            ),
            {"t": str(tenant.id)},
        )
        outbox_row = result.scalar_one()
        assert "phone" in outbox_row, "seed must have phone in outbox payload"
        assert outbox_row["phone"] == PHONE

        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver_id,
        )

    # Verify the outbox payload no longer has the phone key
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT payload FROM outbox "
                "WHERE event_type = 'payment.initiated' AND tenant_id = :t",
            ),
            {"t": str(tenant.id)},
        )
        outbox_row = result.scalar_one()
        assert "phone" not in outbox_row, "U51: phone key must be removed from outbox payload"
        # Other keys should remain
        assert outbox_row["payment_id"] == str(payment.id)
        assert outbox_row["checkout_request_id"] == "ws_CO_1"
        assert outbox_row["amount_cents"] == 1234
        assert outbox_row["provider"] == "daraja"


async def test_erasure_hard_deletes_whatsapp_contacts_and_messages(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U52: erasure must HARD DELETE WhatsApp contacts and messages.

    DSR erasure means "gone", not "redacted but linkable". The tables are
    channel-infrastructure; no business ledger depends on them (orders/payments/
    CRM already scrubbed). Hard delete is cleaner and matches the regulation's
    intent. Must be tenant-scoped — a bystander customer's WhatsApp data in the
    same tenant must be untouched.
    """
    from packages.core.models.whatsapp import (
        WhatsAppContact,
        WhatsAppConversation,
        WhatsAppConversationState,
        WhatsAppMessage,
    )

    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        approver_id = approver.id

        # Target customer to be erased
        customer = await make_customer(session, tenant.id, phone=PHONE)
        customer_id = customer.id

        # Bystander customer in the same tenant (must survive)
        await make_customer(session, tenant.id, phone="254788888888")

        # Create WhatsApp contact for target customer
        contact = WhatsAppContact(
            tenant_id=tenant.id,
            phone=PHONE,
            wa_id=PHONE,
            name="Test Customer",
            metadata_json={},
        )
        session.add(contact)
        await session.flush()

        # Create a conversation for the target customer
        conversation = WhatsAppConversation(
            tenant_id=tenant.id,
            contact_id=contact.id,
            state=WhatsAppConversationState.OPEN,
        )
        session.add(conversation)
        await session.flush()

        # Create 3 messages for the target customer's contact
        for i in range(3):
            msg = WhatsAppMessage(
                tenant_id=tenant.id,
                contact_id=contact.id,
                conversation_id=conversation.id,
                direction="inbound",
                message_type="text",
                status="delivered",
                text_body=f"Identifiable message {i} from {PHONE}",
                metadata_json={},
            )
            session.add(msg)
        await session.flush()

        # Create WhatsApp contact and message for bystander (must survive)
        bystander_contact = WhatsAppContact(
            tenant_id=tenant.id,
            phone="254788888888",
            wa_id="254788888888",
            name="Bystander",
            metadata_json={},
        )
        session.add(bystander_contact)
        await session.flush()

        # Create a conversation for the bystander
        bystander_conversation = WhatsAppConversation(
            tenant_id=tenant.id,
            contact_id=bystander_contact.id,
            state=WhatsAppConversationState.OPEN,
        )
        session.add(bystander_conversation)
        await session.flush()

        bystander_msg = WhatsAppMessage(
            tenant_id=tenant.id,
            contact_id=bystander_contact.id,
            conversation_id=bystander_conversation.id,
            direction="inbound",
            message_type="text",
            status="delivered",
            text_body="Bystander message",
            metadata_json={},
        )
        session.add(bystander_msg)
        await session.flush()

        contact_id = contact.id
        bystander_contact_id = bystander_contact.id

        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver_id,
        )

    # Verify hard delete: target's contact and messages are GONE
    async with session_factory() as session:
        target_contact_count = (
            await session.execute(
                text("SELECT count(*) FROM whatsapp_contacts WHERE id = :c"),
                {"c": str(contact_id)},
            )
        ).scalar()
        target_msg_count = (
            await session.execute(
                text("SELECT count(*) FROM whatsapp_messages WHERE contact_id = :c"),
                {"c": str(contact_id)},
            )
        ).scalar()
        assert int(target_contact_count or 0) == 0, "U52: target WhatsApp contact must be deleted"
        assert int(target_msg_count or 0) == 0, "U52: target WhatsApp messages must be deleted"

        # Verify bystander's data is untouched
        bystander_contact = (
            await session.execute(
                text("SELECT phone, name FROM whatsapp_contacts WHERE id = :c"),
                {"c": str(bystander_contact_id)},
            )
        ).one()
        assert bystander_contact.phone == "254788888888"
        assert bystander_contact.name == "Bystander"

        bystander_msg_count = (
            await session.execute(
                text("SELECT count(*) FROM whatsapp_messages WHERE contact_id = :c"),
                {"c": str(bystander_contact_id)},
            )
        ).scalar()
        assert int(bystander_msg_count or 0) == 1, "U52: bystander messages must survive"
