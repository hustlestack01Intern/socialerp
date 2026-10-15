"""Phase 4: payments table with Row-Level Security (RLS).

Creates the tenant-scoped ``payments`` ledger (one row per STK push attempt)
and enables RLS using the standard tenant-isolation policy, plus grants to
``socialerp_app``, matching the pattern of 0001/0006/0007.

Tenant resolution for inbound Daraja callbacks does not use a separate lookup
table: the callback URL embeds the tenant id and a secret token, so the handler
sets tenant context and then looks the payment up within the tenant (see the
payments API route and ADR 0010). This keeps every tenant table under RLS.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_payments"
down_revision = "0007_metadata_json_outbox_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False, server_default="daraja"),
        sa.Column("method", sa.String(30), nullable=False, server_default="mpesa_stk"),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="initiated"),
        sa.Column("checkout_request_id", sa.String(100), nullable=True),
        sa.Column("merchant_request_id", sa.String(100), nullable=True),
        sa.Column("mpesa_receipt_number", sa.String(100), nullable=True),
        sa.Column("result_code", sa.Integer, nullable=True),
        sa.Column("result_desc", sa.Text, nullable=True),
        sa.Column("failure_reason", sa.String(50), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "metadata_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_tenant_status", "payments", ["tenant_id", "status"])
    op.create_index("ix_payments_tenant_order", "payments", ["tenant_id", "order_id"])
    op.create_index("ix_payments_tenant_created", "payments", ["tenant_id", "created_at"])
    # NULLs are distinct in Postgres unique indexes, so unattempted rows are fine.
    op.create_index(
        "uq_payments_tenant_checkout_request_id",
        "payments",
        ["tenant_id", "checkout_request_id"],
        unique=True,
    )

    # RLS + grants
    op.execute("ALTER TABLE payments ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY payment_isolation ON payments
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON payments TO socialerp_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON payments FROM socialerp_app")
    op.execute("DROP POLICY IF EXISTS payment_isolation ON payments")
    op.execute("ALTER TABLE payments DISABLE ROW LEVEL SECURITY")
    op.drop_index("uq_payments_tenant_checkout_request_id", table_name="payments")
    op.drop_index("ix_payments_tenant_created", table_name="payments")
    op.drop_index("ix_payments_tenant_order", table_name="payments")
    op.drop_index("ix_payments_tenant_status", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_index("ix_payments_tenant_id", table_name="payments")
    op.drop_table("payments")
