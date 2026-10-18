"""
SocialERP Worker - Background job processor (arq).
"""

import asyncio
import contextlib
from typing import Any, ClassVar

from arq.connections import RedisSettings
from socialerp.database import close_db, init_db
from socialerp.idempotency import cleanup_expired_idempotency_keys
from socialerp.observability import configure_logging, get_logger, init_metrics, init_tracing
from socialerp.outbox import OutboxRelay
from socialerp.settings import get_settings

settings = get_settings()
logger = get_logger(__name__)


# =============================================================================
# JOB DEFINITIONS
# =============================================================================


async def cleanup_idempotency_keys(ctx: dict[str, Any]) -> None:
    """Periodic job to clean up expired idempotency keys."""
    try:
        deleted = await cleanup_expired_idempotency_keys()
        logger.info("Cleaned up expired idempotency keys", count=deleted)
    except Exception as e:
        logger.exception("Failed to cleanup idempotency keys", error=str(e))


async def process_outbox(ctx: dict[str, Any]) -> None:
    """Process outbox events."""
    # This is handled by the OutboxRelay running continuously
    pass


async def reconcile_stale_payments(ctx: dict[str, Any]) -> None:
    """Status-query fallback: re-query providers for stale payments.

    Runs across all tenants and applies provider outcomes for payments that
    never received a (terminal) callback. Successes without a verifiable amount
    are flagged for human review rather than auto-credited.
    """
    from socialerp.database import get_session_factory

    from packages.core.payments import reconcile_all
    from packages.integrations.daraja import build_direct_daraja_provider

    try:
        provider = build_direct_daraja_provider()
        reconciled = await reconcile_all(get_session_factory(), provider)
        logger.info("Payment reconciliation complete", reconciled=reconciled)
    except Exception as e:
        logger.exception("Failed to reconcile payments", error=str(e))


async def expire_reservations(ctx: dict[str, Any]) -> None:
    """Release unpaid stock reservations past their TTL.

    Reuses the orders service. In production the worker connects with a role
    that can see all tenants; the job sweeps every tenant at once.
    """
    from socialerp.database import get_session_factory

    from packages.core.orders import OrderService

    try:
        async with get_session_factory()() as session:
            service = OrderService(session)
            released = await service.expire_stale_reservations()
            await session.commit()
        logger.info("Expired stale reservations", released=released)
    except Exception as e:
        logger.exception("Failed to expire reservations", error=str(e))


# =============================================================================
# OUTBOX HANDLERS
# =============================================================================


async def _noop_handler(payload: dict[str, Any], tenant_id: Any) -> None:
    """Acknowledge an event that has no side effect yet.

    Registering a handler keeps the relay from marking the event FAILED; the
    event stays recorded in the outbox for future consumers/observability.
    """
    logger.debug("outbox event acknowledged (no handler)", event=payload)


