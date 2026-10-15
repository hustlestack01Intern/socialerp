"""
FastAPI Authentication Dependencies for SocialERP

Re-exports from core auth dependencies module.
"""

from packages.core.auth.dependencies import (
    get_api_key,
    get_api_key_manager,
    get_current_active_user,
    get_current_tenant_id,
    get_current_user,
    get_current_user_payload,
    get_db,
    get_optional_user,
    get_token_manager,
    require_api_key,
    require_api_key_scopes,
    require_owner,
    require_rider,
    require_role,
    require_staff,
)

# Backwards-compatible alias for the old public name.
require_scopes = require_api_key_scopes

__all__ = [
    "get_api_key",
    "get_api_key_manager",
    "get_current_active_user",
    "get_current_tenant_id",
    "get_current_user",
    "get_current_user_payload",
    "get_db",
    "get_optional_user",
    "get_token_manager",
    "require_api_key",
    "require_api_key_scopes",
    "require_owner",
    "require_rider",
    "require_role",
    "require_scopes",
    "require_staff",
]
