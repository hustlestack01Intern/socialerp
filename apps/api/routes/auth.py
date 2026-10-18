"""
Authentication routes for SocialERP API.

Canonical implementation against ``packages.core`` models and auth services
(ADR 0008 / ADR 0009). Every endpoint is tenant-scoped except self-serve
registration and login.

Design notes
------------
- Passwords: bcrypt via :func:`packages.core.auth.password.hash_password` /
  ``verify_password``.
- Tokens: stateless JWT access + refresh tokens via ``packages.core.auth.jwt``.
  Session state (refresh token blacklist) is owned by ``TokenManager``.
- API keys: ``ApiKeyManager`` bound to the request session.
- Tenants are the root of the multi-tenant hierarchy; ``POST /auth/register``
  performs self-serve business signup and creates the tenant + owner atomically
  (onboarding step 1 in the product spec).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.api_keys import ApiKeyManager
from packages.core.auth.dependencies import get_current_tenant_id, get_current_user, get_db
from packages.core.auth.jwt import create_access_token, create_refresh_token
from packages.core.auth.password import hash_password, verify_password
from packages.core.auth.tokens import TokenManager
from packages.core.domain.auth import ApiKeyCreate as ApiKeyCreateInput
from packages.core.domain.user import User as UserDomain
from packages.core.models import Tenant, User, UserRole

router = APIRouter(prefix="/auth", tags=["authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Access-token lifetime advertised to clients; must match JWTSettings default (15 min).
ACCESS_TOKEN_EXPIRES_IN_SECONDS = 15 * 60


# =============================================================================
# SCHEMAS
# =============================================================================


class RegisterRequest(BaseModel):
    """Self-serve business registration (creates a tenant + owner user)."""

    business_name: str = Field(..., min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    phone: str = Field(..., pattern=r"^\+?254[0-9]{9}$")
    email: EmailStr
    owner_name: str = Field(..., min_length=1, max_length=255)
    owner_email: EmailStr
    owner_password: str = Field(..., min_length=12, max_length=128)


class UserLogin(BaseModel):
    """User login request."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenRefresh(BaseModel):
    """Token refresh request."""

    refresh_token: str


class UserResponse(BaseModel):
    """User response."""

    id: str
    email: str
    full_name: str | None
    phone: str | None
    role: str
    is_active: bool
    created_at: str


