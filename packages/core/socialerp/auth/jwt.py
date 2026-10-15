"""
JWT Token Utilities for SocialERP

Re-exports from core auth jwt module plus the TokenPayload schema.
"""

from packages.core.auth.jwt import (
    JWTSettings,
    configure_jwt,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_access_token,
    decode_refresh_token,
    decode_token,
    generate_secure_secret,
    generate_token_id,
    get_jwt_settings,
    get_token_claims,
)
from packages.core.domain.auth import TokenPayload

__all__ = [
    "JWTSettings",
    "TokenPayload",
    "configure_jwt",
    "create_access_token",
    "create_refresh_token",
    "create_token_pair",
    "decode_access_token",
    "decode_refresh_token",
    "decode_token",
    "generate_secure_secret",
    "generate_token_id",
    "get_jwt_settings",
    "get_token_claims",
]
