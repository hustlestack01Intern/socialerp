# ADR 0021: Onboarding Wizard Instrumentation

## Status
Proposed

## Context
Phase 9 requires a 30-minute instrumented onboarding wizard per AGENTS.md §11:
1. Business profile — 3 min
2. Connect WhatsApp + Instagram — 8 min
3. Catalog bootstrap — 8 min
4. Payments — 5 min
5. Rules and tone — 4 min
6. Go live shadow test — 2 min
Total: 30 min

Specialist input from:
- Architect: `packages/core/onboarding/` module, `OnboardingWizardProgress` model, `OnboardingWizardService`
- Observability: Timer schema, Prometheus metrics, OTel spans, Grafana panels, alert rules
- Security/Compliance: Fallback tracks for missing Meta assets/Safaricom Till, zero-interference gate
- Product/Legal: Consent collection for cross-channel linking in step 2

## Decision

### 1. Module Structure
```
packages/core/onboarding/
├── __init__.py
├── model.py              # OnboardingWizardProgress (SQLAlchemy)
├── service.py            # OnboardingWizardService (start_step, complete_step, get_progress)
├── steps.py              # Step enum, config (name, target_seconds, required_assets)
├── events.py             # Domain events: OnboardingStepStarted, OnboardingCompleted, etc.
└── metrics.py            # Prometheus metrics (histograms, counters, gauges)
```

### 2. Model: `OnboardingWizardProgress`
```python
class OnboardingWizardProgress(Base):
    __tablename__ = "onboarding_wizard_progress"
    __table_args__ = (RLS_TENANT_POLICY,)

    tenant_id = Column(UUID, ForeignKey("tenants.id"), primary_key=True)
    state = Column(String, default="NOT_STARTED")  # NOT_STARTED|IN_PROGRESS|COMPLETED|FAILED|ABORTED
    current_step = Column(Integer, default=0)      # 1-6
    step_timings = Column(JSONB, default={})       # Per-step schema below
    fallback_flags = Column(JSONB, default={})     # Aggregated fallbacks
    launch_approved_at = Column(TIMESTAMPTZ, nullable=True)
    completed_at = Column(TIMESTAMPTZ, nullable=True)
    created_at = Column(TIMESTAMPTZ, default=now)
    updated_at = Column(TIMESTAMPTZ, default=now, onupdate=now)
```

### 3. Step Timings Schema (JSONB)
```json
{
  "1": {
    "started_at": "2026-10-18T10:00:00Z",
    "completed_at": "2026-10-18T10:03:15Z",
    "duration_ms": 195000,
    "status": "COMPLETED",
    "attempts": 1,
    "paused_at": null,
    "resumed_at": null,
    "fallback": null
  },
  "2": {
    "started_at": "2026-10-18T10:03:15Z",
    "completed_at": null,
    "duration_ms": null,
    "status": "IN_PROGRESS",
    "attempts": 1,
    "paused_at": null,
    "resumed_at": null,
    "fallback": {
      "type": "MANUAL_PAYMENT_MODE",
      "triggered_at": "2026-10-18T10:10:00Z",
      "reason": "no_daraja_credentials"
    }
  }
}
```

### 4. Fallback Types (Enum)
| Type | Trigger | Behavior |
|------|---------|----------|
| `MANUAL_PAYMENT_MODE` | No Daraja Till / aggregator credentials | Record fallback, allow go-live, async guide to complete |
| `MANUAL_CATALOG` | No existing catalog/posts/price list | Owner creates from scratch; no block |
| `NO_META_ASSETS` | No verified FB Page / IG Professional account | Assisted mode (agent drafts, owner sends); record fallback |
| `NO_DARAJA_TILL` | No Safaricom Till/Paybill | Manual payment confirmation mode; record fallback |

