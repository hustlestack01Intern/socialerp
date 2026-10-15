#!/usr/bin/env python3
"""Fix socialerp_app role for testing."""

import psycopg2

conn = psycopg2.connect("postgresql://socialerp:socialerp@postgres:5432/socialerp")
conn.autocommit = True
cur = conn.cursor()

# Set password and login for socialerp_app
cur.execute("ALTER ROLE socialerp_app WITH LOGIN PASSWORD 'socialerp_app'")

# Verify
cur.execute("SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname = 'socialerp_app'")

# Test connection as socialerp_app
conn.close()

conn2 = psycopg2.connect("postgresql://socialerp_app:socialerp_app@postgres:5432/socialerp")
cur2 = conn2.cursor()
cur2.execute("SELECT current_user, session_user")
conn2.close()
