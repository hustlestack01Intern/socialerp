"""
SocialERP Core Package - Public API
"""

from socialerp.audit import (
    AuditLog,
    AuditLogEntry,
    log_agent_action,
    log_event,
    log_payment_event,
    log_security_event,
    log_webhook_event,
)
from socialerp.database import Base, close_db, create_engine, get_session, init_db
from socialerp.idempotency import (
    IdempotencyKey,
    IdempotencyRecord,
    check_idempotency,
    cleanup_expired_idempotency_keys,
    complete_idempotency,
    fail_idempotency,
    idempotent,
)
from socialerp.observability import (
    active_conversations,
    agent_duration,
    agent_runs_total,
    configure_logging,
    db_query_duration,
    get_logger,
    get_meter,
    get_metrics,
    get_tracer,
    http_request_duration,
    # Metrics
    http_requests_total,
    init_instrumentation,
    init_metrics,
    init_tracing,
    messages_received_total,
    messages_sent_total,
    observability_middleware,
    orders_total,
    payment_callback_total,
    payment_stk_push_total,
    trace_async_operation,
    trace_operation,
)
from socialerp.outbox import (
    Outbox,
    OutboxEvent,
    OutboxEventStatus,
    OutboxRelay,
    add_outbox_event,
    publish_message_send,
    publish_order_created,
    publish_payment_confirmed,
    publish_stock_changed,
)
from socialerp.settings import Settings, get_settings, settings
from socialerp.tenancy import (
    Tenant,
    TenantAwareSession,
    TenantMixin,
    get_current_tenant_id,
    reset_current_tenant_id,
    set_current_tenant_id,
)

__all__ = [
    # Audit
    "AuditLog",
    "AuditLogEntry",
    # Database
    "Base",
    "IdempotencyKey",
    # Idempotency
    "IdempotencyRecord",
    # Outbox
    "Outbox",
    "OutboxEvent",
    "OutboxEventStatus",
    "OutboxRelay",
    # Settings
    "Settings",
    # Tenancy
    "Tenant",
    "TenantAwareSession",
    "TenantMixin",
    "active_conversations",
    "add_outbox_event",
    "agent_duration",
    "agent_runs_total",
    "check_idempotency",
    "cleanup_expired_idempotency_keys",
    "close_db",
    "complete_idempotency",
    # Observability
    "configure_logging",
    "create_engine",
    "db_query_duration",
    "fail_idempotency",
    "get_current_tenant_id",
    "get_logger",
    "get_meter",
    "get_metrics",
    "get_session",
    "get_settings",
    "get_tracer",
    "http_request_duration",
    # Metric instances
    "http_requests_total",
    "idempotent",
    "init_db",
    "init_instrumentation",
    "init_metrics",
    "init_tracing",
    "log_agent_action",
    "log_event",
    "log_payment_event",
    "log_security_event",
    "log_webhook_event",
    "messages_received_total",
    "messages_sent_total",
    "observability_middleware",
    "orders_total",
    "payment_callback_total",
    "payment_stk_push_total",
    "publish_message_send",
    "publish_order_created",
    "publish_payment_confirmed",
    "publish_stock_changed",
    "reset_current_tenant_id",
    "set_current_tenant_id",
    "settings",
    "trace_async_operation",
    "trace_operation",
]
