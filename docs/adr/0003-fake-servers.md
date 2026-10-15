# ADR 0003: Fake Meta and Daraja Servers

## Status
Accepted

## Context
SPEC §14 requires: "Provider sandboxes/fakes: build fake Meta and Daraja servers for local and CI testing."

We need deterministic, fast, offline testing for:
- WhatsApp webhook signature verification
- 24h customer service window enforcement
- Rate limiting (20 msg/s under Coexistence)
- Template vs free-form message rules
- STK Push request/response flows
- All 7 callback scenarios (success, timeout, insufficient_funds, cancelled, duplicate, late_callback, wrong_amount)
- Query-status fallback
- Reconciliation mismatch detection
- Record/replay of webhook sequences for regression testing

## Decision
Build two standalone FastAPI applications:

### Fake Meta Server (`packages/integrations/fake_meta/`)
Endpoints:
- `GET /webhook` - Meta challenge verification
- `POST /webhook` - Receive webhooks, verify `X-Hub-Signature-256`, normalize to internal events
- `POST /messages` - Send messages (enforces 24h window, rate limits)
- `POST /admin/record` / `GET /admin/replay/{id}` - Record/replay webhook sequences
- `GET /health` - Health check

Behavior:
- HMAC-SHA256 signature verification using configurable app secret
- In-memory 24h window tracking per customer phone
- Token bucket rate limiting at 20 req/s per IP
- Free-form messages only allowed within 24h window; templates required outside
- Records webhook sequences for deterministic replay

### Fake Daraja Server (`packages/integrations/fake_daraja/`)
Endpoints:
- `POST /stkpush` - Initiate STK Push, return `CheckoutRequestID`
- `POST /callback` - Receive callbacks (for testing)
- `POST /query` - Query transaction status
- `POST /admin/scenario` - Configure scenario: success, timeout, insufficient_funds, cancelled, wrong_pin, duplicate, late_callback, wrong_amount
- `POST /admin/record` / `GET /admin/replay/{id}` - Record/replay event sequences
- `GET /health` - Health check

Behavior:
- Validates BusinessShortCode, Amount, PhoneNumber
- Generates realistic `CheckoutRequestID` and `MerchantRequestID`
- Simulates async callbacks with configurable delay
- All 7 scenarios produce correct callback shapes matching Daraja docs
- Query-status returns current state
- Detects reconciliation mismatch (callback amount ≠ query amount)
- Idempotent callback handling (dedupe by CheckoutRequestID)

## Implementation Details
- Both run as separate containers in docker-compose (`--profile fake`)
- In-memory state (sufficient for M0; can persist to Redis/Postgres later)
- No external dependencies
- OpenAPI docs at `/docs`

## Consequences
- Positive: Fully offline CI; deterministic tests; fast feedback; no sandbox credentials needed
- Negative: Must maintain parity with real APIs; drift detection needed in M2+
- Risk: Fake behavior diverges from real API → contract tests in M2+ will catch this

## Alternatives Considered
- Use real Meta/Daraja sandboxes: Flaky, requires credentials, rate limited, no replay
- Mock libraries in tests: Don't test integration code paths, no contract validation
- WireMock/Hoverfly: Generic but less control over business logic simulation

## References
- SPEC.md §3 Integration Truth Table
- SPEC.md §14 Testing
- Meta WhatsApp Cloud API docs
- Safaricom Daraja API docs