### 5. Zero-Interference Gate
- `tenant.settings["launch_approved"] = True` set ONLY on step 6 completion
- Webhook receive: reject Instagram/WhatsApp if not approved (return 200, log, no processing)
- Sender: raise `ChannelNotEnabledError` if not approved
- API: no customer messaging endpoints work until approved

### 6. Metrics (Prometheus)
| Metric | Type | Labels | Buckets |
|--------|------|--------|---------|
| `onboarding_wizard_step_duration_seconds` | Histogram | `step` (1-6), `status`, `fallback_type` | [60, 120, 180, 240, 300, 480, 600, 900, 1800, 3600] |
| `onboarding_wizard_total_duration_seconds` | Histogram | `status`, `fallback_type` | Same |
| `onboarding_wizard_step_started_total` | Counter | `step` | — |
| `onboarding_wizard_step_completed_total` | Counter | `step`, `status`, `fallback_type` | — |
| `onboarding_wizard_active_sessions` | Gauge | — | — |
| `onboarding_wizard_fallback_total` | Counter | `step`, `fallback_type` | — |
| `onboarding_wizard_slo_breach_total` | Counter | `scope` (step_1..step_6, total) | — |

**No `tenant_id` label** — use OTel span attribute for debugging.

### 7. OpenTelemetry Spans
Each step: `onboarding.step.{step_num}` with attributes:
- `onboarding.step`, `onboarding.tenant_id`, `onboarding.target_seconds`
- On complete: `onboarding.duration_ms`, `onboarding.status`, `onboarding.fallback_type`

### 8. Alert Rules
| Alert | Condition | Severity |
|-------|-----------|----------|
| `OnboardingMedianExceeds30Min` | p50 total > 1800s over 1h | warning |
| `OnboardingStepMedianExceeds2xTarget` | Any step p50 > 2x target | warning |
| `OnboardingFallbackRateHigh` | Fallback rate > 20% for any step | critical |

### 9. Step Configuration
```python
STEP_CONFIG = {
    1: {"name": "business_profile", "target_seconds": 180, "required_assets": []},
    2: {"name": "connect_channels", "target_seconds": 480, "required_assets": ["meta_business_verification"]},
    3: {"name": "catalog_bootstrap", "target_seconds": 480, "required_assets": []},
    4: {"name": "payments_setup", "target_seconds": 300, "required_assets": ["daraja_till"]},
    5: {"name": "rules_tone", "target_seconds": 240, "required_assets": []},
    6: {"name": "go_live_shadow", "target_seconds": 120, "required_assets": []},
}
```

### 10. E2E Test Contract
- Scripted onboarding on fakes: tenant creation → step 1 → step 2 (fake OAuth) → step 3 (catalog import) → step 4 (payments) → step 5 (rules) → step 6 (shadow order via fake IG DM)
- Must complete < 30 min wall-clock on CI
- Asserts: all step timers recorded, fallback flags correct, `launch_approved_at` set, Instagram DM processed through pipeline

## Consequences

### Positive
- Deterministic, server-controlled timers (no client-side trust)
- Full audit trail per tenant
- SLO visibility from day one
- Fallback tracks non-blocking per AGENTS.md §11
- Zero-interference enforced at channel layer

### Negative
- New module + table + metrics surface
- Requires legal sign-off on consent flow (step 2)
- Fake server complexity for Instagram OAuth mock

### Risks
- Owner drops mid-wizard → paused_at/resumed_at handles but SLO breaches
- Fake Instagram OAuth may not match real flow exactly
- 30-min target may need adjustment after real traffic

## Related ADRs
- ADR 0020: Instagram channel integration (step 2 dependency)
- ADR 0017: CRM memory (consent, identity linking)
- ADR 0008: Packages.core canonical

## Next Steps
1. Legal review of step 2 consent flow
2. Begin Commit 6: Onboarding wizard model + service + metrics
3. Commit 7: API endpoints + zero-interference gate
4. Commit 8: E2E test + ADR finalization