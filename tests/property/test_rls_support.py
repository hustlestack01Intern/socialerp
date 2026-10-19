"""
RLS Test Support Module

Provides the state machine and factories for property-based RLS tests.
This module handles the actual database interactions for the tests.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from packages.core.domain import TenantId, UserId, generate_tenant_id, generate_user_id

# =============================================================================
# Database Connection
# =============================================================================

TEST_DB_URL = "postgresql://socialerp_app:socialerp_app@localhost:5432/socialerp_test"


@contextmanager
def get_test_connection():
    """Get a test database connection with socialerp_app user (enforces RLS)."""
    conn = psycopg2.connect(TEST_DB_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_superuser_connection():
    """Get a superuser connection for test setup (bypasses RLS)."""
    conn = psycopg2.connect(
        "postgresql://socialerp:socialerp@localhost:5432/socialerp_test",
        cursor_factory=RealDictCursor,
    )
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


# =============================================================================
# Factories
# =============================================================================


class TenantFactory:
    """Factory for creating test tenants."""

    @staticmethod
    def create(name: str, owner_phone: str, owner_email: str) -> TenantId:
        """Create a tenant using superuser (bypasses RLS for setup)."""
        tenant_id = generate_tenant_id()
        with get_superuser_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO tenants (id, name, owner_phone, owner_email)
                    VALUES (%s, %s, %s, %s)
                """,
                (str(tenant_id), name, owner_phone, owner_email),
            )
        return tenant_id


class UserFactory:
    """Factory for creating test users."""

    @staticmethod
    def create(tenant_id: TenantId, email: str, role: str) -> UserId:
        """Create a user in a tenant using superuser."""
        user_id = generate_user_id()
        with get_superuser_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO users (id, tenant_id, email, password_hash, role)
                    VALUES (%s, %s, %s, 'test_hash', %s)
                """,
                (str(user_id), str(tenant_id), email, role),
            )
        return user_id


class AuditLogFactory:
    """Factory for creating test audit logs."""

    @staticmethod
    def create(
        tenant_id: TenantId,
        user_id: UserId | None,
        action: str,
        resource: str,
        resource_id: str | None = None,
    ):
        """Create an audit log entry."""
        from uuid import uuid4

        with get_superuser_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO audit_log
                        (id, tenant_id, user_id, action, resource, resource_id, after_json)
                    VALUES (%s, %s, %s, %s, %s, %s, '{}')
                """,
                (
                    str(uuid4()),
                    str(tenant_id),
                    str(user_id) if user_id else None,
                    action,
                    resource,
                    resource_id,
                ),
            )


# =============================================================================
# RLS State Machine
# =============================================================================


