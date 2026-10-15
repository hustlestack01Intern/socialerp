# ADR 0012: Agent LLM gateway, autonomy and the policy engine (Phase 5)

- Status: Accepted
- Date: 2026-10-15
- Phase: 5 (agent L0/L1)
- Relates to: ADR `0004-payment-provider-interface` (same "one interface, many providers"
  pattern), AGENTS.md §5 (LLM never does money/stock arithmetic) and §6 (autonomy policy).

## Context

The agent must answer and draft customer messages on WhatsApp while being safe around money,
stock and tenant isolation. We need one model-agnostic gateway (so we can move between an
open-weight local model, a hosted model or a paid one), deterministic policy that decides how
much autonomy the agent has, and protections against prompt injection and hallucinated
prices/stock. Evals must run in CI without contacting live endpoints.

## Decisions

1. **One gateway interface, model-agnostic.** `packages.agent.gateway.LLMGateway` exposes
   `complete(LLMRequest) -> LLMResponse`. `LLMRequest` carries messages, model, temperature,
   max tokens, an optional JSON `response_schema`, a timeout and a `cache_key`. `LLMResponse`
   carries text, the resolved `model` **and** `model_version`, token usage/cost, an
   `cached` flag and `finish_reason`. Every call records model+version for traces.

2. **Ollama is the default backend; `ScriptedGateway` is the test/CI backend.**
   `OllamaGateway` talks to the local Ollama `/api/chat` endpoint (open-weight models).
   `ScriptedGateway` is deterministic and makes no network calls, so unit tests and evals are
   hermetic. The gateway is the only code that knows a provider's wire format.

3. **Cross-cutting wrappers, not per-call code.** `SpendCapGateway` enforces per-tenant and
   per-request caps (raising `SpendCapError`), `RetryingGateway` adds bounded retries with
   backoff, and response caching (by `cache_key`) is applied where safe (deterministic prompts).
   Composition keeps each concern testable.

4. **Fail safe.** A gateway error (timeout, provider down, cap) is a typed error
   (`LLMError`/`LLMTimeoutError`/`SpendCapError`); the agent must **not** improvise. It sends a
   safe fallback message and hands off to a human. Never claim a price, stock level, delivery
   time or payment status the system has not confirmed.

5. **Model output is untrusted input.** Structured outputs are parsed against strict pydantic
   schemas with `extra="forbid"`; a validation failure is a hard error, not a best-effort parse.
   Customer text is data, never instructions — the prompt-injection defense keeps rules/system
   prompts out of the model's control and scopes tools to the current tenant and customer.

6. **Policy decides autonomy; the always-human set is fixed.** `PolicyEngine` maps
   (autonomy level, intent, confidence) to a decision: `auto`, `draft`, `handoff`, `escalate`.
   Default level is **L0 (shadow)**: the agent drafts, a human approves with one tap; nothing is
   auto-sent. **L1 (answer)** auto-replies to FAQs using only catalog/rules data. Refunds,
   discounts beyond the configured limit, price changes, bulk marketing sends, customer-data
   deletion and anything scored high-risk/low-confidence are **always** human-approved at every
   level.

7. **Grounding rule for L1.** L1 answers may use only catalog and rules data fetched by tools
   scoped to the tenant. If the needed data is absent, stale or ambiguous, the agent clarifies or
   hands off — it does not guess. The eval gate fails on any wrong-price/wrong-stock answer.

8. **`/evals` is a protected zone.** Eval datasets and held-out sets require explicit human
   approval; the codebase does not self-authorize changes there.

## Consequences

- Swapping model/provider is a config change, not a refactor; tests stay offline.
- Spend is bounded and observable; a runaway loop fails closed to a human.
- Safety properties (no invented prices/stock, no auto-send at L0, always-human set) are
  testable and gate releases, matching AGENTS.md §5/§6.
