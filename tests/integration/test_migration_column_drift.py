"""The migration chain must produce every COLUMN the ORM defines.

This is the guard that the table-level drift test could not be (see
``tests/integration/test_migration_schema_drift.py``, which checks tables and
RLS). It caught a real defect on 2026-10-21: the ORM had grown
``tenants.slug``, ``users.full_name``, ``users.phone`` and seven agent-action
``audit_log`` columns that **no migration ever created**. A database built by
``alembic upgrade head`` could therefore not register a user
(``column users.full_name does not exist``) and could not record the agent
actions the constitution requires.

The 915-test suite stayed green through all of it, because the test schema
is built from ORM metadata - the very thing that had drifted. A fresh
migrated database was broken and nothing said so.

So this test migrates a scratch database and compares, column by column:

* the column exists;
* its type family matches (string / uuid / json / float / boolean / integer /
  timestamp);
* its nullability matches.

The type comparison is by *family*, not by exact type, because the ORM
declares enums as strings (``native_enum=False``) while the migration
created a Postgres enum type: both carry the same values, and demanding an
exact match would make the test cry wolf.

**And the values of an enum column are compared too** (added 2026-10-21 after
the skeptic's finding). The family check said "string == string" for
``users.role`` while the ORM wrote ``OWNER`` and the database only accepted
``owner`` - the second crash of the same day, and this guard would have
missed it. The labels of a native Postgres enum and the literals of a
CHECK constraint are now compared with the values the ORM actually writes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

pytestmark = pytest.mark.integration

_ADMIN_DSN = "postgresql://socialerp:socialerp@localhost:5432/postgres"

# Postgres data_type -> our type family.
_DB_FAMILY = {
    "character varying": "string",
    "character": "string",
    "text": "string",
    "uuid": "uuid",
    "jsonb": "json",
    "json": "json",
    "double precision": "float",
    "real": "float",
    "numeric": "float",
    "boolean": "boolean",
    "integer": "integer",
    "bigint": "integer",
    "smallint": "integer",
    "timestamp with time zone": "timestamp",
    "timestamp without time zone": "timestamp",
    "date": "timestamp",
    "USER-DEFINED": "string",  # Postgres enum: carries strings
}


def _orm_family(column) -> str:
    """The type family the ORM expects for a column."""
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB, UUID

    python_type = column.type
    if isinstance(python_type, UUID):
        return "uuid"
    if isinstance(python_type, JSONB):
        return "json"
    if isinstance(python_type, sa.JSON):
        return "json"
    if isinstance(python_type, sa.String):
        return "string"
    if isinstance(python_type, sa.Enum):
        # native_enum=False -> a string column with a CHECK constraint.
        return "string"
    if isinstance(python_type, sa.Float):
        return "float"
    if isinstance(python_type, sa.Boolean):
        return "boolean"
    if isinstance(python_type, sa.Integer):
        return "integer"
    if isinstance(python_type, sa.DateTime):
        return "timestamp"
    return "unknown"


def _orm_columns() -> dict[str, dict[str, tuple[str, bool]]]:
    """{table: {column: (family, nullable)}} straight from the ORM metadata."""
    import socialerp.idempotency  # ruff: ignore[unused-import] - registers idempotency_keys
    import socialerp.outbox  # ruff: ignore[unused-import] - registers outbox

    from packages.core.models import Base

    return {
        table.name: {
            column.name: (_orm_family(column), bool(column.nullable))
            for column in table.columns
        }
        for table in Base.metadata.tables.values()
    }


@pytest.fixture(scope="module")
def migrated_db() -> tuple[str, object]:
    """Migrate a scratch database once; return (database name, connection)."""
    db_name = f"socialerp_colcheck_{uuid.uuid4().hex[:8]}"

    try:
        admin = psycopg2.connect(_ADMIN_DSN)
    except psycopg2.OperationalError as exc:  # pragma: no cover - env dependent
        if os.getenv("CI"):
            pytest.fail(f"Postgres is not reachable in CI, so the column-drift "
                        f"guard would silently skip: {exc}")
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
            yield db_name, conn
        finally:
            conn.close()
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        admin.close()


@pytest.fixture(scope="module")
def migrated_columns(migrated_db) -> dict[str, dict[str, tuple[str, bool]]]:
    """Migrate a scratch database and return {table: {column: (family, nullable)}}."""
    _db_name, conn = migrated_db
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """,
        )
        columns: dict[str, dict[str, tuple[str, bool]]] = {}
        for table, column, data_type, is_nullable in cur.fetchall():
            family = _DB_FAMILY.get(data_type, f"unknown({data_type})")
            columns.setdefault(table, {})[column] = (
                family,
                is_nullable == "YES",
            )
    return columns


def test_every_orm_column_exists_in_the_migrated_schema(migrated_columns) -> None:
    """A column the ORM writes but the migration never created breaks the app."""
    missing: list[str] = []
    for table, columns in _orm_columns().items():
        db_columns = migrated_columns.get(table)
        if db_columns is None:
            missing.append(f"{table} (whole table)")
            continue
        for column in columns:
            if column not in db_columns:
                missing.append(f"{table}.{column}")
    assert not missing, (
        "the ORM has columns the migration chain never creates (a migrated "
        f"database would crash on them): {sorted(missing)}"
    )


