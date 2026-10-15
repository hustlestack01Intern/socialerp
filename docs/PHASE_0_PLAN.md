# Phase 0 Plan: Scaffold, Tooling, CI, Docs, Fake Meta/Daraja Servers

## Objective
Establish the complete development foundation so that all subsequent phases can be built with confidence, fast feedback, and zero ambiguity about interfaces.

## Deliverables

### 1. Repository Scaffold (per Section 15)
```
/apps/api          # FastAPI service (modular monolith entry)
/apps/worker       # arq/Celery background worker
/apps/dashboard    # Next.js PWA (owner dashboard)
/packages/core     # Domain models, schemas, shared logic
/packages/integrations
  /meta            # WhatsApp/Instagram clients, webhook handling
  /daraja          # M-Pesa STK Push client, callback handling
  /aggregators     # Payment aggregator interfaces
/packages/agent    # Prompts, tools, policy engine, evals
/infra             # Docker, docker-compose, k8s stubs
/docs              # SPEC, ADRs, THREAT_MODEL, STATE, RUNBOOKS
/tests             # Unit, integration, contract, property tests
/evals             # Labeled conversation sets, evaluation harness
Makefile
docker-compose.yml
.env.example
```

### 2. Tooling & CI
- **Python**: 3.12, `uv` for dependency management, `ruff` (lint/format), `mypy --strict`, `pytest`
- **Node**: 20+, `pnpm`, `eslint`, `prettier`, `typescript --strict`
- **Git hooks**: `pre-commit` (ruff, mypy, eslint, secret scan)
- **CI (GitHub Actions)**: lint → typecheck → unit tests → integration tests → contract tests → evals
- **Makefile targets**: `check`, `test`, `test-unit`, `test-integration`, `test-contract`, `eval`, `lint`, `typecheck`, `db-up`, `db-down`, `migrate`, `seed`, `run-api`, `run-worker`, `run-dashboard`

### 3. Core Infrastructure (packages/core)
- **Database**: SQLAlchemy 2 + Alembic, async engine, session management
- **Multi-tenancy**: `TenantMixin` with `tenant_id` UUID, RLS policy helpers, `current_tenant_id()` context var
- **Audit log**: Append-only table with `event_id`, `tenant_id`, `actor_type`, `actor_id`, `action`, `payload`, `decision`, `rule_ref`, `created_at`
- **Outbox pattern**: `outbox_events` table + relay worker
- **Idempotency**: `idempotency_keys` table (key, tenant_id, scope, result, expires_at)
- **Settings**: Pydantic Settings per service, `.env` loading, validation
- **Observability**: OpenTelemetry init, structured logging (structlog), metrics (Prometheus), tracing

### 4. Fake Servers (packages/integrations/*/fakes)
- **Fake Meta Server** (`/fake-meta`):
  - WhatsApp Cloud API: `/v20.0/{phone_id}/messages`, webhook verification, template management
  - Instagram Messaging API: `/v20.0/{page_id}/messages`, webhook verification
  - Simulates: 24h window, rate limits, signature headers (`X-Hub-Signature-256`), delivery receipts, read receipts
  - State persists across requests (in-memory SQLite)
  - Replay recorded webhook fixtures from `/tests/fixtures/meta/`

- **Fake Daraja Server** (`/fake-daraja`):
  - STK Push: `/mpesa/stkpush/v1/processrequest` → returns `CheckoutRequestID`
  - Callback endpoint: configurable per tenant, receives `ResultCode`, `CallbackMetadata`
  - Query Status: `/mpesa/stkpushquery/v1/query`
  - Simulates: success, timeout, insufficient funds, wrong PIN, cancelled, duplicate callback, late callback
  - State persists; supports reconciliation endpoint

