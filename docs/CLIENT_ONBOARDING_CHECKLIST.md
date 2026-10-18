# SocialERP — Client Onboarding Checklist & Information Requirements

> **For**: Customer Success / Sales Engineering | **Version**: 1.0 | **Date**: 2026-10-19

---

## 📋 PRE-ONBOARDING PREREQUISITES (Client Must Have Before Call)

### Mandatory — Hard Blockers
| # | Requirement | Verification Method | Status |
|---|-------------|---------------------|--------|
| 1 | **WhatsApp Business App** (not personal WhatsApp) on target number | Screenshot of app home screen showing business name + number | ☐ |
| 2 | **Phone number** — Kenyan (2547XX or 2541XX), not on WhatsApp Cloud API | Send OTP via WhatsApp Business App | ☐ |
| 3 | **Meta Business Manager** — Verified | Business Manager ID + link | ☐ |
| 4 | **Facebook Page** — Published, connected to Business Manager | Page URL + "Connected to Business Manager" badge | ☐ |
| 5 | **Instagram Professional Account** — Connected to that Facebook Page | IG handle + Page settings screenshot | ☐ |
| 6 | **Safaricom Till/Paybill** — Approved for Lipa Na M-Pesa Online | Till number + approval letter/email from Safaricom | ☐ |
| 7 | **KRA PIN** — Valid | iTax verification (PIN checker) | ☐ |
| 8 | **Business Registration** — Certificate | Certificate of Incorporation / Business Name Registration | ☐ |

### Strongly Recommended
| # | Requirement | Why |
|---|-------------|-----|
| 9 | Payment Aggregator (Pesapal/IntaSend/iPay) — if no own Till | Backup payment path |
| 10 | Existing catalog (WhatsApp Catalog URL, IG Highlights, Price List PDF/Excel, Photos) | Faster catalog bootstrap |
| 11 | Own rider fleet contacts (names + phones) | Rider onboarding parallel track |
| 12 | Domain name (for custom tracking page) | Branding |

---

## 📝 INFORMATION WE COLLECT DURING ONBOARDING

### 1. Business Identity (Legal Entity)
| Field | Required | Source | Validation |
|-------|----------|--------|------------|
| Legal Business Name | ✅ | Cert of Incorporation | Exact match |
| Trading Name (DBA) | Optional | Owner input | — |
| KRA PIN | ✅ | PIN Certificate | iTax checker |
| Business Registration Number | ✅ | Certificate | Registrar check |
| Physical Address (County, Sub-county, Ward, Landmark) | ✅ | Owner input | Google Maps |
| Owner Full Name | ✅ | ID/Passport | Exact match |
| Owner Phone (2547XX/2541XX) | ✅ | Owner input | OTP verify |
| Owner Email | ✅ | Owner input | Verify link |
| Business Type (Ltd/Partnership/Sole Prop) | ✅ | Certificate | — |

### 2. Channel Configuration
| Field | Required | Source | Notes |
|-------|----------|--------|-------|
| WhatsApp Number (2547XX/2541XX) | ✅ | Owner input | Must match WA Business App |
| WhatsApp Business Account ID | Auto | Meta Embedded Signup | Retrieved via API |
| Instagram Handle | ✅ | Owner input | Must be Professional account |
| Facebook Page ID | Auto | Meta OAuth | Retrieved via API |
| Meta Business Manager ID | Auto | Meta OAuth | Retrieved via API |

### 3. Payment Configuration
| Field | Required | Source | Validation |
|-------|----------|--------|------------|
| Provider Choice (Own Till / Aggregator / Manual) | ✅ | Owner selection | — |
| Till Number (if own) | If own Till | Safaricom letter | 6-8 digits |
| Aggregator (Pesapal/IntaSend/iPay/Other) | If aggregator | Owner selection | API keys test |
| Aggregator API Keys | If aggregator | Aggregator dashboard | Sandbox test |
| Aggregator Webhook URL | If aggregator | Auto-generated | Must be HTTPS |
| Manual Till Number (fallback) | If manual | Owner input | 6-8 digits |
| Manual Confirmation Token | If manual | Auto-generated | Shared with operator |