def test_column_types_match(migrated_columns) -> None:
    """A column of the wrong type fails at runtime, not at import time."""
    wrong: list[str] = []
    for table, columns in _orm_columns().items():
        db_columns = migrated_columns.get(table, {})
        for column, (family, _nullable) in columns.items():
            db = db_columns.get(column)
            if db is None:
                continue
            if db[0] != family:
                wrong.append(f"{table}.{column}: ORM says {family}, DB says {db[0]}")
    assert not wrong, f"column types differ between the ORM and the migrations: {wrong}"


def test_column_nullability_matches(migrated_columns) -> None:
    """A NOT NULL column the ORM treats as optional rejects a valid row."""
    wrong: list[str] = []
    for table, columns in _orm_columns().items():
        db_columns = migrated_columns.get(table, {})
        for column, (_family, nullable) in columns.items():
            db = db_columns.get(column)
            if db is None:
                continue
            if db[1] != nullable:
                wrong.append(
                    f"{table}.{column}: ORM says "
                    f"{'nullable' if nullable else 'not null'}, DB says "
                    f"{'nullable' if db[1] else 'not null'}"
                )
    assert not wrong, f"column nullability differs between the ORM and the migrations: {wrong}"


# ---------------------------------------------------------------------------
# Enum values: what the ORM writes must be what the database accepts
# ---------------------------------------------------------------------------


def _orm_persisted_values(column) -> list[str] | None:
    """The values the ORM actually writes for an enum column.

    ``sqlalchemy.Enum`` writes the member NAMES by default and the member
    VALUES when ``values_callable`` is given (see
    ``packages.core.models.base.enum_values``). Getting this wrong is a
    runtime crash, not an import error - which is exactly what happened on
    2026-10-21 with ``users.role``.
    """
    import sqlalchemy as sa

    if not isinstance(column.type, sa.Enum):
        return None
    members = list(column.type.enums)
    if column.type.values_callable is not None:
        enum_cls = column.type.enum_class
        assert enum_cls is not None
        return [str(v) for v in column.type.values_callable(enum_cls)]
    # No values_callable: SQLAlchemy writes the member NAME. An enum built
    # from plain strings writes the string itself.
    return [m if isinstance(m, str) else str(m.name) for m in members]


def _db_allowed_values(conn, table: str, column: str) -> set[str] | None:
    """The values the database accepts for a column, or None if it accepts any.

    * a native Postgres enum -> its labels;
    * a varchar with a CHECK constraint -> the literals in the constraint;
    * a plain column -> None (anything goes).
    """
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT udt_name, data_type FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        row = cur.fetchone()
        if row is None:
            return None
        udt_name, data_type = row

        if data_type == "USER-DEFINED":
            cur.execute(
                """
                SELECT e.enumlabel FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = %s
                """,
                (udt_name,),
            )
            return {r[0] for r in cur.fetchall()}

        if data_type in ("character varying", "character", "text"):
            cur.execute(
                """
                SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
                WHERE c.conrelid = to_regclass(%s) AND c.contype = 'c'
                """,
                (f"public.{table}",),
            )
            for (definition,) in cur.fetchall():
                if column in definition:
                    return set(re.findall(r"'([^']*)'", definition))
    return None


def test_enum_values_match_what_the_database_accepts(migrated_db) -> None:
    """The ORM's enum values must be in the database's set of allowed values.

    The type-family check calls every enum a "string", so it cannot see a
    value mismatch. This one can: on 2026-10-21 the ORM wrote ``OWNER`` into
    a Postgres enum that only accepted ``owner``, and the migrated database
    could not register an owner.
    """
    import socialerp.idempotency  # ruff: ignore[unused-import] - registers idempotency_keys
    import socialerp.outbox  # ruff: ignore[unused-import] - registers outbox

    from packages.core.models import Base

    _db_name, conn = migrated_db
    wrong: list[str] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            values = _orm_persisted_values(column)
            if values is None:
                continue
            allowed = _db_allowed_values(conn, table.name, column.name)
            if allowed is None:
                continue
            missing = sorted(set(values) - allowed)
            if missing:
                wrong.append(
                    f"{table.name}.{column.name}: the ORM writes {missing}, "
                    f"the database accepts {sorted(allowed)}"
                )
    assert not wrong, (
        "the ORM writes enum values the migrated database rejects: "
        f"{wrong}"
    )


def test_the_application_role_cannot_rewrite_an_invite_code(migrated_db) -> None:
    """The app role may update the redemption columns, nothing else.

    A table-wide UPDATE would let any future code path reset ``uses`` to 0
    and re-mint redemptions from one code. The grant is therefore column
    scoped, and this guard says so.
    """
    _db_name, conn = migrated_db
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT privilege_type, column_name FROM information_schema.column_privileges
            WHERE grantee = 'socialerp_app' AND table_name = 'invite_codes'
            """,
        )
        rows = cur.fetchall()
    table_wide_update = any(
        privilege == "UPDATE" and column_name is None for privilege, column_name in rows
    )
    column_updates = {
        column_name for privilege, column_name in rows if privilege == "UPDATE"
    }
    assert not table_wide_update, (
        "the application role has a table-wide UPDATE on invite_codes; it must "
        "be limited to the redemption columns"
    )
    assert column_updates == {
        "uses",
        "last_redeemed_at",
        "redeemed_by_tenant_id",
        "redeemed_at",
    }, f"unexpected UPDATE columns on invite_codes: {sorted(column_updates)}"
