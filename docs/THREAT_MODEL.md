# Threat Model - SocialERP

**Document Version**: 1.1
**Last Updated**: 2026-11-01
**Classification**: Internal - Security Sensitive

> **2026-11-01 correction (M0 truth-freeze, HEAD `a39db67`).** Several claims in the
> original v1.0 text were aspirational, not measured. Corrected here rather than
> silently kept:
> - **RLS is NOT yet enforced.** Policies exist on the tenant tables, but the runtime
>   API/worker connects as the **table owner** and no table is `FORCE ROW LEVEL
>   SECURITY`, so the policies do not apply to the application's own reads/writes.
>   The public registration path also writes tenant rows with no tenant context.
>   This is U76/U77/U82 and is the **M1** work; until it lands, tenant isolation is an
>   application-level guarantee (tenant-scoped queries + tests), not a database one.
> - **ODPC registration and a DPIA are not established facts.** Whether either is
>   required depends on turnover/employee thresholds, purposes and counsel's
>   classification. Treat both as **pending Kenyan counsel review** (pilot plan M4).
> - **"7-year retention default" is not a verified configured default.** Retention is
>   not yet mapped per data class; that is M4 work.
> - Reserve figures elsewhere in this document (e.g. "MFA", "field-level encryption",
>   "encrypted backups") are design intent unless the linked code/test exists; the
>   pilot plan's M1/M3/M4 acceptance criteria are the evidence gate.

---

## Overview

This threat model follows the STRIDE methodology to identify, classify, and mitigate threats to the SocialERP platform. It covers the multi-tenant SaaS platform serving Kenyan social commerce sellers via WhatsApp, Instagram, and TikTok integrations.

---

## System Context

### Actors
| Actor | Description | Trust Level |
|-------|-------------|-------------|
| **Business Owner** | Tenant admin, manages catalog, orders, settings | High (authenticated) |
| **Staff** | Tenant staff, handles conversations, orders | Medium (authenticated, scoped) |
| **Rider** | Delivery personnel, updates delivery status via WhatsApp | Medium (authenticated via WhatsApp) |
| **Customer** | External party, communicates via WhatsApp/IG/TikTok | Low (unauthenticated, external) |
| **Platform Admin** | SocialERP operations, manages tenants, billing | High (authenticated, privileged) |
| **Attacker** | External or internal malicious actor | None |

### Data Flows
```
Customer (WhatsApp/IG/TikTok)
    │
    ▼
Meta Webhooks ──────► Channels Service (verify sig, normalize)
    │                      │
    │                      ▼
    │               Conversation Service
    │                      │
    │                      ▼
    │               Agent Service (LLM)
    │                      │
    │         ┌────────────┴────────────┐
    │         ▼                         ▼
    │  Catalog/Inventory           Orders/Payments
    │         │                         │
    │         ▼                         ▼
    │  Stock Ledger              STK Push (Daraja)
    │                              │
    │                              ▼
    │                       Callback/Query
    │                              │
    ▼                              ▼
Delivery Service ◄─────────────► Notifications
    │
    ▼
Rider (WhatsApp)
```

### Trust Boundaries
1. **Internet ↔ Platform** - TLS termination, WAF, rate limiting
2. **Tenant A ↔ Tenant B** - Database RLS, no shared resources
3. **Platform ↔ Meta/Daraja** - Signature verification, IP allowlists, mutual TLS
4. **LLM Gateway ↔ Models** - Tenant-scoped prompts, output validation, spend caps
5. **Admin ↔ Platform** - Separate auth, audit logging, MFA required

---

## STRIDE Analysis

### S - Spoofing Identity

| ID | Threat | Impact | Likelihood | Mitigation |
|----|--------|--------|------------|------------|
| S-01 | Attacker forges Meta webhook signature | High | Medium | **Verify X-Hub-Signature-256** on every webhook; reject invalid |
| S-02 | Attacker forges Daraja callback | Critical | Medium | **Verify callback source IP** (Safaricom ranges); validate payload against expected order |
| S-03 | Customer spoofs another's phone number | High | Low | WhatsApp Business API verifies identity; Instagram/TikTok use platform auth |
| S-04 | Attacker steals tenant API keys | Critical | Low | **Short-lived tokens**, rotation, audit log on key usage, IP restrictions |
| S-05 | Rider impersonates another rider | Medium | Low | WhatsApp verified business account; rider linked to tenant |

### T - Tampering

| ID | Threat | Impact | Likelihood | Mitigation |
|----|--------|--------|------------|------------|
| T-01 | Attacker modifies webhook payload in transit | High | Low | **TLS 1.3** everywhere; signature verification |
| T-02 | Attacker modifies STK Push amount in callback | Critical | Medium | **Never trust callback amount**; match against stored order |
| T-03 | Attacker tampers with stock levels | High | Low | **Event-sourced ledger**; immutable movements; audit trail |
| T-04 | Attacker modifies order state illegally | High | Low | **State machine enforcement**; invalid transitions rejected |
| T-05 | Prompt injection alters agent behavior | Critical | High | **Input sanitization**; customer text as data only; tool parameter validation |
| T-06 | Attacker modifies reconciliation data | Medium | Low | Append-only reconciliation log; daily automated comparison |

