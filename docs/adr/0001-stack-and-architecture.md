# ADR 0001: Stack and Architecture Decisions

**Status**: Accepted
**Date**: 2024-01-15
**Deciders**: Chief Engineer

---

## Context

We need to choose the technology stack for SocialERP, a multi-tenant AI-powered ERP for Kenyan social commerce sellers. The system must handle WhatsApp/Instagram/TikTok conversations, inventory, orders, M-Pesa payments, delivery, and customer memory at scale.

Key requirements:
- Multi-tenancy with strict isolation
- High reliability for payment processing
- AI agent with deterministic tool use
- 30-minute onboarding target
- Production-grade observability
- Kenyan regulatory compliance (DPA)

---

## Decisions

### Language & Runtime
| Choice | Rationale |
|--------|-----------|
| **Python 3.12** | Best ecosystem for AI/ML, FastAPI, SQLAlchemy 2, data processing; team expertise |
| **uv** | Fast, reliable dependency management; replaces pip-tools/poetry |

### Web Framework
| Choice | Rationale |
|--------|-----------|
| **FastAPI** | Async-native, OpenAPI generation, pydantic integration, excellent performance |
| **Pydantic v2** | Validation, serialization, settings management; 17x faster than v1 |

### Database
| Choice | Rationale |
|--------|-----------|
| **PostgreSQL 16** | Mature, JSONB support, Row-Level Security (critical for tenancy), extensions |
| **SQLAlchemy 2.0** | Async support, 2.0 style, type-safe, Alembic migrations |
| **asyncpg** | High-performance async driver |

### Multi-tenancy
| Choice | Rationale |
|--------|-----------|
| **PostgreSQL Row-Level Security** | Enforced at database level; impossible to bypass via application bugs |
| **TenantMixin + context variable** | Application-level enforcement for defense in depth |

### Job Queue
| Choice | Rationale |
|--------|-----------|
| **arq (Redis)** | Async-native, simple, reliable, integrates with FastAPI; lighter than Celery |

### Observability
| Choice | Rationale |
|--------|-----------|
| **OpenTelemetry** | Vendor-neutral, auto-instrumentation, traces + metrics + logs |
| **Prometheus** | Metrics collection, alerting integration |
| **structlog** | Structured JSON logging, context binding |

### Frontend
| Choice | Rationale |
|--------|-----------|
| **Next.js 14 (App Router)** | React framework, PWA support, server components, excellent DX |
| **Tailwind CSS** | Utility-first, small bundle, consistent design |
| **NextAuth.js** | Authentication, tenant-scoped sessions |

### AI/LLM
| Choice | Rationale |
|--------|-----------|
| **Model-agnostic gateway** | Swap local (Ollama) ↔ paid (OpenAI/Anthropic) without code changes |
| **Per-tenant spend caps** | Cost control, abuse prevention |
| **Tool-calling with validation** | LLM never does math; deterministic code executes |

### Infrastructure
| Choice | Rationale |
|--------|-----------|
| **Docker Compose (local)** | Simple, reproducible, matches production topology |
| **Modular monolith** | Clean service boundaries; can split to microservices later |

---

## Consequences

### Positive
- Strong type safety across stack (Python + TypeScript)
- Database-enforced tenancy eliminates cross-tenant leaks
- Async throughout for high concurrency
- Vendor-neutral observability
- Local-first development with production-parity fakes

### Negative
- Python GIL limits CPU-bound parallelism (mitigated: async I/O, worker processes)
- Redis dependency for queue (mitigated: managed Redis in production)
- More complex than synchronous frameworks (mitigated: team expertise)

---

## Alternatives Considered

| Alternative | Rejected Because |
|-------------|------------------|
| Node.js/TypeScript backend | Weaker AI/ML ecosystem; pydantic/FastAPI superior for validation |
| Celery | Overkill for our needs; synchronous result backend complexity |
| MongoDB | No RLS; schema flexibility not needed; ACID critical for payments |
| GraphQL | Overhead not justified; REST + OpenAPI sufficient |
| Kubernetes (local) | Complexity overhead; Docker Compose sufficient for dev |

---

## Related ADRs
- ADR 0002: Multi-tenancy with RLS
- ADR 0003: Agent Autonomy Levels
- ADR 0004: Payment Provider Interface