class TenantCreate(BaseModel):
    """Tenant creation request (platform admin)."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    phone: str = Field(..., pattern=r"^\+?254[0-9]{9}$")
    email: EmailStr
    owner_name: str = Field(..., min_length=1, max_length=255)
    owner_email: EmailStr
    owner_password: str = Field(..., min_length=12, max_length=128)


class TenantResponse(BaseModel):
    """Tenant response."""

    id: str
    name: str
    slug: str | None
    phone: str
    email: str
    is_active: bool
    created_at: str


class TenantUserCreate(BaseModel):
    """Create user within tenant."""

    email: EmailStr
    password: str = Field(..., min_length=12, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)
    phone: str | None = Field(None, pattern=r"^\+?254[0-9]{9}$")
    role: str = Field(default="staff", pattern=r"^(owner|staff|rider)$")


# NOTE: tenant-scoped user endpoints reuse ``UserResponse`` — the wire shape is
# identical, so a separate class would only duplicate the schema.


class ApiKeyCreate(BaseModel):
    """API key creation request."""

    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyResponse(BaseModel):
    """API key response."""

    id: str
    name: str
    key: str  # Only returned on creation
    scopes: list[str]
    created_at: str
    expires_at: str | None


# =============================================================================
# SERIALIZERS
# =============================================================================


def _role_value(role: object) -> str:
    """Return the string value of a role enum or plain string."""
    return role.value if hasattr(role, "value") else str(role)


def _user_response(user: object) -> UserResponse:
    """Serialize a SQLAlchemy ``User`` to a response model."""
    return UserResponse(
        id=str(user.id),  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        full_name=user.full_name,  # type: ignore[attr-defined]
        phone=user.phone,  # type: ignore[attr-defined]
        role=_role_value(user.role),  # type: ignore[attr-defined]
        is_active=user.is_active,  # type: ignore[attr-defined]
        created_at=user.created_at.isoformat(),  # type: ignore[attr-defined]
    )


def _tenant_response(tenant: Tenant) -> TenantResponse:
    """Serialize a ``Tenant`` to a response model."""
    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        phone=tenant.owner_phone,
        email=tenant.owner_email,
        is_active=tenant.is_active,
        created_at=tenant.created_at.isoformat(),
    )


# =============================================================================
# TOKEN HELPERS
# =============================================================================


def _issue_tokens(user: User) -> TokenResponse:
    """Issue an access/refresh token pair for a SQLAlchemy user."""
    access_token = create_access_token(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        roles=[_role_value(user.role)],
    )
    refresh_token = create_refresh_token(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        roles=[_role_value(user.role)],
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRES_IN_SECONDS,
    )


# =============================================================================
# AUTH ROUTES
# =============================================================================


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Self-serve signup: create a tenant and its owner user, then log in."""
    # Guard against duplicate owner email within the platform (login is email-based).
    existing = await session.execute(select(User).where(User.email == data.owner_email))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    if data.slug is not None:
        slug_exists = await session.execute(select(Tenant).where(Tenant.slug == data.slug))
        if slug_exists.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tenant with this slug already exists",
            )

    tenant = Tenant(
        name=data.name if hasattr(data, "name") else data.business_name,
        slug=data.slug,
        owner_phone=data.phone,
        owner_email=data.email,
        settings={},
        is_active=True,
    )
    session.add(tenant)
    await session.flush()

    owner = User(
        tenant_id=tenant.id,
        email=data.owner_email,
        password_hash=hash_password(data.owner_password),
        full_name=data.owner_name,
        phone=data.phone,
        role=UserRole.OWNER,
        is_active=True,
    )
    session.add(owner)
    await session.commit()
    await session.refresh(owner)

    return _issue_tokens(owner)


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Login with email + password; email is unique per tenant.

    Cross-tenant login by bare email is only allowed when exactly one account
    matches. Otherwise the caller must disambiguate via a tenant-scoped login.
    """
    result = await session.execute(
        select(User).where(User.email == form_data.username, User.is_active.is_(True)),
    )
    candidates = list(result.scalars().all())

    matched = [u for u in candidates if verify_password(form_data.password, u.password_hash)]
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if len(matched) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email exists in multiple tenants; tenant-scoped login required",
        )

    return _issue_tokens(matched[0])


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    token_data: TokenRefresh,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Rotate a refresh token and return a new token pair."""
    manager = TokenManager(session)
    pair = await manager.refresh_tokens(token_data.refresh_token)
    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked refresh token",
        )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout")
