"""
Observability: OpenTelemetry tracing, structured logging, and Prometheus metrics.

Initialized once at application startup. All services use shared configuration.
"""

import logging
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import Any, cast

import structlog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from pythonjsonlogger.json import JsonFormatter
from socialerp.settings import get_settings
from starlette.requests import Request
from starlette.responses import Response

settings = get_settings()

# =============================================================================
# METRICS (Prometheus)
# =============================================================================

# Registry for custom metrics
METRICS_REGISTRY = CollectorRegistry()

# HTTP Metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=METRICS_REGISTRY,
)

http_request_duration = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    registry=METRICS_REGISTRY,
)

# Database Metrics
db_query_duration = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation", "table"],
    registry=METRICS_REGISTRY,
)

db_connections_active = Gauge(
    "db_connections_active",
    "Active database connections",
    registry=METRICS_REGISTRY,
)

# Message/Channel Metrics
messages_received_total = Counter(
    "messages_received_total",
    "Total messages received from channels",
    ["channel", "type"],
    registry=METRICS_REGISTRY,
)

messages_sent_total = Counter(
    "messages_sent_total",
    "Total messages sent to channels",
    ["channel", "status"],
    registry=METRICS_REGISTRY,
)

# Agent Metrics
agent_runs_total = Counter(
    "agent_runs_total",
    "Total agent runs",
    ["tenant_id", "level", "outcome"],
    registry=METRICS_REGISTRY,
)

agent_duration = Histogram(
    "agent_duration_seconds",
    "Agent run duration in seconds",
    ["tenant_id", "level"],
    registry=METRICS_REGISTRY,
)

agent_llm_tokens = Histogram(
    "agent_llm_tokens_total",
    "LLM tokens used by agent",
    ["tenant_id", "model", "type"],
    registry=METRICS_REGISTRY,
)

# Payment Metrics
payment_stk_push_total = Counter(
    "payment_stk_push_total",
    "Total STK Push requests",
    ["tenant_id", "provider", "status"],
    registry=METRICS_REGISTRY,
)

payment_callback_total = Counter(
    "payment_callback_total",
    "Total payment callbacks received",
    ["tenant_id", "provider", "result"],
    registry=METRICS_REGISTRY,
)

payment_reconciliation_mismatches = Counter(
    "payment_reconciliation_mismatches_total",
    "Payment reconciliation mismatches",
    ["tenant_id", "provider"],
    registry=METRICS_REGISTRY,
)

# Queue Metrics
queue_depth = Gauge(
    "queue_depth",
    "Number of pending jobs in queue",
    ["queue_name"],
    registry=METRICS_REGISTRY,
)

job_duration = Histogram(
    "job_duration_seconds",
    "Job processing duration in seconds",
    ["job_name", "status"],
    registry=METRICS_REGISTRY,
)

# Business Metrics
orders_total = Counter(
    "orders_total",
    "Total orders created",
    ["tenant_id", "status"],
    registry=METRICS_REGISTRY,
)

orders_value = Histogram(
    "orders_value_ksh",
    "Order value in KSH",
    ["tenant_id"],
    registry=METRICS_REGISTRY,
)

active_conversations = Gauge(
    "active_conversations",
    "Number of active conversations",
    ["tenant_id"],
    registry=METRICS_REGISTRY,
)


# =============================================================================
# STRUCTURED LOGGING
# =============================================================================


def configure_logging() -> None:
    """Configure structured JSON logging with structlog."""
    # Standard library logging to JSON
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        ),
    )

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    # Structlog configuration
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.LOG_LEVEL)),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger instance."""
    return cast(structlog.BoundLogger, structlog.get_logger(name))


# =============================================================================
# OPENTELEMETRY TRACING
# =============================================================================

_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def init_tracing() -> TracerProvider:
    """Initialize OpenTelemetry tracing."""
    global _tracer_provider

    if _tracer_provider is not None:
        return _tracer_provider

    resource = Resource.create({SERVICE_NAME: settings.OTEL_SERVICE_NAME})

    _tracer_provider = TracerProvider(resource=resource)

    # OTLP Exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        insecure=True,
    )
    _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    trace.set_tracer_provider(_tracer_provider)

    return _tracer_provider


def init_metrics() -> MeterProvider:
    """Initialize OpenTelemetry metrics."""
    global _meter_provider

    if _meter_provider is not None:
        return _meter_provider

    resource = Resource.create({SERVICE_NAME: settings.OTEL_SERVICE_NAME})

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
            insecure=True,
        ),
        export_interval_millis=30000,
    )

    _meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(_meter_provider)

    return _meter_provider


def init_instrumentation(app: Any = None) -> None:
    """Auto-instrument common libraries."""
    SQLAlchemyInstrumentor().instrument(
        engine=None,  # Will be instrumented when engine is created
    )
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()

    if app:
        # U35: the ASGI instrumentation records the full request URL (query
        # string included) on its server span; the hook drops the query so a
        # signed `?t=` tracking token can never land in a span attribute.
        FastAPIInstrumentor.instrument_app(app, server_request_hook=redact_server_span_urls)


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer instance."""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """Get a meter instance."""
    return metrics.get_meter(name)


