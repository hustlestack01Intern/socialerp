# ADR 0020: Instagram Webhook Simulation in Fake Meta Server

## Status
Proposed

## Context
Phase 9 (Instagram channel) requires deterministic CI testing of Instagram DM webhook handling, comment-to-DM private replies, and Graph API send endpoints. SPEC §14 requires provider fakes with recorded replay capability.

The existing `fake_meta` server (ADR 0003) simulates WhatsApp webhooks. This ADR extends it with Instagram-specific routes, state, and fixtures.

## Decision
Extend `packages/integrations/fake_meta/main.py` with Instagram webhook simulation. Keep single FastAPI app on port 8081 (no port conflict with fake_daraja on 8082).

### Architecture
- **Single FastAPI app** with routes under `/instagram/*` namespace
- **In-memory state** extended with `InstagramState` dataclass
- **Record/replay** works identically to WhatsApp: `POST /admin/record`, `GET /admin/replay/{id}`
- **Signature verification** uses same `X-Hub-Signature-256` HMAC-SHA256 with `app_secret`
- **Rate limiting** shared bucket with WhatsApp (20 req/s per IP)

### New Routes

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/instagram/webhook` | Receive Instagram webhooks (DM, comments, story replies) |
| `POST` | `/instagram/messages` | Send DM (simulates Graph API `/{ig_user_id}/messages`) |
| `POST` | `/{comment_id}/private_replies` | Comment-to-DM private reply (eligibility check) |
| `GET` | `/instagram/oauth/authorize` | Mock OAuth authorization (redirects with code) |
| `POST` | `/instagram/oauth/token` | Mock token exchange (returns access_token) |
| `GET` | `/instagram/accounts` | Mock `GET /me/accounts` for connected pages |
| `GET` | `/instagram/ig-users` | Mock `GET /{page_id}/instagram_accounts` |

### Instagram State (`InstagramState`)
```python
@dataclass
class InstagramState:
    # Comment-to-DM eligibility tracking
    # comment_id -> {"expires_at": float, "attempts_remaining": int, "consumed": bool}
    comment_eligibility: dict[str, dict] = field(default_factory=dict)
    
    # Rate limit for comment-to-DM (200/hour per app per page per Meta docs)
    comment_dm_rate_limit: int = 200
    comment_dm_buckets: dict[str, list[tuple[float, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    
    # Mock OAuth state
    oauth_codes: dict[str, dict] = field(default_factory=dict)  # code -> {client_id, redirect_uri, expires_at}
    access_tokens: dict[str, dict] = field(default_factory=dict)  # token -> {user_id, scopes, expires_at}
    
    # Connected IG accounts for test tenant
    test_ig_accounts: dict[str, dict] = field(default_factory=lambda: {
        "ig_user_123": {
            "id": "ig_user_123",
            "username": "test_shop",
            "account_type": "BUSINESS",
            "media_count": 0,
        }
    })
    
    # Sent messages log for inspection
    sent_messages: list[dict] = field(default_factory=list)
```

### Comment-to-DM Eligibility Simulation
- On receiving comment webhook with `is_eligible_for_private_reply: true`, create eligibility record:
  - `expires_at = now() + 7 days` (Meta: 7-day eligibility window)
  - `attempts_remaining = 1` (Meta: one private reply per comment)
- On `POST /{comment_id}/private_replies`:
  - Check eligibility exists and not expired
  - Check `attempts_remaining > 0`
  - Check rate limit (200/hour per app)
  - On success: decrement attempts, mark consumed, return 200 with message ID
  - On expired: return 400 `{"error": {"code": 200, "message": "Eligibility expired"}}`
  - On consumed: return 400 `{"error": {"code": 200, "message": "Already replied"}}`
  - On rate limit: return 429 with `Retry-After` header (seconds)

### Record/Replay Contract
- Recordings include Instagram webhooks with `type: "instagram_*` prefix
- Replay endpoint returns webhooks in recorded order with original timestamps
- Deterministic: same seed → same webhook sequence (use fixed UUIDs in fixtures)

---

## Required Instagram Webhook JSON Fixtures

### 1. Inbound DM (Text)
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "messaging": [{
      "sender": {"id": "ig_user_456"},
      "recipient": {"id": "ig_user_123"},
      "timestamp": 1700000000,
      "message": {
        "mid": "mid_abc123",
        "text": "Hello, I want to order the blue hoodie"
      }
    }]
  }]
}
```

### 2. Inbound DM (Image)
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "messaging": [{
      "sender": {"id": "ig_user_456"},
      "recipient": {"id": "ig_user_123"},
      "timestamp": 1700000000,
      "message": {
        "mid": "mid_img456",
        "attachments": [{
          "type": "image",
          "payload": {"url": "https://example.com/image.jpg"}
        }]
      }
    }]
  }]
}
```

