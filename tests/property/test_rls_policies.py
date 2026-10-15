"""
Property-based tests for RLS policies.

These tests derive from the invariant: "Cross-tenant access returns 0 rows (fail-closed)."
Run with: pytest tests/property/test_rls_policies.py -v
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from tests.property.test_rls_support import (
    RLSStateMachine,
    setup_database,
    teardown_database,
)

# =============================================================================
# Configuration
# =============================================================================

# Reduce examples for faster feedback in CI; disable deadline for DB tests
settings.register_profile(
    "ci", max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.register_profile(
    "dev", max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile("ci")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def db_setup():
    """Set up database once per test session."""
    setup_database()
    yield
    teardown_database()


# =============================================================================
# Stateful Property Tests
# =============================================================================


class TestRLSIsolation(RuleBasedStateMachine):
    """
    Stateful property tests for RLS isolation.

    Models the system as a state machine where:
    - Tenants and users are created
    - Context is switched between tenants
    - Operations are performed
    - Invariants are verified after each step
    """

    def __init__(self):
        super().__init__()
        self.machine = RLSStateMachine()

    @initialize()
    def setup(self):
        self.machine.setup()

    @rule(
        tenant_name=st.text(min_size=1, max_size=50),
        owner_phone=st.from_regex(r"^254[17]\d{8}$", fullmatch=True),
        owner_email=st.emails(),
    )
    def create_tenant(self, tenant_name, owner_phone, owner_email):
        """Create a new tenant."""
        self.machine.create_tenant(tenant_name, owner_phone, owner_email)

    @rule(
        email=st.emails(),
        role=st.sampled_from(["owner", "staff", "rider"]),
        data=st.data(),
    )
    def create_user(self, email, role, data):
        """Create a user in an existing tenant."""
        tenant = data.draw(st.sampled_from(self.machine.tenants)) if self.machine.tenants else None
        if tenant:
            self.machine.create_user(tenant, email, role)

    @rule(data=st.data())
    def switch_context(self, data):
        """Switch tenant context."""
        if self.machine.tenants:
            tenant = data.draw(st.sampled_from(self.machine.tenants))
            self.machine.set_context(tenant)

    @rule(data=st.data())
    def read_tenants(self, data):
        """Read tenants table."""
        self.machine.read_tenants()

    @rule(data=st.data())
    def read_users(self, data):
        """Read users table."""
        self.machine.read_users()

    @rule(data=st.data())
    def read_audit_logs(self, data):
        """Read audit logs table."""
        self.machine.read_audit_logs()

    @invariant()
    def cross_tenant_isolation(self):
        """Invariant: Cross-tenant access always returns 0 rows."""
        self.machine.verify_isolation()

    @invariant()
    def no_context_means_no_access(self):
        """Invariant: No context means no access to any tenant data."""
        self.machine.verify_no_context()


# Create test case
TestRLSIsolation.TestCase.settings = settings


# =============================================================================
# Traditional Property Tests
# =============================================================================


class TestRLSPoliciesTraditional:
    """Traditional property tests for RLS policies."""

    @given(st.integers(min_value=1, max_value=10))
    def test_multiple_tenants_isolation(self, num_tenants: int):
        """Test isolation with multiple tenants."""
        machine = RLSStateMachine()
        machine.setup()

        # Create tenants
        for i in range(num_tenants):
            machine.create_tenant(f"Tenant {i}", f"25471111111{i:02d}", f"tenant{i}@example.com")

        # Test each tenant's isolation
        for tenant in machine.tenants:
            machine.set_context(tenant)
            machine.read_tenants()
            machine.read_users()
            machine.read_audit_logs()
            machine.verify_isolation()

        # Test no context
        machine.reset_context()
        machine.read_tenants()
        machine.read_users()
        machine.read_audit_logs()
        machine.verify_no_context()

        machine.teardown()

    @given(st.integers(min_value=1, max_value=5))
    def test_user_isolation_within_tenant(self, num_users: int):
        """Test user isolation within a tenant."""
        machine = RLSStateMachine()
        machine.setup()

        tenant = machine.create_tenant("Test Tenant", "254711111111", "test@example.com")

        for i in range(num_users):
            machine.create_user(tenant, f"user{i}@example.com", "staff")

        machine.set_context(tenant)
        machine.read_users()
        machine.verify_isolation()

        machine.teardown()


# =============================================================================
# Fixture-based Tests
# =============================================================================


@pytest.fixture
def rls_machine():
    """Create RLS state machine for testing."""
    machine = RLSStateMachine()
    machine.setup()
    yield machine
    machine.teardown()


def test_single_tenant_isolation(rls_machine):
    """Test basic single tenant isolation."""
    tenant = rls_machine.create_tenant("Tenant A", "254711111111", "a@example.com")
    rls_machine.create_user(tenant, "user1@a.com", "owner")
    rls_machine.create_user(tenant, "user2@a.com", "staff")

    rls_machine.set_context(tenant)
    rls_machine.read_tenants()
    rls_machine.read_users()
    rls_machine.verify_isolation()


def test_two_tenant_isolation(rls_machine):
    """Test isolation between two tenants."""
    tenant_a = rls_machine.create_tenant("Tenant A", "254711111111", "a@example.com")
    tenant_b = rls_machine.create_tenant("Tenant B", "254722222222", "b@example.com")

    rls_machine.create_user(tenant_a, "user1@a.com", "owner")
    rls_machine.create_user(tenant_a, "user2@a.com", "staff")
    rls_machine.create_user(tenant_b, "user1@b.com", "owner")

    # Test tenant A context
    rls_machine.set_context(tenant_a)
    rls_machine.read_tenants()
    rls_machine.read_users()
    rls_machine.verify_isolation()

    # Test tenant B context
    rls_machine.set_context(tenant_b)
    rls_machine.read_tenants()
    rls_machine.read_users()
    rls_machine.verify_isolation()

    # Test no context
    rls_machine.reset_context()
    rls_machine.read_tenants()
    rls_machine.read_users()
    rls_machine.verify_no_context()


def test_tenant_cannot_see_other_tenant_users(rls_machine):
    """Test that tenant A cannot see tenant B's users."""
    tenant_a = rls_machine.create_tenant("Tenant A", "254711111111", "a@example.com")
    tenant_b = rls_machine.create_tenant("Tenant B", "254722222222", "b@example.com")

    rls_machine.create_user(tenant_a, "user1@a.com", "owner")
    rls_machine.create_user(tenant_b, "user1@b.com", "owner")

    rls_machine.set_context(tenant_a)
    users = rls_machine.read_users()
    # Should only see tenant A's users
    assert len(users) == 1
    assert users[0]["email"] == "user1@a.com"

    rls_machine.set_context(tenant_b)
    users = rls_machine.read_users()
    assert len(users) == 1
    assert users[0]["email"] == "user1@b.com"


