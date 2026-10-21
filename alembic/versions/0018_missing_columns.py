"""Close the column drift between the ORM and the migration chain (M1.5).

**How this was found**: the sales-spine proof (scripts/prove_sales_spine.py)
ran a real registration against a database built by ``alembic upgrade head``
and it failed with ``column users.full_name does not exist``. The test suite
could not see it, because the test schema is built from ORM metadata - the
very thing that had drifted. 915 green tests, one broken production
database.

**What was missing** (ORM had it, the migrations never created it):

- ``tenants.slug`` - the public slug the registration route writes;
- ``users.full_name``, ``users.phone`` - written by the registration route;
- ``audit_log.actor_type``, ``actor_id``, ``payload``, ``decision``,
  ``rule_ref``, ``confidence``, ``correlation_id`` - the agent-action audit
  fields the constitution requires (ADR 0008). Without them the audit log
  cannot record *who* acted, *what was decided* or *which rule allowed it*.

**Also fixed**: ``audit_log.resource`` was ``NOT NULL`` in the migration while
the ORM allows NULL (agent-action entries carry ``payload``/``decision``
instead of a CRUD resource). The migration was stricter than the code, so an
agent-action audit row would have been rejected by the database.

The new guard ``tests/integration/test_migration_column_drift.py`` compares
every ORM column (name, type family, nullability) with the migrated schema,
so this cannot rot back.

Revision ID: 0018_missing_columns
Revises: 0017_invite_codes
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018_missing_columns"
down_revision = "0017_invite_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # tenants.slug (nullable, unique, indexed)
    # ------------------------------------------------------------------
    op.add_column(
        "tenants",
        sa.Column("slug", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # ------------------------------------------------------------------
    # users.full_name, users.phone
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))

    # ------------------------------------------------------------------
    # audit_log: the agent-action fields (ADR 0008)
    # ------------------------------------------------------------------
    op.add_column("audit_log", sa.Column("actor_type", sa.String(length=50), nullable=True))
    op.add_column("audit_log", sa.Column("actor_id", sa.String(length=100), nullable=True))
    op.add_column("audit_log", sa.Column("payload", postgresql.JSONB, nullable=True))
    op.add_column("audit_log", sa.Column("decision", sa.String(length=50), nullable=True))
    op.add_column("audit_log", sa.Column("rule_ref", sa.String(length=100), nullable=True))
    op.add_column("audit_log", sa.Column("confidence", sa.Float, nullable=True))
    op.add_column("audit_log", sa.Column("correlation_id", sa.String(length=100), nullable=True))
    op.create_index("ix_audit_log_actor_type", "audit_log", ["actor_type"])
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_decision", "audit_log", ["decision"])
    op.create_index("ix_audit_log_correlation_id", "audit_log", ["correlation_id"])

    # The ORM allows a NULL resource (agent-action entries use payload and
    # decision instead). The migration was stricter than the code, which
    # would have rejected a valid agent-action audit row.
    op.alter_column("audit_log", "resource", existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    op.alter_column("audit_log", "resource", existing_type=sa.String(length=100), nullable=False)
    op.drop_index("ix_audit_log_correlation_id", table_name="audit_log")
    op.drop_index("ix_audit_log_decision", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_type", table_name="audit_log")
    op.drop_column("audit_log", "correlation_id")
    op.drop_column("audit_log", "confidence")
    op.drop_column("audit_log", "rule_ref")
    op.drop_column("audit_log", "decision")
    op.drop_column("audit_log", "payload")
    op.drop_column("audit_log", "actor_id")
    op.drop_column("audit_log", "actor_type")
    op.drop_column("users", "phone")
    op.drop_column("users", "full_name")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_column("tenants", "slug")
