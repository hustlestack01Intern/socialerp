## What and why

<!-- What does this change, and which problem does it address? -->

## Safety check

- [ ] This change does not touch a protected zone (tenancy, money, privacy, auth, tests, gate),
      or I have prior human approval for it.
- [ ] The model still does not perform money or stock arithmetic.
- [ ] New state-changing paths are idempotent and tenant-scoped.

## Proof

<!-- Paste the real `make check` output. A previous green run is not proof. -->

```
ruff / eslint:
mypy / tsc:
pytest:
evals:
```

## Records

- [ ] `docs/STATE.md` / `docs/UNCERTAINTY.md` updated if a gap was opened or closed.
- [ ] ADR added or amended if a non-trivial decision was made.
- [ ] No secrets, real customer data, or live endpoint calls.
