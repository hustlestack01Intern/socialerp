# Contributing

This is a security- and money-sensitive codebase. Contributions are welcome, but the rules
are stricter than a typical project. Read [`docs/CONSTITUTION.md`](docs/CONSTITUTION.md) first;
it wins over everything here.

---

## The short version

1. **Plan first.** Write down what you will change and how you will test it.
2. **Test first.** Add a test that fails for the right reason, then make it pass.
3. **One concern per commit.** Small, reviewable, honest.
4. **Run `make check`.** Report the real output. Never claim something ran when it did not.
5. **Never fake a result or weaken a test to make it pass.**
6. **Never commit a secret.** Only `.env.example` is allowed.
7. **Synthetic data only.** Do not call live payment, tax or messaging endpoints.
8. **Get human approval before touching a protected zone** (below). Before, not after.

---

## Setup

```bash
git clone https://github.com/hustlestack01Intern/socialerp.git
cd socialerp
cp .env.example .env
uv sync
make db-up
make migrate
make run-api          # :8000
make run-worker       # separate shell
make run-dashboard    # :3000, requires pnpm install in apps/dashboard
```

Requirements: Python 3.13, `uv`, Docker (for Postgres and Redis), Node 22 + pnpm for the
dashboard.

---

## The gate

```bash
make check   # ruff + eslint + mypy --strict + tsc + pytest + evals
```

Nothing is done until this passes and you paste the real numbers in the pull request. The
evaluation harness enforces a hard invariant: **zero wrong-price and zero wrong-stock
claims** on the labelled set.

**One test runner at a time.** The integration suite uses a shared test database. A second
concurrent run produces false results. Wait for a clean, solo run before trusting a gate.

---

## Protected zones

The following areas require explicit human approval **before** editing. This is a safety rule,
not a formality:

- tenant isolation, row-level security, middleware, and the database role
- authentication, sessions, tokens, invites
- payments, billing, entitlements, subscription state
- privacy, consent, export and erasure
- security tests, evaluation sets, and the CI gate itself

If a task touches one of these, stop and ask. Do not self-approve.

---

## Commit style

Match the repository's voice: a short imperative subject, a specific body, and the reason.

```
fix(security): the refresh token leaves the response body (U79)

The refresh token now lives only in the HttpOnly cookie. /register,
/login and /refresh return the short-lived access token alone, and the
test asserts the body no longer carries refresh_token.
```

Prefixes in use: `feat`, `fix`, `docs`, `test`, `chore`, `ci`, `refactor`.

---

## When you change behaviour

- **Record the decision.** Add or amend an ADR in [`docs/adr/`](docs/adr/). Numbered files;
  if a number already exists, do not renumber history — add a note or a new number.
- **Update the truth.** `docs/STATE.md` and `docs/UNCERTAINTY.md` are living records. If you
  resolve a known gap, say so with evidence. If you open one, add it.
- **Add observability.** New money, stock or message paths need logs/traces/metrics and PII
  redaction.
- **Prove isolation.** If you add a tenant table or endpoint, add a cross-tenant test.

---

## What gets a change rejected

- A green test that was weakened to pass.
- A claim in the UI or docs that the code does not enforce.
- A hard-coded credential, phone number, token or real customer record.
- An unfiltered tenant query, or a new table without `tenant_id` and RLS policy.
- Money or stock arithmetic performed by the model.
- A non-idempotent handler on a webhook, payment or outbox path.
- An edit to a protected zone without prior approval.

---

## Questions

Open a discussion or an issue. For security reports, follow [`SECURITY.md`](SECURITY.md),
not a public issue.