### 4. Catalog Bootstrap
| Source | Format | Owner Effort |
|--------|--------|--------------|
| WhatsApp Catalog URL | Auto-import | Click "Import" |
| Instagram Highlights/Posts | Screenshots/URLs | Swipe review |
| Price List (PDF/Excel/CSV) | Upload + parse | Column mapping |
| Product Photos (folder/Drive/WhatsApp) | Upload | Drag-drop |
| Manual Entry | Form | Per product |

**Per Product We Capture:**
| Field | Required | Notes |
|-------|----------|-------|
| Name | ✅ | — |
| SKU (auto if blank) | Auto | Unique per tenant |
| Category | ✅ | From predefined + custom |
| Description | Optional | Markdown supported |
| Base Price (KES) | ✅ | Integer cents |
| Sale Price (KES) | Optional | For promotions |
| Cost Price (KES) | Optional | For margin calc |
| Variants (Size/Color/Material) | Optional | Matrix: SKU per combo |
| Stock Qty | ✅ | Integer |
| Reorder Level | Optional | Default 10 |
| Supplier | Optional | For reorder suggestions |
| Images (1-5) | Optional | Max 5MB each |
| Tags | Optional | Search/filter |

### 5. Delivery Configuration
| Field | Required | Default |
|-------|----------|---------|
| Serviceable Areas (Nairobi neighborhoods) | ✅ | Multi-select from master list |
| Fee per Zone (KES) | ✅ | Per zone |
| Free Delivery Threshold (KES) | Optional | 0 = never free |
| Cutoff Time (e.g., "3pm = same day") | ✅ | 15:00 |
| Own Fleet? (Yes/No) | ✅ | No = rider pool |
| Rider Contacts (Name + Phone + Zone) | If own fleet | CSV/manual |

### 6. Business Rules (Agent Behavior)
| Rule | Default | Owner Sets |
|------|---------|------------|
| Business Hours (e.g., "8am-8pm Mon-Sat") | 08:00-20:00 Mon-Sat | Timezone-aware |
| Order Cutoff Time | 15:00 | Same-day vs next-day |
| Max Discount % | 10% | 0-100% |
| Min Order Value (KES) | 0 | Free delivery threshold |
| Auto-accept COD? | Yes | Yes/No |
| Handoff Triggers (complaint, refund, complex) | Default set | Customize |
| Tone (Formal / Casual / Sheng) | Casual | Dropdown |
| Language Priority (En/Sw/Sheng) | En > Sw > Sheng | Order |

### 7. Team & Access
| Role | Phone | Permissions |
|------|-------|-------------|
| Owner | ✅ | Full: launch, refunds, pricing, team, analytics |
| Manager | Optional | Orders, inventory, staff, insights (no launch/refunds) |
| Staff | Optional | Orders, inventory, customer chat (read-only analytics) |
| Rider | Optional | Pickup/Arrive/Deliver/Failed (no customer data) |

### 8. Consent & Compliance (Kenya DPA)
| Consent Type | Method | Evidence Stored |
|--------------|--------|-----------------|
| Marketing (WhatsApp/IG) | Opt-in QR / verbal / form | Source + timestamp + IP hash |
| Cross-channel linking (WA ↔ IG) | Explicit per customer | Message hash / OAuth token hash |
| Data Retention Period | Owner choice (default 7 yrs) | Configurable |
| Data Subject Rights Contact | Email/Phone | For export/delete requests |

---

## ⏱️ ONBOARDING SESSION AGENDA (30 Minutes)

| Time | Step | Owner Action | CS Action |
|------|------|--------------|-----------|
| 0:00-3:00 | **1. Business Profile** | Fill form / confirm pre-filled | Guide, validate KRA PIN |
| 3:00-11:00 | **2. Connect Channels** | Scan QR (WA) / OAuth (IG) / approve Embedded Signup | Monitor, troubleshoot |
| 11:00-19:00 | **3. Catalog Bootstrap** | Swipe review: approve/reject/edit each item | Bulk import, fix parsing |
| 19:00-24:00 | **4. Payments** | Select provider, enter creds, test STK (KES 1) | Verify callback |
| 24:00-28:00 | **5. Rules & Tone** | Answer 8 questions, set tone | Confirm logic |
| 28:00-30:00 | **6. Shadow Test** | Send test order → STK → confirm → mark delivered | Verify end-to-end |

