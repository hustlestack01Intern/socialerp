"""The refresh token is not in the browser's storage (M1.6, U79).

A token in ``localStorage`` is readable by any script on the page and stays
readable after the tab is closed. The API now puts the refresh token in an
``HttpOnly`` cookie, which script cannot read at all.

These tests pin:

* the login and refresh responses set the cookie, and it is ``HttpOnly``,
  ``SameSite=Lax`` and scoped to the auth paths;
* the refresh route accepts the cookie (a browser) as well as the body (an
  API client), and the body path still works;
* the logout route clears the cookie;
* the dashboard keeps no copy of the refresh token.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.routes.auth import REFRESH_COOKIE_NAME
from packages.core.auth.jwt import create_refresh_token
from packages.core.auth.password import hash_password
from packages.core.models.core import Tenant, User, UserRole

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


def _cookie_headers(response) -> dict[str, str]:
    """The Set-Cookie headers, by cookie name."""
    out: dict[str, str] = {}
    for value in response.headers.get_list("set-cookie"):
        name = value.split("=", 1)[0].strip()
        out[name] = value
    return out


async def _owner(
    session_factory: async_sessionmaker[AsyncSession], email: str
) -> tuple[UUID, UUID]:
    """A tenant with one owner account: the tenant id and the user id."""
    tenant_id = uuid4()
    user_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Tenant(
                id=tenant_id,
                name="Cookie Biz",
                slug=f"cookie-biz-{uuid4().hex[:8]}",
                owner_phone=f"2547{uuid4().hex[:8]}",
                owner_email=f"cookie-{uuid4().hex[:6]}@example.com",
                is_active=True,
                settings={},
            )
        )
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                email=email,
                password_hash=hash_password("S3cret-pass!"),
                full_name="Cookie Owner",
                role=UserRole.OWNER,
                is_active=True,
            )
        )
    return tenant_id, user_id


async def test_login_sets_an_httponly_cookie(api_client, session_factory) -> None:
    await _owner(session_factory, "owner-cookie@example.com")

    resp = await api_client.post(
        "/api/v1/auth/login",
        data={"username": "owner-cookie@example.com", "password": "S3cret-pass!"},
    )
    assert resp.status_code == 200, resp.text
    cookies = _cookie_headers(resp)
    assert REFRESH_COOKIE_NAME in cookies, resp.headers
    header = cookies[REFRESH_COOKIE_NAME]
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.replace("Lax", "lax")
    assert "Path=/api/v1/auth" in header

    # U79 residual: the token must not also be readable in the JSON body.
    assert "refresh_token" not in resp.json(), resp.json()


async def test_the_refresh_route_accepts_the_cookie(api_client, session_factory) -> None:
    """A browser renews with the cookie; an API client renews with the body."""
    tenant_id, user_id = await _owner(session_factory, "owner-refresh@example.com")

    login = await api_client.post(
        "/api/v1/auth/login",
        data={"username": "owner-refresh@example.com", "password": "S3cret-pass!"},
    )
    assert login.status_code == 200, login.text
    cookie = login.cookies.get(REFRESH_COOKIE_NAME)
    assert cookie, "the login did not set the cookie"

    # The browser path: the cookie only, no body.
    resp = await api_client.post("/api/v1/auth/refresh", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    assert "refresh_token" not in resp.json(), resp.json()

    # The API client path: the body still works. The rotated token is returned
    # only in the cookie, never in the JSON body.
    token = create_refresh_token(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        roles=["owner"],
    )
    resp = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": token}
    )
    assert resp.status_code == 200, resp.text
    assert "refresh_token" not in resp.json(), resp.json()


async def test_logout_clears_the_cookie(api_client, session_factory) -> None:
    await _owner(session_factory, "owner-logout@example.com")

    await api_client.post(
        "/api/v1/auth/login",
        data={"username": "owner-logout@example.com", "password": "S3cret-pass!"},
    )
    resp = await api_client.post("/api/v1/auth/logout", json={})
    assert resp.status_code == 200, resp.text
    cookies = _cookie_headers(resp)
    assert REFRESH_COOKIE_NAME in cookies
    header = cookies[REFRESH_COOKIE_NAME]
    assert "Max-Age=0" in header or "expires=" in header.lower()


def test_the_dashboard_keeps_no_copy_of_the_refresh_token() -> None:
    """The page must not write the refresh token anywhere."""
    session = (REPO / "apps" / "dashboard" / "src" / "lib" / "session.ts").read_text(
        encoding="utf-8"
    )
    assert "REFRESH_TOKEN_KEY" not in session, (
        "the dashboard still keeps a refresh token in localStorage"
    )
    assert not re.search(r"localStorage\.setItem\([^)]*refresh", session, re.I)
    # The access token is still kept, and the file says so.
    assert "ACCESS_TOKEN_KEY" in session


def test_the_dashboard_sends_the_cookie_when_it_refreshes() -> None:
    api = (REPO / "apps" / "dashboard" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/auth/refresh" in api
    assert 'credentials: "same-origin"' in api
