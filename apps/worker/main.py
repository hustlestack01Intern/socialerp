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
    ]
    cron_jobs: ClassVar[list[Any]] = [
        # Cleanup every hour
        {"func": "cleanup_idempotency_keys", "cron": "0 * * * *"},
        # Status-query fallback for missing/late callbacks, every 5 minutes.
        {"func": "reconcile_stale_payments", "cron": "*/5 * * * *"},
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

    # Register handlers (to be implemented as phases progress)
    # relay.register_handler("order.created", handle_order_created)
    # relay.register_handler("payment.confirmed", handle_payment_confirmed)
    # relay.register_handler("message.send", handle_message_send)
    # relay.register_handler("stock.changed", handle_stock_changed)

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
