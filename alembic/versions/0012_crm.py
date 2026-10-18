"""Phase 8: CRM memory tables with RLS (+ append-only trigger).

customer_events (append-only log), consent_records (latest-wins consent),
customer_traits (versioned, evidence-backed). Every table carries tenant_id and
follows the standard isolation pattern (policy + socialerp_app grants) from
migrations 0002-0011. A trigger blocks UPDATE/DELETE on customer_events so the
log is immutable even against raw SQL (the service exposes no such path either).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_crm"
down_revision = "0011_owner_alerts"
branch_labels = None
depends_on = None

_TENANT_TABLES = ("customer_events", "consent_records", "customer_traits")


def upgrade() -> None:
    op.create_table(
        "customer_events",
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
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=True),
        sa.Column("summary", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "data_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("dedupe_key", sa.String(150), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_customer_events_tenant_dedupe"),
    )
    op.create_index(
        "ix_customer_events_tenant_customer_time",
        "customer_events",
        ["tenant_id", "customer_id", "occurred_at"],
    )
    op.create_index("ix_customer_events_tenant_type", "customer_events", ["tenant_id", "event_type"])

    op.create_table(
        "consent_records",
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
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("evidence", sa.String(300), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index(
        "ix_consent_records_tenant_customer_purpose",
        "consent_records",
        ["tenant_id", "customer_id", "purpose"],
    )

    op.create_table(
        "customer_traits",
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
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "tenant_id", "customer_id", "name", "version", name="uq_customer_traits_version"
        ),
    )
    op.create_index(
        "ix_customer_traits_tenant_customer_name",
        "customer_traits",
        ["tenant_id", "customer_id", "name"],
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_isolation ON {table}
            FOR ALL
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id())
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO socialerp_app")

    # Append-only backstop: the event log cannot be rewritten or erased via SQL,
    # EXCEPT when an explicit erasure session flag is set (data-subject erasure).
    op.execute("""
        CREATE OR REPLACE FUNCTION customer_events_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.allow_event_erasure', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'customer_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER customer_events_no_mutate
        BEFORE UPDATE OR DELETE ON customer_events
        FOR EACH ROW EXECUTE FUNCTION customer_events_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS customer_events_no_mutate ON customer_events")
    op.execute("DROP FUNCTION IF EXISTS customer_events_immutable()")
    for table in _TENANT_TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM socialerp_app")
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("customer_traits")
    op.drop_table("consent_records")
    op.drop_table("customer_events")
