#!/usr/bin/env python3
"""Debug RLS policy."""

import psycopg2

conn = psycopg2.connect("postgresql://socialerp:socialerp@postgres:5432/socialerp")
cur = conn.cursor()

# Test with explicit transaction
cur.execute("BEGIN")
cur.execute("SET LOCAL app.current_tenant_id = '6fa4130b-4840-4bf6-8799-869f0bff4d4f'")
cur.execute("SELECT app.current_tenant_id()")
cur.execute("SELECT current_setting('app.current_tenant_id', true)")
cur.execute("SELECT id, name FROM tenants")
conn.commit()

# Check if RLS is actually enforced
cur.execute("BEGIN")
cur.execute("RESET app.current_tenant_id")
cur.execute("SELECT id FROM tenants")
conn.commit()

conn.close()
