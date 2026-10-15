# PLAN: Phase 0 - Scaffold, Tooling, CI, Docs, Fake Meta/Daraja Servers

## Goal and Non-Goals
**Goal:** Establish a working development foundation where `make check` passes (ruff + mypy --strict + pytest + evals) and fake Meta/Daraja servers can replay recorded webhooks for integration testing in later phases.

**Non-Goals:**
- No business logic (tenants, orders, payments, agents, inventory)
- No real Meta/Daraja API calls
- No database schema or migrations
- No authentication or authorization
- No dashboard UI

---

## Classification
- **Type:** Infrastructure
- **Risk:** Low
- **Complexity:** Medium

---

## Assumptions with Confidence (copied to /docs/UNCERTAINTY.md)

| Assumption | Confidence | Why | How to Verify |
|------------|------------|-----|---------------|
| Python 3.12 is available and stable | 0.95 | Standard version, widely adopted | Check `python3.12 --version` in CI |
| FastAPI + pydantic v2 + SQLAlchemy 2 stack works together | 0.9 | Well-documented compatible versions | Run `pip install` in CI; import test |
| Docker Compose can run PostgreSQL + Redis locally | 0.95 | Standard containers | `docker-compose up -d` health checks pass |
| Fake Meta server can simulate WhatsApp Cloud API webhooks faithfully | 0.8 | Meta API docs are public but complex | Record real sandbox webhooks later; compare shapes |
| Fake Daraja server can simulate STK Push callbacks faithfully | 0.8 | Daraja docs public; callback formats documented | Record real sandbox callbacks later; compare shapes |
| `make check` target can run all tools in sequence | 0.95 | Standard Make pattern | Run locally and in CI |
| GitHub Actions (or similar) available for CI | 0.9 | Repo is on GitHub | Configure workflow; run once |

---

## Derivation: First-Principles Reasoning and Invariants

**Invariant 1: Reproducible Environment**
- `docker-compose.yml` must start all required services (PostgreSQL, Redis) with fixed versions.
- `.env.example` documents every required variable; no secrets in repo.
- `Makefile` provides `make up`, `make down`, `make check`, `make test`, `make lint`, `make typecheck`.

**Invariant 2: Strict Type Safety from Day One**
- `mypy --strict` passes on empty codebase (no errors, no `ignore` without justification).
- All new code must be typed; `pyproject.toml` configures mypy strictly.

**Invariant 3: Code Quality Gates Are Automated**
- `ruff` for linting + formatting (replaces black, isort, flake8).
- `pytest` with `--strict-markers` and `--strict-config`.
- CI fails on any warning or error.

**Invariant 4: Fake Servers Are Contract Test Beds**
- Fake Meta server implements: webhook signature verification (HMAC-SHA256), 24h window logic, rate limiting (20 msg/s), message normalization, template enforcement.
- Fake Daraja server implements: STK Push request validation, callback generation (success/timeout/insufficient funds/cancelled), duplicate callbacks, query-status endpoint, reconciliation mismatches.
- Both fakes can **record** and **replay** webhook sequences for deterministic integration tests.

**Invariant 5: Documentation Is Living**
- `/docs/SPEC.md` = product spec (done).
- `/docs/STATE.md` = current phase, done, next, risks, decisions (updated each phase).
- `/docs/MAP.md` = module map, dependency graph (updated by architect/scribe).
- `/docs/UNCERTAINTY.md` = assumption ledger (updated each phase).
- `/docs/adr/` = Architecture Decision Records for non-trivial choices.
- `/docs/THREAT_MODEL.md` = threat model (start in Phase 0, evolve).

---

## Options Considered (at least 3 candidates)

### Option A: Minimal Scaffold (Chosen)
- Single `pyproject.toml` at root with workspace packages (`packages/*`, `apps/*`).
- `docker-compose.yml` with postgres:16, redis:7.
- `Makefile` with targets: `up`, `down`, `check`, `test`, `lint`, `typecheck`, `migrate`, `seed`.
- Fakes as standalone FastAPI apps in `packages/integrations/fake_meta`, `packages/integrations/fake_daraja`.
- CI: GitHub Actions running `make check` on push/PR.

**Why:** Matches repo layout in SPEC §15; modular monolith boundaries clear from start; fakes co-located with real integrations.

### Option B: Monorepo with Poetry Workspaces
- Uses Poetry for dependency management across packages.
- More complex setup; Poetry lock files add CI time.
- Rejected: `pip` + `pyproject.toml` + `uv` (if available) is simpler and faster.

### Option C: Separate Repos per Service
- Violates "modular monolith first" (SPEC §4); premature splitting.
- Cross-service changes become painful; tenant isolation harder to test.
- Rejected.

