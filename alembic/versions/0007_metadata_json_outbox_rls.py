"""M3 alignment: outbox_events table + RLS, audit_log.metadata_json.

Aligns the migrated schema with packages.core.models (system of record):

1. Creates `outbox_events` (missing from 0001-0006; it is the canonical
   OutboxEvent table in packages.core.models).
2. Enables Row-Level Security on `outbox_events` (tenant-scoped) with the
   standard tenant-isolation policy and grants to `socialerp_app`,
   matching the pattern of 0001. (M3 tables stock_movements,
   reservations, orders, order_items, customers, and the catalog tables
   already enable RLS in 0002-0006.)
3. Adds `audit_log.metadata_json JSONB` (used by AuditLogger for
   free-form metadata; the column existed in the model but not in 0001).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_metadata_json_outbox_rls"
down_revision = "0006_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # OUTBOX EVENTS table (canonical packages.core.models.OutboxEvent)
    # =========================================================================
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_outbox_events_tenant_created", "outbox_events", ["tenant_id", "created_at"])
    op.create_index("ix_outbox_events_processed", "outbox_events", ["processed_at"])

    # RLS + grants
    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY outbox_isolation ON outbox_events
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON outbox_events TO socialerp_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")

    # =========================================================================
    # AUDIT_LOG alignment: metadata_json column
    # =========================================================================
    op.add_column(
        "audit_log",
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    # =========================================================================
    # AUDIT_LOG alignment
    # =========================================================================
    op.drop_column("audit_log", "metadata_json")

    # =========================================================================
    # OUTBOX EVENTS: remove grants, policy, RLS, table
    # =========================================================================
    op.execute("REVOKE ALL ON outbox_events FROM socialerp_app")
    op.execute("DROP POLICY IF EXISTS outbox_isolation ON outbox_events")
    op.execute("ALTER TABLE outbox_events DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_outbox_events_processed", table_name="outbox_events")
    op.drop_index("ix_outbox_events_tenant_created", table_name="outbox_events")
    op.drop_table("outbox_events")