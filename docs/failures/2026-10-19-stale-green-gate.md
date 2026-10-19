# 2026-10-19 — Stale "green gate" belief (Phase 9 not gate-verified at HEAD)

**Protocol**: CONSTITUTION §4 (reasoning-failure) — plan prediction vs tool result disagree.

## What was expected
`docs/STATE.md` and `docs/FINAL_DELIVERY_SUMMARY.md` claim the gate is green
(764 passed, evals 8/8, `make check` EXIT=0) at the wave-2 head `e7170d2`, with
Phases 0–9 complete. The session's working assumption entering this task was
"code complete and green; only Phase 10 remains."

## What happened (measured this session, HEAD = `c955fbd` + dirty working tree)
- `uv run ruff check .` → **34 errors** across 15 files, including COMMITTED files:
  `packages/core/onboarding/*` (6), `packages/core/models/instagram.py` (2),
  `packages/integrations/meta/webhook.py`, `packages/core/socialerp/outbox.py`,
  `packages/core/models/crm.py`, `tests/property/test_domain_primitives.py` (4),
  `tests/e2e/test_instagram_channel.py`, `tests/insights/test_insights_reports.py`.
- `uv run pytest tests/contract -q` → **10 failed, 1 passed** (uncommitted
  Phase-10-Slice-A contract tests: missing imports, fake-server fixture mismatch).
- Subagent full run (decision-scientist, exclusive DB): **788 passed, 15 failed,
  22 errors** (onboarding E2E 401s, fixture scoping for `fake_meta_server`,
  missing `app` schema in RLS property tests).
- `Makefile` lines 109/113/117/121/125/147/151/170 reference the **deleted**
  `infra/docker-compose.yml` and the **deleted** `packages/core/alembic.ini` —
  `make db-up`, `make migrate`, `make seed`, `make fake-*` would fail today.
- `make check` therefore exits red at `lint-python` before tests even run.

## What I believed
1. "Phases 0–9 code is done and the gate is green" — true only **at `e7170d2`**
   (2026-10-18 wave-2 gate). The subsequent commits (`913fe69`, Phase 9
   `c4db565…d1344fd`, docs `c955fbd`) were **never gated by a full solo
   `make check`**, and the dirty working tree (Slice A fragment) adds more breakage.
2. STATE.md is the state of record — it was **not updated after Phase 9 commits**,
   so it reports a superseded measurement as current.

## Which belief was wrong or unverified
- Wrong: "gate green at current HEAD". Unverified: that Phase 9 commits passed
  the full gate (evidence: `.pyc` files show partial runs, but lint failures in
  Phase 9 files prove no clean `make check` was run after them).

## Next different strategy (adopted in the PLAN)
1. Treat **gate truth (M0)** as the first milestone: sweep lint, fix the 15 test
   faults, repair the Makefile, then measure a clean solo `make check` at a clean
   working tree and paste the real output in STATE.md.
2. Never trust a green claim in STATE.md unless it names a **commit SHA + log
   file** from a run **after** the last code commit; the scribe must re-measure
   after every wave.
3. All downstream estimates (trial spine, first-client timeline) are recalibrated
   from the measured 788/15/22 baseline, not from the claimed 764-green.

## Additional findings (skeptic, 2026-10-19)
- **CI was red (or did not run) on the Phase 9 pushes.** `.github/workflows/ci.yml`
  runs `make lint` on push; the committed Phase 9 files carry lint errors, so a
  green CI would have caught them. **Root failure mode = pushing to main with a
  red/absent CI**, not only a stale scribe. Recovery adds `make check` CI truth in
  M0.5 and proposes branch protection / required checks (⚠ CI gate = protected
  zone → human approval).
- **`docs/FINAL_DELIVERY_SUMMARY.md` (client-facing) compounds the overclaim**:
  it claims "827+ total" and "ruff all checks passed", which is not true at HEAD.
  It must be corrected or withdrawn before external use; it is part of the M0
  docs-reconciliation scope.
- **Evals were not re-measured at HEAD.** The plan's AC demands 8/8; treat the
  eval number as unverified until M0.5.

