"""
Initial migration: Core tables with RLS.

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-15 10:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable UUID extension
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # Create tenants table
    op.create_table(
        "tenants",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("settings", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_tenants_slug_active", "tenants", ["slug", "is_active"])

    # Create audit_log table
    op.create_table(
        "audit_log",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(50), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("decision", sa.String(50), nullable=True),
        sa.Column("rule_ref", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])
    op.create_index("ix_audit_log_action_created", "audit_log", ["action", "created_at"])
    op.create_index("ix_audit_log_correlation", "audit_log", ["correlation_id"])
    op.create_foreign_key(
        "fk_audit_log_tenant", "audit_log", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
    )

    # Create outbox table
    op.create_table(
        "outbox",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("idempotency_key", sa.String(200), nullable=True, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbox_tenant_status_created", "outbox", ["tenant_id", "status", "created_at"]
    )
    op.create_foreign_key(
        "fk_outbox_tenant", "outbox", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
    )

    # Create idempotency_keys table
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("scope", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_idempotency_tenant_scope_key",
        "idempotency_keys",
        ["tenant_id", "scope", "key"],
        unique=True,
    )
    op.create_index(
        "ix_idempotency_tenant_scope_hash",
        "idempotency_keys",
        ["tenant_id", "scope", "request_hash"],
        unique=True,
    )
    op.create_index("ix_idempotency_expires", "idempotency_keys", ["expires_at"])
    op.create_foreign_key(
        "fk_idempotency_tenant",
        "idempotency_keys",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # =============================================================================
    # ROW LEVEL SECURITY SETUP
    # =============================================================================

    # Function to get current tenant_id from session variable
    op.execute("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
            SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE;
    """)

    # Enable RLS on all tenant-scoped tables
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;")

    # RLS Policies
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON tenants
            USING (id = current_tenant_id());
    """)

    op.execute("""
        CREATE POLICY tenant_isolation_policy ON audit_log
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id());
    """)

    op.execute("""
        CREATE POLICY tenant_isolation_policy ON outbox
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id());
    """)

    op.execute("""
        CREATE POLICY tenant_isolation_policy ON idempotency_keys
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id());
    """)


def downgrade() -> None:
    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON idempotency_keys;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON outbox;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON audit_log;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON tenants;")

    # Drop function
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id();")

    # Drop tables
    op.drop_table("idempotency_keys")
    op.drop_table("outbox")
    op.drop_table("audit_log")
    op.drop_table("tenants")

    # Disable UUID extension (only if no other tables use it)
    # op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
