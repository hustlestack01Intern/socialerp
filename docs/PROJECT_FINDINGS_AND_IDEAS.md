# SocialERP: project findings and product ideas

**Review date:** 23 October 2026  
**Status:** Strategic review memo; recommendations and hypotheses, not approved implementation decisions.  
**Code baseline reviewed:** `a39db67` before the accompanying production pilot plan was added. Recheck code and records before relying on this snapshot.

For the ordered engineering work, gates, rollback plan, and trial rules, see the [production pilot implementation plan](plans/2026-10-23-production-pilot-launch.md).

## Executive finding

SocialERP is a promising idea: a controlled operating layer for small Kenyan businesses that already sell through WhatsApp and social channels. Its strongest potential is to connect customer conversations to a correct catalog, available stock, an order, payment status, and delivery updates without making the owner move customers to a new channel.

The strongest launch position is narrower than “an AI ERP for every business.” Start with one job: **help a WhatsApp seller respond, capture an accurate order, and keep the owner informed.** The product can expand after it demonstrates that it saves time or recovers sales.

The repository contains substantial feature code, but feature presence is not production readiness. In the reviewed baseline, tenant isolation, separate merchant WhatsApp routing, subscription payment verification, token exposure, data-erasure evidence, and production operations still need resolution. The project should be treated as a **pilot candidate, not ready for unrestricted live customer data**.

