# SocialERP — Pricing Strategy v1.0

> **⚠️ CORRECTION NOTICE (2026-10-19):** The revenue/projection tables in this document do
> **not foot against this document's own pricing and usage assumptions** — transaction-MRR
> projections are overstated by roughly **4–7×**. The corrected, footed figures are in
> **`docs/BUSINESS_PLAN.md` §12** (10 tenants ≈ KES 179K/mo; 50 ≈ 844K/mo; 200 ≈ 3.38M/mo;
> 1000 ≈ 16.9M/mo). The original tables below are preserved unchanged pending owner
> sign-off — do NOT use them for planning or external sharing until corrected.

> **Effective**: 2026-10-19 | **Currency**: KES (Kenyan Shillings) | **Billing**: Monthly in advance

---

## 🎯 PRICING PHILOSOPHY

1. **Value-based**: Price reflects revenue uplift from automation, not feature count
2. **Kenya-first**: KES pricing, M-Pesa native, local support
3. **Scalable**: Grows with merchant — no penalty for success
4. **Transparent**: No hidden fees, clear transaction costs
5. **Low barrier**: Free onboarding for Starter, low CAC

---

## 💰 TIERED SUBSCRIPTION (Per Tenant/Month)

| Feature | **Starter** KES 2,999 | **Professional** KES 7,999 | **Enterprise** KES 19,999 |
|---------|----------------------|---------------------------|--------------------------|
| **Target** | Micro-merchants (<50 orders/mo) | Growing sellers (50-500 orders) | High-volume (500+ orders) |
| **Channels** | WhatsApp only | WhatsApp + Instagram | All (incl. TikTok when ready) |
| **Agent Level** | L1 (FAQ only) | L2 (Transact) | L3 (Optimize) |
| **Products** | 100 | Unlimited | Unlimited |
| **Staff Seats** | 1 | 3 | Unlimited |
| **Insights** | Basic (weekly) | Daily + segments | Daily + weekly + custom |
| **API Access** | ❌ | ✅ | ✅ + webhooks |
| **Support** | WhatsApp group + email (24h) | Dedicated WhatsApp + phone (4h) | Slack + phone + quarterly review (1h) |
| **SLA** | Best effort | 99.5% uptime | 99.9% + 1h response |
| **Onboarding** | Self-serve wizard | Assisted (30-min call) | White-glove + data migration |

---

## 💳 TRANSACTION FEES (Per Successful Order)

| Payment Method | Fee Structure | Cap |
|----------------|---------------|-----|
| **M-Pesa STK Prepay** | 1.5% + KES 5 | KES 50 max |
| **M-Pesa Pay-on-Delivery** | 2% + KES 10 | KES 100 max |
| **Manual Confirmation** | KES 20/order | — |
| **Aggregator (Pesapal/iPay)** | Per aggregator terms | Per aggregator |

> **Rationale**: Pay-on-delivery has higher risk (failed delivery, cash handling) → higher fee. Manual confirmation requires operator time.

---

## 🎁 ONBOARDING FEES (One-Time)

| Tier | Fee | Includes |
|------|-----|----------|
| **Starter** | KES 0 | Self-serve wizard + docs |
| **Professional** | KES 5,000 | 30-min assisted onboarding call + catalog review |
| **Enterprise** | KES 15,000 | White-glove: dedicated CS, custom rules setup, data migration, staff training (2hr) |

> **Refundable**: If client cancels within 14 days (less transaction fees incurred).

---

## 📊 VOLUME DISCOUNTS (Annual Prepay)

| Commitment | Discount | Effective Monthly |
|------------|----------|-------------------|
| Monthly | 0% | Base price |
| Quarterly (3mo) | 5% | Starter: 2,849 | Professional: 7,599 | Enterprise: 18,999 |
| Annual (12mo) | 15% | Starter: 2,549 | Professional: 6,799 | Enterprise: 16,999 |

> **Cashflow**: Annual upfront improves our cashflow; passes savings to committed merchants.

---

## 🔄 ADD-ONS (Per Tenant/Month)

| Add-On | Price | Description |
|--------|-------|-------------|
| **Extra Staff Seat** | KES 500/seat | Beyond tier limit |
| **Extra Channel** | KES 1,000 | TikTok (when ready) or additional WhatsApp number |
| **Custom Integration** | KES 10,000 setup + 2,000/mo | ERP/Accounting sync (QuickBooks, Xero, Odoo) |
| **Dedicated Rider Pool** | KES 5,000 | Managed rider fleet (SocialERP partners) |
| **Priority Support** | KES 3,000 | 1h SLA + dedicated engineer |
| **Custom Domain Tracking** | KES 1,000 | `track.yourbrand.com` instead of `track.socialerp.com` |

---

## 💸 REVENUE PROJECTIONS (Conservative)

### Customer Mix Assumptions
| Tier | % of Base | Avg Orders/Mo | Transaction Revenue/Mo |
|------|-----------|---------------|------------------------|
| Starter | 40% | 20 | KES 600 (1.5% × 20 × 2,000 AOV) |
| Professional | 45% | 200 | KES 6,000 (1.5% × 200 × 2,000 AOV) |
| Enterprise | 15% | 800 | KES 24,000 (1.5% × 800 × 2,000 AOV) |

### Monthly Recurring Revenue (MRR) Projection

| Month | Total Tenants | Mix (S/P/E) | Subscription MRR | Transaction MRR | Total MRR |
|-------|---------------|-------------|------------------|-----------------|-----------|
| 1-3 (Pilot) | 10 | 2/6/2 | 96K | 180K | **276K** |
| 4-6 | 50 | 15/25/10 | 400K | 1.8M | **2.2M** |
| 7-12 | 200 | 60/100/40 | 1.6M | 12M | **13.6M** |
| Year 2 | 1,000 | 300/500/200 | 8M | 60M | **68M+** |

