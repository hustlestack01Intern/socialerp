# Security Policy

SocialERP handles tenant data, customer conversations, order and payment state. We take
security reports seriously and we try to be unusually honest about what is and is not yet
enforced.

---

## Reporting a vulnerability

**Do not open a public issue with exploit details.**

Use GitHub's private vulnerability reporting (the **Security** tab of this repository), or
contact the maintainer directly through the email on the GitHub profile.

Please include:

- a description of the issue and its impact;
- reproduction steps or a proof of concept (use synthetic data);
- the affected commit or version;
- any suggested fix.

We will acknowledge the report, investigate, and keep you informed. Please give us reasonable
time to fix the issue before public disclosure.

---

## Scope

In scope:

- the API, worker and dashboard in this repository;
- webhook signature verification and payment callback handling;
- tenant isolation and authorization;
- authentication, sessions and token handling;
- data export and erasure;
- prompt-injection and agent tool boundaries.

Out of scope:

- the security of third-party providers (Meta, Safaricom Daraja, aggregators, LLM hosts);
- issues that require a compromised developer machine or leaked production credentials;
- social engineering of the maintainer;
- reports that rely on real customer data (never use real data in a report).

---

## Known, open security work

This is a pilot candidate. The following gaps are **known, documented, and tracked**. They
are listed here so nobody mistakes a design intent for a deployed control. See the linked
ledger rows for evidence.

| Area | Status | Tracking |
|---|---|---|
| Database-enforced tenant isolation | Policies and tenant-scoped queries exist, but the runtime role owns the tables and RLS is not `FORCE`d. Enforcement is milestone M1. | U76, U77, U82 |
| Subscription payment verification | A caller-supplied M-Pesa reference is recorded; it is not provider-confirmed funds. Provider verification / operator approval is M3. | F2/M3 in the pilot plan |
| Access token storage | The short-lived access token remains in browser `localStorage`; the refresh token is now `HttpOnly` and out of the response body. | U79 |
| Production deployment | No production deployment profile, restore drill or incident rehearsal is evidence-backed yet. | M4 |
| Data-protection classification | Retention, ODPC registration duty and DPIA applicability await Kenyan counsel review. | M4 |

The full threat model is [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md). The live ledger is
[`docs/UNCERTAINTY.md`](docs/UNCERTAINTY.md).

---

## Design controls in place

- Webhook signature verification for Meta; token-authenticated, source-allowlisted callbacks
  for Daraja and aggregators.
- Idempotent, amount-matched payment callbacks with a status-query fallback and reconciliation.
- Tenant-scoped services and queries; cross-tenant attempts tested to fail closed at the
  application layer (database enforcement is M1).
- Signed, expiring, tenant-and-order-bound tracking tokens; URL redaction in traces.
- Default-deny consent; owner-approved, audited erasure; append-only audit log.
- Prompt-injection detection and tenant-scoped tool parameters; the model never performs
  money or stock arithmetic.
- No secrets in the repository; only `.env.example` is tracked. Secrets belong in managed
  secret storage in any real deployment.
- PII redaction in logs and traces.

---

## Safe harbour

We will not pursue legal action against researchers who:

- act in good faith and avoid privacy violations, data destruction, and service disruption;
- use synthetic or their own test accounts;
- report privately and give us time to remediate.

Thank you for helping keep merchants and their customers safe.
