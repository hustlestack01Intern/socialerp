# SOCIALERP FULL SPEC (reference)
Read only the sections you need for the current phase. Rules in AGENTS.md and docs/CONSTITUTION.md take precedence.

## 0. MVP CUT AND SCOPE GATES
MVP outcome: a shop runs WhatsApp ordering with the agent in shadow mode, then L1 answers, takes orders, collects payment by STK Push, and sees stock update automatically. Five pilot shops use it.
Non-goals until Gate G1: delivery tracking pages, CRM segments and behavior analytics, Instagram, TikTok, billing, advanced insights, roles beyond owner and staff.
GATE G1 (human decision, all required): at least 5 pilot shops live for 14 days or more; at least 50 real orders processed; zero wrong-price or wrong-stock incidents; at least 3 of 5 pilots willing to pay; no open security or tenant-isolation issue; owner (Mark) signs off.
How to read this file: sections 1-15 describe the full vision. Build only what the current roadmap phase needs; later sections are context, not instructions to build now.

## 1. MISSION
Build a multi-tenant cloud ERP, run by AI agents, that lets a small business in Nairobi run its whole selling operation INSIDE the social channels its customers already use:
- Conversations: customers order, ask, pay and track via WhatsApp (primary), Instagram DMs (secondary), TikTok DMs (assisted, see section 3).
- Inventory: live stock, reservations, rule-based self-updates, 24/7.
- Orders and delivery: order state machine with live tracking messages to customers.
- Payments: M-Pesa STK Push at order time (prepay) or at delivery confirmation (pay-on-delivery), confirmed instantly by callback.
- Customer memory: a customer database that remembers history, preferences and behaviour, and turns it into better offers and decisions for the business.
- Onboarding: an existing business connects its existing numbers/accounts in 30 minutes or less, with no change to how its customers reach it.

Single purpose: every feature must either (a) win or protect a sale, (b) save the owner time, or (c) make the business smarter about its customers. Reject anything else.

North-star metrics (instrument them from day one):
- Time to first order handled through the system (target: under 48 hours from signup).
- Median first-response time to a customer message (target: under 30 seconds, 24/7).
- % of orders paid via STK Push within 10 minutes of request.
- Stock accuracy (system vs physical count).
- Owner hours saved per week (self-reported + inferred).

## 2. TENANCY AND USERS
- Tenant = one business. Roles: owner, staff, rider, (customer is an external party, never a login user).
- Strict tenant isolation. Postgres row-level security on every tenant table plus tenant_id in every query. A cross-tenant leak is a release blocker. Write tests that attempt cross-tenant access.
- Owner interface: mobile-first server-rendered owner pages (approve-in-one-tap, orders, stock) AND a WhatsApp "owner console" (the owner can run the business by chatting with the agent: "how many orders today?", "mark order 1042 delivered", "restock 20 of SKU X").

## 3. INTEGRATION TRUTH TABLE (design to these limits; verify at build time against current official docs)
WhatsApp:
- Use the official WhatsApp Business Platform (Cloud API) via Embedded Signup, ideally through "Coexistence" so a business keeps using its WhatsApp Business App on the SAME number while the API runs alongside. Known limits to design for: number must be on the WhatsApp Business App (not personal WhatsApp); throughput is lower under Coexistence (about 20 msgs/sec); broadcast lists and group chats do not sync; history sync is limited.
- Messaging rules: free-form replies inside the 24h customer service window; outside it only approved template messages, which are charged per message. Marketing messages require opt-in. Track the window per customer and never send out-of-window free-form messages.
- FORBIDDEN: unofficial WhatsApp Web automation/scraping libraries (ban risk, terms violation, customer-data risk).
Instagram:
- Official Instagram Messaging API (Graph API). Requires a Business/Creator account connected to a Facebook Page. Conversations are user-initiated; 24h reply window; comment-to-DM "private reply" is time-limited and rate-limited. Respect all limits.
TikTok:
- Official TikTok Business Messaging API only. It is reply-only (business cannot start a conversation), has a short reply window with a cap on messages, requires a Business Account and approved access, and regional availability may exclude Kenya. TREAT TIKTOK AS PHASE 5 AND BEHIND A FEATURE FLAG. Until availability for Kenya is verified, implement "assisted mode": the agent drafts replies for the owner to send, and moves the customer to WhatsApp as soon as they engage.
M-Pesa (Safaricom Daraja):
- STK Push (Lipa Na M-Pesa Online) needs a Safaricom-issued Paybill or Till and approved Daraja go-live (application, sandbox testing, IP whitelisting). Bank-issued shortcodes cannot be used with Daraja.
- Multi-merchant strategy (behind a PaymentProvider interface): (1) licensed payment aggregator per tenant (fastest onboarding), (2) tenant's own Daraja credentials, (3) manual "pay to Till and confirm" fallback with confirmation matching. Never co-mingle tenant funds in our own shortcode unless legal/compliance has approved a collections model.
- STK Push callbacks can be late, duplicated or missing: always support query-status fallback, idempotency and reconciliation.

