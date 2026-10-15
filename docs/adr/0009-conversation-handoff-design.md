# ADR 0009: Conversation State Machine and Human Handoff Design

## Status
Accepted

## Context
WhatsApp conversations need structured state management and human escalation for complex queries.

## Decision

### Conversation State Machine
```
OPEN → AWAITING_RESPONSE → HANDOFF_REQUESTED → HANDOFF_ACTIVE → CLOSED
                      ↘→ AWAITING_RESPONSE (return to agent)
```

States:
- `OPEN`: New conversation, no messages yet
- `AWAITING_RESPONSE`: Agent can reply freely (within 24h window)
- `HANDOFF_REQUESTED`: Agent escalated, waiting for owner acceptance
- `HANDOFF_ACTIVE`: Owner accepted, handling conversation
- `CLOSED`: Conversation resolved
- `BLOCKED`: Contact blocked

### Human Handoff Workflow
1. **Trigger**: Agent confidence < threshold, high-risk intent, customer request, or system rule
2. **Request**: Conversation → `HANDOFF_REQUESTED`, SLA set (5 min default)
3. **Notify**: Owner/staff notified via WhatsApp template + in-app
4. **SLA**: 5 minutes default (configurable by priority)
5. **Accept**: Owner accepts → `HANDOFF_ACTIVE`, owner assigned
5. **Complete**: Owner resolves → `CLOSED` or returns to agent `AWAITING_RESPONSE`

### SLA by Priority
- `low`: 30 minutes
- `normal`: 5 minutes (default)
- `high`: 2 minutes
- `urgent`: 1 minute

### Escalation
Overdue handoffs → notify senior owners with "urgent" priority

## Data Model
```python
class WhatsAppConversation:
    state: WhatsAppConversationState
    handoff_requested_at: datetime
    handoff_reason: str
    handoff_assigned_to: UUID  # User ID
    handoff_accepted_at: datetime
    first_response_due_at: datetime  # SLA deadline
```

## Handoff Triggers
- Agent confidence < 0.7
- High-risk intent (fraud, large order, payment issues)
- Explicit customer request ("talk to human")
- System rule (repeat complaints, failed payments)

## Notifications
- WhatsApp template message to owner
- In-app notification (future dashboard)
- Escalation: senior owners notified with "urgent" priority

## Testing
- State transition tests (valid/invalid)
- Handoff trigger tests
- SLA monitoring tests
- Escalation tests
- Cross-tenant isolation

## References
- SPEC §6 Agent Design and Autonomy Policy
- SPEC §5 Non-Negotiable Engineering Principles
- SPEC §13 Reliability and Observability (SLA monitoring)