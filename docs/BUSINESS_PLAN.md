# SocialERP — Business Plan v1.0

> **Date**: 2026-10-19 | **Status**: Working plan for first-client launch | **Currency**: KES
> Companion docs: `PRICING_STRATEGY.md` (price book), `SHIPPING_GUIDE.md` (technical),
> `CLIENT_ONBOARDING_CHECKLIST.md` (delivery), `plans/2026-10-19-trial-spine-first-clients.md`
> (engineering plan), `SPEC.md` (product spec).
> External figures cite their source; blog-grade sources are marked ⚠ lower confidence.

---

## 1. EXECUTIVE SUMMARY

**What we sell**: SocialERP is an AI-run operating system for Kenyan social-media
sellers. A shop that sells through WhatsApp and Instagram gets a 24/7 agent that
answers customers, captures orders, reserves stock, pushes M-Pesa STK payments,
coordinates riders and remembers every customer — all inside the chat app the
seller already lives in. No website, no POS, no IT staff.

**Why now**: Kenya's commerce has already moved to chat. The Communications
Authority of Kenya (Customer Satisfaction Survey, Jul 2024–Jun 2025) reports
mobile apps lead online orders at **44.8%**, with **WhatsApp at 20.2%** — the
largest *seller-controllable* channel — while websites have fallen to **12%**;
**71.3%** of shoppers buy on smartphones and **65.7%** pay by mobile money.
Statista puts 2026 Kenyan social-commerce revenue at **~US$1.6B growing ~24%
CAGR**. WhatsApp effectively *is* the storefront for micro and informal
businesses (CA, verbatim) — yet the seller's operations (stock, orders, delivery,
follow-ups) still run on memory, notebooks and manual chat.

**Why us**: A working multi-tenant ERP with the hard parts already built
(Phases 0–9 merged: WhatsApp + Instagram channels, event-sourced stock ledger,
order state machine, M-Pesa STK with callback/reconciliation, pay-on-delivery +
rider WhatsApp flow, CRM memory, owner insights, owner WhatsApp console,
30-minute onboarding wizard) — not a chatbot demo. Gate status, stated honestly:
the suite measured **764 tests green on 18 Oct 2026**; the later Phase 9 commits
carry lint/test debt and **re-verifying the full gate is the first engineering
milestone** (M0) before "green" is claimed again. Eval invariant to date: **zero
wrong-price/wrong-stock hallucinations** on the labeled eval set (re-measured in
M0).

**The motion**: 14-day free trial (Professional-tier agent features, proposed —
pending owner confirmation, U60) → proven value via a day-7/day-11 value report
→ conversion to a monthly subscription (Starter **KES 2,999** / Professional
**KES 7,999** / Enterprise **KES 19,999**); during the pilot, the first month is
collected by **manual M-Pesa + founder-confirmed plan change** (audited) because
our own merchant STK account is an external application — automatic subscription
STK comes after that approval. Small capped transaction fees only accrue when
the merchant earns.

**The ask of this plan**: approve the engineering "trial spine" plan, start the
Meta + Safaricom external applications now (they are the real critical path),
and run the first 10 clients as a white-glove concierge pilot.

---

## 2. THE PROBLEM

A Nairobi seller with 100–5,000 WhatsApp/Instagram followers:

1. **Loses sales in the inbox.** Customer messages arrive faster than one human
   can answer; expectation has compressed to **<5 minutes** (2026) from 30 minutes
   (2023)⚠. Missed or slow replies = lost orders. North-star target we attack:
   median first response **<30s, 24/7**.
2. **Cannot run operations from a phone.** Stock is tracked in heads/notebooks →
   overselling, dead stock, no reorder signal. Orders live in chat scrolls →
   forgotten orders, no state, no accountability.
3. **Payments are manual and risky.** "Send to my Till, I'll confirm" invites
   disputes and fake-confirmation fraud; STK Push exists but most sellers can't
   integrate Daraja.
4. **Delivery is chaos.** Riders coordinated by SMS/DM, no proof of delivery,
   customers calling "where is my order?" (each call = one more lost minute).
5. **No customer memory.** No one knows who bought what, who churned, who deserves
   a win-back — so every sale starts from zero.
6. **Tool fatigue and trust debt.** Previous "systems" failed them; anything that
   looks like an enterprise dashboard is dead on arrival. The tool must live
   where they already work: WhatsApp.

**Who feels it**: micro and small merchants (1–10 staff) in Nairobi and
 satellite towns — fashion/beauty, electronics/phone accessories, food & grocery,
 homeware, supplements — selling via WhatsApp Status, Instagram Reels and TikTok,
 typically KES 50K–2M monthly revenue, 50–500 orders/month.