### R - Repudiation

| ID | Threat | Impact | Likelihood | Mitigation |
|----|--------|--------|------------|------------|
| R-01 | Tenant denies making configuration change | Medium | Medium | **Append-only audit log** with actor, action, timestamp, payload |
| R-02 | Customer denies placing order | High | Medium | **WhatsApp message logs**; signed webhooks; STK Push receipt |
| R-03 | Rider denies delivery status update | Medium | Medium | **WhatsApp message with location/photo**; timestamped |
| R-04 | Platform admin denies action | High | Low | **Admin audit log** separate from tenant logs; immutable storage |

### I - Information Disclosure

| ID | Threat | Impact | Likelihood | Mitigation |
|----|--------|--------|------------|------------|
| I-01 | Cross-tenant data leak via query bug | Critical | Medium | **PARTIAL (2026-11-01):** policies exist on tenant tables + isolation tests, but the app role owns the tables and RLS is not `FORCE`d, so the database does not yet enforce this (U76/U77/U82 → M1). Application-level tenant scoping is the current control. |
| I-02 | Customer sees another's order history | Critical | Low | Conversation scoped to phone number; agent tools tenant-scoped |
| I-03 | PII in logs (phone, names, addresses) | High | Medium | **Structured logging with redaction**; separate PII fields |
| I-04 | LLM leaks tenant data in response | Critical | High | **Output validation**; no raw data in prompts; tool results validated |
| I-05 | Daraja credentials exposed in logs | Critical | Low | **Never log secrets**; field-level encryption for stored tokens |
| I-06 | Backup/restore exposes tenant data | Medium | Low | **Encrypted backups**; access-controlled restore process |

### D - Denial of Service

| ID | Threat | Impact | Likelihood | Mitigation |
|----|--------|--------|------------|------------|
| D-01 | Webhook flood overwhelms API | High | Medium | **Rate limiting** per tenant/channel; queue buffering |
| D-02 | STK Push callback storm | High | Low | **Idempotency keys**; async processing via outbox |
| D-03 | LLM spending spike (prompt injection) | Critical | Medium | **Per-tenant daily caps**; request validation; circuit breaker |
| D-04 | Database connection exhaustion | High | Low | **Connection pooling**; query timeouts; read replicas |
| D-05 | Queue backlog from failed deliveries | Medium | Medium | **Dead letter queues**; alerting; manual replay |
| D-06 | Meta/Daraja API downtime | High | Medium | **Graceful degradation**; queue messages; holding replies |

### E - Elevation of Privilege

| ID | Threat | Impact | Likelihood | Mitigation |
|----|--------|--------|------------|------------|
| E-01 | Staff escalates to owner permissions | High | Low | **RBAC enforced at API layer**; RLS + role checks |
| E-02 | Customer accesses admin endpoints | Critical | Low | **AuthN/AuthZ on every endpoint**; separate admin API |
| E-03 | Agent executes unauthorized tool | Critical | Medium | **Tool allowlist per autonomy level**; parameter validation |
| E-04 | Tenant accesses platform admin functions | Critical | Low | **Separate auth domains**; platform admin requires MFA |
| E-05 | Compromised rider accesses all orders | Medium | Low | **Rider scoped to assigned orders only**; token per delivery |

---

## Critical Asset Inventory

| Asset | Classification | Protection |
|-------|----------------|------------|
| Tenant database (PostgreSQL) | Critical | RLS policies exist but are **not yet enforced** (app role owns tables; M1); encryption-at-rest and backups are deployment-dependent, not verified here |
| M-Pesa credentials (per tenant) | Critical | Field-level encryption, rotation, audit on access |
| Meta App Secret / Webhook tokens | High | Environment variables, rotation, no logging |
| LLM API keys | High | Encrypted storage, per-tenant spend caps |
| Audit logs | High | Append-only, immutable, separate storage |
| Customer PII (phone, address, orders) | High | Field encryption, access logging, retention policy |
| Platform admin credentials | Critical | MFA, hardware keys, privileged access management |

---

## Kenya Data Protection Act (DPA) Compliance

| Requirement | Implementation |
|-------------|----------------|
| Lawful basis for processing | Consent (customer messages), Contract (orders), Legitimate interest (fraud) |
| Data minimization | Only collect necessary fields; no sensitive attributes inferred |
| Purpose limitation | Data used only for order fulfillment, support, analytics (opt-in) |
| Storage limitation | **Pending M4:** retention not yet mapped per data class; the old "7-year default" was not a verified configuration |
| Data subject rights | Export/delete API; consent withdrawal; automated within 30 days |
| Security safeguards | Encryption, access controls, breach notification (72h) |
| DPIA required | **Pending Kenyan counsel review** (threshold/purpose dependent) — do not treat as decided |
| ODPC registration | **Pending Kenyan counsel review** — threshold exemption depends on turnover and employees; do not assume it qualifies |

