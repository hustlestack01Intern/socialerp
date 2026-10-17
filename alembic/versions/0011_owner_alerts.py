"""Phase 7: owner alerts table with Row-Level Security (RLS).

Replaces log-only alerting (docs/UNCERTAINTY.md U15): money captured on a
non-collectable order, reconciliation mismatches and failed deliveries now
persist as queryable, acknowledgeable rows instead of a warning line that
rotation can swallow. Every table carries ``tenant_id`` and is covered by the
standard tenant isolation policy plus grants to the ``socialerp_app`` API role
(the same pattern as 0002-0010; see ADR 0010).

``dedupe_key`` is unique per tenant so an at-least-once outbox handler cannot
raise two alerts for the same subject; it is nullable, so ad-hoc alerts (no key)
are unconstrained (Postgres treats NULLs as distinct).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_owner_alerts"
down_revision = "0010_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="warning"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedupe_key", sa.String(120), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_owner_alerts_tenant_dedupe"),
    )
    op.create_index("ix_owner_alerts_tenant_status", "owner_alerts", ["tenant_id", "status"])
    op.create_index("ix_owner_alerts_tenant_created", "owner_alerts", ["tenant_id", "created_at"])

    # RLS + grants (standard tenant isolation policy; ADR 0010).
    table = "owner_alerts"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {table}_isolation ON {table}
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO socialerp_app")


def downgrade() -> None:
    table = "owner_alerts"
    op.execute(f"REVOKE ALL ON {table} FROM socialerp_app")
    op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Indexes are dropped with their table.
    op.drop_table("owner_alerts")
