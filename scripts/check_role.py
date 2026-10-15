#!/usr/bin/env python3
"""Check role permissions."""

import psycopg2

conn = psycopg2.connect("postgresql://socialerp:socialerp@postgres:5432/socialerp")
cur = conn.cursor()

cur.execute(
    "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('socialerp', 'socialerp_app')"
)

cur.execute("SELECT current_user, session_user")

conn.close()
