# ADR 0003: Agent Autonomy Levels

**Status**: Accepted
**Date**: 2024-01-15
**Deciders**: Chief Engineer

---

## Context

The AI agent operates at different autonomy levels per tenant. Owners promote levels after reviewing results. We need clear boundaries for what the agent can do at each level, with safety guarantees.

---

## Decision

Four autonomy levels (L0-L3) with explicit capabilities and **always human-approved** actions.

---

## Autonomy Levels

| Level | Name | Capabilities | Human Approval Required |
|-------|------|--------------|------------------------|
| **L0** | Shadow | Draft replies only; owner approves/edits with one tap | **Every reply** |
| **L1** | Answer | Auto-reply to FAQs: prices, stock, hours, delivery areas, policies | None (within catalog/rules data) |
| **L2** | Transact | Create orders, reserve stock, send STK Push, confirm payment, dispatch delivery | Refunds, discounts > limit, price changes, bulk marketing |
| **L3** | Optimize | Propose/apply reorder, win-back messages (opted-in), inventory adjustments | All L2 + any policy-scored high-risk |

---

## Always Human-Approved (All Levels)

- Refunds (any amount)
- Discounts beyond configured limit
- Price changes
- Bulk marketing sends
- Customer data deletion
- Any action policy engine scores **high risk** or **low confidence** (< 0.8)

---

## Safety Architecture

### Prompt Injection Defense
```
Customer Message → Sanitizer → Intent Extractor → Structured Intent
                                    ↓
                            Policy Engine → Tool Validator → Deterministic Executor
                                    ↓
                            Confidence Scorer → Handoff if < threshold
```

- Customer text is **data, never instructions**
- Strip/ignore attempts to change rules, reveal prompts, access other data
- Tools receive **only validated parameters** scoped to current tenant + customer

### Confidence Gating
- Every decision has confidence score (0.0-1.0)
- Below threshold → clarify question or handoff to human
- Thresholds: L1=0.9, L2=0.95, L3=0.85

### High-Stakes Consensus (7+1 Method)
For: large orders (>100k KSH), unusual payments, suspected fraud
1. Sample 7 independent candidate decisions
2. Score each on variables (+1/0/-1 with confidence)
3. Aggregate into 8th consensus decision
4. High disagreement → human review

---

## Agent Action Logging (Audit)

Every agent action logged with:
```python
await log_agent_action(
    action="order.create",
    payload={"items": [...], "total": 5000, "customer": "2547..."},
    agent_run_id="run_abc123",
    decision="allowed",
    rule_ref="policy:max_order_value:50000",
    confidence=0.97,
    correlation_id="conv_xyz",
)
```

---

## Consequences

### Positive
- Clear escalation path for owners
- Safety by default (L0 is safe)
- Audit trail for every decision
- Measurable progression (shadow → answer → transact → optimize)

### Negative
- Complexity in policy engine implementation
- Need evaluation harness per level
- Owner education required for promotion

---

## Evaluation Criteria for Promotion

| Metric | L0→L1 | L1→L2 | L2→L3 |
|--------|-------|-------|-------|
| Wrong price/stock rate | 0% | 0% | 0% |
| Hallucination rate | <1% | <0.5% | <0.1% |
| Handoff precision | >95% | >98% | >99% |
| Owner edit rate | <10% | <5% | <2% |
| Cost per conversation | <$0.10 | <$0.05 | <$0.03 |

---

## Related
- ADR 0001: Stack (LLM Gateway)
- ADR 0004: Payment Provider (L2 uses STK Push)