### 5. Documentation
- `/docs/SPEC.md` (copied from AGENTS.md)
- `/docs/STATE.md` (this file)
- `/docs/THREAT_MODEL.md` (skeleton with STRIDE categories filled)
- `/docs/adr/0001-stack-and-architecture.md` (records stack choices)
- `/docs/adr/0002-multi-tenancy-rls.md`
- `/docs/adr/0003-agent-autonomy-levels.md`
- `/docs/adr/0004-payment-provider-interface.md`
- `/docs/adr/0005-event-sourced-stock-ledger.md`
- `/docs/adr/0006-order-state-machine.md`
- `/docs/RUNBOOKS/oncall.md` (stub)

### 6. Test Infrastructure
- **Test DB**: Postgres in docker-compose, migrations run on `make db-up`
- **Fixtures**: FactoryBoy for domain objects, `pytest-asyncio`, `httpx.AsyncClient`
- **Contract tests**: OpenAPI schema validation for all external integrations
- **Property tests**: Hypothesis for stock ledger (invariant: sum(movements) == current_stock) and order state machine (invariant: only legal transitions)
- **Eval harness**: `/evals/runner.py` loads labeled JSONL, runs agent, scores against rubric

## Acceptance Tests (must all pass for Phase 0 done)

| ID | Test | Command |
|----|------|---------|
| P0-1 | `make check` passes (ruff + mypy --strict + pytest + evals) | `make check` |
| P0-2 | Repo structure matches Section 15 exactly | `tree -L 2` diff vs spec |
| P0-3 | Docker compose starts all services healthy | `docker compose up -d && make check` |
| P0-4 | Alembic migrations run clean up/down | `make migrate && make migrate-down && make migrate` |
| P0-5 | RLS policies enforce tenant isolation (cross-tenant query returns 0 rows) | `pytest tests/tenancy/test_rls.py` |
| P0-6 | Fake Meta server accepts webhook, verifies signature, returns 200 | `pytest tests/integrations/meta/test_fake_server.py` |
| P0-7 | Fake Meta server replays recorded WhatsApp webhook fixtures | `pytest tests/integrations/meta/test_fixture_replay.py` |
| P0-8 | Fake Daraja server processes STK Push, returns CheckoutRequestID | `pytest tests/integrations/daraja/test_fake_server.py` |
| P0-9 | Fake Daraja server simulates all failure modes (callback + query fallback) | `pytest tests/integrations/daraja/test_failure_modes.py` |
| P0-10 | Outbox relay delivers events at-least-once with idempotent handler | `pytest tests/core/test_outbox.py` |
| P0-11 | Idempotency key dedupes repeated requests | `pytest tests/core/test_idempotency.py` |
| P0-12 | Audit log captures tenant_id, action, payload, decision | `pytest tests/core/test_audit_log.py` |
| P0-13 | OpenTelemetry emits traces for API request + DB query | `pytest tests/observability/test_tracing.py` |
| P0-14 | Eval harness runs on sample labeled set (even if empty) | `make eval` |

## Assumptions & Open Questions (max 5)

1. **Python package manager**: Use `uv` (fast, modern) vs `pip-tools`/`poetry`? *Assumption: uv.*
2. **Job queue**: `arq` (Redis, async-native) vs `Celery` (mature, more ops)? *Assumption: arq for simplicity.*
3. **Fake server framework**: `FastAPI` test servers vs `pytest-httpserver` vs standalone processes? *Assumption: standalone FastAPI apps in docker-compose for realism.*
4. **Dashboard auth**: NextAuth.js vs custom JWT + HttpOnly cookies? *Assumption: NextAuth.js with credentials provider (tenant-scoped).*
5. **Kenya Data Protection Act registration**: Do we need ODPC registration before pilot? *Open question: confirm with legal counsel; flag in THREAT_MODEL.*

## Dependencies
- None (this is the first phase)

## Estimated Effort
- Scaffold & tooling: 2 days
- Core infrastructure (DB, tenancy, audit, outbox, idempotency): 3 days
- Fake Meta server: 2 days
- Fake Daraja server: 2 days
- Documentation & ADRs: 1 day
- Test infrastructure & acceptance tests: 2 days
- **Total: ~12 working days**