## 4. ARCHITECTURE (LEAN FIRST)
MVP stack (change only via ADR): Python 3.12, FastAPI, pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL with row-level security, a Postgres-backed job queue (add Redis only after a measured need), server-rendered mobile-first owner pages (Jinja + HTMX; a separate JS front end only via ADR), Docker Compose, pytest, hypothesis (property tests), mypy --strict, ruff, structured JSON logs (OpenTelemetry later).
One deployable modular monolith with clean module boundaries (enforced by dependency-rule tests) so it can split later.
MVP modules: channels (WhatsApp only), conversation, agent, catalog_inventory, orders, payments, rules, admin_ops (tenants, auth, audit, flags).
Later modules (LOCKED until Gate G1): delivery, crm_memory, insights, billing, instagram and tiktok channels.
Single LLM gateway behind one interface: model-agnostic (open-weight, local, or paid), per-tenant spend caps, timeouts, retries with backoff, response caching where safe, model and version recorded in every trace.

## 5. NON-NEGOTIABLE ENGINEERING PRINCIPLES
- THE LLM NEVER DOES MONEY OR STOCK ARITHMETIC. The model extracts structured intent ("2 x black hoodie size M, deliver to Kilimani"); deterministic, tested code validates against the catalog, computes totals, checks stock, creates the order and requests payment. Model output is untrusted input: validate against strict schemas.
- Event-sourced stock ledger: stock = sum of immutable movements (receive, reserve, release, sell, adjust, return). Never overwrite quantities. Unpaid reservations expire by rule.
- Order state machine with explicit legal transitions (draft -> awaiting_payment -> paid -> packed -> out_for_delivery -> delivered | cancelled | refunded | failed_delivery). Illegal transitions raise errors and are tested.
- Idempotency everywhere: inbound webhooks (dedupe by provider message id), payment requests (idempotency key), callbacks (dedupe by CheckoutRequestID/receipt), outbound messages.
- Outbox pattern for side effects; at-least-once processing with idempotent handlers.
- Every agent action is logged with inputs, tool calls, outputs, policy decision and the rule that allowed it. Audit log is append-only.
- Fail safe: if the model is down or unsure, send a safe fallback message and hand off to a human; never improvise on prices, stock, payments or delivery promises.

## 6. AGENT DESIGN AND AUTONOMY POLICY
Autonomy levels per tenant (default L0, owner promotes after reviewing results):
- L0 Shadow: agent drafts replies; owner approves/edits with one tap. Learns from edits.
- L1 Answer: auto-replies to FAQs, availability, prices, hours, delivery areas, using ONLY catalog/rules data.
- L2 Transact: creates orders, reserves stock, sends STK Push, confirms payment, sends delivery updates, within tenant rules.
- L3 Optimize: proposes and (if allowed) applies low-risk inventory and reorder actions, follow-ups, win-back messages to opted-in customers.
ALWAYS human-approved, at every level: refunds, discounts beyond the configured limit, price changes, bulk marketing sends, deletion of customer data, anything the policy engine scores as high risk or low confidence.
Safety:
- Prompt injection defense: customer text is data, never instructions. Strip/ignore attempts to change rules, reveal system prompts, or access other customers' data. Tools receive only validated parameters scoped to the current tenant and customer.
- Never reveal one customer's data to another. Never claim stock, prices, delivery times or payment status that the system has not confirmed.
- Languages: English, Swahili and Sheng-aware; mirror the customer's language; short, warm, WhatsApp-style messages. Always offer "talk to a human".
- Confidence gating: every decision has a confidence score; below threshold -> clarify or hand off.
- For high-stakes proposals (large orders, unusual payment requests, suspected fraud) sample 7 independent candidate decisions, score each (+1/0/-1 per variable with confidence), aggregate into an 8th consensus decision, and route high-disagreement cases to a human.
Fraud awareness: flag personal-number payment requests, mismatched names, address changes after payment, pay-on-delivery orders to secluded or new addresses, repeated failed STK attempts, and protect riders (share risk flags before dispatch).