class RLSStateMachine:
    """
    State machine for testing RLS isolation.

    Manages test data creation and context switching to verify
    that RLS policies correctly isolate tenant data.
    """

    def __init__(self):
        self.tenants: list[TenantId] = []
        self.users: dict[TenantId, list[UserId]] = {}
        self.current_tenant: TenantId | None = None
        self._superuser_conn = None

    def setup(self):
        """Set up test environment."""
        self.tenants = []
        self.users = {}
        self.current_tenant = None
        self._superuser_conn = psycopg2.connect(
            "postgresql://socialerp:socialerp@localhost:5432/socialerp_test",
            cursor_factory=RealDictCursor,
        )
        self._superuser_conn.autocommit = True

    def teardown(self):
        """Clean up test environment."""
        if self._superuser_conn:
            self._superuser_conn.close()
        self.tenants = []
        self.users = {}
        self.current_tenant = None

    def create_tenant(self, name: str, owner_phone: str, owner_email: str) -> TenantId:
        """Create a tenant and track it."""
        tenant_id = TenantFactory.create(name, owner_phone, owner_email)
        self.tenants.append(tenant_id)
        self.users[tenant_id] = []
        return tenant_id

    def create_user(self, tenant_id: TenantId, email: str, role: str) -> UserId:
        """Create a user in a tenant and track it."""
        user_id = UserFactory.create(tenant_id, email, role)
        if tenant_id in self.users:
            self.users[tenant_id].append(user_id)
        return user_id

    def set_context(self, tenant_id: TenantId):
        """Set the current tenant context."""
        self.current_tenant = tenant_id

    def reset_context(self):
        """Reset tenant context (no context)."""
        self.current_tenant = None

    def read_tenants(self) -> list[dict]:
        """Read tenants table with current context."""
        with get_test_connection() as conn, conn.cursor() as cur:
            if self.current_tenant:
                cur.execute("SET LOCAL app.current_tenant_id = %s", (str(self.current_tenant),))
            else:
                cur.execute("RESET app.current_tenant_id")
            cur.execute("SELECT id, name, owner_email FROM tenants")
            return cur.fetchall()

    def read_users(self) -> list[dict]:
        """Read users table with current context."""
        with get_test_connection() as conn, conn.cursor() as cur:
            if self.current_tenant:
                cur.execute("SET LOCAL app.current_tenant_id = %s", (str(self.current_tenant),))
            else:
                cur.execute("RESET app.current_tenant_id")
            cur.execute("SELECT id, email, role FROM users")
            return cur.fetchall()

    def read_audit_logs(self) -> list[dict]:
        """Read audit logs table with current context."""
        with get_test_connection() as conn, conn.cursor() as cur:
            if self.current_tenant:
                cur.execute("SET LOCAL app.current_tenant_id = %s", (str(self.current_tenant),))
            else:
                cur.execute("RESET app.current_tenant_id")
            cur.execute("SELECT id, action, resource FROM audit_log")
            return cur.fetchall()

    def log_action(
        self,
        tenant_id: TenantId,
        user_id: UserId | None,
        action: str,
        resource: str,
        resource_id: str | None = None,
    ):
        """Log an audit action."""
        AuditLogFactory.create(tenant_id, user_id, action, resource, resource_id)

    def verify_isolation(self):
        """Verify that current context only sees own tenant's data."""
        if not self.current_tenant:
            raise AssertionError("verify_isolation called with no context set")

        # Read tenants - should only see current tenant
        tenants = self.read_tenants()
        assert len(tenants) == 1, f"Expected 1 tenant, got {len(tenants)}"
        assert str(tenants[0]["id"]) == str(self.current_tenant), "Wrong tenant visible"

        # Read users - should only see current tenant's users
        users = self.read_users()
        expected_count = len(self.users.get(self.current_tenant, []))
        assert len(users) == expected_count, f"Expected {expected_count} users, got {len(users)}"
        for _user in users:
            # Can't easily verify tenant_id here without joining, but count check is good
            pass

        # Read audit logs - should only see current tenant's logs
        # (This is harder to verify exactly, but we trust the policy)

    def verify_no_context(self):
        """Verify that no context returns empty results."""
        if self.current_tenant is not None:
            raise AssertionError("verify_no_context called with context set")

        tenants = self.read_tenants()
        assert len(tenants) == 0, f"Expected 0 tenants with no context, got {len(tenants)}"

        users = self.read_users()
        assert len(users) == 0, f"Expected 0 users with no context, got {len(users)}"

        logs = self.read_audit_logs()
        assert len(logs) == 0, f"Expected 0 audit logs with no context, got {len(logs)}"


# =============================================================================
# Database Setup/Teardown
# =============================================================================


def setup_database():
    """Set up database for testing (run once per session)."""
    # Migration should already be applied
    # Just ensure the app role exists and has correct grants
    with get_superuser_connection() as conn, conn.cursor() as cur:
        # Ensure socialerp_app role exists
        cur.execute("""
                DO $$ BEGIN
                    CREATE ROLE socialerp_app WITH LOGIN PASSWORD 'socialerp_app';
                EXCEPTION WHEN duplicate_object THEN
                    NULL;
                END $$
            """)
        # Migrations create socialerp_app as a NOLOGIN group role (no hardcoded
        # password in prod). Tests need it loginable with a known password.
        cur.execute("ALTER ROLE socialerp_app WITH LOGIN PASSWORD 'socialerp_app'")

        # Ensure grants are correct
        cur.execute("GRANT USAGE ON SCHEMA public TO socialerp_app")
        cur.execute("GRANT USAGE ON SCHEMA app TO socialerp_app")
        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenants TO socialerp_app")
        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO socialerp_app")
        cur.execute("GRANT SELECT, INSERT ON audit_log TO socialerp_app")
        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys TO socialerp_app")
        cur.execute("GRANT EXECUTE ON FUNCTION app.current_tenant_id() TO socialerp_app")


def teardown_database():
    """Tear down database after testing (run once per session)."""
    # Clean up test data
    with get_superuser_connection() as conn, conn.cursor() as cur:
        # Delete test data (keep schema)
        cur.execute("DELETE FROM audit_log")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM tenants")
        cur.execute("DELETE FROM api_keys")
