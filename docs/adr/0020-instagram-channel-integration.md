# ADR 0020: Instagram Channel Integration

## Status
Proposed

## Context
Phase 9 adds Instagram Messaging API (Graph API) as a second channel alongside WhatsApp. Requirements:
- Same normalized pipeline (outbox → worker → agent) — ADR 0010/0014
- User-initiated conversations only, 24h reply window
- Comment-to-DM "private reply" with time/rate limits
- Tenant isolation, feature flag, zero-interference
- Fake Meta server for CI replay

Specialist input from:
- Architect: shared webhook entry, generic window tracker, new eligibility model
- Security/Compliance: API available in Kenya, 7-day comment window, 1 reply/comment, DPA consent for linking
- Integrations: webhook shapes, thread identity, private reply endpoint, signature verification
- Product/Legal: explicit consent for cross-channel linking, evidence artifacts, joint controller agreement

## Decision

### 1. Webhook Entry & Normalization
- `packages/integrations/meta/webhook.py` — shared HTTP entry point (router, HMAC-SHA256 verification, channel detection)
- `packages/integrations/meta/instagram_parser.py` — NEW: Instagram-specific parsing → normalized `MessageReceived`
- Normalized event: same `MessageReceived` family, `channel="instagram"`, `provider_message_id=message.mid`

### 2. Window Tracking
- Generic `WindowTracker[ChannelIdentity]` protocol
- `WhatsAppIdentity(phone: CustomerPhone)` + `InstagramIdentity(thread_id: str, ig_handle: str)`
- New model: `InstagramContact` (tenant_id, thread_id, ig_handle, name, profile_pic, window_expires_at, last_customer_message_at, window_state)
- RLS: tenant_id

### 3. Comment-to-DM Eligibility
- New table: `instagram_dm_eligibility` (tenant_id, contact_id, comment_id, media_id, thread_id, expires_at, attempts_remaining, consumed_at, consumed_by_message_id)
- RLS: tenant_id
- Unique: (tenant_id, comment_id)
- Enforcement: `instagram_sender.py` → `send_private_reply()` checks eligibility before API call

### 4. Sender & Policy
- `packages/integrations/meta/instagram_sender.py` — NEW: Instagram outbound
- 24h window: same as WhatsApp but NO human agent tag (Instagram doesn't support 7-day extension)
- Templates: Marketing + Utility only (no Authentication)
- Feature flag: `tenant.settings.get("instagram_enabled", False) and tenant.settings.get("launch_approved", False)`

### 5. Cross-Channel Identity Linking
- New model: `CustomerIdentity` in `packages/core/models/crm.py` (tenant_id, customer_id, channel, channel_identity, channel_identity_type, consent_given_at, consent_source, evidence_json, is_primary)
- New service: `IdentityLinkingService` in `packages/core/crm/identity_linking.py`
- Consent evidence schema per Product/Legal specialist

### 6. Fake Meta Server
- Extend `packages/integrations/fake_meta/main.py` with Instagram routes
- 9 webhook fixtures, comment-to-DM eligibility state, rate-limit 429 + Retry-After, OAuth mock

## Consequences

### Positive
- Reuses 80% of WhatsApp pipeline (normalization, window, sender pattern, outbox, agent)
- Single pipeline, no duplication
- Tenant isolation by design (RLS on all new tables)
- Compliant with Kenya DPA (explicit consent, evidence, DPIA, ODPC registration)

### Negative
- Two ADRs needed (this + onboarding wizard)
- Joint controller agreement required with tenants (legal blocker for launch)
- Comment-to-DM eligibility adds stateful complexity
- Instagram API rate limits (750/hr) require monitoring

### Risks
- API availability in Kenya: confirmed available but business verification 7-21 days
- Cross-channel linking requires legal sign-off (joint controller agreement)
- Assisted mode fallback: legally compliant but still processes thread_ids (DPA applies)

## Implementation Order (8 Commits)
1. Instagram types + normalization (`instagram_parser.py`, tests)
2. Window + eligibility tracking (`InstagramIdentity`, `InstagramContact`, `instagram_dm_eligibility`, `WindowTracker[InstagramIdentity]`, tests)
3. Sender + policy (`instagram_sender.py`, feature flag, 24h window, template enforcement, tests)
4. Webhook integration + fake server (`webhook.py` channel dispatch, `fake_meta` Instagram routes + fixtures, contract tests)
5. Channel enablement + isolation (feature flag, RLS policies, cross-tenant tests)
6. Onboarding wizard schema + timers (`packages/core/onboarding/`, `CustomerIdentity`, `IdentityLinkingService`, tests)
7. API surface for wizard (endpoints, authZ, zero-interference gate, tests)
8. E2E + eval hooks (scripted onboarding < 30 min, Instagram E2E, ADRs, docs)

## Related ADRs
- ADR 0010: Channel abstraction
- ADR 0014: Agent conversation pipeline
- ADR 0021: Onboarding wizard instrumentation
- ADR 0017: CRM memory (consent, erasure scope)

## Compliance Notes
- Kenya DPA: Explicit consent per customer, per purpose, granular scope
- Evidence: OAuth token hash, message hash, timestamp, IP hash, consent text version
- Retention: Business relationship + 5 years tax; evidence 5 years post-withdrawal
- ODPC: SocialERP registers as processor; tenants as controllers
- DPIA: Required for cross-channel linking (systematic monitoring + large scale)

## Next Steps
1. Legal review of joint controller agreement template
2. ODPC registration confirmation
3. Begin Commit 1: Instagram parser + normalization tests