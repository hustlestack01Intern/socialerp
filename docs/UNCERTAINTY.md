# UNCERTAINTY LEDGER

Updated 2026-10-14 by Chief Engineer after a measured reconciliation session. Anything marked OPEN is a
STOP condition if it affects money, stock, security or privacy.

| # | Assumption | Confidence 0-1 | Why | How to Verify | Status |
|---|---|---|---|---|---|
| U1 | Order FSM semantics match ADR 0006 | 0.95 | Code table is `paid->{packed,refunded}`, no cancel-after-paid, `failed_delivery->{out_for_delivery,refunded}`, DELIVERED terminal; 149 property tests green | Re-verified in-process 2026-10-14 | **RESOLVED** (was OPEN) |
| U2 | Two import namespaces (`packages.core.*` + `socialerp.*`) can co-exist in ONE venv | 0.9 | Workspace membership added; `pytest --collect-only` 0 errors | `import socialerp`, `import packages.core` both succeed | RESOLVED (packaging); naming target still OPEN |
| U3 | `socialerp.domain.*` re-exports are broken | 0.05 | FALSE: all 65 re-export targets resolve; only issue was the package not installed + `Iterator` bug | verified in-process | **REFUTED** (stale doc) |
| U4 | M3 domain code is buggy (OrderItem/Money/over-release/FSM) | 0.1 | REFUTED: Money rejects floats, OrderItem constructible, over-release raises, timestamps apply, FSM matches ADR | in-process probes + 149 tests | **REFUTED** (stale report) |
| U5 | `make check` can run the real suite | 0.1 | Still FALSE: Makefile targets nonexistent per-package dirs + empty `tests/contract`; CI gate is protected | `make check` fails at step 1 | **OPEN — needs approved Makefile fix** |
| U6 | Root venv can import + collect the suite | 0.95 | `pytest --collect-only` → 241 collected, 0 errors | re-run anytime | RESOLVED |
| U7 | Runtime interpreter: pyproject says >=3.12/mypy 3.13, venv is 3.13.15 | 0.8 | Single venv now used by root; CI pins 3.12 | align pyproject `requires-python`/targets to 3.13 (or pin CI to 3.13) | OPEN (low risk) |
| U8 | M3 tables get Alembic migration + RLS before M3 done | 0.5 | TWO Alembic trees exist; M3 canonical tables have no migration + no RLS; `make migrate` runs the socialerp tree | collapse to one tree, add M3 + RLS | **OPEN — must happen** |
| U9 | Fake Meta/Daraja servers simulate provider behaviour faithfully | 0.8 | Fake Daraja now starts (SQL bug fixed); 12 fake-server tests green; no recorded sandbox webhooks yet | record real sandbox webhooks | Pending |
| U10 | `socialerp` vs `packages.core` as system of record | 0.55 | Two Bases + two migration trees; recommend `packages.core` canonical, `socialerp` facade (option D) | human decision | **OPEN — release blocker** |
| U11 | `routes/auth.py` targets canonical `User`/`ApiKeyManager` | 0.05 | FALSE: `User.full_name`/`phone` and old manager API do not exist on canonical models (201 mypy errors) | repoint to canonical models; re-run mypy | **REFUTED — must fix** |
| U12 | Full test suite is green | 0.9 | 234 passed, 1 failed (cross-event-loop flake in a protected tenant-isolation test), 6 skipped | fix event-loop harness w/ approval | OPEN (narrow) |

## Action required
- U10: human decision (ONE question). Recommend option D.
- U5: fix Makefile/CI with human approval (protected zone).
- U8: collapse Alembic trees + add M3/RLS migration.
- U11: repoint `routes/auth.py`; drive mypy to 0.
- U12: fix the event-loop flake in the tenant-isolation harness (approval).
