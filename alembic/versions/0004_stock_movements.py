"""
M3: Add Stock Movements table with Row-Level Security (RLS)

This migration creates:
- stock_movements table (event-sourced ledger)

And enables RLS with policies that enforce tenant isolation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_stock_movements"
down_revision = "0003_catalog_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # STOCK MOVEMENTS TABLE (event-sourced ledger)
    # =========================================================================
    op.create_table(
        "stock_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("quantity_change", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference_type", sa.String(50), nullable=True),
        sa.Column("previous_available", sa.Integer, nullable=False),
        sa.Column("new_available", sa.Integer, nullable=False),
        sa.Column("unit_cost_cents", sa.Integer, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_stock_movements_tenant_id", "stock_movements", ["tenant_id"])
    op.create_index("ix_stock_movements_tenant_product", "stock_movements", ["tenant_id", "product_id"])
    op.create_index("ix_stock_movements_tenant_variant", "stock_movements", ["tenant_id", "variant_id"])
    op.create_index("ix_stock_movements_tenant_type", "stock_movements", ["tenant_id", "movement_type"])
    op.create_index("ix_stock_movements_tenant_created", "stock_movements", ["tenant_id", "created_at"])
    op.create_index("ix_stock_movements_reference", "stock_movements", ["reference_id", "reference_type"])

    # Enable RLS on stock_movements
    op.execute("ALTER TABLE stock_movements ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY stock_movement_isolation ON stock_movements
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # GRANTS FOR APPLICATION ROLE
    # =========================================================================
    op.execute("GRANT SELECT, INSERT ON stock_movements TO socialerp_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE ALL ON stock_movements FROM socialerp_app")

    # Drop policy
    op.execute("DROP POLICY IF EXISTS stock_movement_isolation ON stock_movements")

    # Drop table
    op.drop_table("stock_movements")