async def handle_payment_collected_after_terminal(payload: dict[str, Any], tenant_id: Any) -> None:
    """Alert: money was collected for an order that could not be marked paid.

    This must NOT be silently swallowed (that is the whole point of the event):
    a human must decide refund vs manual honour. We log AND persist a durable
    ``owner_alerts`` row (Phase 7 remainder) — a log line can be lost to
    rotation; a queryable, acknowledgeable alert cannot. No automatic stock or
    order side effect here; refunds stay human-approved.
    """
    from uuid import UUID

    from socialerp.database import get_session_factory
    from sqlalchemy import text

    from packages.core.alerts import AlertService
    from packages.core.domain import TenantId
    from packages.core.models.alert import AlertSeverity

    logger.warning(
        "payment collected on a non-collectable order -- needs refund/review",
        order_id=payload.get("order_id"),
        order_status=payload.get("order_status"),
        mpesa_receipt_number=payload.get("mpesa_receipt_number"),
        amount_cents=payload.get("amount_cents"),
        tenant_id=str(tenant_id),
    )

    order_id = UUID(payload["order_id"])
    amount_cents = payload.get("amount_cents")
    receipt = payload.get("mpesa_receipt_number") or "unknown receipt"
    order_status = payload.get("order_status") or "unknown status"
    # Integer math, never float, even for a label (money is displayed, not mutated).
    if isinstance(amount_cents, int):
        shillings, cents = divmod(amount_cents, 100)
        amount_str = f"KES {shillings:,}.{cents:02d}"
    else:
        amount_str = "an unknown amount"

    async with get_session_factory()() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        alerts = AlertService(session)
        alert = await alerts.raise_alert(
            TenantId(UUID(str(tenant_id))),
            kind="payment.collected_after_terminal",
            severity=AlertSeverity.CRITICAL,
            title="Money collected on a non-collectable order",
            body=(
                f"{amount_str} was paid (receipt {receipt}) but the order is "
                f"{order_status}. Decide: refund or honour the order."
            ),
            resource_type="order",
            resource_id=order_id,
            metadata={
                "payment_id": payload.get("payment_id"),
                "order_status": order_status,
                "checkout_request_id": payload.get("checkout_request_id"),
                "mpesa_receipt_number": receipt,
                "amount_cents": amount_cents,
            },
            # Idempotent under outbox replay: one alert per order, ever.
            dedupe_key=f"payment_collected_after_terminal:{order_id}",
        )
        await session.commit()
    logger.info(
        "Raised owner alert for payment collected after terminal",
        alert_id=str(alert.id),
        order_id=str(order_id),
    )


async def handle_payment_completed(payload: dict[str, Any], tenant_id: Any) -> None:
    """Record the sale (SELL movement) when a payment completes.

    The payment service (protected zone) moves the order to ``paid`` and emits
    ``payment.completed``; this handler performs the stock side effect via the
    orders service, idempotently (a replayed event cannot double-count a sale).
    """
    from uuid import UUID

    from socialerp.database import get_session_factory
    from sqlalchemy import text

    from packages.core.orders import OrderService

    order_id = UUID(payload["order_id"])
    async with get_session_factory()() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        service = OrderService(session)
        await service.record_sale_for_order(tenant_id=UUID(str(tenant_id)), order_id=order_id)
        await session.commit()
    logger.info("Recorded sale for paid order", order_id=str(order_id))


def build_conversation_agent_service(session: Any, tenant_id: Any) -> Any:
    """Construct the conversation→agent service with the real Meta sender.

    Isolated as a factory so tests can substitute a deterministic extractor and
    a fake sender without touching the network (ADR 0014).
    """
    from packages.conversation.agent_service import ConversationAgentService
    from packages.integrations.meta.sender import WhatsAppSenderAdapter

    return ConversationAgentService(session, tenant_id, sender=WhatsAppSenderAdapter(session))


def build_rider_flow_service(session: Any, tenant_id: Any) -> Any:
    """Construct the rider quick-reply flow with the real Meta sender.

    Separate factory so tests inject a fake sender (ADR 0014).
    """
    from packages.conversation.rider_flow import RiderFlowService
    from packages.integrations.meta.sender import WhatsAppSenderAdapter

    return RiderFlowService(session, tenant_id, sender=WhatsAppSenderAdapter(session))