### 3. Inbound DM (Video)
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "messaging": [{
      "sender": {"id": "ig_user_456"},
      "recipient": {"id": "ig_user_123"},
      "timestamp": 1700000000,
      "message": {
        "mid": "mid_vid789",
        "attachments": [{
          "type": "video",
          "payload": {"url": "https://example.com/video.mp4"}
        }]
      }
    }]
  }]
}
```

### 4. Inbound DM (Quick Reply Button)
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "messaging": [{
      "sender": {"id": "ig_user_456"},
      "recipient": {"id": "ig_user_123"},
      "timestamp": 1700000000,
      "message": {
        "mid": "mid_qr111",
        "text": "Yes, size M please",
        "quick_reply": {"payload": "ORDER_HOODIE_M_BLUE"}
      }
    }]
  }]
}
```

### 5. Comment on Post (Eligible for Private Reply)
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "changes": [{
      "field": "comments",
      "value": {
        "comment_id": "comment_abc123",
        "text": "How much is the hoodie?",
        "from": {"id": "ig_user_456", "username": "customer1"},
        "created_time": "2026-10-18T10:00:00+0000",
        "is_eligible_for_private_reply": true,
        "media": {"id": "media_123", "media_type": "IMAGE"}
      }
    }]
  }]
}
```

### 6. Comment on Reel
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "changes": [{
      "field": "comments",
      "value": {
        "comment_id": "comment_reel_456",
        "text": "Nice reel! Price?",
        "from": {"id": "ig_user_789", "username": "customer2"},
        "created_time": "2026-10-18T10:05:00+0000",
        "is_eligible_for_private_reply": true,
        "media": {"id": "media_reel_123", "media_type": "REEL"}
      }
    }]
  }]
}
```

### 7. Comment on Story (Story Reply)
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "messaging": [{
      "sender": {"id": "ig_user_456"},
      "recipient": {"id": "ig_user_123"},
      "timestamp": 1700000000,
      "message": {
        "mid": "mid_story_111",
        "text": "Interested in the black one",
        "is_echo": false,
        "story": {"id": "story_123", "url": "https://example.com/story.jpg"}
      }
    }]
  }]
}
```

### 8. Comment on Ad
```json
{
  "object": "instagram",
  "entry": [{
    "id": "ig_user_123",
    "time": 1700000000,
    "changes": [{
      "field": "comments",
      "value": {
        "comment_id": "comment_ad_789",
        "text": "Is this available in Nairobi?",
        "from": {"id": "ig_user_999", "username": "customer3"},
        "created_time": "2026-10-18T10:10:00+0000",
        "is_eligible_for_private_reply": true,
        "media": {"id": "media_ad_123", "media_type": "IMAGE"},
        "ad_id": "ad_123"
      }
    }]
  }]
}
```

### 9. Eligibility Expired (Simulated via Private Reply Attempt)
No separate webhook. Eligency expiry is detected when attempting private reply:
```json
{
  "error": {
    "code": 200,
    "message": "Eligibility expired. Comment is older than 7 days.",
    "type": "OAuthException"
  }
}
```

---

## Signature Verification
- All Instagram webhooks must include `X-Hub-Signature-256: sha256=<hmac>`
- Fake verifies using `state.app_secret` (same as WhatsApp)
- Test must compute signature: `hmac_sha256(app_secret, payload_bytes)`

---

## E2E Test Script Outline (< 30 min wall-clock)

### Test: `tests/e2e/test_onboarding_instagram.py`
```python
"""
Scripted onboarding: tenant creation → IG connect → catalog → payments → rules → shadow order
Target: < 30 minutes wall-clock on CI (fake servers only)
"""