### Option D: No Fakes, Use Sandboxes Directly
- Requires network, credentials, rate limits; flaky CI.
- Cannot replay recorded webhooks deterministically.
- Rejected: fakes are required by SPEC §14 ("Provider sandboxes/fakes: build fake Meta and Daraja servers for local and CI testing").

---

## Steps (ordered, each a small commit with tests)

### Step 0.1: Repo Layout & Config Files
- Create directories: `/apps/api`, `/apps/worker`, `/apps/dashboard`, `/packages/core`, `/packages/integrations`, `/packages/agent`, `/infra`, `/tests`, `/evals`, `/docs/adr`.
- Write `pyproject.toml` (root) with: Python 3.12, dependencies (fastapi, pydantic, sqlalchemy, alembic, pytest, ruff, mypy, httpx, redis, arq), tool configs.
- Write `.env.example` with all required vars (DB_URL, REDIS_URL, META_APP_SECRET, DARAJA_CONSUMER_KEY, etc. — all placeholders).
- Write `docker-compose.yml` with postgres, redis, healthchecks.
- Write `Makefile` with targets: `up`, `down`, `check`, `test`, `lint`, `typecheck`, `migrate`, `seed`, `logs`.

**Tests:** `make up` starts containers; `make check` runs ruff + mypy + pytest (empty test suite passes).

### Step 0.2: CI Pipeline
- GitHub Actions workflow `.github/workflows/ci.yml`:
  - Checkout, setup Python 3.12, cache pip.
  - `make up` (or use services: postgres, redis).
  - `make check` (ruff + mypy --strict + pytest).
  - Upload coverage artifact.
- Ensure `make check` exits non-zero on any failure.

**Tests:** Push to trigger CI; verify green on empty codebase.

### Step 0.3: Core Package Skeleton
- `packages/core/__init__.py`, `packages/core/domain/__init__.py`.
- Base types: `TenantId`, `UserId`, `CustomerPhone`, `Money` (KES, integer cents), `Quantity`, `SKU`.
- `packages/core/events/__init__.py`: event base classes, outbox pattern primitives.
- `packages/core/security/__init__.py`: HMAC verification, field-level encryption helpers.

**Tests:** Property tests for `Money` (addition, subtraction, no negative), `CustomerPhone` normalization (2547XXXXXXXX, 2541XXXXXXXX).

### Step 0.4: Fake Meta Server (WhatsatsApp/Instagram)
- `packages/integrations/fake_meta/` as a FastAPI app.
- Endpoints:
  - `POST /webhook` — receive messages, verify `X-Hub-Signature-256`, normalize to internal `MessageEvent`.
  - `POST /messages` — send messages (rate limit 20/s, enforce 24h window).
  - `GET /webhook` — verify challenge (Meta handshake).
  - `POST /admin/record` — start recording webhook sequence.
  - `GET /admin/replay/:id` — replay recorded sequence.
- In-memory store for recordings (swap to Redis later).
- OpenAPI docs at `/docs`.

**Tests:**
- Signature verification rejects forged payloads.
- 24h window: free-form allowed inside, template-only outside.
- Rate limit: 21st request in 1s returns 429.
- Record/replay: same sequence produces same normalized events.

### Step 0.5: Fake Daraja Server (M-Pesa STK Push)
- `packages/integrations/fake_daraja/` as a FastAPI app.
- Endpoints:
  - `POST /stkpush` — validate request, return `CheckoutRequestID`, simulate async callback.
  - `POST /callback` — receive callback (simulated by fake itself or test).
  - `POST /query` — query status by `CheckoutRequestID`.
  - `POST /admin/scenario` — configure scenario: success, timeout, insufficient_funds, cancelled, duplicate, late_callback, wrong_amount.
  - `POST /admin/record` / `GET /admin/replay/:id` — same as fake_meta.
- Callback shapes match Daraja docs exactly (ResultCode, ResultDesc, CallbackMetadata).

**Tests:**
- Each scenario produces correct callback + state transition.
- Duplicate callbacks are idempotent (dedupe by `CheckoutRequestID`).
- Query-status fallback returns correct state after timeout.
- Reconciliation mismatch detection: fake can return different amount in callback vs query.

### Step 0.6: Documentation & ADR Structure
- Create `/docs/adr/0001-stack-choices.md` (Python 3.12, FastAPI, etc.).
- Create `/docs/adr/0002-fake-servers.md` (design of fake Meta/Daraja).
- Update `/docs/MAP.md` with module list and dependency graph (Mermaid).
- Update `/docs/THREAT_MODEL.md` with initial threats (forged webhooks, forged callbacks, tenant escape).
- Update `/docs/UNCERTAINTY.md` with assumptions from this plan.
- Update `/docs/STATE.md` with Phase 0 done.

