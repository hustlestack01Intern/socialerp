"""
Initial migration: Create core tables with Row-Level Security (RLS)

This migration creates:
- tenants table
- users table
- audit_log table
- api_keys table

And enables RLS on all tables with policies that enforce tenant isolation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create uuid-ossp extension for UUID generation (if not using gen_random_uuid)
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    # Create app schema for RLS session variables
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("GRANT USAGE ON SCHEMA app TO PUBLIC")

    # Create function to get current tenant ID from session variable
    op.execute("""
        CREATE OR REPLACE FUNCTION app.current_tenant_id() RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        $$
    """)
    op.execute("GRANT EXECUTE ON FUNCTION app.current_tenant_id() TO PUBLIC")

    # =========================================================================
    # TENANTS TABLE
    # =========================================================================
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner_phone", sa.String(20), nullable=False),
        sa.Column("owner_email", sa.String(255), nullable=False),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("ix_tenants_owner_email", "tenants", ["owner_email"])
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"])

    # Enable RLS on tenants
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")

    # Policy: Tenants can only see their own row
    op.execute("""
        CREATE POLICY tenant_isolation ON tenants
        FOR ALL
        USING (id = app.current_tenant_id())
        WITH CHECK (id = app.current_tenant_id())
    """)

    # =========================================================================
    # USERS TABLE
    # =========================================================================
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("owner", "staff", "rider", name="user_role"), nullable=False, server_default=sa.text("'staff'")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_tenant_id_is_active", "users", ["tenant_id", "is_active"])
    op.create_unique_constraint("uq_users_tenant_email", "users", ["tenant_id", "email"])

    # Enable RLS on users
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")

    # Policy: Users can only see users in their tenant
    op.execute("""
        CREATE POLICY user_isolation ON users
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # AUDIT LOG TABLE
    # =========================================================================
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource", sa.String(100), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before_json", postgresql.JSONB, nullable=True),
        sa.Column("after_json", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])
    op.create_index("ix_audit_log_tenant_action", "audit_log", ["tenant_id", "action"])
    op.create_index("ix_audit_log_tenant_resource", "audit_log", ["tenant_id", "resource", "resource_id"])
    op.create_index("ix_audit_log_user_created", "audit_log", ["user_id", "created_at"])

    # Enable RLS on audit_log
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")

    # Policy: Audit logs visible only to own tenant
    # INSERT allowed for own tenant (audit logging)
    # SELECT allowed for own tenant
    # UPDATE/DELETE NOT allowed (append-only)
    op.execute("""
        CREATE POLICY audit_log_isolation ON audit_log
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # Revoke UPDATE and DELETE on audit_log for application role (append-only)
    # This will be enforced by not granting those permissions
    # We'll handle this via GRANT statements after tables are created

    # =========================================================================
    # API KEYS TABLE
    # =========================================================================
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("scopes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_tenant_id_revoked", "api_keys", ["tenant_id", "revoked_at"])
    op.create_index("ix_api_keys_expires_at", "api_keys", ["expires_at"])

    # Enable RLS on api_keys
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")

    # Policy: API keys visible only to own tenant
    op.execute("""
        CREATE POLICY api_key_isolation ON api_keys
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # GRANTS FOR APPLICATION ROLE
    # =========================================================================
    # Create application role if not exists
    op.execute("DO $$ BEGIN CREATE ROLE socialerp_app; EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # Grant usage on schemas
    op.execute("GRANT USAGE ON SCHEMA public TO socialerp_app")
    op.execute("GRANT USAGE ON SCHEMA app TO socialerp_app")

    # Grant SELECT, INSERT, UPDATE, DELETE on tables (but NOT BYPASSRLS)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenants TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO socialerp_app")
    op.execute("GRANT SELECT, INSERT ON audit_log TO socialerp_app")  # No UPDATE/DELETE - append only
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys TO socialerp_app")

    # Grant usage on sequences
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")

    # Grant execute on functions
    op.execute("GRANT EXECUTE ON FUNCTION app.current_tenant_id() TO socialerp_app")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE ALL ON tenants FROM socialerp_app")
    op.execute("REVOKE ALL ON users FROM socialerp_app")
    op.execute("REVOKE ALL ON audit_log FROM socialerp_app")
    op.execute("REVOKE ALL ON api_keys FROM socialerp_app")
    op.execute("REVOKE USAGE ON SCHEMA app FROM socialerp_app")

    # Drop policies (automatically dropped with tables, but explicit for clarity)
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenants")
    op.execute("DROP POLICY IF EXISTS user_isolation ON users")
    op.execute("DROP POLICY IF EXISTS audit_log_isolation ON audit_log")
    op.execute("DROP POLICY IF EXISTS api_key_isolation ON api_keys")

    # Drop tables
    op.drop_table("api_keys")
    op.drop_table("audit_log")
    op.drop_table("users")
    op.drop_table("tenants")

    # Drop function
    op.execute("DROP FUNCTION IF EXISTS app.current_tenant_id()")

    # Drop enum
    op.execute("DROP TYPE IF EXISTS user_role")

    # Drop schema (if empty)
    op.execute("DROP SCHEMA IF EXISTS app")
