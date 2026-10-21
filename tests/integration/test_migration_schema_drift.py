"""Guard: the migration chain must produce every table the ORM defines.

The test schema is built from ORM metadata, so a table that exists in the ORM
but not in ``alembic/versions`` passes every test while a freshly migrated
database is missing it. That happened for ``onboarding_wizard_progress``,
``customer_identities``, ``outbox`` and ``idempotency_keys`` (fixed in
``0014_missing_tables``).

This test runs the real migration chain against a scratch database,
compares the result with the ORM metadata, and checks that every
tenant-scoped table has row-level security enabled and policied.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

pytestmark = pytest.mark.integration

_ADMIN_DSN = "postgresql://socialerp:socialerp@localhost:5432/postgres"


def _orm_tables() -> set[str]:
    """Table names the ORM knows about at runtime."""
    import socialerp.idempotency  # ruff: ignore[unused-import] - registers idempotency_keys
    import socialerp.outbox  # ruff: ignore[unused-import] - registers outbox

    from packages.core.models import Base

    return set(Base.metadata.tables)


# Platform-scoped tables that carry a ``tenant_id`` column but deliberately
# have NO row-level security. Each entry must state why it is safe (the
# constitution's stop-rule was followed for the first one: see
# docs/failures/2026-10-21-platform-ledger-vs-rls-guard.md).
#
# - ``billing_payment_references`` (F2/U68): append-only ledger of M-Pesa
#   confirmation codes used to pay for plan changes. Its invariant is
#   cross-tenant UNIQUE(reference) — RLS would hide other tenants' rows and
#   destroy the dedupe. No customer PII (codes are merchant-facing); app-role
#   grants are SELECT/INSERT only (no UPDATE/DELETE). Compensating test:
#   tests/billing/test_billing_api.py::TestLedgerInvariants.
_PLATFORM_TABLES = frozenset({"billing_payment_references"})


def _orm_tenant_tables() -> set[str]:
    """ORM tables that carry a ``tenant_id`` column (must be RLS protected)."""
    import socialerp.idempotency  # ruff: ignore[unused-import]
    import socialerp.outbox  # ruff: ignore[unused-import]

    from packages.core.models import Base

    return {
        name
        for name, table in Base.metadata.tables.items()
        if "tenant_id" in table.columns and name not in _PLATFORM_TABLES
    }


@pytest.fixture(scope="module")
def migrated_schema() -> dict[str, object]:
    """Run ``alembic upgrade head`` on a scratch DB and return its schema.

    Yields while the scratch database still exists (a later test inspects its
    grants); the DB is dropped in teardown.
    """
    db_name = f"socialerp_migcheck_{uuid.uuid4().hex[:8]}"

    try:
        admin = psycopg2.connect(_ADMIN_DSN)
    except psycopg2.OperationalError as exc:  # pragma: no cover - env dependent
        pytest.skip(f"Postgres not reachable: {exc}")
    admin.autocommit = True

    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{db_name}"')

    dsn = f"postgresql+asyncpg://socialerp:socialerp@localhost:5432/{db_name}"
    env = {
        **os.environ,
        "DATABASE_URL": dsn,
        "SECRET_KEY": "a" * 40,
        "ENCRYPTION_KEY": "b" * 40,
        "POSTGRES_USER": "socialerp",
        "POSTGRES_PASSWORD": "socialerp",
        "POSTGRES_DB": db_name,
    }

    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"

        conn = psycopg2.connect(
            f"postgresql://socialerp:socialerp@localhost:5432/{db_name}",
        )
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
                )
                tables = {row[0] for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                    """,
                )
                rls = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT tablename, COUNT(*) FROM pg_policies
                    WHERE schemaname = 'public' GROUP BY tablename
                    """,
                )
                policies = dict(cur.fetchall())
        finally:
            conn.close()

        tables.discard("alembic_version")
        yield {
            "tables": tables,
            "rls": rls,
            "policies": policies,
            "db_name": db_name,
        }
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        admin.close()


def test_migrations_create_every_orm_table(migrated_schema) -> None:
    """Every ORM table must exist after ``alembic upgrade head``."""
    orm = _orm_tables()
    missing = sorted(orm - migrated_schema["tables"])
    assert not missing, f"Tables in the ORM but not in migrations: {missing}"


def test_migrations_do_not_create_orphan_tables(migrated_schema) -> None:
    """Migrations must not create tables the ORM does not know about."""
    orphan = sorted(migrated_schema["tables"] - _orm_tables())
    assert not orphan, f"Tables in migrations but not in the ORM: {orphan}"


def test_tenant_tables_have_rls_enabled(migrated_schema) -> None:
    """Every tenant-scoped table must have row-level security enabled.

    ``ENABLE`` is what isolates the application role (``socialerp_app``, a
    non-owner). ``FORCE`` (which also binds the table owner) is deliberately
    not required yet - see the open question in ``docs/UNCERTAINTY.md`` - so
    that owner-run tooling (migrations, seeding) keeps working.
    """
    rls: dict[str, tuple[bool, bool]] = migrated_schema["rls"]  # type: ignore[assignment]
    problems = [
        table for table in sorted(_orm_tenant_tables()) if not rls.get(table, (False, False))[0]
    ]
    assert not problems, f"Tenant tables without RLS enabled: {problems}"


def test_every_tenant_table_has_a_policy(migrated_schema) -> None:
    """A tenant table with RLS but no policy would deny (or leak) everything."""
    policies: dict[str, int] = migrated_schema["policies"]  # type: ignore[assignment]
    missing = sorted(t for t in _orm_tenant_tables() if policies.get(t, 0) == 0)
    assert not missing, f"Tenant tables without an RLS policy: {missing}"


def test_platform_tables_keep_their_carve_out_narrow(migrated_schema) -> None:
    """Compensating guard for the _PLATFORM_TABLES allowlist (F2/U68).

    The exempted ledger must stay exactly as designed: no RLS (cross-tenant
    uniqueness is the invariant), and the app role must hold only the
    append-only grants — if anyone widens the grants or the allowlist picks up
    a table that DOES have RLS (meaning someone enabled it and forgot to
    remove the carve-out), this fails and forces the accounting to be redone.
    """
    assert {"billing_payment_references"} == _PLATFORM_TABLES, (
        "the platform-table carve-out must stay a single, reviewed entry"
    )
    rls: dict[str, tuple[bool, bool]] = migrated_schema["rls"]  # type: ignore[assignment]
    for table in _PLATFORM_TABLES:
        assert rls.get(table, (False, False))[0] is False, (
            f"{table} now has RLS — remove it from _PLATFORM_TABLES"
        )

    conn = psycopg2.connect(
        f"postgresql://socialerp:socialerp@localhost:5432/{migrated_schema['db_name']}",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT privilege_type FROM information_schema.role_table_grants
                WHERE table_name = 'billing_payment_references'
                  AND grantee = 'socialerp_app'
                ORDER BY privilege_type
                """
            )
            grants = [row[0] for row in cur.fetchall()]
        assert grants == ["INSERT", "SELECT"], (
            f"billing_payment_references app grants must be append-only, got {grants}"
        )
    finally:
        conn.close()