---

## 3. SOLUTION & PRODUCT STATUS

### 3.1 What the product does (code merged; full-gate re-verification = M0)
> **Honesty note**: every "✅ merged" row below was developed and tested during
> Phases 0–9, but the **full `make check` has not been re-run green at current
> HEAD** (measured 2026-10-19: 34 lint errors, contract suite failing — see
> `failures/2026-10-19-stale-green-gate.md`). M0 restores a measured-green gate
> before any external use of this table. Do not show this table to a prospect
> until M0.5 is green.
| Capability | Status |
|---|---|
| WhatsApp channel: webhooks, signature verification, 24h window, inbox, human handoff | ✅ merged (fake-server tested) |
| Instagram Messaging channel (24h window, comment-to-DM eligibility, cross-tenant isolation) | ✅ merged (Phase 9) |
| Agent autonomy L0 (shadow) → L2 (transact), policy engine, prompt-injection defense, eval harness | ✅ merged |
| Catalog + **event-sourced stock ledger** + reservations with TTL + concurrent-race safety | ✅ merged |
| Order state machine (illegal transitions tested), integer-cent money, catalog-price authority | ✅ merged |
| M-Pesa: Daraja STK + aggregator + manual pay-to-Till providers, callbacks, status-query fallback, reconciliation, human-approved refunds | ✅ merged |
| Pay-on-delivery: rider WhatsApp quick replies, POD STK trigger, proof-of-delivery gates | ✅ merged |
| Public order tracking (PII-free, signed expiring tokens) | ✅ merged |
| CRM memory: immutable events, consent, versioned evidence-backed traits, DSR export/erasure (Kenya DPA tooling) | ✅ merged |
| Segments + deterministic owner insights (daily/weekly reports, anomalies, stock alerts) | ✅ merged |
| Owner WhatsApp console ("how many orders today?", "mark delivered", "restock") | ✅ merged |
| 30-minute instrumented onboarding wizard with zero-interference launch gate | ✅ merged (Phase 9) |
| Multi-tenant isolation: Postgres RLS, cross-tenant tests, append-only audit log | ✅ merged |
| **Trial/billing (subscriptions, entitlements)** | 🔨 next (engineering plan M1) |
| TikTok assisted mode, load/fairness program, Stripe | ⏳ deferred (Phase 10 slices E/F + Stripe) |

### 3.2 The gap we're closing now
Working tree had drifted (lint/test debt, stale Makefile) and **no billing code
exists** — the trial→subscription motion is the single engineering blocker to
first revenue. See `plans/2026-10-19-trial-spine-first-clients.md`.

---

## 4. MARKET OPPORTUNITY

### 4.1 Macro numbers (cited)
| Fact | Source | Confidence |
|---|---|---|
| Kenya e-commerce market US$2.31B (2024) → **US$3.83B by 2029** (~10% CAGR) | Research and Markets, Kenya B2C Ecommerce Databook Q4 2025 (pub. Jan 2026) | high |
| Social-commerce revenue in Kenya **~US$1.6B in 2026, ~24% CAGR to 2028** | Statista via Digital 2026 Kenya coverage | medium |
| WhatsApp = **20.2%** of online orders; websites = 12%; mobile apps = 44.8% | **Communications Authority of Kenya**, Customer Satisfaction Survey Jul 2024–Jun 2025 | high (regulator) |
| **71.3%** of shoppers use smartphones; **65.7%** checkout with mobile money; COD 13% | CA of Kenya, same survey | high |
| **~33M Meta users** in Kenya; **>60% engage in social commerce** | Datareportal, cited in Bumpa Kenya launch press (Jun 2026) — secondhand, competitor PR | ⚠ medium-low |
| WhatsApp adoption **>96% of online adults**; 28M+ internet users | DataReportal Digital 2026 Kenya | medium |
| **78%** of Kenyan smartphone shoppers bought via WhatsApp in last 12 months | GeoPoll 2026 mobile commerce study | medium |
| M-Pesa STK Push = default checkout for **71%** of social-commerce transactions (54% in 2024) | Industry coverage, 2026 | ⚠ medium-low |
| Mobile money **KES 699.6B in Jan 2026** alone (~KES 8.5T/yr run-rate) | Central Bank of Kenya | high |
| **91%** of Kenyan SMEs already use digital payments; 97% want simpler payment methods | Mastercard SME Confidence Index | medium-high |
| 85% of consumers prefer messaging brands before purchase; messaging customers show **60%+ higher CLV** | CustomersAI (via Bumpa release) | ⚠ medium-low |
| TikTok Shop opened to Kenyan sellers late 2025, live-shopping growth in 2026 | Trade press | medium |

