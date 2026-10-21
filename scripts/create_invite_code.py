#!/usr/bin/env python3
"""Issue invite codes for the first cohort (M1.5).

The founder runs this to hand one code to one prospect. The code is the only
thing standing between "the first cohort is invited" and "the internet starts
trials", so the output is deliberately loud: print the code, print what it is
worth, print where it went.

Usage (from the repo root, with the database up)::

    uv run python scripts/create_invite_code.py --note "Amina - cohort 1"
    uv run python scripts/create_invite_code.py --note "campaign" --max-uses 5 \
        --expires-in-days 14

The script talks to ``DATABASE_URL`` (default: the local compose database).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Create an invite code.")
    parser.add_argument("--note", default=None, help="Who the code is for.")
    parser.add_argument(
        "--max-uses",
        type=int,
        default=1,
        help="How many registrations the code may pay for (default: 1).",
    )
    parser.add_argument(
        "--expires-in-days",
        type=int,
        default=None,
        help="Expire the code after this many days (default: never).",
    )
    parser.add_argument("--created-by", default="founder", help="Who issued it.")
    args = parser.parse_args()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from packages.core.auth.invites import InviteCodeService

    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://socialerp:socialerp@localhost:5432/socialerp",
    )
    expires_at = (
        datetime.now(UTC) + timedelta(days=args.expires_in_days)
        if args.expires_in_days
        else None
    )

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session, session.begin():
            invite = await InviteCodeService(session).create(
                note=args.note,
                max_uses=args.max_uses,
                expires_at=expires_at,
                created_by=args.created_by,
            )
        out = sys.stdout.write
        out("Invite code issued.\n")
        out(f"  code      : {invite.code}\n")
        out(f"  max uses  : {invite.max_uses}\n")
        expires = invite.expires_at.isoformat() if invite.expires_at else "never"
        out(f"  expires   : {expires}\n")
        out(f"  note      : {invite.note or '-'}\n\n")
        out("Give it to the prospect; they enter it at /signup.\n")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
