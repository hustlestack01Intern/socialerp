# PLAN: Phase 5 — Agent L0/L1, policy engine and the LLM gateway

Date: 2026-10-15 · Author: Chief Engineer · Status: **L0/L1 core complete** (gateway, intent,
policy, agent loop, real eval harness); conversation integration + L2 deferred to Phase 6.

## Goal and non-goals
- Goal: a model-agnostic LLM gateway and an agent that can (a) **L0 shadow** — draft replies
  for owner approval — and (b) **L1 answer** — auto-reply to FAQs using **only** catalog/rules
  data, never inventing prices, stock, hours or delivery promises.
- Goal: a policy engine that decides autonomy and always routes high-risk/low-confidence
  actions to a human.
- Goal: a real eval harness so `make eval` asserts something (wrong-price/stock rate must be 0).
- Non-goal: L2 transact (Phase 6), L3 optimize (later), delivery, Instagram, onboarding.
- Non-goal: no live external messaging. Never send to a real customer.

## Classification (taxonomist)
- Type: new subsystem (`packages/agent`) + integration with `packages/conversation`.
- Risk: **high** (money/stock adjacency, prompt injection, tenant isolation).
- Complexity: **high**.

## Decisions already made
- LLM gateway: **local open-weight via Ollama**, behind a model-agnostic interface, so CI and
  evals run without contacting live endpoints (a deterministic `ScriptedGateway` is used in tests).
- The LLM never does arithmetic: it returns structured intent; deterministic code validates
  against the catalog, computes totals and checks stock (AGENTS.md §5).
- `/evals` is a **PROTECTED ZONE** (constitution): eval datasets and the held-out sets require
  explicit human approval. This plan proposes them; it does not self-authorize edits.

## Design
1. **Gateway interface** (`packages/agent/gateway.py`)
   - `LLMGateway` ABC: `complete(request: LLMRequest) -> LLMResponse`.
   - `LLMRequest` (messages, model, temperature, max_tokens, response_schema, timeout, cache_key).
   - `LLMResponse` (text, model, model_version, usage/cost, cached, finish_reason).
   - Per-tenant spend caps, timeouts, retries with backoff, response caching where safe,
     and model+version recorded on every call. Fail safe: on error the caller gets a typed
     error and must hand off (never improvise).
   - Implementations: `ScriptedGateway` (deterministic, for tests/evals) and `OllamaGateway`
     (HTTP `/api/chat`), plus a `RecordingGateway` decorator for traces.
2. **Intent extraction** (`packages/agent/intent.py`): strict pydantic schema for a customer
   message → intent + extracted slots (product, qty, size/color, area). Validated; model output
   is untrusted.
3. **Policy engine** (`packages/agent/policy.py`): maps (autonomy level, intent, confidence)
   → decision (auto / draft / handoff / escalate-human). Refunds, discounts > limit, price
   changes, bulk sends, data deletion are **always** human.
4. **Agent loop** (`packages/agent/agent.py`): answer L1 FAQs from catalog/rules only; otherwise
   draft (L0) or hand off. Every action logged with inputs, tool calls, output, policy decision.
5. **Eval harness** (`evals/`, protected): labeled sets per capability; metrics accuracy,
   hallucination rate, wrong-price/wrong-stock rate (must be 0), handoff precision, cost/conversation.
   Promote prompts/models only if they beat the current version with no safety regression.

## Acceptance criteria (tests + evals)
- Gateway: timeout, retry/backoff, spend-cap breach, cache hit, model+version recorded — unit tests
  with a fake transport. No network in CI.
- Intent: strict-schema validation rejects malformed model output; injection attempt in customer
  text cannot change slots/rules (property/abuse tests).
- Policy: the always-human set; low-confidence → handoff; L0 never auto-sends; L1 only uses data.
- Agent L1: answers price/availability/hours/delivery-area questions from catalog/rules; on an
  unknown item or unavailable data it does **not** guess — it clarifies or hands off.
- Evals: `make eval` runs the sets and fails on wrong-price/stock > 0 or a safety regression.

## Sequencing
1. Plan + ADR 0012/0013 (this doc). ✅
2. Gateway interface + `ScriptedGateway` + `OllamaGateway` + tests. ✅
3. Intent extraction + tests. ✅
4. Policy engine + tests. ✅
5. Agent L0/L1 loop + conversation integration + tests. ✅ (loop done; conversation wiring deferred)
6. Eval harness. ✅ (approved by the owner; runs the real agent, reports Phase-6 assertions as deferred)

## Open questions — resolved
- Q1: Approve editing `/evals` (protected) to build the harness and the first labeled sets?
  **Yes — approved by the owner (2026-10-15).**