### 4.2 Sizing (order-of-magnitude, to be replaced by pilot funnel data)
- **SAM (Year 1–2)**: Nairobi-metro social sellers doing ≥50 orders/month with a
  WhatsApp Business presence. Merchant count is a **guess** (30,000–60,000) — no
  reliable public count exists and the multiplication below inherits that
  uncertainty; the pilot will replace it with a measured funnel.
  Arithmetic: 30,000–60,000 merchants × ARPU KES 14,000/mo × 12 = **≈ KES 5–10B/yr**.
- **SOM (Year 1)**: 200 tenants. From the corrected revenue table (§12):
  subscription KES 1.78M/mo + transaction KES 1.60M/mo = **≈ KES 3.38M/mo
  (~KES 40.5M/yr)**, i.e. 200/30,000 ≈ **0.7% of the low SAM**.
- **Beachhead (first 90 days)**: **10–20 paying tenants** (pilot cohort), chosen
  for referenceability, not revenue.

### 4.3 Why Kenya, why now (timing triggers)
1. WhatsApp Commerce is default behaviour (CA data), sellers already sell in chat.
2. Daraja STK Push is mature; aggregator route removes onboarding friction.
3. TikTok Shop arrival (late 2025) is creating a new class of overwhelmed seller.
4. Kenya Data Protection Act (2019) enforcement is tightening — DIY sellers
   handling customer PII in notebooks is a liability we solve (consent, export,
   erasure tooling shipped).
5. Competitor window: publicly visible local tools are point solutions (payments
   OR storefront); based on their public sites/press as of Oct 2026, none is
   openly marketed as running the full order→stock→payment→delivery→memory loop
   (to be re-checked before each sales cycle).

---

## 5. TARGET AUDIENCE & CUSTOMER PERSONAS

### 5.1 Primary ICP (Ideal Customer Profile)
**"The Overwhelmed Social Seller"**
- Location: Nairobi (Eastleigh, Gikomba, Nyamakima, Luthuli, CBD, Westlands,
  Kilimani/Karen boutiques, Thika Road, Kasarani) + satellite (Ruiru, Kitengela,
  Juja, Nairobi South).
- Category: fashion & beauty, phone accessories/electronics, supplements/health,
  homeware, food & groceries, decor.
- Size: 1–10 staff (owner + 1–3 shop assistants + 1–2 riders); 50–500 orders/mo;
  KES 50K–2M monthly revenue; sells via WhatsApp Status + Instagram Reels + TikTok.
- Pain signal (qualifying questions): "How many orders did you get yesterday?"
  → hesitation; "How do you know when stock is finished?"; "My customer is
  calling me about delivery again"; "Someone paid and I didn't notice."
- Tech comfort: smartphone-native, uses WhatsApp Business app + M-Pesa, no
  website, no accountant. Will adopt anything that works **inside WhatsApp**.

### 5.2 Personas (sales scripts anchor on these)
| # | Persona | Context | Winning message | Trap to avoid |
|---|---|---|---|---|
| P1 | **Mama Njeri**, fashion seller, Eastleigh — 150 orders/mo via Status | Misses messages at night; sells out then oversells | "Your shop answers at 2am and never oversells: 24/7 agent + live stock" | Don't show dashboards first; demo in her WhatsApp |
| P2 | **Brian**, phone accessories, TikTok/Instagram, 300 orders/mo | Cash-on-delivery chaos, riders via SMS | "Riders text 'picked up'/'delivered'; customer gets tracking; pay-on-demand STK" | Don't promise TikTok DM automation (flagged; assisted mode only) |
| P3 | **Wanjiku & Paul**, homeware boutique, Kilimani — 2 staff | Owner is bottleneck for pricing/FAQ | "L1 answers prices, hours, delivery areas from YOUR catalog — never invented" | Don't oversell L3 optimization; start L0 shadow for trust |
| P4 | **Kamau**, electronics B2B, 500+ orders/mo, 3 riders | Wants control, fears rogue AI on money | "Agent never touches money/stock math; refunds and discounts always need your OK" | Never demo autonomy beyond policy; show audit log |
| P5 | **Grace**, food & grocery, Kilimani — subscription-style repeats | Restock/reorder memory | "System knows who buys every Friday and nudges them (with consent)" | Consent/opt-in must be explained upfront (DPA) |

### 5.3 Anti-ICP (disqualify early)
- No Kenyan 2547/2541 number or unwillingness to connect WhatsApp Business.
- Wants to sell prohibited/regulated goods (no compliance path).
- <10 orders/mo (below Starter value threshold) → free/self-serve only.
- Unwilling to do the 30-minute catalog confirmation (we never publish
  unconfirmed prices).

