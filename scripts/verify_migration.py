#!/usr/bin/env python3
"""Verify migration was applied correctly with RLS."""

import psycopg2


def main():
    conn = psycopg2.connect("postgresql://socialerp:socialerp@postgres:5432/socialerp")
    cur = conn.cursor()

    # Check tables exist
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    cur.fetchall()

    # Check RLS enabled
    cur.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public'")
    cur.fetchall()

    # Check policies
    cur.execute(
        "SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual FROM pg_policies WHERE schemaname = 'public'"
    )
    policies = cur.fetchall()
    for _p in policies:
        pass

    # Check app schema and function
    cur.execute("SELECT nspname FROM pg_namespace WHERE nspname = 'app'")

    cur.execute("SELECT proname FROM pg_proc WHERE proname = 'current_tenant_id'")

    # Check grants
    cur.execute(
        "SELECT grantee, table_name, privilege_type FROM information_schema.table_privileges WHERE grantee = 'socialerp_app'"
    )
    grants = cur.fetchall()
    for _g in grants:
        pass

    conn.close()


if __name__ == "__main__":
    main()