There is a market-fit signal: the Communications Authority of Kenya’s 2024–25 customer survey reports WhatsApp for 20.2% of e-commerce order placement and mobile money as the preferred payment method for 65.7% of respondents. That supports testing a WhatsApp/M-Pesa workflow; it does not establish demand, price acceptance, or profitability for SocialERP. [Official survey report](https://www.ca.go.ke/sites/default/files/2025-12/Customer%20Satisfaction%20Survey%20Report%202024-2025.pdf)

## What business problem to solve first

Many social sellers manage selling work across chat threads, memory, notebooks, spreadsheets, payment messages, and rider calls. The practical costs can include:

- A customer inquiry is missed or answered too late.
- A seller confirms an item that is out of stock or quotes an outdated price.
- An order is copied incorrectly between chat and a stock record.
- The owner manually checks a payment or repeatedly answers “where is my order?”
- Customer preferences and order history are lost when the owner changes phones or staff.

These are useful problems only when they occur often enough to justify changing a seller’s workflow and paying for a service. Discovery should establish which one is most expensive for the first customer group. Do not assume all sellers share the same pain.

### Best first customer profile to test

Interview and recruit Nairobi businesses that:

- already receive regular orders and questions on WhatsApp;
- have a product catalog and stock that changes often enough to cause errors;
- can identify missed messages, slow responses, manual payment checks, or delivery follow-up as a real cost;
- have an owner who can confirm prices, policies, and a staff handoff path; and
- are willing to test with synthetic/sample data first and then explicitly approve their own real-data use.

The existing plan’s 50–500 monthly-order range is a recruiting hypothesis, not a validated market definition. Start with one product category and add an adjacent one only if early discovery shows the workflow transfers cleanly.

## Product ideas that fit Kenyan operating conditions

1. **Keep the seller’s existing customer channel.** WhatsApp should be the first channel because it is familiar to customers and aligns with the project’s existing work. Preserve owner approval, official platform integrations, messaging-window rules, opt-out, and a fast human handoff.
2. **Make M-Pesa status dependable.** Show “requested,” “confirmed,” “failed,” or “needs checking” based on trusted provider evidence. A sent STK request or a customer-supplied reference must never be presented as confirmed payment. Keep merchant customer payments in the merchant’s provider arrangement; SocialERP subscription billing is separate.
3. **Start with owner-in-control automation.** Begin in shadow mode, then automate only catalog-backed FAQs. Enable order/payment actions for a merchant only after safety checks and explicit opt-in. Human approval remains necessary for refunds, risky discounts, bulk messages, and uncertain or high-risk cases.
4. **Make the first workflow useful on a phone.** The owner should be able to see an order, confirm an item, hand a conversation to a person, and review payment/delivery status without learning a complex back-office system. Use the existing WhatsApp owner console and concierge help for the first cohort; do not imply a complete dashboard exists.
5. **Design for local communication and operations.** Support concise English, Swahili, and Sheng-aware replies; human fallback; merchant-defined delivery zones and fees; manual payment confirmation when an approved provider path is unavailable; and clear behavior during connectivity, queue, or provider outages.
6. **Use customer memory only when useful and explainable.** Store only the minimum evidence needed to improve support and operations, honor customer rights and opt-outs, and avoid inferring sensitive traits.

The product’s value should be visible in a short loop: customer asks, seller confirms, system checks catalog and stock, order is recorded, payment state is verified, and the owner/customer receives an accurate update.

## Commercial direction

### Trial and paid offer

A seven-calendar-day trial is worth testing if it starts only after the merchant can use the service. Start the clock after account readiness, catalog and business-rule confirmation, test order, privacy/retention notice, and owner approval—not at signup while Meta/provider setup is pending.

Use one simple WhatsApp-only pilot offer. The proposed KES 7,999 per month is a **price experiment**, not a validated price. The pilot should have no automatic charge, no SocialERP order fee, and no unclear setup charge. At expiry, the merchant chooses whether to continue and SocialERP verifies its subscription payment before granting paid entitlements. Existing customers and trial records need an explicit, audited migration rule before changing the current 14-day policy.

### How to tell whether it can be profitable

Measure actual collected subscription revenue and subtract, per tenant:

- messaging, model, payment-provider, and other variable charges;
- allocated hosting and infrastructure;
- onboarding and ongoing support labor;
- refunds, credits, and failed-collection costs; and
- the cost to acquire that paying merchant.

Do not treat signup, a payment reference, gross monthly recurring revenue, or a projected funnel as profit. First demonstrate positive contribution per paying merchant and repeatable onboarding/support. The pilot should also ask merchants whether the demonstrated time or sales benefit is worth the disclosed price.

Avoid a SocialERP per-order fee during the initial pilot. It introduces extra reconciliation and commercial complexity before the subscription value is proven. A later pricing model can be reconsidered using observed usage, willingness-to-pay, provider costs, and legal/accounting advice.

### Go-to-market idea

Use a founder-led, concierge cohort rather than broad advertising:

1. Interview a small set of sellers about their current order and payment workflow.
2. Select three merchants with frequent, measurable pain and an owner willing to participate.
3. Configure one real workflow per merchant; keep early actions in shadow/approval mode.
4. Track support minutes, response time, correct orders, payment exceptions, time saved, and actual willingness to pay.
5. Offer the same clearly priced subscription to each qualified merchant.
6. Invite up to ten only after the first cohort can be supported safely and economically.

This approach is likely to reveal product and onboarding problems faster than building every roadmap module before speaking with customers.

## Technical and launch findings

The reviewed code and records show a useful modular-monolith foundation. A rewrite or microservice split would add time without addressing the current launch blockers. Prioritize production proof in this order:

1. Establish one truthful release baseline and reconcile conflicting project status, trial, billing, pricing, and security records.
2. Make PostgreSQL tenant isolation hold under the real API/worker roles and migrated schema, with cross-tenant tests.
3. Connect and route each merchant’s own official WhatsApp account with protected credentials, webhook verification, consent/opt-out, and messaging-window enforcement.
4. Correct payment and subscription state transitions so only trusted provider confirmation or audited privileged reconciliation can activate paid access.
5. Resolve browser session token exposure and prove current data export/erasure behavior against an inventory of stored data.
6. Rehearse production deployment, backups/restores, alerts, key rotation, incident response, and tenant/channel kill switches.
7. Run the end-to-end flow with fakes/sandbox and synthetic data before separately authorizing real merchant data, live messaging, or live payment operations.

The detailed plan estimates roughly 12–21 engineer-days (about 2–4 elapsed weeks with parallel founder/operator work), then seven trial days per merchant. This is a planning estimate. Provider review, account eligibility, counsel, and defects can extend it.

## Risks and countermeasures

| Risk | Why it matters | Recommended response |
|---|---|---|
| Cross-tenant data exposure | Could disclose one seller’s customers, orders, or credentials to another | Treat database-enforced isolation as a release gate; test using production-like roles and migrations |
| Wrong price, stock, payment, or delivery claim | Direct financial and trust harm | Keep money/stock arithmetic deterministic; require provider evidence; hand off uncertainty |
| Platform dependency or approval delay | Affects onboarding timeline and channel availability | Verify official account eligibility early; keep Instagram/TikTok out of the first paid pilot |
| Poor trial activation | Seven days can be wasted on setup rather than product value | Begin only after readiness; provide concierge onboarding; measure time to first useful order |
| Price does not match value | Signup interest may not become paid retention | Present the actual price before trial; measure verified conversions and churn, not stated enthusiasm alone |
| High support or model costs | A growing customer base could lose money | Set provisional hard spend caps before live traffic; measure costs and support by tenant |
| Privacy/compliance gaps | Merchant and customer data create legal and reputational exposure | Inventory data, document lawful basis/notices/rights, and get Kenyan counsel on applicable obligations |
| Automation damages existing relationships | Unexpected messages or wrong responses can harm the merchant | Start L0, require owner launch approval, respect platform policy and opt-out, keep kill switches and handoff |

## What success should mean

Use the project’s north-star measures and add commercial measures. Report actual cohort values with sample sizes; do not market pilot results as representative of all Kenyan businesses.

- Time from signup to first order handled in the system; target under 48 hours once setup is unblocked.
- Median first response time; target under 30 seconds for supported automated replies.
- Correct-price and correct-stock rate; wrong claims must be zero in the release evaluation set.
- Share of eligible STK requests confirmed within ten minutes, plus late/missing callback and reconciliation rates.
- Stock accuracy compared with a physical count.
- Owner hours saved, self-reported and checked against workflow activity.
- Trial activation and verified paid conversion, support minutes per tenant, cancellation/churn, and contribution margin.

The first cohort is evidence for iteration, not proof of national market size. A useful go/no-go question is: **Can the team onboard and safely support a merchant, demonstrate a benefit the owner recognizes, collect the disclosed subscription reliably, and retain positive contribution after support and provider costs?**

## Decisions and next action

Before implementation, the owner should approve the pilot scope, resolve the existing 14-day versus proposed seven-day trial policy, choose one enforceable package and price, assign Meta/payment/counsel owners, and confirm the budget for capped pilot traffic. Keep estimates and unresolved legal/provider questions visible rather than converting them into assumptions.

This memo and the accompanying plan are proposals. They do not authorize imports of real customer data, live customer messaging, or live payment endpoint activity. The project’s existing working instructions require an explicit “go” before coding; each live-data or provider action also needs its own readiness and authorization gate.

## Sources and related project records

- Communications Authority of Kenya, [Customer Satisfaction Survey 2024–2025](https://www.ca.go.ke/sites/default/files/2025-12/Customer%20Satisfaction%20Survey%20Report%202024-2025.pdf).
- International Labour Organization, [Embracing digital in Kenya](https://www.ilo.org/sites/default/files/2024-11/24030-ILOP-Embracing-digital-Kenya-report-v1-5-D.pdf). Its business example is illustrative, not market sizing.
- ODPC, [Data Protection (Registration of Data Controllers and Data Processors) Regulations, 2021](https://www.odpc.go.ke/wp-content/uploads/2024/03/THE-DATA-PROTECTION-REGISTRATION-OF-DATA-CONTROLLERS-AND-DATA-PROCESSORS-REGULATIONS-2021.pdf). Applicability needs classification and counsel review.
- Internal project requirements and records: `docs/CONSTITUTION.md`, `docs/STATE.md`, `docs/SPEC.md`, `docs/UNCERTAINTY.md`, `docs/THREAT_MODEL.md`, `docs/BUSINESS_PLAN.md`, `docs/PRICING_STRATEGY.md`, and the linked production pilot plan.
