"""
M3: Add Reservations table with Row-Level Security (RLS)

This migration creates:
- reservations table (stock reservations with TTL)

And enables RLS with policies that enforce tenant isolation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_reservations"
down_revision = "0004_stock_movements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # RESERVATIONS TABLE (stock reservations with TTL)
    # =========================================================================
    op.create_table(
        "reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("reason", sa.String(30), nullable=False, server_default=sa.text("'order_created'")),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference_type", sa.String(50), nullable=False, server_default=sa.text("'order'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_reservations_tenant_id", "reservations", ["tenant_id"])
    op.create_index("ix_reservations_tenant_status", "reservations", ["tenant_id", "status"])
    op.create_index("ix_reservations_tenant_expires", "reservations", ["tenant_id", "expires_at"])
    op.create_index("ix_reservations_tenant_product", "reservations", ["tenant_id", "product_id"])
    op.create_index("ix_reservations_reference", "reservations", ["reference_id", "reference_type"])

    # Enable RLS on reservations
    op.execute("ALTER TABLE reservations ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY reservation_isolation ON reservations
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # GRANTS FOR APPLICATION ROLE
    # =========================================================================
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON reservations TO socialerp_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE ALL ON reservations FROM socialerp_app")

    # Drop policy
    op.execute("DROP POLICY IF EXISTS reservation_isolation ON reservations")

    # Drop table
    op.drop_table("reservations")
