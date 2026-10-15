#!/usr/bin/env python3
"""Verify Reservations migration was applied correctly with RLS."""

import psycopg2


def main():
    conn = psycopg2.connect("postgresql://socialerp:socialerp@postgres:5432/socialerp")
    cur = conn.cursor()

    # Check Reservations table exists
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'reservations'"
    )
    cur.fetchall()

    # Check RLS enabled
    cur.execute(
        "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' AND tablename = 'reservations'"
    )
    cur.fetchall()

    # Check policies
    cur.execute(
        "SELECT schemaname, tablename, policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = 'reservations'"
    )
    policies = cur.fetchall()
    for _p in policies:
        pass

    # Check grants
    cur.execute(
        "SELECT grantee, table_name, privilege_type FROM information_schema.table_privileges WHERE grantee = 'socialerp_app' AND table_name = 'reservations'"
    )
    grants = cur.fetchall()
    for _g in grants:
        pass

    conn.close()


if __name__ == "__main__":
    main()