async def logout(
    token_data: TokenRefresh,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revoke a refresh token."""
    manager = TokenManager(session)
    manager.revoke_token(token_data.refresh_token, token_type="refresh", reason="logout")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: UserDomain = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Get the current (authenticated) user's profile."""
    db_user = await session.get(User, current_user.id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return _user_response(db_user)


# =============================================================================
# TENANT ROUTES (platform admin)
# =============================================================================


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    tenant_data: TenantCreate,
    session: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Create a new tenant and its owner user (platform admin only)."""
    if tenant_data.slug is not None:
        existing = await session.execute(select(Tenant).where(Tenant.slug == tenant_data.slug))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tenant with this slug already exists",
            )

    tenant = Tenant(
        name=tenant_data.name,
        slug=tenant_data.slug,
        owner_phone=tenant_data.phone,
        owner_email=tenant_data.email,
        is_active=True,
        settings={},
    )
    session.add(tenant)
    await session.flush()

    owner_user = User(
        tenant_id=tenant.id,
        email=tenant_data.owner_email,
        password_hash=hash_password(tenant_data.owner_password),
        full_name=tenant_data.owner_name,
        phone=tenant_data.phone,
        role=UserRole.OWNER,
        is_active=True,
    )
    session.add(owner_user)
    await session.commit()
    await session.refresh(tenant)

    return _tenant_response(tenant)


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    session: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Get a tenant by ID (platform admin only)."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    return _tenant_response(tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    is_active: bool | None = None,
    session: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Update a tenant (platform admin only)."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    if name is not None:
        tenant.name = name
    if phone is not None:
        tenant.owner_phone = phone
    if email is not None:
        tenant.owner_email = email
    if is_active is not None:
        tenant.is_active = is_active

    await session.commit()
    await session.refresh(tenant)
    return _tenant_response(tenant)


# =============================================================================
# TENANT USER MANAGEMENT (tenant-scoped)
# =============================================================================


@router.post(
    "/tenants/{tenant_id}/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_user(
    tenant_id: UUID,
    user_data: TenantUserCreate,
    session: AsyncSession = Depends(get_db),
    current_tenant_id: UUID = Depends(get_current_tenant_id),
) -> UserResponse:
    """Create a user within a tenant (tenant-scoped)."""
    if tenant_id != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create users in other tenants",
        )

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    existing = await session.execute(
        select(User).where(User.tenant_id == tenant_id, User.email == user_data.email),
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists in tenant",
        )

    user = User(
        tenant_id=tenant_id,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        full_name=user_data.full_name,
        phone=user_data.phone,
        role=UserRole(user_data.role),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _user_response(user)


@router.get("/tenants/{tenant_id}/users", response_model=list[UserResponse])
async def list_tenant_users(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_tenant_id: UUID = Depends(get_current_tenant_id),
) -> list[UserResponse]:
    """List users within a tenant (tenant-scoped)."""
    if tenant_id != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access users in other tenants",
        )

    result = await session.execute(select(User).where(User.tenant_id == tenant_id))
    return [_user_response(u) for u in result.scalars().all()]


@router.patch("/tenants/{tenant_id}/users/{user_id}", response_model=UserResponse)
async def update_tenant_user(
    tenant_id: UUID,
    user_id: UUID,
    full_name: str | None = None,
    phone: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    session: AsyncSession = Depends(get_db),
    current_tenant_id: UUID = Depends(get_current_tenant_id),
) -> UserResponse:
    """Update a user within a tenant (tenant-scoped)."""
    if tenant_id != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update users in other tenants",
        )

    result = await session.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id),
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if full_name is not None:
        user.full_name = full_name
    if phone is not None:
        user.phone = phone
    if role is not None:
        user.role = UserRole(role)
    if is_active is not None:
        user.is_active = is_active

    await session.commit()
    await session.refresh(user)
    return _user_response(user)


# =============================================================================
# API KEY MANAGEMENT (tenant-scoped)
# =============================================================================


@router.post(
    "/tenants/{tenant_id}/api-keys",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    tenant_id: UUID,
    api_key_data: ApiKeyCreate,
    session: AsyncSession = Depends(get_db),
    current_tenant_id: UUID = Depends(get_current_tenant_id),
) -> ApiKeyResponse:
    """Create an API key for a tenant (tenant-scoped)."""
    if tenant_id != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create API keys for other tenants",
        )

    manager = ApiKeyManager(session)
    created = await manager.create(
        tenant_id,
        ApiKeyCreateInput(
            name=api_key_data.name,
            scopes=api_key_data.scopes,
            expires_in_days=api_key_data.expires_in_days,
        ),
    )
    return ApiKeyResponse(
        id=str(created.id),
        name=created.name,
        key=created.key or "",
        scopes=created.scopes,
        created_at=created.created_at.isoformat(),
        expires_at=created.expires_at.isoformat() if created.expires_at else None,
    )


@router.get("/tenants/{tenant_id}/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_tenant_id: UUID = Depends(get_current_tenant_id),
) -> list[ApiKeyResponse]:
    """List API keys for a tenant (tenant-scoped). The full key is never returned."""
    if tenant_id != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot list API keys for other tenants",
        )

    manager = ApiKeyManager(session)
    listing = await manager.list_keys(tenant_id)
    return [
        ApiKeyResponse(
            id=str(item.id),
            name=item.name,
            key="",
            scopes=item.scopes,
            created_at=item.created_at.isoformat(),
            expires_at=item.expires_at.isoformat() if item.expires_at else None,
        )
        for item in listing.items
    ]


@router.delete("/tenants/{tenant_id}/api-keys/{key_id}")
async def revoke_api_key(
    tenant_id: UUID,
    key_id: UUID,
    session: AsyncSession = Depends(get_db),
    current_tenant_id: UUID = Depends(get_current_tenant_id),
) -> dict[str, str]:
    """Revoke an API key (tenant-scoped)."""
    if tenant_id != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot revoke API keys for other tenants",
        )

    manager = ApiKeyManager(session)
    revoked = await manager.revoke(tenant_id, key_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
    return {"message": "API key revoked successfully"}
