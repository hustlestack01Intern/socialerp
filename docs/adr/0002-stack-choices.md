# ADR 0002: Stack Choices

## Status
Accepted

## Context
We need to choose the core technology stack for SocialERP. The requirements are:
- Multi-tenant SaaS with strict isolation
- Event-sourced stock ledger
- Order state machine
- WhatsApp Business API integration
- M-Pesa STK Push integration
- AI agent orchestration
- High reliability and observability

## Decision
We will use the following stack:

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12 | Strong typing support, rich ecosystem for web, data, AI |
| Web Framework | FastAPI | Async, OpenAPI, pydantic integration, high performance |
| Validation | pydantic v2 | Fast, strict typing, JSON Schema generation |
| Database | PostgreSQL 16 | RLS for tenant isolation, JSONB, mature |
| ORM | SQLAlchemy 2.0 | Async, 2.0 style, flexible |
| Migrations | Alembic | Standard with SQLAlchemy |
| Job Queue | Postgres-backed (pgmq/arq) | No Redis dependency initially; SPEC §4 says "add Redis only after measured need" |
| Owner UI | Server-rendered (Jinja2 + HTMX) | Simpler than SPA; SPEC §4 says "separate JS front end only via ADR" |
| Linting | ruff | Fast, replaces black/isort/flake8 |
| Type Checking | mypy --strict | Catch bugs early |
| Testing | pytest + hypothesis | Property-based testing for invariants |
| Observability | OpenTelemetry (deferred to M1) | SPEC §13 says "instrument from day one" but M0 focuses on scaffold |

## Consequences
- Positive: Single language across backend, agents, tooling; strong typing; minimal dependencies
- Negative: Python async ecosystem still maturing; GIL limits CPU-bound work (mitigate with workers)
- Risk: Postgres-backed queue may need Redis later for high throughput (addressed by SPEC §4)

## Alternatives Considered
- Node.js/TypeScript: Good for full-stack but weaker data/AI ecosystem
- Go: Excellent performance but slower AI/ML integration
- Rust: Best performance but steep learning curve, slower iteration

## References
- SPEC.md §4 Architecture
- CONSTITUTION.md §8 Efficiency targets