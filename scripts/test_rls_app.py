#!/usr/bin/env python3
"""Test RLS with socialerp_app user."""

import psycopg2

conn = psycopg2.connect("postgresql://socialerp_app:socialerp_app@postgres:5432/socialerp")
cur = conn.cursor()

# Test with explicit transaction
cur.execute("BEGIN")
cur.execute("SET LOCAL app.current_tenant_id = '6fa4130b-4840-4bf6-8799-869f0bff4d4f'")
cur.execute("SELECT app.current_tenant_id()")
cur.execute("SELECT id, name FROM tenants")
conn.commit()

# Test without context
cur.execute("BEGIN")
cur.execute("RESET app.current_tenant_id")
cur.execute("SELECT id FROM tenants")
conn.commit()

conn.close()