async def test_full_onboarding_flow(fake_meta, fake_daraja, api_client):
    # 1. Create tenant (POST /api/v1/admin/tenants)
    tenant = await api_client.post("/admin/tenants", json={...})
    
    # 2. Step 1: Business profile (3 min)
    await api_client.post(f"/tenants/{tenant.id}/profile", json={...})
    
    # 3. Step 2: Connect Instagram via fake OAuth (8 min)
    #    - Redirect to /instagram/oauth/authorize
    #    - Receive code, exchange at /instagram/oauth/token
    #    - Store access_token, get ig_user_id from /instagram/ig-users
    oauth_code = await fake_meta.get_oauth_code(tenant.id)
    token = await fake_meta.exchange_code(oauth_code)
    ig_account = await fake_meta.get_ig_accounts(token)
    
    # 4. Step 3: Catalog bootstrap (8 min)
    #    - POST /catalog/import with sample items
    #    - Owner confirms via PATCH /catalog/items/{id}/confirm
    await api_client.post(f"/tenants/{tenant.id}/catalog/import", json=[...])
    await api_client.patch(f"/tenants/{tenant.id}/catalog/items/bulk-confirm", json={...})
    
    # 5. Step 4: Payments (5 min)
    #    - Select provider: manual fallback (no Daraja creds needed)
    await api_client.post(f"/tenants/{tenant.id}/payments/provider", json={
        "provider": "manual_fallback",
        "config": {}
    })
    
    # 6. Step 5: Rules & tone (4 min)
    await api_client.post(f"/tenants/{tenant.id}/rules", json={
        "delivery_areas": ["Kilimani", "Westlands"],
        "hours": "9am-8pm",
        "max_discount_pct": 10,
        "tone": "warm_swahili"
    })
    
    # 7. Step 6: Shadow test order via fake IG DM (2 min)
    #    - Fake Meta sends inbound DM webhook to our /api/v1/webhooks/instagram
    #    - Agent (L0) drafts reply
    #    - Owner approves via console
    #    - Order created, STK Push simulated via fake Daraja
    #    - Callback received, order marked paid
    await fake_meta.send_webhook("instagram_dm_text", {
        "sender_id": "ig_user_456",
        "text": "I want 1 black hoodie size M, deliver to Kilimani"
    })
    
    # Assert: order created, payment requested, callback processed
    order = await wait_for_order(tenant.id, timeout=10)
    assert order.status == "paid"
    
    # Total elapsed < 1800 seconds (30 min)
    assert elapsed < 1800
```

### Required Fake Endpoints for E2E
| Endpoint | Used By |
|----------|---------|
| `GET /instagram/oauth/authorize` | Onboarding step 2 |
| `POST /instagram/oauth/token` | Onboarding step 2 |
| `GET /instagram/accounts` | Onboarding step 2 |
| `GET /instagram/ig-users` | Onboarding step 2 |
| `POST /instagram/webhook` | Shadow test order (step 6) |
| `POST /instagram/messages` | Agent sends reply |
| `POST /{comment_id}/private_replies` | Comment-to-DM test |
| `POST /admin/record` + `GET /admin/replay/{id}` | Record/replay for regression |

---

## Integration with Fake Daraja
- Both run in docker-compose with `--profile fake`
- Ports: fake_meta=8081, fake_daraja=8082 (no conflict)
- Shared network: `socialerp_fake`
- E2E tests start both via `docker-compose -f docker-compose.yml --profile fake up -d`

---

## Acceptance Criteria
1. All 9 webhook fixtures above accepted by `POST /instagram/webhook` with valid signature
2. `POST /{comment_id}/private_replies` enforces eligibility, rate limit (429 + Retry-After), expiry
3. OAuth flow completes: authorize → code → token → accounts → ig-users
4. `POST /instagram/messages` sends DM, logs to message_log, respects rate limit
5. Record/replay captures Instagram webhooks with deterministic IDs
6. E2E test `test_onboarding_instagram.py` passes in < 30 min on CI
7. `make check` passes (ruff, mypy --strict, pytest including new Instagram tests)

---

## Implementation Plan
1. Add `InstagramState` dataclass and merge into `FakeMetaState`
2. Add Instagram routes under `/instagram/*` namespace
3. Add comment-to-DM eligibility logic with 7-day TTL and 1-attempt limit
4. Add OAuth mock endpoints
5. Add Instagram webhook fixtures as test data in `tests/fixtures/instagram_webhooks.py`
6. Write unit tests for each webhook shape, eligibility, rate limit
7. Write E2E test `tests/e2e/test_onboarding_instagram.py`
8. Update docker-compose to expose both fake servers
9. Add drift detection: contract test comparing fake shapes to Meta Graph API docs (Phase 10)

---

## Open Questions
1. **Media URL handling**: Fake should return mock CDN URLs or accept any URL?
   - Decision: Accept any URL in inbound; return mock `https://fake-meta.local/media/{mid}` for outbound
2. **Story reply webhook shape**: Meta docs show story replies via `messaging` not `changes`. Confirm fixture matches.
3. **Ad comment `ad_id` field**: Include in fixture for correlation; fake doesn't validate ad existence.
4. **Webhook deduplication**: Fake should dedupe by `mid`/`comment_id` like real Meta? Yes, for replay fidelity.
5. **OAuth scope validation**: Fake should validate requested scopes against allowed list? Minimal: accept any, return fixed token.

---

## References
- SPEC.md §3 Integration Truth Table (Instagram)
- SPEC.md §11 Onboarding Spec (30-minute promise)
- SPEC.md §14 Testing (provider fakes, recorded replay)
- ADR 0003 Fake Servers (base architecture)
- Meta Instagram Messaging API docs
- Meta Graph API OAuth docs