# =============================================================================
# CONTEXT MANAGERS FOR TRACING
# =============================================================================


@contextmanager
def trace_operation(
    name: str, attributes: dict[str, Any] | None = None,
) -> Generator[trace.Span, None, None]:
    """Context manager for tracing a synchronous operation."""
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise


@asynccontextmanager
async def trace_async_operation(
    name: str, attributes: dict[str, Any] | None = None,
) -> AsyncGenerator[trace.Span, None]:
    """Context manager for tracing an async operation."""
    tracer = get_tracer(__name__)
    span = tracer.start_span(name)
    if attributes:
        for k, v in attributes.items():
            span.set_attribute(k, v)
    try:
        # use_span() works in both sync and async contexts; we end the span
        # ourselves (end_on_exit=False) so the async wrapper controls lifetime.
        with trace.use_span(span, end_on_exit=False):
            yield span
    except Exception as e:
        span.record_exception(e)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        raise
    finally:
        span.end()


# =============================================================================
# FASTAPI MIDDLEWARE
# =============================================================================


def redact_url_query(url: str) -> str:
    """Drop the query string from a URL (U35).

    Public tracking URLs carry a signed ``?t=`` capability token (U29); any
    span attribute or log line holding the full URL turns that bearer secret
    into a replayable credential for anyone with trace/log access. The query
    string is removed entirely rather than filtered key-by-key, so a token
    added under a new parameter name can never leak through here.
    """
    return url.split("?", 1)[0]


def http_span_attributes(request: Request) -> dict[str, Any]:
    """Span attributes for one inbound HTTP request (query-redacted, U35).

    This is the single builder used by :func:`observability_middleware`, so
    tests can assert the exact attribute set without a live server.
    """
    return {
        "http.method": request.method,
        "http.url": redact_url_query(str(request.url)),
        "http.scheme": request.url.scheme,
        "http.host": request.url.netloc,
    }


def redact_server_span_urls(span: trace.Span, scope: dict[str, Any]) -> None:
    """Strip query strings from URL attributes on an instrumented span (U35).

    Passed to ``FastAPIInstrumentor.instrument_app(server_request_hook=...)``:
    the OpenTelemetry ASGI instrumentation puts the full request URL
    (including the query string) on its server span, which would leak the
    ``?t=`` tracking token. Any query-carrying URL attribute is rewritten to
    its query-free form; ``url.query`` is blanked.
    """
    # ``.attributes`` is on ReadableSpan, not the abstract Span API; the hook
    # always receives a real SDK span, but getattr keeps mypy --strict happy.
    attributes: Mapping[str, Any] = getattr(span, "attributes", None) or {}
    for name in ("http.url", "url.full", "http.target"):
        value = attributes.get(name)
        if isinstance(value, str) and "?" in value:
            span.set_attribute(name, redact_url_query(value))
    if attributes.get("url.query"):
        span.set_attribute("url.query", "")


async def observability_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """FastAPI middleware for request tracing and metrics."""
    import time

    from opentelemetry.trace import SpanKind

    start_time = time.time()
    tracer = get_tracer(__name__)

    with tracer.start_as_current_span(
        f"{request.method} {request.url.path}",
        kind=SpanKind.SERVER,
        attributes=http_span_attributes(request),
    ) as span:
        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Record metrics
            http_requests_total.labels(
                method=request.method,
                endpoint=request.url.path,
                status=response.status_code,
            ).inc()
            http_request_duration.labels(
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration)

            span.set_attribute("http.status_code", response.status_code)
            return response
        except Exception as e:
            duration = time.time() - start_time
            http_requests_total.labels(
                method=request.method,
                endpoint=request.url.path,
                status=500,
            ).inc()
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise


# =============================================================================
# METRICS ENDPOINT
# =============================================================================


def get_metrics() -> bytes:
    """Get Prometheus metrics output."""
    return generate_latest(METRICS_REGISTRY)