async def handle_inbound_message(payload: dict[str, Any], tenant_id: Any) -> None:
    """Run the agent for one inbound WhatsApp message.

    Invoked by the outbox relay. The outbox event is idempotent on
    ``inbound_message:{provider_message_id}``; this handler opens its own
    tenant-scoped session and commits the outcome.

    A message from a *known rider* is handled by the deterministic delivery
    flow first (Phase 7b); everyone else goes to the customer agent.
    """
    from uuid import UUID

    from socialerp.database import get_session_factory
    from sqlalchemy import text

    text_body = payload.get("text_body")
    if not text_body:
        # Non-text inbound (media/status) is not handled by the text agent yet.
        logger.debug("inbound message has no text body; skipping", payload=payload)
        return

    tenant_uuid = UUID(str(tenant_id))
    async with get_session_factory()() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )

        rider_flow = build_rider_flow_service(session, tenant_uuid)
        try:
            rider_result = await rider_flow.try_handle(
                conversation_id=UUID(payload["conversation_id"]),
                contact_id=UUID(payload["contact_id"]),
                rider_phone=payload["customer_phone"],
                text=text_body,
                provider_message_id=payload.get("provider_message_id"),
            )
            if rider_result.handled:
                await session.commit()
                # POD collection is triggered only AFTER the arrival is
                # committed, in its own transaction: the arrival transition is
                # the idempotency guard, so a redelivered event cannot fire a
                # second STK prompt (see packages.conversation.rider_flow).
                if rider_result.collection_due and rider_result.delivery_id:
                    from packages.conversation.rider_flow import trigger_pod_collection

                    await trigger_pod_collection(
                        tenant_id=tenant_uuid,
                        delivery_id=UUID(rider_result.delivery_id),
                    )
                logger.info(
                    "Handled rider quick reply",
                    conversation_id=payload["conversation_id"],
                    rider_action=rider_result.action,
                    delivery_id=rider_result.delivery_id,
                    sent=rider_result.sent,
                )
                return
        finally:
            await rider_flow.aclose()

        service = build_conversation_agent_service(session, tenant_uuid)
        try:
            result = await service.respond_to_message(
                conversation_id=UUID(payload["conversation_id"]),
                contact_id=UUID(payload["contact_id"]),
                customer_phone=payload["customer_phone"],
                text=text_body,
                provider_message_id=payload.get("provider_message_id"),
            )
            await session.commit()
        finally:
            await service.aclose()

    logger.info(
        "Handled inbound message",
        conversation_id=payload["conversation_id"],
        intent=result.intent,
        action=result.action,
        sent=result.sent,
        order_id=result.order_id or None,
    )


# =============================================================================
# ARQ SETTINGS
# =============================================================================


class WorkerSettings:
    """arq worker settings."""

    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=0,
    )
    functions: ClassVar[list[Any]] = [
        cleanup_idempotency_keys,
        process_outbox,
        reconcile_stale_payments,
        expire_reservations,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        # Cleanup every hour
        {"func": "cleanup_idempotency_keys", "cron": "0 * * * *"},
        # Status-query fallback for missing/late callbacks, every 5 minutes.
        {"func": "reconcile_stale_payments", "cron": "*/5 * * * *"},
        # Release unpaid reservations past their TTL, every minute.
        {"func": "expire_reservations", "cron": "* * * * *"},
    ]
    on_startup = None
    on_shutdown = None
    max_jobs = 10
    job_timeout = 300
    keep_result = 86400  # 24 hours


# =============================================================================
# OUTBOX RELAY
# =============================================================================


async def run_outbox_relay() -> None:
    """Run the outbox relay continuously."""
    relay = OutboxRelay(batch_size=100, poll_interval=1.0)

    # Register handlers.
    relay.register_handler("payment.completed", handle_payment_completed)
    relay.register_handler(
        "payment.collected_after_terminal", handle_payment_collected_after_terminal,
    )
    relay.register_handler("inbound_message", handle_inbound_message)
    # Acknowledged events with no side effect yet (keeps them from being marked
    # FAILED; these become real handlers in later phases).
    for event_type in (
        "order.created",
        "order.cancelled",
        "payment.initiated",
        "payment.failed",
        "payment.refunded",
    ):
        relay.register_handler(event_type, _noop_handler)

    logger.info("Starting outbox relay")
    await relay.start()


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    """Main worker entry point."""
    configure_logging()
    init_tracing()
    init_metrics()
    await init_db()

    logger.info("Worker starting", environment=settings.ENVIRONMENT)

    # Start outbox relay in background
    relay_task = asyncio.create_task(run_outbox_relay())

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay_task
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