### Pre-Call Prep (CS — 15 Min Before)
- [ ] Verify all prerequisites collected
- [ ] Create tenant in staging
- [ ] Pre-fill business profile from collected data
- [ ] Generate test STK credentials
- [ ] Open wizard link + share screen

### During Call — CS Cheat Sheet
| If Stuck | Action |
|----------|--------|
| WA QR won't scan | Use "Link with phone number" fallback |
| IG OAuth fails | Check Business Manager → Instagram → Connected Tools toggle |
| STK test fails | Verify Till/Paybill + callback URL + simulator mode |
| Catalog parse fails | Switch to manual entry mode |
| Owner unsure on rules | Use defaults, note for later |

---

## ✅ POST-ONBOARDING VERIFICATION (First 48 Hours)

| Time | Check | Method | Pass Criteria |
|------|-------|--------|---------------|
| T+0 | Launch approved | Owner clicks "Go Live" | `launch_approved_at` set |
| T+1h | First real order | Owner receives order | STK received + order created |
| T+2h | STK callback | Callback logs | `payment_status = completed` |
| T+4h | First delivery | Rider completes | `delivered` + proof |
| T+24h | Daily insight | 6am email/dashboard | Orders, revenue, AOV populated |
| T+48h | CS check-in | Call/WhatsApp | Owner confirms no blockers |

### Red Flags (Escalate Immediately)
| Signal | Action |
|--------|--------|
| STK failure rate > 10% | Check Till/Paybill + callback URL + Safaricom status |
| No orders after 24h | Verify channel connected + launch approved + test order |
| Owner can't access console | Verify tenant context + role = owner |
| Agent hallucinating | Check prompt + catalog sync + fallback to L0 |
| Delivery failures > 20% | Check rider assignment + zone config + rider app |

---

## 📞 POST-LAUNCH SUPPORT (First 30 Days)

| Day | Touchpoint | Channel | Owner Action |
|-----|------------|---------|--------------|
| 1 | Welcome message | WhatsApp | Reply "OK" |
| 2 | Check-in call | Phone/WhatsApp | 10-min sync |
| 7 | Weekly insight review | Email + call | Confirm metrics |
| 14 | Mid-month check-in | WhatsApp | Quick pulse |
| 30 | Monthly business review | Call (30 min) | Review metrics, plan next month |

### Support Channels (By Tier)
| Tier | Channels | SLA |
|------|----------|-----|
| Starter | WhatsApp group + email | 24h business hours |
| Professional | Dedicated WhatsApp + phone | 4h business hours |
| Enterprise | Slack + phone + quarterly on-site | 1h 24/7 |

---

## 📦 DELIVERABLES TO CLIENT (Post-Launch)

| Document | Format | When |
|----------|--------|------|
| **Welcome Pack** | PDF + WhatsApp | T+0 |
| **Quick Reference Card** | 1-page PDF | T+0 |
| **Agent Command Cheatsheet** | PDF | T+0 |
| **Rider Onboarding Guide** | PDF | If own fleet |
| **Monthly Business Review Template** | Google Slides | Month 1 |
| **Tax Invoice** | PDF (auto) | Monthly 1st |
| **Data Export Guide** | PDF | On request |

---

## 🚨 ESCALATION CONTACTS

| Issue | L1 (CS) | L2 (Engineering) | L3 (Founders) |
|-------|---------|------------------|---------------|
| STK Failure | Retry + manual fallback | Callback debug | Safaricom escalation |
| Stock Mismatch | Ledger reconcile | Race condition fix | Architecture |
| Agent Error | Prompt tune | Model swap | Safety review |
| Data Breach | Contain + notify | Forensics | Legal + ODPC |

---

## ✅ SIGN-OFF

**Client Confirmation** (Signed at launch):
> "I confirm all information provided is accurate. I authorize SocialERP to process customer data per Kenya DPA. I understand the pricing, transaction fees, and cancellation terms. I approve launching my channels."

**Signature**: _______________ **Date**: _______________ **Name**: _______________

**CS Confirmation**:
> "All prerequisites verified. Onboarding completed. Launch approved. Monitoring active."

**CS Signature**: _______________ **Date**: _______________

---

*Version 1.0 | Effective 2026-10-19 | Next Review: 2027-01-19*