def test_no_context_returns_empty(rls_machine):
    """Test that no context returns empty results."""
    tenant = rls_machine.create_tenant("Tenant A", "254711111111", "a@example.com")
    rls_machine.create_user(tenant, "user1@a.com", "owner")

    rls_machine.reset_context()

    tenants = rls_machine.read_tenants()
    assert len(tenants) == 0

    users = rls_machine.read_users()
    assert len(users) == 0


def test_audit_logs_isolation(rls_machine):
    """Test audit logs are isolated by tenant."""
    import uuid

    tenant_a = rls_machine.create_tenant("Tenant A", "254711111111", "a@example.com")
    tenant_b = rls_machine.create_tenant("Tenant B", "254722222222", "b@example.com")

    user_a = rls_machine.create_user(tenant_a, "user@a.com", "owner")
    user_b = rls_machine.create_user(tenant_b, "user@b.com", "owner")

    prod_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())

    rls_machine.set_context(tenant_a)
    # Simulate some actions that create audit logs
    rls_machine.log_action(tenant_a, user_a, "create", "product", prod_id)
    rls_machine.log_action(tenant_a, user_a, "update", "product", prod_id)

    rls_machine.set_context(tenant_b)
    rls_machine.log_action(tenant_b, user_b, "create", "order", order_id)

    # Verify isolation
    rls_machine.set_context(tenant_a)
    logs = rls_machine.read_audit_logs()
    assert len(logs) == 2

    rls_machine.set_context(tenant_b)
    logs = rls_machine.read_audit_logs()
    assert len(logs) == 1