### 5.4 Where the audience lives (channels to reach them)
- **WhatsApp**: Status viewers, seller WhatsApp groups (Eastleigh/Gikomba
  supplier groups, SACCO groups, chama business groups), broadcast networks of
  aggregators (iPesa/Payhero merchant lists — partner, don't scrape).
- **Instagram**: Reels comments in Nairobi retail hashtags
  (#NairobiFashion, #EastleighStyle, #NairobiBusiness, #TikTokShopKenya),
  "how I manage orders" content creators.
- **Physical**: Gikomba/Nyamakima/Luthuli market WhatsApp groups; Nairobi
  business parks; trade fairs (Nairobi International Trade Fair, SHARP/SME
  expos); Matatu-route retail corridors.
- **Partners**: Safaricom merchant-agency network, aggregators (Pesapal/iPay),
  SACCOs, KRA/e-invoicing resellers, inventory-app resellers, business coaches
  and "digital marketing trainers" who already sell to this audience.

---

## 6. COMPETITIVE LANDSCAPE & POSITIONING

> Claims below are from public sites/press as of **Oct 2026**, re-checked before
> each sales cycle; competitor internals are not verified.

| Player | What they do | vs SocialERP |
|---|---|---|
| **Chpter** (ZA, active in KE) | Conversational commerce + Daraja + reconciliation; publicly cites brand clients incl. NCBA, Britam, Decathlon | Closest threat. Publicly upmarket/brand-focused; our wedge is **micro-sellers**: full ERP loop (stock ledger, orders, delivery, CRM) at KES 2,999, agent-native |
| **Bumpa** (NG, entered KE Jun 2026 per press) | MSME social-commerce mgmt: storefront, sales recording, DM unified inbox; Meta integration announced as forthcoming | Feature-similar on surface; publicly no agentic order→STK→delivery loop, no event-sourced stock; new in market (no local track record) |
| **Jumia/Kilimall/Jiji** | Marketplaces | Not tools for *their own* channel sellers; commission + traffic dependency |
| **Sokoni and local marketplaces** | Escrow marketplaces | Compete for the buyer, not the seller's ops layer |
| **Chatbot/broadcast tools** (many, incl. deadpooled Shopaz/Soko Digital) | Blast marketing or FAQ bots | No money/stock/delivery semantics; ban-risk unofficial tools explicitly excluded by our spec |
| **Do nothing** (dominant) | Notebooks, memory, manual Till | Our real market-maker: price of status quo = lost orders + overselling + fraud |

**Positioning statement**: *For Nairobi social sellers, SocialERP is the AI
shop-manager that runs orders, stock, M-Pesa and delivery inside WhatsApp —
unlike broadcast tools or storefronts, it executes the whole sale safely, with
every action audited and every price taken from your catalog, never invented.*

**Moats (deepening over time)**: (1) event-sourced stock + order money-path hardening
(tested for callback races, double-STK, oversell) — boring but expensive to
replicate correctly; (2) multi-tenant RLS + audit + DPA tooling (compliance
asset); (3) per-tenant CRM memory (switching cost); (4) eval harness that keeps
wrong-price/stock hallucinations at **zero** (trust asset); (5) data network:
reorder-cycle benchmarks per category ("sellers like you restock X every 14 days").

---

## 7. BUSINESS MODEL (summary — detail in PRICING_STRATEGY.md)

1. **SaaS subscription per tenant/month**: Starter **2,999** / Professional
   **7,999** / Enterprise **19,999** (feature matrix: channels, agent level,
   products, seats, insights, API, SLA).
2. **Transaction fees on success** (aligned incentive): STK prepay 1.5%+KES5
   (cap 50), POD 2%+KES10 (cap 100), manual confirm KES20 — *we earn when they
   earn*; disclosed at signup, never waived.
3. **One-time onboarding**: 0 / 5,000 / 15,000 (refundable within 14 days less
   transaction fees).
4. **Add-ons**: seats, channels, integrations, priority support, branded
   tracking, rider pool (pricing doc).
5. **Deliberately not doing**: lifetime deals, revenue-share on merchant GMV,
   hidden fees, annual lock-in (trust-first, churn-friendly).

**Unit economics (Professional, from pricing doc)**: ARPU ~KES 14,000/mo
(KES 8,000 sub + ~6,000 txn), blended CAC **target** KES 3,500 (no acquisition
data yet), gross margin 78% modeled, payback target <1 month, LTV (24mo @5%
churn, unvalidated) KES 250K+. **LTV:CAC: no measured figure exists — the
working target is >10:1 measured (see §13)**; the pricing doc's ">70:1" is a
model artifact built on a target CAC and is NOT quoted to prospects.

---

## 8. TRIAL → SUBSCRIPTION MOTION (the conversion machine)

> **Status: proposed design — pending owner confirmation (U60).** Engineering
> reads this as a **per-plan entitlement matrix**, not a boolean.

**Design (incentive-reviewed — see plan)**:
- **14 days**, clock starts at **channel connect** (not signup), no grace period,
  one human-approved 7-day extension only if they've had ≥1 real paid order.
- **Agent entitlement = Professional tier on WhatsApp**: L1 + L2 (order capture,
  stock reservation, STK push) — a trial that can't close a sale can't prove the
  north-star.
- **Gated during trial (upsell surfaces)**: Instagram channel, L3 optimize,
  bulk/win-back sends, API access. (Implementation = entitlements-per-plan
  matrix with a test over all plans × features — NOT an entitled/denied boolean.)
- **Capped and enforced**: daily message + LLM spend caps per trial tenant; the
  gate **denies and sends the holding reply when a cap is hit** (protects margins
  and paid tenants under load) — cap enforcement is a testable M1.4 requirement.
- **Expiry behaviour**: NEVER silence and NEVER auto-charge. Deterministic
  holding reply to customers + handoff to owner + upgrade ask; owner keeps full
  access to their data (no hostage).
- **Conversion moments**: (1) immediately after their **first paid order**
  completes — during the pilot this is a **manual M-Pesa payment + founder
  confirms the plan change (audited)**; automatic one-tap STK for month 1 is
  enabled only after our own Daraja/aggregator merchant account is approved;
  (2) day-7 value report in the owner console; (3) day-11 plan recommendation
  derived from real order volume; (4) day-14 expiry as the last ask.
- **Value proof shown (the arithmetic the prospect can check)**: orders handled
  and their KES value, median first-response time vs their pre-trial baseline,
  time-to-first-order, conversations auto-answered × their stated hourly cost,
  % orders paid via STK <10min, and the recommended plan with derivation
  ("N orders/mo → Starter covers <50 → 2,999").
- **Abuse controls**: one trial per verified WhatsApp number (concierge-verified
  for cohort 1 — no OTP exists yet), **invite-code-gated signup during the
  pilot**, spend/message caps enforced, manual payment confirmations
  human-verified during trial, bulk sends plan-gated off, no referral program
  until first paid month.
- **Compliance from day 1**: consent records, export/erasure active during trial
  (Kenya DPA applies to trials).

---

## 9. GO-TO-MARKET: AUDIENCE TARGETING & MARKETING PLAYBOOK

### 9.1 Phase GTM-1 — Concierge pilot (Days 0–45): 10 tenants, done *with* them
- **Doors**: personal network + 2 market anchors (a respected seller in
  Eastleigh/Gikomba who pulls 5 friends), one SACCO business group, one
  aggregator's merchant list (partner intro, opt-in only).
- **Offer**: "We set you up free in 30 minutes, you test for 14 days, you tell us
  what broke. If it works, you pay; if not, you keep your data and we part
  friends." No card, no contract.
- **Closing motion**: 10-minute **live demo on THEIR phone** (not a deck):
  customer texts the shop → agent replies in 5s → order created → STK push →
  paid → packed → rider "picked up" → tracking link. The demo *is* the pitch.
- **Kill metric**: if 3 of the first 10 don't reach "first paid order through the
  system", stop and fix onboarding before spending on ads.

### 9.2 Phase GTM-2 — Beachhead content (Days 30–90): own the category language
- **WhatsApp-first content engine**: short EN/SW/Sheng clips of real demos
  ("Mambo! Hii ni order ya black hoodie size M…"), posted as Status + Reels.
  Sellers buy from sellers; show the inbox, not the dashboard.
- **Anchor content series**: "Order ya 12:00 imefika wapi?" — 60s videos
  answering the exact pains (missed orders, overselling, 'where is my order').
- **Micro-creators**: 5–10 Nairobi small-business TikTok/IG creators (5–50K
  followers) — pay in free plan + KES, tracked via their own referral WhatsApp
  number. **Click-to-WhatsApp ads** (Meta) with "message us to start free trial"
  — the CTA is a chat because that's where the audience converts (85% prefer
  messaging brands ⚠ source above).
- **Community**: join + add value in existing seller WhatsApp groups (rule: 10
  helpful answers before 1 pitch); host a weekly "Sunday Stock & Sales" voice
  note clinic.
- **PR/credibility**: case study #1 with **real, measured numbers under the
  tenant's real name (with written permission)** — template: "<tenant, category,
  location>: N orders/mo handled, Xs median first reply, KES Y lost orders
  recovered"; no fabricated figures; local tech press (TechTrendsKE, Business
  Daily), and a public status/uptime page.

