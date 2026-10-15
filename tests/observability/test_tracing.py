"""
Tests for observability (OpenTelemetry tracing).
"""

import pytest
from socialerp.observability import (
    METRICS_REGISTRY,
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
    http_requests_total,
    init_metrics,
    init_tracing,
    messages_received_total,
    messages_sent_total,
    orders_total,
    payment_callback_total,
    payment_stk_push_total,
    trace_async_operation,
    trace_operation,
)


class TestObservability:
    """Tests for observability components."""

    def test_configure_logging(self):
        """Configure logging runs without error."""
        configure_logging()
        logger = get_logger("test")
        assert logger is not None

    def test_get_logger(self):
        """Get logger returns structlog logger."""
        logger = get_logger("test.module")
        assert logger is not None
        # Should be able to log
        logger.info("test message", key="value")

    def test_init_tracing_returns_provider(self):
        """Init tracing returns TracerProvider."""
        provider = init_tracing()
        assert provider is not None
        # Second call returns same provider
        provider2 = init_tracing()
        assert provider is provider2

    def test_init_metrics_returns_provider(self):
        """Init metrics returns MeterProvider."""
        provider = init_metrics()
        assert provider is not None
        provider2 = init_metrics()
        assert provider is provider2

    def test_get_tracer(self):
        """Get tracer returns tracer."""
        tracer = get_tracer("test")
        assert tracer is not None

    def test_get_meter(self):
        """Get meter returns meter."""
        meter = get_meter("test")
        assert meter is not None

    def test_trace_operation_context_manager(self):
        """Trace operation context manager works."""
        with trace_operation("test.operation", {"attr": "value"}) as span:
            assert span is not None
            span.set_attribute("test", "value")

    @pytest.mark.asyncio
    async def test_trace_async_operation(self):
        """Trace async operation context manager works."""
        async with trace_async_operation("test.async", {"attr": "value"}) as span:
            assert span is not None
            span.set_attribute("test", "value")

    def test_metrics_registry_exists(self):
        """Metrics registry is initialized."""
        assert METRICS_REGISTRY is not None

    def test_get_metrics_output(self):
        """Get metrics returns Prometheus format."""
        output = get_metrics()
        assert isinstance(output, bytes)
        assert len(output) > 0
        # Should contain metric names
        output_str = output.decode()
        assert "http_requests_total" in output_str

    def test_metric_instances_exist(self):
        """All metric instances are created."""
        assert http_requests_total is not None
        assert http_request_duration is not None
        assert db_query_duration is not None
        assert messages_received_total is not None
        assert messages_sent_total is not None
        assert agent_runs_total is not None
        assert agent_duration is not None
        assert payment_stk_push_total is not None
        assert payment_callback_total is not None
        assert orders_total is not None
        assert active_conversations is not None

    def test_counter_increment(self):
        """Counter can be incremented."""

        def sample_value() -> float:
            # Public API: collect() returns Metric with samples (one per label set)
            return sum(float(s.value) for s in http_requests_total.collect()[0].samples)

        initial = sample_value()
        http_requests_total.labels(method="GET", endpoint="/test", status="200").inc()
        assert sample_value() > initial

    def test_histogram_observe(self):
        """Histogram can observe values."""
        http_request_duration.labels(method="GET", endpoint="/test").observe(0.1)
        # Should not raise

    def test_gauge_set(self):
        """Gauge can be set."""
        active_conversations.labels(tenant_id="test").set(5)
        active_conversations.labels(tenant_id="test").dec()
        # Should not raise


class TestMetricsOutput:
    """Tests for metrics output format."""

    def test_metrics_contains_expected_metrics(self):
        """Metrics output contains all expected metric names."""
        output = get_metrics().decode()
        expected = [
            "http_requests_total",
            "http_request_duration_seconds",
            "db_query_duration_seconds",
            "messages_received_total",
            "messages_sent_total",
            "agent_runs_total",
            "agent_duration_seconds",
            "payment_stk_push_total",
            "payment_callback_total",
            "orders_total",
            "active_conversations",
        ]
        for metric in expected:
            assert metric in output, f"Missing metric: {metric}"

    def test_metrics_format_prometheus(self):
        """Metrics output is valid Prometheus format."""
        output = get_metrics().decode()
        lines = output.strip().split("\n")
        # Should have HELP and TYPE lines
        help_lines = [line for line in lines if line.startswith("# HELP")]
        type_lines = [line for line in lines if line.startswith("# TYPE")]
        assert len(help_lines) > 0
        assert len(type_lines) > 0
