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
def migrated_columns() -> dict[str, dict[str, tuple[str, bool]]]:
    """Migrate a scratch database and return {table: {column: (family, nullable)}}."""
    db_name = f"socialerp_colcheck_{uuid.uuid4().hex[:8]}"

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
        finally:
            conn.close()
        yield columns
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        admin.close()


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
