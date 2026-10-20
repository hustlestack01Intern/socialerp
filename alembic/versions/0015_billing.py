"""Billing: subscriptions + usage counters/events (trial spine, plan M1.2).

Creates three tenant-scoped tables and applies the standard isolation pattern
(policy using ``app.current_tenant_id()`` + ``socialerp_app`` grants):

- ``subscriptions`` — one row per tenant (UNIQUE). Backfilled for every existing
  tenant as ``trial/active`` (fail-safe: a pilot tenant must not be bricked).
- ``usage_counters`` — monotonic counter per ``(tenant, metric, period)``.
- ``usage_events`` — append-only ledger; the counter is the sum of events, with
  UNIQUE ``(tenant_id, metric, source_event_id)`` so replay cannot double-credit.

Revision ID: 0015_billing
Revises: 0014_missing_tables
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_billing"
down_revision = "0014_missing_tables"
branch_labels = None
depends_on = None

_TENANT_TABLES = ("subscriptions", "usage_counters", "usage_events")


def upgrade() -> None:
    # subscriptions
    op.create_table(
        "subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan", sa.String(length=20), server_default="trial", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extension_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_extension_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_change_evidence", sa.String(length=200), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])
    op.create_index("ix_subscriptions_period_end", "subscriptions", ["period_end"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])

    # usage_counters
    op.create_table(
        "usage_counters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "metric", "period", name="uq_usage_counters_key"),
    )
    op.create_index("ix_usage_counters_tenant_id", "usage_counters", ["tenant_id"])

    # usage_events
    op.create_table(
        "usage_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source_event_id", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "metric",
            "source_event_id",
            name="uq_usage_events_source",
        ),
    )
    op.create_index("ix_usage_events_tenant_id", "usage_events", ["tenant_id"])
    op.create_index(
        "ix_usage_events_tenant_metric_period",
        "usage_events",
        ["tenant_id", "metric", "period"],
    )

    # Row-level security + grants for every new tenant-scoped table.
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_isolation ON {table}
            FOR ALL
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id())
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO socialerp_app")

    # Backfill: every existing tenant gets a subscription. Existing pilot
    # tenants are marked active so they are not bricked by the new gate; the
    # plan stays `trial` so they can still be converted. Idempotent (ON CONFLICT
    # DO NOTHING) so re-running never resets an already-configured row (INV20).
    op.execute("""
        INSERT INTO subscriptions (tenant_id, plan, status, period_start, period_end)
        SELECT id, 'trial', 'active', now(), now() + interval '14 days'
        FROM tenants
        ON CONFLICT (tenant_id) DO NOTHING
    """)


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("usage_events")
    op.drop_table("usage_counters")
    op.drop_table("subscriptions")