---

## Mitigation Priorities

### P0 - Must Fix Before Launch
- [ ] RLS **enforcement** (restricted role + `FORCE RLS` + public-path tenant context) — policies + tests exist, enforcement is M1
- [ ] Signature verification on all webhooks (Meta, Daraja, aggregators)
- [ ] STK Push callback amount validation against order
- [ ] Prompt injection defenses (input sanitization, output validation)
- [ ] Audit log on all agent actions, payments, admin operations
- [ ] Per-tenant LLM spend caps with hard limits
- [ ] Field-level encryption for PII and secrets
- [ ] ODPC registration duty confirmed by Kenyan counsel (M4)

### P1 - Fix Within First Month
- [ ] Automated daily reconciliation with alerting
- [ ] Comprehensive backup/restore testing
- [ ] Load testing with 100 tenants, 50 msg/sec burst
- [ ] Incident response runbook for payment fraud
- [ ] Customer data export/deletion automation

### P2 - Continuous Improvement
- [ ] Penetration testing (quarterly)
- [ ] Threat model review (per major release)
- [ ] Security training for team
- [ ] Bug bounty program

---

## Attack Trees

### A1: Steal Tenant Funds via Payment Manipulation
```
Steal Funds
├── Forge STK Push callback with higher amount
│   └── MITIGATED: Callback amount validated against order
├── Intercept and modify Daraja callback in transit
│   └── MITIGATED: TLS + IP allowlist + payload validation
├── Compromise tenant Daraja credentials
│   └── MITIGATED: Encrypted storage + rotation + audit
├── Social engineer rider to mark "delivered" without payment
│   └── MITIGATED: Pay-on-delivery requires STK confirmation
└── Exploit refund flow
    └── MITIGATED: Human-approved only + audit log
```

### A2: Access Another Tenant's Data
```
Cross-Tenant Access
├── SQL injection in tenant-scoped query
│   └── PARTIAL: parameterized queries are in place; RLS is not yet FORCEd (M1)
├── IDOR via API endpoint
│   └── PARTIAL: explicit tenant checks in the service layer; database RLS not yet enforcing (M1)
├── LLM prompt injection to access other tenant data
│   └── MITIGATED: Tenant-scoped tools + output validation
├── Misconfigured database permissions
│   └── OPEN: the app currently connects as the table owner (U77/U82 → M1)
└── Shared cache/queue leakage
    └── MITIGATED: Tenant-scoped keys + namespaces
```

### A3: Disrupt Service via Agent Abuse
```
Agent DoS / Cost Spike
├── Prompt injection to trigger expensive operations
│   └── MITIGATED: Confidence gating + human handoff + spend caps
├── Massive conversation volume from bot
│   └── MITIGATED: Rate limiting per customer + conversation limits
├── Recursive tool calls
│   └── MITIGATED: Max tool calls per run + timeout
└── Model hallucination causing wrong actions
    └── MITIGATED: Deterministic code for money/stock + validation
```

---

## Security Testing Requirements

| Test Type | Frequency | Scope |
|-----------|-----------|-------|
| SAST (ruff, bandit) | Every PR | All code |
| DAST (OWASP ZAP) | Weekly | API endpoints |
| Dependency scanning | Every PR | All dependencies |
| Secret scanning | Every PR + scheduled | All repos |
| Penetration test | Quarterly | Full platform |
| RLS isolation tests | Every PR | Database layer |
| Prompt injection evals | Every model change | Agent service |
| Chaos engineering | Monthly | Infrastructure |

---

## Incident Response

| Scenario | Detection | Response | Recovery |
|----------|-----------|----------|----------|
| Payment callback fraud | Reconciliation mismatch alert | Freeze tenant payments, investigate, reverse | Manual reconciliation, credential rotation |
| Cross-tenant data leak | Audit log anomaly / customer report | Immediate DB read-only, isolate tenant | Root cause fix, notify affected, ODPC report |
| LLM spend spike | Daily cap alert / billing anomaly | Disable agent for tenant, investigate prompts | Fix prompt, adjust caps, review logs |
| Daraja credential compromise | Unusual STK patterns / Safaricom alert | Revoke credentials, re-onboard tenant | New credentials, audit all transactions |
| Meta webhook secret leak | Unusual webhook traffic | Rotate secret, verify webhook signatures | Update all tenants, monitor |

---

## Review History

| Date | Reviewer | Changes |
|------|----------|---------|
| 2024-01-15 | Chief Engineer | Initial version |

---

**Next Review**: Before Phase 6 (L2 Transact) launch
**Owner**: Chief Engineer / Security Lead