### 9.3 Phase GTM-3 — Scalable acquisition (Days 60–180): channels that compound
1. **Referral engine (after first paid month only)**: "Give a month, get a
   month" — credit on the *referred tenant's first paid invoice*, never on trial
   signup (anti-gaming).
2. **Partner channel**: aggregator/agent networks & SACCOs resell or refer for
   15–20% first-year commission (aligns with their merchant relationships).
3. **Category landing pages + SEO**: "WhatsApp shop management Kenya",
   "Instagram seller inventory app", "M-Pesa STK for small shops" — long-tail
   intent is small but qualified; publish comparison pages vs Chpter/Bumpa
   honestly.
4. **Broadcast to our own base** (consent-gated, template messages, 24h-window
   rules respected): product updates to trials that lapsed = win-back segment
   (shipped: segments + consent).
5. **Trade presence**: 1–2 SME expos/market days with a live demo rig (phone +
  projector), QR → WhatsApp trial start.
6. **Bundled onboarding**: partner with 2–3 "digital-skills trainers" who
   onboard clients for a fee split.

### 9.4 The marketing "tricks" (ethical, high-leverage tactics)
| # | Tactic | Why it works here |
|---|---|---|
| T1 | **The demo is a real order on the prospect's own phone** | Trust-by-experience beats slides; they *see* STK arrive |
| T2 | **Sell the reply time, not the AI** | "<30 seconds, 2am, in Swahili" is tangible; "agentic ERP" is not |
| T3 | **Proof card at day 7**: WhatsApp-rendered value summary (orders, KES, hours saved) | Makes conversion arithmetic inspectable |
| T4 | **Anchor price on their pain**: "one lost order/week = KES 8,000/mo" vs Starter 2,999 | Value > price by 2.5x before we mention fees |
| T5 | **Status-quo enemy**: benchmark their current response time during onboarding; show improvement in proof card | Self-generated evidence converts best |
| T6 | **Zero-interference guarantee as a headline**: "We never message your customers until YOU approve the launch message" | Removes the #1 fear (ruining their reputation) |
| T7 | **Concierge scarcity**: "10 setups this month" (true by design during pilot) | Honest urgency; protects quality |
| T8 | **Sheng/Swahili mirroring** in marketing and product | Matches the product's language rule (EN/SW/Sheng) and how the audience actually talks; no external statistic claimed |
| T9 | **Objection pre-handling in public** (pricing FAQ: why transaction fees — "we only earn when you earn", cap stated) | Pre-qualifies and disarms |
| T10 | **Rider-side virality**: every rider using quick replies sees the product; riders move between shops | Free distribution inside the ecosystem |

