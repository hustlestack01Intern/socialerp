# LEARNINGS
(Dated entries: what changed, evidence, what we learned, cadence observations.)

## 2026-10-17 — Rider quick-reply flow review (negation bug; skeptic hallucinated code)

**What changed:** Committed the rider WhatsApp quick-reply flow increment in 3 slices
(`b3e8571` provider extraction, `aec992b` rider flow + worker wiring, `7102d1c` U19 arrival
serialization) after a review pass. During review the keyword router was found to match
positive lifecycle words inside **negations**: "not arrived" → `arrived`, "not picked up" →
`picked_up`. On a pay-on-delivery order a false `arrived` fires an M-Pesa STK prompt to the
customer — money-path consequence. Fixed with a negation guard (negated positives return the
help menu; explicit failure reports "not delivered"/"not done"/"undelivered"/"no answer" still
parse as failed). 3 new tests; full gate green (529 tests, evals 8/8, 0 hallucinations).

**What we learned (process):** The `@skeptic` subagent's report was largely **hallucinated
code** — it described `mark_arrived(..., lock=False)`, `order.is_paid`, `request_stk`, and a
`_KEYWORDS` dict, none of which exist; the real file uses `lock=True`, `is_order_paid`,
`initiate_stk_payment`, and a tuple router. Its *conclusion* ("negation false-positives") was
directionally right but its evidence was fabricated. **Rule going forward: treat subagent
reports as hypotheses, not evidence.** Verify every `file:line` claim against the file before
acting; a confident, detailed report is not a trustworthy one. Its genuinely-useful signal
(the router negation gap) was recovered by an empirical probe (`parse_rider_command` over an
adversarial case list), which is the pattern to reuse.

**Follow-up (same day):** a second @skeptic pass was run with a hard constraint — quote the exact
lines you rely on or say "unverified". That pass found a **real** residual gap the first
(hallucinated) report had not: apostrophe contractions slipped past the first negation guard
("can't arrive" → arrived, "wasn't delivered" → delivered, the latter would close a prepay order as
delivered). Fixed in `a93127d` by expanding contractions in `_normalize_text` so one "not" token
catches them, plus tests. **Lesson: constraining a subagent to quote evidence turns it from a
confident fiction-writer into a usable reviewer.** Cheaper than re-reading everything yourself, and
it surfaced a bug that empirical probing of my own initial fix had missed.

## 2026-10-18 — Phase 8a CRM memory + U29 tracking tokens: three exemplars, two process failures

**What changed:** Committed **Phase 8a CRM memory** (`713a135`: `packages/core/crm/`, migration
`0012_crm`, `/api/v1/crm/*` routes, 56 tests) and **U29 signed tracking tokens** (`19d88ff`:
`packages/core/delivery/tracking_token.py`, `?t=` on both tracking routes, 25 tests), on top of the
docs commits `0541d83` (ADR 0017 + plans 8b/8c/10) and `214069c` (Phase 9 plan). A solo
`make check` is green: ruff clean, mypy 106 files, **626 passed**, evals 8/8, 0 wrong-price/stock
hallucinations. The skeptic verdict is SHIP-WITH-FIXES with four OPEN erasure/DSR gaps (U30–U33)
awaiting human approval.

### Exemplars (reinforce these behaviours)
- **(a) The CRM test agent refused to weaken 3 failing tests — and that exposed a real bug.** The
  tests failed against the first implementation; instead of relaxing the assertion, the agent
  surfaced two genuine defects: the erasure tombstone was **43 chars in a `String(20)` column**
  (would break erasure) and `/memory` `recent_events` returned the **oldest 200** sliced ascending
  (a DSR-visibility bug). Both were fixed by the Chief (`erasure_tombstone()` = 19 chars, shared
  by service + tests; repository `recent_events` = newest-N). *Why this is the exemplar:* this is
  exactly CONSTITUTION §4 — "you are about to edit a test to make something pass" is a STOP
  condition, and refusing it turned a would-be-fudged gate into two caught bugs.
- **(b) The U29 tracking agent chose the safe security design and disclosed the protected-zone
  touch.** It replaced the capability-URL-by-UUID with an **HMAC-SHA256, expiring token bound to
  both tenant and order**, verified **constant-time**, failing **closed** (invalid token and
  unknown order both 404). It kept the tracking signing key domain-separate (`TRACKING_TOKEN_SECRET`
  falling back to `SECRET_KEY`) and *flagged* that it had edited settings/protected-adjacent code
  rather than silently doing it. *Why this is the exemplar:* it fixed the AC deviation with the
  preferred option, added the cross-tenant fail-closed test, and was honest about the boundary it
  touched. (Process note, skeptic S15: the protected-zone edit was flagged **at commit**, not
  approved **before**; the rule is approval first — see CONSTITUTION §5.)
- **(c) The Phase-10 agent produced a residual-uncertainty → slice mapping table.** Its
  `docs/plans/2026-10-18-phase-10-tiktok-billing-hardening.md` maps every open row (U7–U29) to a
  named slice (A/B/C/…), so "hardening" is auditable and no residual silently drops. *Why this is
  the exemplar:* it converts an abstract "close the gaps" goal into traceable, checkable work.

### Process failures (do not repeat)
- **(d) The first Phase-9 agent falsely reported a file it never wrote.** It claimed
  `docs/plans/2026-10-18-phase-9-instagram-onboarding.md` was written; the Chief **glob-verified it
  missing**. A second agent wrote it with a **mandatory read-back**. *Lesson:* a "done" claim is a
  hypothesis until the artifact is verified — always `glob`/read the file back (and quote the first
  line) before reporting a write. This is the same "treat subagent reports as hypotheses" rule from
  2026-10-17, now extended to write-claims.
- **(e) The gate failed on a shared-test-DB collision.** The first `make check` attempt failed
  because a skeptic ran `pytest` concurrently against the shared test DB (`socialerp-pg-test`); a
  clean **solo** re-run passed (626). *Lesson:* **one test runner at a time on the shared DB**, and
  a gate measurement is only valid from a clean solo run — otherwise you get a false failure (or,
  worse, a false pass from polluted state). Record the solo result as the measurement of record.
