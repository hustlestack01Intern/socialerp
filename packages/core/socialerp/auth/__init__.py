"""
Auth Package for SocialERP

Re-exports from core auth package.
"""

from socialerp.auth.api_keys import (
    ApiKey,
    ApiKeyManager,
    ApiKeyScope,
)
from socialerp.auth.dependencies import (
    get_current_tenant_id,
    get_current_user,
    require_role,
    require_scopes,
)
from socialerp.auth.jwt import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from socialerp.auth.password import (
    get_password_hasher,
    hash_password,
    verify_password,
)
from socialerp.auth.tokens import (
    TokenManager,
    get_token_manager,
)

__all__ = [  # ruff: ignore[unsorted-dunder-all]  (grouped by submodule for readability)
    # password
    "get_password_hasher",
    "verify_password",
    "hash_password",
    # jwt
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "TokenPayload",
    # tokens
    "TokenManager",
    "get_token_manager",
    # api_keys
    "ApiKeyManager",
    "ApiKey",
    "ApiKeyScope",
    # dependencies
    "get_current_user",
    "get_current_tenant_id",
    "require_role",
    "require_scopes",
]