### 9.5 What we explicitly will NOT do (trust protection for first 10 clients)
- ❌ Cold-blast purchased lists (consent law + burn-in risk).
- ❌ Unofficial WhatsApp automation or scraping (ban risk — forbidden by spec).
- ❌ Dark patterns in trial (silent auto-charge, data hostage, degraded replies).
- ❌ Overpromising TikTok DM automation (feature-flagged; assisted mode only until Kenya availability verified).
- ❌ Fake scarcity or hidden pricing.

---

## 10. TECHNOLOGY

### 10.1 Stack
- **Backend**: Python **3.13** — declared in AGENTS.md as 3.12; an uncommitted
  tree change has already moved CI/Docker/lockfile to 3.13, so M0 decides via ADR
  (recommendation: complete the 3.13 move and record it). FastAPI, pydantic v2,
  SQLAlchemy 2 + Alembic, PostgreSQL with **Row-Level Security** (tenant
  isolation), Redis + ARQ durable job queue/outbox workers, Docker Compose
  (root-canonical).
- **Frontend**: Next.js 14 PWA (owner dashboard; currently landing + expanding
  pages), server-rendered HTMX fallback for low-end devices.
- **AI**: single model-agnostic **LLM gateway** (OpenAI-compatible: Ollama/local,
  OpenAI, any open-weight) with per-tenant spend caps, timeouts, backoff,
  caching, model+version recorded per trace; strict JSON intent schema
  (`extra="forbid"`), heuristic offline extractor as fallback; **the LLM never
  does money or stock arithmetic**.
- **Integrations**: Meta WhatsApp Cloud API + Instagram Messaging API (HMAC
  signature verification, 24h window tracker, template enforcement), Safaricom
  Daraja STK (OAuth, callback verification, status-query fallback), payment
  aggregator + manual pay-to-Till providers behind a `PaymentProvider`
  interface; **fake Meta/Daraja servers** for CI and demos (contract replay
  tests from recorded fixtures).