### Unit Economics (Professional Tier)
| Metric | Value |
|--------|-------|
| ARPU (subscription) | KES 8,000/mo |
| ARPU (transactions) | ~KES 6,000/mo |
| **Total ARPU** | **~KES 14,000/mo** |
| CAC (blended) | KES 3,500 |
| Gross Margin | 78% (infra 15%, support 7%) |
| Payback Period | < 1 month |
| LTV (24mo, 5% monthly churn) | KES 250,000+ |
| LTV:CAC | > 70:1 |

---

## 🎯 PRICING RULES & GUARDRAILS

### Price Changes
- **Grandfathering**: Existing tenants keep price for 12 months after increase
- **Notice**: 60 days written notice for any increase
- **Cap**: Max 10% YoY increase without new contract

### Discount Authority
| Discount | Approval Required |
|----------|-------------------|
| ≤ 10% | CS Manager |
| 10-20% | Sales Director |
| > 20% | CEO + CFO |

### Prohibited
- ❌ Lifetime deals
- ❌ Revenue share (we don't take % of merchant revenue)
- ❌ Hidden fees (all fees published)
- ❌ Lock-in contracts (monthly cancel anytime)

---

## 💰 PAYMENT TERMS

| Term | Detail |
|------|--------|
| **Billing Cycle** | Monthly in advance (1st of month) |
| **Payment Method** | M-Pesa STK Push (auto) or Bank Transfer |
| **Grace Period** | 7 days (service continues) |
| **Suspension** | Day 8: service paused, data retained 30 days |
| **Reactivation** | Immediate on payment (no fee) |
| **Cancellation** | End of billing period, no refund for partial month |
| **Data Export** | Free within 30 days of cancellation |

---

## 📈 PRICING EVOLUTION ROADMAP

| Quarter | Change | Rationale |
|---------|--------|-----------|
| Q1 2027 | Launch pricing (above) | Market entry |
| Q3 2027 | +10% on Enterprise | Proven ROI, dedicated support |
| Q1 2028 | +5% on Professional | Feature parity with competitors |
| Q3 2028 | Introduce "Growth" tier (KES 12,999) | Gap between Pro/Enterprise |
| Annual | Inflation adjustment (CPI Kenya) | Cost base protection |

---

## 📋 PRICING COMMUNICATION TEMPLATES

### Website Pricing Page (Key Messages)
> **"Pay for what you use. Grow without penalties."**
>
> - No setup fees on Starter
> - Cancel anytime, no questions
> - All prices include M-Pesa transaction fees (no surprise charges)
> - 14-day money-back guarantee

### Sales Objection Handling
| Objection | Response |
|-----------|----------|
| "Too expensive" | "At 200 orders/mo, Professional saves you ~40hrs/mo. At KES 200/hr labor cost, that's KES 8,000 saved. The tier pays for itself." |
| "Why transaction fees?" | "We only make money when you make money. No order = no fee. Capped at KES 50/order so you never overpay." |
| "Competitor is cheaper" | "They charge per message. We charge per order. At 50 orders/mo, we're 40% cheaper. And our agent actually closes sales." |

---

## 📋 INTERNAL PRICING OPERATIONS

### Quote Generation
1. Use internal calculator (Google Sheet: `SocialERP_Pricing_Calculator`)
2. Input: expected orders/mo, channels, staff count
3. Output: recommended tier + total monthly estimate
4. Send via WhatsApp PDF + verbal walkthrough

### Contract
- Digital acceptance via dashboard (click "Accept Terms")
- Auto-generates M-Pesa STK for first month + onboarding fee
- Auto-renews monthly unless cancelled

### Invoice
- Generated 1st of month for that month
- Includes: subscription + prior month transactions + any add-ons
- Paid via STK Push (auto) or bank transfer (Enterprise)
- PDF emailed + available in dashboard

---

## 📊 PRICING METRICS TO TRACK

| Metric | Target | Frequency |
|--------|--------|-----------|
| **ARPU** | > KES 12,000 | Monthly |
| **Transaction Revenue %** | > 40% of total | Monthly |
| **Tier Mix** | 30/50/20 (S/P/E) | Quarterly |
| **Annual Prepay Rate** | > 30% | Quarterly |
| **Add-on Attach Rate** | > 20% | Quarterly |
| **Price Increase Acceptance** | > 80% | Per increase |
| **Churn by Tier** | Starter < 8%, Pro < 5%, Ent < 3% | Monthly |

---

## 📋 PRICE BOOK (Internal Only)

| SKU | Name | Monthly | Setup | Description |
|-----|------|---------|-------|-------------|
| SERP-STA | Starter | 2,999 | 0 | WhatsApp, L1, 100 products |
| SERP-PRO | Professional | 7,999 | 5,000 | WA+IG, L2, unlimited |
| SERP-ENT | Enterprise | 19,999 | 15,000 | All channels, L3, custom |
| ADD-SEAT | Extra Staff | 500 | 0 | Per seat/mo |
| ADD-CHAN | Extra Channel | 1,000 | 0 | Per channel/mo |
| ADD-INT | Custom Integration | 2,000 | 10,000 | ERP sync |
| ADD-RIDER | Rider Pool | 5,000 | 0 | Managed fleet |
| ADD-SUPP | Priority Support | 3,000 | 0 | 1h SLA |
| ADD-DOM | Custom Domain | 1,000 | 0 | Branded tracking |

---

*Approved by: CEO + CFO | Effective: 2026-10-19 | Next Review: 2027-01-19*