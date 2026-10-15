"""
M3: Add Catalog tables with Row-Level Security (RLS)

This migration creates:
- categories table
- products table
- product_variants table
- suppliers table
- locations table

And enables RLS on all tables with policies that enforce tenant isolation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_catalog_tables"
down_revision = "0002_whatsapp_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # CATEGORIES TABLE (no dependencies)
    # =========================================================================
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_categories_tenant_id", "categories", ["tenant_id"])
    op.create_index("ix_categories_tenant_parent", "categories", ["tenant_id", "parent_id"])
    op.create_index("ix_categories_tenant_active", "categories", ["tenant_id", "is_active"])
    op.create_unique_constraint("uq_categories_tenant_name", "categories", ["tenant_id", "name"])

    # Enable RLS on categories
    op.execute("ALTER TABLE categories ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY category_isolation ON categories
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # SUPPLIERS TABLE (no dependencies)
    # =========================================================================
    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(100), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])
    op.create_index("ix_suppliers_tenant_active", "suppliers", ["tenant_id", "is_active"])

    # Enable RLS on suppliers
    op.execute("ALTER TABLE suppliers ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY supplier_isolation ON suppliers
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # LOCATIONS TABLE (no dependencies)
    # =========================================================================
    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_locations_tenant_id", "locations", ["tenant_id"])
    op.create_index("ix_locations_tenant_active", "locations", ["tenant_id", "is_active"])
    op.create_unique_constraint("uq_locations_tenant_name", "locations", ["tenant_id", "name"])

    # Enable RLS on locations
    op.execute("ALTER TABLE locations ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY location_isolation ON locations
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # PRODUCTS TABLE (depends on categories, tenants)
    # =========================================================================
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("base_sku", sa.String(50), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True, server_default=sa.text("''")),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("images", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_index("ix_products_tenant_status", "products", ["tenant_id", "status"])
    op.create_index("ix_products_tenant_category", "products", ["tenant_id", "category_id"])

    # Enable RLS on products
    op.execute("ALTER TABLE products ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY product_isolation ON products
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # PRODUCT VARIANTS TABLE (depends on products, suppliers, locations, tenants)
    # =========================================================================
    op.create_table(
        "product_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cost_price_cents", sa.Integer, nullable=False),
        sa.Column("sell_price_cents", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("reorder_level", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_product_variants_tenant_id", "product_variants", ["tenant_id"])
    op.create_index("ix_product_variants_tenant_product", "product_variants", ["tenant_id", "product_id"])
    op.create_index("ix_product_variants_tenant_active", "product_variants", ["tenant_id", "is_active"])
    op.create_unique_constraint("uq_product_variants_tenant_sku", "product_variants", ["tenant_id", "sku"])
    op.create_unique_constraint("uq_product_variants_product_sku", "product_variants", ["product_id", "sku"])

    # Enable RLS on product_variants
    op.execute("ALTER TABLE product_variants ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY product_variant_isolation ON product_variants
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # GRANTS FOR APPLICATION ROLE
    # =========================================================================
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON categories TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON products TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON product_variants TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON suppliers TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON locations TO socialerp_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE ALL ON categories FROM socialerp_app")
    op.execute("REVOKE ALL ON products FROM socialerp_app")
    op.execute("REVOKE ALL ON product_variants FROM socialerp_app")
    op.execute("REVOKE ALL ON suppliers FROM socialerp_app")
    op.execute("REVOKE ALL ON locations FROM socialerp_app")
    op.execute("REVOKE USAGE ON SCHEMA app FROM socialerp_app")

    # Drop policies
    op.execute("DROP POLICY IF EXISTS category_isolation ON categories")
    op.execute("DROP POLICY IF EXISTS product_isolation ON products")
    op.execute("DROP POLICY IF EXISTS product_variant_isolation ON product_variants")
    op.execute("DROP POLICY IF EXISTS supplier_isolation ON suppliers")
    op.execute("DROP POLICY IF EXISTS location_isolation ON locations")

    # Drop tables in reverse dependency order
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("suppliers")
    op.drop_table("locations")
    op.drop_table("categories")