- **Quality gates**: `make check` = ruff + `mypy --strict` + pytest (unit,
  integration, contract, property-based on stock/FSM) + LLM evals (labeled
  conversation sets; hard-fail on wrong price/stock), GitHub Actions CI.
- **Observability**: OpenTelemetry traces/metrics, structured logs with PII
  redaction, alerts (webhook failures, callback gaps, reconciliation mismatch,
  queue backlog, spend spikes).
- **Security**: HMAC/webhook verification, field-level token encryption, signed
  tracking tokens, injection defense, append-only audit, OWASP ASVS baseline,
  gitleaks/pip-audit planned in CI (pending approval).

### 10.2 Architecture (one paragraph + pointer)
Modular monolith with clean boundaries (channels / conversation / agent /
catalog / orders / payments / delivery / crm / rules / insights / billing /
admin) so it can split later; **event-sourced stock ledger** (stock = Σ of
immutable movements), **outbox pattern** for side effects (at-least-once with
idempotent handlers), order/delivery finite state machines with tested legal
transitions. Full diagram + flows: `docs/MAP.md`.

### 10.3 Technology risks & mitigations
| Risk | Mitigation |
|---|---|
| LLM cost blowout on free trials | Spend caps + daily message caps per trial; zero LLM on denied entitlement; caching |
| Meta quality-rating damage from a bad tenant | Template-only out-of-window, consent-gated bulk, zero-interference launch gate, human-approved marketing |
| Daraja callback late/duplicate/missing | Idempotency + status-query cron + reconciliation + owner alerts (tested) |
| Single-dev bus factor | Constitution-driven docs (MAP/STATE/ADRs), 700+-test suite, fakes for every external service |
| Kenya DPA enforcement | Consent records, DSR export/erasure merged (gate re-verify in M0); lawyer review flagged for Terms/Privacy/DPA |

---

## 11. OPERATIONS & DELIVERY MODEL

- **Onboarding**: instrumented 30-minute path (profile → connect channels →
  catalog confirm → payments path → 8-question rules wizard → shadow-mode test
  order). Fallback tracks (no Meta assets / no Till yet → manual-confirm mode
  without blocking go-live). First cohort = **white-glove concierge** run by us.
- **Support**: WhatsApp-first (dogfooding), tiered by plan (pricing doc);
  escalation runbooks exist (`docs/RUNBOOKS/`).
- **Reliability targets**: p95 reply <5s for L1, 99.5% uptime (Pro), backups
  with tested restore (planned M2), feature flags for graceful degradation.
- **Roles until ~50 tenants**: founder = sales + onboarding; part-time CS =
  support + catalog review; contractor = dashboard/DevOps; advisory board =
  retail + Safaricom/Meta ecosystem person.

---

## 12. FINANCIAL PLAN

> **Correction (2026-10-19)**: the projection table in `PRICING_STRATEGY.md`
> overstates transaction MRR by ~4–7× (its transaction column does not foot
> against its own ARPU/orders assumptions). The table below is **recomputed from
> the fee book and tier mix** and supersedes it. The pricing doc needs the same
> correction (owner sign-off) before external use.

| Horizon | Tenants (mix) | Subscription MRR | Transaction MRR | **Total MRR** |
|---|---|---|---|---|
| Months 1–3 (pilot) | 10 (2/6/2) | 93,990 | 85,200 | **~KES 179K/mo** |
| Months 4–6 | 50 (15/25/10) | 444,950 | 399,000 | **~KES 844K/mo** |
| Months 7–12 | 200 (60/100/40) | 1,779,800 | 1,596,000 | **~KES 3.38M/mo** |
| Year 2 | 1,000 (300/500/200) | 8,899,000 | 7,980,000 | **~KES 16.9M/mo** |

Foot: transaction MRR = Σ tier_count × (orders/mo × AOV KES 2,000 × 1.5%) using
the pricing doc's order assumptions (Starter 20, Pro 200, Enterprise 800
orders/mo). Annual-prepay discounts and add-ons excluded (upside).

**Cost base (indicative, first year)**: cloud infra ~15% of revenue (scales with
LLM spend — capped), support 7%, CAC blended **target** KES 3,500 (no measured
CAC yet), Meta/Safaricom application costs (business verification, till) —
one-off, external. **Key economic watch-item**: LLM cost per conversation
(instrument from day one; target < KES 8/conversation at L1, measured, to be
baselined in M2).

**Funding stance**: the pilot MRR (~KES 179K/mo at 10 tenants) covers a
meaningful share of a lean run-rate but not all of it — expect founder-time
subsidisation through the pilot; raise only after **20 paying tenants with <8%
monthly churn** (measured), to fund GTM-3 acceleration.