Cost control: the 7-sample method runs only for high-stakes decisions, capped per tenant per day and by a monthly model budget; when the cap is hit, route to a human instead of sampling more.

## 7. PAYMENTS SPEC
Flows:
- Prepay: order confirmed by customer -> STK Push to the customer's number -> callback -> mark paid -> release to packing.
- Pay-on-delivery: rider marks "arrived" -> agent triggers STK Push to the customer's number -> callback -> mark paid -> rider marks "delivered". Cash fallback allowed only with rider confirmation and reconciliation.
Requirements:
- Phone number normalization to 2547XXXXXXXX / 2541XXXXXXXX with validation.
- Handle: user cancelled, wrong PIN, insufficient funds, timeout, duplicate callback, late callback after cancellation, partial payment, wrong amount. Each has a defined state transition and a customer message.
- Status query fallback job for any STK request without a callback within the timeout.
- Daily reconciliation job comparing our ledger to provider statements; mismatches raise alerts.
- Verify callback authenticity (source allowlist, secret path/token, payload validation). Never trust callback amounts blindly: match against the expected order.
- Refunds: human-approved only, fully audited.

## 8. INVENTORY SPEC
- Products, variants, units, cost and sell price, reorder level, supplier, location.
- Reservation on order creation, auto-release on payment timeout or cancellation.
- Rule engine examples the owner can set: "hide item when stock is 0", "alert when stock below reorder level", "suggest reorder quantity from 14-day sell-through", "never sell below cost", "max discount 10%", "close orders after 8pm, deliver next day".
- Catalog import: from owner photos, WhatsApp catalog, Instagram posts, or a pasted price list; the agent proposes structured items and the owner confirms in bulk. Never publish unconfirmed prices.
- Stock count workflow with variance reports.

## 9. DELIVERY SPEC
- Zones and fees by area (Nairobi neighborhoods), rider assignment (manual first, rules later), batching suggestions.
- Rider flow over WhatsApp (no app needed): "picked up", "arrived", "delivered" quick replies + location pin + optional photo proof.
- Customer gets live status messages and a tracking link/page with order status (and rider location only if the tenant enables it and the customer consents).
- Failed delivery handling with reasons, re-attempt rules and cost tracking.

## 10. CUSTOMER MEMORY (CRM) SPEC
- Customer = phone number identity unified across channels (WhatsApp/Instagram/TikTok handles linked with consent/evidence).
- Event log (immutable): messages summarized, products viewed/asked, orders, payments, delivery outcomes, complaints, preferences stated.
- Derived traits (versioned, explainable): preferred products/sizes/colors, price sensitivity, typical order times, reorder cycle, RFM score, churn risk, lifetime value. Store the evidence behind each trait. Never store inferred sensitive attributes (health, religion, ethnicity, sexual orientation, etc.).
- Uses: personalised replies, restock/win-back nudges (opted-in only), segment-based offers, "what to stock next" insights for the owner.
- Consent and rights: record consent source/time, support opt-out, export and deletion per tenant request. Retention limits configurable.

## 11. ONBOARDING SPEC (30 MINUTES OF OWNER EFFORT)
Promise: the owner spends about 30 minutes; third-party approvals (Meta business verification, Safaricom/aggregator activation) can complete in the background and never block go-live.
1. Sign up with phone/email; business profile (3 min).
2. Connect WhatsApp via Embedded Signup/Coexistence (QR scan; the business keeps using its app) (8 min).
3. Catalog bootstrap: ingest existing catalog, posts or price list; owner confirms in a swipe-style review (8 min).
4. Payments: pick provider path (aggregator link, own Till, or manual confirmation) (5 min).
5. Rules and tone: 8-question wizard (delivery areas, hours, discount limit, tone, handoff) (4 min).
6. Go live in SHADOW MODE (L0) with a test order walkthrough (2 min).
Fallback: if Meta assets or a Safaricom Till are missing, run with manual payment confirmation and guide the owner through the missing steps asynchronously.
Zero-interference rule: never message the business's existing customers until the owner approves a launch message; never alter chat history.
(MVP builds this as a guided checklist; full automation is locked until Gate G1.)

