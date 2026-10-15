# ADR 0008: 24h Customer Service Window Tracking

## Status
Accepted

## Context
WhatsApp Cloud API enforces a 24-hour customer service window:
- Free-form messages allowed only within 24 hours of last customer message
- Outside window: only approved template messages allowed
- Under Coexistence mode: ~20 messages/second rate limit

## Decision
Implement a dual-layer window tracker:
1. **Database-persisted**: `whatsapp_contacts` table tracks `last_customer_message_at` and `window_expires_at`
2. **In-memory cache**: 5-minute TTL for fast lookups, survives restarts via DB

## Implementation

### Window Logic
```python
# On customer message:
contact.last_customer_message_at = now
contact.window_expires_at = now + 24 hours

# Check if free-form allowed:
is_open = contact.window_expires_at > datetime.now(timezone.utc)
```

### Rate Limiting
Token bucket algorithm per tenant:
- Capacity: 20 tokens (Coexistence limit)
- Refill rate: 20 tokens/second
- In-memory buckets per tenant

### API
```python
class WhatsAppLimitsManager:
    async def can_send_message(tenant_id, phone, is_template=False) -> (bool, dict)
    async def send_message(tenant_id, phone, is_template) -> None
```

## Persistence Strategy
- `whatsapp_contacts` table: `last_customer_message_at`, `window_expires_at`
- `whatsapp_dedupe` table: `provider_message_id` for idempotency
- In-memory cache with 5-minute TTL, populated from DB on miss

## Clock Skew Handling
- Use server time for all calculations
- Log discrepancies between Meta timestamp and server time
- Allow 30-second grace period for edge cases

## Testing
- Property tests for window boundaries
- Rate limit burst tests
- Clock skew tests
- Cross-tenant isolation tests

## References
- Meta WhatsApp Business API: 24-hour Window
- SPEC §3 Integration Truth Table (WhatsApp rate limits)
- SPEC §5 Non-Negotiable Engineering Principles (idempotency)