---

## 13. MILESTONES & KPIs

| When | Milestone | Success measure |
|---|---|---|
| **Week 1** | Gate truth restored + trial spine merged (M0–M1) | `make check` green at named SHA; register→trial→expiry→convert E2E test green |
| **Week 2** | Sales surface live (pricing/signup/login/legal) + demo script rehearsed | E2 timed demo <30 min, zero 404 CTAs |
| **Week 2–3** | Meta + Daraja applications submitted (external critical path) | Submitted, tracking IDs recorded |
| **Week 3–4** | Sandbox phone test + 30-min onboarding dry-run | WhatsApp sandbox inbound → reply; sandbox STK → paid; transcript attached |
| **Week 4–8** | Pilot: 10 tenants onboarded | ≥7/10 reach first paid order in system |
| **Week 8–12** | Conversions | ≥4/10 convert to paid; first KES 20K MRR |
| **Month 4–6** | 50 tenants, partner channel live | CAC < KES 3,500; churn <8%; LLM cost/conversation measured & < target |
| **Month 7–12** | 200 tenants | MRR ~KES 3.38M (corrected table); gross margin ≥75%; LTV:CAC >10:1 (measured, not modeled) |

**North-star product KPIs** (instrumented from day 1): time to first order
<48h from signup; median first response <30s; % STK paid <10min; stock accuracy;
owner hours saved/week.
**Business KPIs**: trial→paid conversion %, trial activation (first paid order
in trial), MRR, churn by tier, referral share, cost per conversation.

---

## 14. RISKS & MITIGATION

| Risk | L | I | Mitigation |
|---|---|---|---|
| Meta/Daraja approvals slow (real critical path) | H | H | Start now; manual-confirm fallback keeps selling; aggregator route |
| Gate/security regression discovered pre-pilot | M | H | M0 gate truth + M2 G-lite gate; skeptic sign-off; fakes for all externals |
| Trial economics (LLM freeloading) | M | M | Caps + one-trial-per-number + concierge review |
| Competitor (Chpter/Bumpa) targets our segment | M | M | Speed to 10 references; depth of ERP loop; local trust |
| WhatsApp number ban/quality drop | L | H | Official Cloud API only, template discipline, human-approved bulk |
| Churn after trial ("got what they needed") | H | M | Day-7/11 proof, stock/CRM lock-in, ongoing insight value |
| Single-developer concentration | M | H | Docs/constitution/CI culture; fakes; small commits |
| Kenya DPA breach/complaint | L | H | Consent/DSR shipped, lawyer review, breach runbook (flagged) |
| Scope creep (dashboard, TikTok, Stripe) | H | M | Explicit deferrals in plan; phase gate discipline |

---

## 15. COMPLIANCE & LEGAL CHECKLIST
- ☐ Terms of Service, Privacy Policy, Data Processing terms (Kenyan DPA 2019) —
  drafted from shipped consent/export/erasure capabilities; **lawyer review
  before charging** (flagged as external task).
- ☐ ODPC data-protection registration assessment (flag: Kenyan counsel).
- ☐ Meta Business verification + WhatsApp Business Platform policy compliance.
- ☐ Safaricom Daraja go-live / aggregator KYC — **no co-mingling of tenant
  funds in our shortcode** (multi-merchant strategy; aggregator-first).
- ☐ Consumer protection: published prices, no dark patterns, clear trial terms
  (14 days, what's included, no auto-charge).
- ☐ Trial tenants' customer PII: lawful basis + consent records active from
  day 1 (shipped).

---

## 16. THE 90-DAY ACTION LIST (owner view)
1. **This week**: approve engineering plan → M0 gate repair → M1 trial spine.
2. **This week (parallel)**: submit Meta business verification + Daraja/aggregator
   applications; draft Terms/Privacy (send to lawyer).
3. **Week 2**: pricing/signup/login pages + rehearsed 10-min demo on a real phone.
4. **Week 3**: sandbox end-to-end test with your phone (WhatsApp test number +
   Daraja sandbox STK) — **no real money** unless you explicitly say otherwise.
5. **Weeks 4–8**: onboard 10 concierge tenants (market anchors first), instrument
   everything, collect case study #1.
6. **Weeks 8–12**: convert ≥4, publish proof, open partner channel, re-baseline
   the plan with real funnel data (then GTM-2 content machine).

*Everything above is reviewable: engineering plan in `docs/plans/`, pricing in
`docs/PRICING_STRATEGY.md`, product truth in `docs/STATE.md` + `docs/MAP.md`.*