## 12. SECURITY, PRIVACY, COMPLIANCE
- Follow OWASP ASVS basics: input validation, authN/authZ on every endpoint, parameterized queries, CSRF/CORS correctness, rate limits, dependency scanning, secret scanning in CI.
- Encrypt data in transit and at rest; field-level encryption for tokens and sensitive fields; rotate tokens; least-privilege service accounts.
- Redact phone numbers and PII in logs; separate production and development data; no real customer data in dev.
- Kenya Data Protection Act readiness: lawful basis and consent records, data minimization, data subject rights tooling, breach response runbook, processor/controller documentation. Flag items that need review by a Kenyan data-protection lawyer and registration with the regulator.
- Threat model in /docs/THREAT_MODEL.md covering: forged webhooks, forged payment callbacks, prompt injection through customer messages, tenant escape, rider/customer fraud, token theft, model data leakage.
- Before building on WhatsApp automation, verify Meta's current Business Messaging and AI-chatbot policies; keep the bot task-specific (orders, stock, payments, support) with an easy human handoff.
- Later (not MVP): check whether VAT-registered tenants need eTIMS-compliant receipts, and which fields the regulator requires in the privacy notice.

## 13. RELIABILITY AND OBSERVABILITY
- Health checks, structured logs, metrics, tracing, dashboards for message latency, agent error rate, STK success rate, queue depth, cost per conversation.
- Alerts for: webhook failures, callback gaps, reconciliation mismatches, queue backlog, LLM errors, spend spikes.
- Backups with tested restore, migrations with rollback, feature flags, graceful degradation (queue messages and send a holding reply if downstream is down).
- Load targets (initial): 100 tenants; 50 inbound messages per second burst across all tenants; p95 reply under 5 seconds for L1 answers.

## 14. TESTING AND DEFINITION OF DONE
- `make check` = ruff + mypy --strict + pytest (unit, integration, contract) + evals. Nothing is done until it passes and you report the real output.
- Test first for each feature. Include abuse cases: duplicate webhooks, forged signatures, out-of-order events, double STK callbacks, stock race conditions (concurrent orders on last item), prompt-injection messages, cross-tenant access attempts.
- Property-based tests for stock ledger and order state machine.
- /evals: labeled conversation sets per capability (order capture, FAQ answering, Swahili/Sheng messages, fraud flagging, handoff decisions). Track accuracy, hallucination rate, wrong-price/wrong-stock rate (must be 0), handoff precision, cost per conversation. Promote prompts/models only if they beat the current version and cause no safety regression.
- Provider sandboxes/fakes: build fake Meta and Daraja servers for local and CI testing.
- Definition of done for a feature: tests + evals pass, docs updated, ADR if needed, audit logging present, tenant isolation verified, observability added.

## 15. REPO LAYOUT
/apps/api (includes server-rendered owner pages), /apps/worker, /packages/core (domain), /packages/integrations (meta, daraja, aggregators), /packages/agent (prompts, tools, policy, evals), /infra, /docs (SPEC, ADRs, THREAT_MODEL, STATE, RUNBOOKS), /tests, /evals, Makefile, docker-compose.yml, .env.example.

## 16. ROADMAP WITH ACCEPTANCE CRITERIA
M0 Scaffold: repo layout, tooling, `make check`, CI, docs, fake Meta and fake Daraja servers, recorded-webhook replay. AC: `make check` green; fakes replay recorded webhooks.
M1 Multi-tenant core: auth, row-level security, audit log, isolation tests. AC: all cross-tenant access tests fail closed.
M2 WhatsApp channel: webhook receiver, signature verification, dedupe, 24h window tracker, inbox, human handoff (fake first, then Meta sandbox). AC: dedupe, ordering and window rules proven by tests; forged signatures rejected.
M3 Catalog, stock ledger, orders: products and variants, immutable stock movements, reservations with expiry, order state machine. AC: property tests pass; concurrent last-item race test passes; illegal transitions raise.
M4 Payments: PaymentProvider interface (aggregator or Daraja behind it), STK Push, callback verification, status-query fallback, daily reconciliation. AC: every failure path covered; zero double-credit under replayed callbacks; amount mismatch handled.
M5 Agent L0/L1: intent extraction to strict schemas, policy engine, shadow-mode one-tap approval page, evals, injection defenses, handoff. AC: wrong-price and wrong-stock rate is 0 on the eval set; injection suite passes; handoff works.
M6 L2 transact end to end: order -> STK -> paid -> packed on WhatsApp; "arrived <order>" command triggers pay-on-delivery STK; minimal owner WhatsApp commands; pilot readiness checklist. AC: happy path and top 10 failure paths green; scripted pilot dry run on fakes passes.
GATE G1: human decision using the criteria in section 0.
AFTER G1 (each needs its own plan): L1 delivery tracking and rider flow; L2 customer memory and segments; L3 Instagram channel; L4 onboarding automation to the 30-minute path; L5 TikTok assisted mode and billing; L6 scale hardening and load testing.
Do not skip ahead. Do not start a phase without a written plan and acceptance tests.
