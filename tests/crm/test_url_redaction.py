"""U35: query-string redaction for telemetry (token-in-logs).

The public tracking endpoints carry a signed capability token in ``?t=``
(U29). Anything that puts the full request URL on a span or in a log turns
that bearer secret into a replayable credential for anyone with log/trace
access, so ``http.url``-style attributes must drop the query string entirely
(Chief's decision: drop it for ALL http.url attributes, not filter per key).

Covers the pure helper, the attributes our own
``observability_middleware`` builds, and the hook that scrubs the
OpenTelemetry FastAPI/ASGI instrumentation's server span.
"""

from __future__ import annotations

from typing import Any

from socialerp.observability import (
    http_span_attributes,
    redact_server_span_urls,
    redact_url_query,
)
from starlette.requests import Request

TOKEN = "t=eyJhbGciOiJIUzI1NiJ9.super-secret-capability-token"
TRACKING_URL = "http://testserver/tracking/8f4c2d1e-1111-2222-3333-444455556666?" + TOKEN


def _scope(path: str, query: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


class _FakeRecordingSpan:
    """Minimal stand-in for an OpenTelemetry recording span."""

    def __init__(self, attributes: dict[str, Any]) -> None:
        self._attributes = dict(attributes)
        self.set_calls: list[tuple[str, Any]] = []

    @property
    def attributes(self) -> dict[str, Any]:
        return dict(self._attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        self._attributes[key] = value
        self.set_calls.append((key, value))


def test_redact_url_query_drops_query_string() -> None:
    redacted = redact_url_query(TRACKING_URL)
    assert redacted == TRACKING_URL.split("?", 1)[0]
    assert "?" not in redacted
    assert "t=" not in redacted
    assert "super-secret-capability-token" not in redacted


def test_redact_url_query_is_a_noop_without_query() -> None:
    assert redact_url_query("http://testserver/tracking/abc") == "http://testserver/tracking/abc"


def test_http_span_attributes_from_request_contain_no_query_token() -> None:
    """The attributes our middleware puts on its SERVER span are query-free."""
    request = Request(_scope("/tracking/8f4c2d1e-1111-2222-3333-444455556666", TOKEN))

    attributes = http_span_attributes(request)

    assert attributes["http.method"] == "GET"
    assert attributes["http.url"] == (
        "http://testserver/tracking/8f4c2d1e-1111-2222-3333-444455556666"
    )
    assert TOKEN not in str(attributes)
    assert "t=" not in attributes["http.url"]
    assert attributes["http.scheme"] == "http"
    assert attributes["http.host"] == "testserver"


def test_redact_server_span_urls_strips_instrumented_span_attributes() -> None:
    """The FastAPI/ASGI instrumentation hook drops query-carrying attributes."""
    span = _FakeRecordingSpan({
        "http.url": TRACKING_URL,
        "url.full": TRACKING_URL,
        "http.target": "/tracking/8f4c2d1e-1111-2222-3333-444455556666?" + TOKEN,
        "url.query": TOKEN,
        "http.method": "GET",
    })

    redact_server_span_urls(span, _scope("/tracking/x", TOKEN))

    dumped = str(span.attributes)
    assert "t=" not in dumped, f"query token leaked into span attributes: {dumped}"
    assert "super-secret-capability-token" not in dumped
    assert (
        span.attributes["http.url"]
        == "http://testserver/tracking/8f4c2d1e-1111-2222-3333-444455556666"
    )
    assert span.attributes["http.target"] == "/tracking/8f4c2d1e-1111-2222-3333-444455556666"
    assert span.attributes["url.query"] == ""
    # Non-URL attributes are untouched.
    assert span.attributes["http.method"] == "GET"
