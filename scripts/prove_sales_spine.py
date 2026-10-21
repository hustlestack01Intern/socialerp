"""End-to-end proof of the M1.5 sales spine (signup -> trial decision).

Not part of the test suite: this is a manual proof against a RUNNING api
(the dashboard pages are browser code, so the only honest check is a real
HTTP call against a real database).

    uvicorn apps.api.main:app --port 8011 &
    python scripts/prove_sales_spine.py

It proves:
  1. with the gate on, a registration without a code is refused (403);
  2. a registration with a valid code is accepted (201) and the code is
     consumed;
  3. the new tenant's subscription says what the server says: pending,
     because the trial starts at channel connect;
  4. the same code cannot be used a second time (403);
  5. logging in with the new credentials works and returns the same decision.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API = os.environ.get("PROVE_API_URL", "http://localhost:8011")
DB = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://socialerp:socialerp@localhost:5432/socialerp",
)

PASSWORD = "demo-proof-password-2026"


def out(message: str) -> None:
    sys.stdout.write(message + "\n")


async def _make_code() -> str:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from packages.core.auth.invites import InviteCodeService

    engine = create_async_engine(DB)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session, session.begin():
            invite = await InviteCodeService(session).create(note="sales-spine proof")
            return invite.code
    finally:
        await engine.dispose()


async def main() -> int:
    code = await _make_code()
    out(f"1. issued an invite code: {code}")

    payload = {
        "business_name": "Proof Traders Ltd",
        "slug": f"proof-{uuid.uuid4().hex[:6]}",
        "phone": "254712000111",
        "email": "proof@example.com",
        "owner_name": "Proof Owner",
        "owner_email": "proof-owner@example.com",
        "owner_password": PASSWORD,
    }

    async with httpx.AsyncClient(base_url=API, timeout=30) as client:
        without = await client.post("/api/v1/auth/register", json=payload)
        out(f"2. registration without a code -> HTTP {without.status_code}")
        assert without.status_code == 403, without.text
        assert without.json()["detail"] == "This invite code is not valid."

        with_code = await client.post(
            "/api/v1/auth/register", json={**payload, "invite_code": code}
        )
        out(f"3. registration with the code  -> HTTP {with_code.status_code}")
        assert with_code.status_code == 201, with_code.text
        tokens = with_code.json()
        assert tokens["access_token"], "no access token returned"

        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        subscription = await client.get("/api/v1/billing/subscription", headers=headers)
        out(f"4. the server's trial decision  -> HTTP {subscription.status_code}")
        assert subscription.status_code == 200, subscription.text
        decision = subscription.json()
        out(f"   status={decision['status']} plan={decision['plan']} "
            f"entitled={decision['entitled']} reason={decision['entitlement_reason']}")
        assert decision["status"] == "pending", decision
        assert decision["entitled"] is False, decision

        again = await client.post(
            "/api/v1/auth/register",
            json={**payload, "slug": f"proof-{uuid.uuid4().hex[:6]}", "invite_code": code},
        )
        out(f"5. the same code again          -> HTTP {again.status_code}")
        assert again.status_code == 403, again.text

        login = await client.post(
            "/api/v1/auth/login",
            data={"username": payload["owner_email"], "password": PASSWORD},
        )
        out(f"6. login with the new account   -> HTTP {login.status_code}")
        assert login.status_code == 200, login.text

        subscription_2 = await client.get(
            "/api/v1/billing/subscription",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert subscription_2.json() == decision, "the two answers disagree"
        out("   the login session sees the same decision")

    out("")
    out("SALES SPINE OK: the gate, the signup, the trial decision and the")
    out("login all agree with what the server says.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
