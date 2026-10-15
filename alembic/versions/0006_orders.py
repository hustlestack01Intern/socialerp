"""
M3: Add Orders tables with Row-Level Security (RLS)

This migration creates:
- customers table
- orders table
- order_items table

And enables RLS with policies that enforce tenant isolation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_orders"
down_revision = "0005_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # CUSTOMERS TABLE (no dependencies)
    # =========================================================================
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("default_address", sa.Text, nullable=True),
        sa.Column("default_area", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_unique_constraint("uq_customers_tenant_phone", "customers", ["tenant_id", "phone"])

    # Enable RLS on customers
    op.execute("ALTER TABLE customers ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY customer_isolation ON customers
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # ORDERS TABLE (depends on customers, tenants)
    # =========================================================================
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_phone", sa.String(20), nullable=False),
        sa.Column("customer_name", sa.String(200), nullable=True),
        sa.Column("customer_email", sa.String(255), nullable=True),
        sa.Column("subtotal_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("discount_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("order_type", sa.String(20), nullable=False, server_default=sa.text("'standard'")),
        sa.Column("delivery_address", sa.Text, nullable=True),
        sa.Column("delivery_area", sa.String(100), nullable=True),
        sa.Column("delivery_lat", sa.Float, nullable=True),
        sa.Column("delivery_lng", sa.Float, nullable=True),
        sa.Column("delivery_instructions", sa.Text, nullable=True),
        sa.Column("delivery_fee_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_method", sa.String(30), nullable=False, server_default=sa.text("'mpesa_stk'")),
        sa.Column("payment_status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("payment_amount_cents", sa.Integer, nullable=True),
        sa.Column("payment_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("packed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True, server_default=sa.text("''")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])
    op.create_index("ix_orders_tenant_status", "orders", ["tenant_id", "status"])
    op.create_index("ix_orders_tenant_customer", "orders", ["tenant_id", "customer_id"])
    op.create_index("ix_orders_tenant_created", "orders", ["tenant_id", "created_at"])
    op.create_index("ix_orders_tenant_status_created", "orders", ["tenant_id", "status", "created_at"])

    # Enable RLS on orders
    op.execute("ALTER TABLE orders ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY order_isolation ON orders
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # ORDER ITEMS TABLE (depends on orders, products, product_variants)
    # =========================================================================
    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("unit_price_cents", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("total_price_cents", sa.Integer, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_tenant_id", "order_items", ["tenant_id"])

    # Enable RLS on order_items
    op.execute("ALTER TABLE order_items ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY order_item_isolation ON order_items
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # GRANTS FOR APPLICATION ROLE
    # =========================================================================
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON customers TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON orders TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON order_items TO socialerp_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE ALL ON customers FROM socialerp_app")
    op.execute("REVOKE ALL ON orders FROM socialerp_app")
    op.execute("REVOKE ALL ON order_items FROM socialerp_app")

    # Drop policies
    op.execute("DROP POLICY IF EXISTS customer_isolation ON customers")
    op.execute("DROP POLICY IF EXISTS order_isolation ON orders")
    op.execute("DROP POLICY IF EXISTS order_item_isolation ON order_items")

    # Drop tables in reverse dependency order
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("customers")