**Tests:** `make check` includes a doc-build step (e.g., `markdownlint` or simple link check).

### Step 0.7: Evals Scaffold
- `/evals/__init__.py`, `/evals/conversation_sets/` (empty directories for: order_capture, faq_answering, swahili_sheng, fraud_flagging, handoff_decisions).
- `/evals/run.py` — runner that loads sets, calls agent (stub), scores.
- CI runs `python -m evals.run --smoke` (passes with stub).

---

## Blast Radius and Rollback
- **Blast radius:** Entire repo (foundational). No existing code to break.
- **Rollback:** `git revert` any commit; `make down` stops containers; no migrations to roll back.
- **Verification:** Each step's tests must pass before next step.

---

## Incentive Review

| Stakeholder | Gains | Abuse Risk | Mitigation |
|-------------|-------|------------|------------|
| **Engineering team** | Fast feedback, reliable CI | Skipping `make check` locally | Pre-commit hooks; CI gate |
| **Product** | Clear phase gates, measurable AC | Pushing features into Phase 0 | Strict phase definitions in SPEC §16 |
| **Security** | Threat model started early | Fake servers too permissive | Fakes enforce same validation as real APIs |
| **Future tenants** | Stable platform | None yet | N/A |

---

## Failure Modes and Detection

| Failure Mode | Detection | Response |
|--------------|-----------|----------|
| `mypy --strict` fails on generated code | CI red | Fix types; add `py.typed` markers |
| Fake Meta/Daraja diverge from real APIs | Integration tests fail in Phase 2+ | Record real sandbox webhooks; update fakes; add contract tests |
| Docker Compose healthchecks flaky | `make up` times out | Increase timeouts; use `pg_isready`/`redis-cli ping` |
| CI timeout (>10 min) | GitHub Actions timeout | Split jobs; cache aggressively; parallelize |
| `ruff`/`mypy` version drift | CI passes locally, fails remotely | Pin versions in `pyproject.toml`; use `uv` lockfile if adopted |

---

## Acceptance Criteria (Measurable)
1. `make check` runs `ruff + mypy --strict + pytest` and exits 0 on clean repo.
2. `make up` starts PostgreSQL and Redis; `make down` stops them.
3. Fake Meta server:
   - Returns 200 on valid webhook with correct signature.
   - Returns 401 on invalid signature.
   - Enforces 24h window (free-form vs template).
   - Rate limits at 20 req/s.
   - Records and replays a sequence of 10 webhooks identically.
4. Fake Daraja server:
   - Returns `CheckoutRequestID` on valid STK Push.
   - Generates callbacks for all 7 scenarios (success, timeout, insufficient_funds, cancelled, duplicate, late, wrong_amount).
   - Query-status returns correct state.
   - Detects reconciliation mismatch (callback amount ≠ query amount).
   - Records and replays a scenario sequence.
5. CI pipeline runs `make check` on every push/PR and reports status.
6. `/docs/adr/0001-stack-choices.md` and `/docs/adr/0002-fake-servers.md` exist.
7. `/docs/MAP.md`, `/docs/THREAT_MODEL.md`, `/docs/UNCERTAINTY.md`, `/docs/STATE.md` updated.
8. `/evals/run.py --smoke` passes.

---

## Docs to Update
- `/docs/STATE.md` — Phase 0 done, next = Phase 1.
- `/docs/MAP.md` — Module list, dependency graph.
- `/docs/UNCERTAINTY.md` — Assumptions from this plan.
- `/docs/THREAT_MODEL.md` — Initial threats.
- `/docs/adr/0001-stack-choices.md`, `/docs/adr/0002-fake-servers.md`.
- `/docs/SPEC.md` — Already created.

---

## Open Questions for Human (max 5)

1. **CI Platform:** GitHub Actions confirmed, or prefer GitLab CI / CircleCI / other?
2. **Python Package Manager:** `pip` + `pyproject.toml` (PEP 621) is assumed. Any preference for `uv`, `poetry`, or `pip-tools`?
3. **Fake Server Deployment:** Run fakes as separate containers in `docker-compose.yml`, or as in-process test fixtures? (Containers = more realistic network behavior; fixtures = faster tests.)
4. **Database for Fakes:** Fakes currently use in-memory storage. Should they persist to PostgreSQL/Redis to match production patterns, or stay in-memory for speed?
5. **OpenTelemetry:** Include OTel SDK + exporter in Phase 0, or defer to Phase 1 when real services emit traces?

---

**Ready for human review. Awaiting "go" to begin implementation.**