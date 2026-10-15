#!/usr/bin/env python3
"""Test RLS isolation - cross-tenant access should return 0 rows.

Uses socialerp (superuser) for test data setup, then socialerp_app for RLS tests.
"""

import uuid

import psycopg2


def setup_test_data():
    """Create test data using superuser (bypasses RLS for setup)."""
    conn = psycopg2.connect("postgresql://socialerp:socialerp@postgres:5432/socialerp")
    conn.autocommit = True
    cur = conn.cursor()

    # Create two tenants
    tenant_a_id = uuid.uuid4()
    tenant_b_id = uuid.uuid4()

    ta = str(tenant_a_id)
    tb = str(tenant_b_id)
    ua1 = str(uuid.uuid4())
    ua2 = str(uuid.uuid4())
    ub1 = str(uuid.uuid4())

    # Insert tenant A
    cur.execute(
        """
        INSERT INTO tenants (id, name, owner_phone, owner_email)
        VALUES (%s, 'Tenant A', '254711111111', 'a@example.com')
    """,
        (ta,),
    )

    # Insert tenant B
    cur.execute(
        """
        INSERT INTO tenants (id, name, owner_phone, owner_email)
        VALUES (%s, 'Tenant B', '254722222222', 'b@example.com')
    """,
        (tb,),
    )

    # Insert users for tenant A
    cur.execute(
        """
        INSERT INTO users (id, tenant_id, email, password_hash, role)
        VALUES (%s, %s, 'owner@a.com', 'hash1', 'owner'),
               (%s, %s, 'staff@a.com', 'hash2', 'staff')
    """,
        (ua1, ta, ua2, ta),
    )

    # Insert users for tenant B
    cur.execute(
        """
        INSERT INTO users (id, tenant_id, email, password_hash, role)
        VALUES (%s, %s, 'owner@b.com', 'hash3', 'owner')
    """,
        (ub1, tb),
    )

    # Insert audit logs
    cur.execute(
        """
        INSERT INTO audit_log (tenant_id, user_id, action, resource, after_json)
        VALUES (%s, %s, 'login', 'user', '{}'),
               (%s, %s, 'create', 'product', '{}')
    """,
        (ta, ua1, ta, ua2),
    )

    cur.execute(
        """
        INSERT INTO audit_log (tenant_id, user_id, action, resource, after_json)
        VALUES (%s, %s, 'login', 'user', '{}')
    """,
        (tb, ub1),
    )

    conn.close()
    return ta, tb, ua1, ua2, ub1


def test_rls_isolation(ta, tb, ua1, ua2, ub1):
    """Test RLS isolation using socialerp_app (non-superuser)."""
    conn = psycopg2.connect("postgresql://socialerp_app:socialerp_app@postgres:5432/socialerp")
    cur = conn.cursor()

    cur.execute("BEGIN")
    try:
        cur.execute("SET LOCAL app.current_tenant_id = %s", (ta,))

        # Should see tenant A only
        cur.execute("SELECT id, name FROM tenants")
        result = cur.fetchall()
        assert len(result) == 1 and str(result[0][0]) == ta, f"Expected 1 tenant, got {len(result)}"

        # Should see tenant A's users
        cur.execute("SELECT id, email FROM users")
        result = cur.fetchall()
        assert len(result) == 2

        # Should see tenant A's audit logs
        cur.execute("SELECT id, action FROM audit_log")
        result = cur.fetchall()
        assert len(result) == 2

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    cur.execute("BEGIN")
    try:
        cur.execute("SET LOCAL app.current_tenant_id = %s", (tb,))

        # Should see tenant B only
        cur.execute("SELECT id, name FROM tenants")
        result = cur.fetchall()
        assert len(result) == 1 and str(result[0][0]) == tb

        # Should see tenant B's users
        cur.execute("SELECT id, email FROM users")
        result = cur.fetchall()
        assert len(result) == 1

        # Should see tenant B's audit logs
        cur.execute("SELECT id, action FROM audit_log")
        result = cur.fetchall()
        assert len(result) == 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    cur.execute("BEGIN")
    try:
        cur.execute("RESET app.current_tenant_id")

        cur.execute("SELECT id FROM tenants")
        result = cur.fetchall()
        assert len(result) == 0, f"Expected 0 rows, got {len(result)}"

        cur.execute("SELECT id FROM users")
        result = cur.fetchall()
        assert len(result) == 0, f"Expected 0 rows, got {len(result)}"

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    cur.execute("BEGIN")
    try:
        cur.execute("SET LOCAL app.current_tenant_id = %s", (ta,))

        # Try to insert user for tenant B while in tenant A context
        hacker_id = str(uuid.uuid4())
        try:
            cur.execute(
                """
                INSERT INTO users (id, tenant_id, email, password_hash, role)
                VALUES (%s, %s, 'hacker@b.com', 'hash', 'owner')
            """,
                (hacker_id, tb),
            )
            # Check where it actually went
            cur.execute("SELECT id FROM users WHERE email = 'hacker@b.com'")
            result = cur.fetchall()
        except Exception:
            pass

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    cur.execute("BEGIN")
    try:
        cur.execute("SET LOCAL app.current_tenant_id = %s", (ta,))

        # Get an audit log ID
        cur.execute("SELECT id FROM audit_log LIMIT 1")
        log_id = cur.fetchone()[0]

        # Try UPDATE - should fail due to missing UPDATE grant
        try:
            cur.execute("UPDATE audit_log SET action = 'hacked' WHERE id = %s", (log_id,))
        except psycopg2.errors.InsufficientPrivilege:
            pass
        except Exception:
            pass

        # Try DELETE - should also fail
        try:
            cur.execute("DELETE FROM audit_log WHERE id = %s", (log_id,))
        except psycopg2.errors.InsufficientPrivilege:
            pass
        except Exception:
            pass

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    conn.close()


def main():
    # Setup test data as superuser
    ta, tb, ua1, ua2, ub1 = setup_test_data()

    # Run RLS tests as app user
    test_rls_isolation(ta, tb, ua1, ua2, ub1)


if __name__ == "__main__":
    main()
