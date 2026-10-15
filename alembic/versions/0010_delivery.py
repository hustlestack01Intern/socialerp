"""Phase 7: delivery tables with Row-Level Security (RLS).

Creates ``delivery_zones``, ``riders`` and ``deliveries`` for the delivery
flow (zones with flat fees, rider identity by phone, and one physical
delivery attempt per order). Every table carries ``tenant_id`` and is covered
by the standard tenant isolation policy plus grants to the ``socialerp_app``
API role (the same pattern as 0002-0008; see ADR 0010). This keeps every tenant
table under RLS — a cross-tenant leak is a release blocker.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_delivery"
down_revision = "0009_metadata_rename"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_zones",
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
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("fee_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_delivery_zones_tenant_name"),
    )
    op.create_index("ix_delivery_zones_tenant_active", "delivery_zones", ["tenant_id", "active"])

    op.create_table(
        "riders",
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
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "phone", name="uq_riders_tenant_phone"),
    )
    op.create_index("ix_riders_tenant_active", "riders", ["tenant_id", "active"])

    op.create_table(
        "deliveries",
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
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_zones.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fee_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="assigned",
        ),
        sa.Column("failure_reason", sa.String(50), nullable=True),
        sa.Column("proof_photo", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_deliveries_tenant_status", "deliveries", ["tenant_id", "status"])
    op.create_index("ix_deliveries_order", "deliveries", ["order_id"])
    op.create_index("ix_deliveries_rider", "deliveries", ["rider_id"])

    # RLS + grants (standard tenant isolation policy; ADR 0010).
    for table in ("delivery_zones", "riders", "deliveries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_isolation ON {table}
            FOR ALL
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id())
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO socialerp_app")


def downgrade() -> None:
    for table in ("deliveries", "riders", "delivery_zones"):
        op.execute(f"REVOKE ALL ON {table} FROM socialerp_app")
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Indexes are dropped with their tables.
    op.drop_table("deliveries")
    op.drop_table("riders")
    op.drop_table("delivery_zones")
