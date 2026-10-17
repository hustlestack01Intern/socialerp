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
