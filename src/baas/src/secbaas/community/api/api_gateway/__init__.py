"""
API Gateway domain types.

Package defining the canonical API abstraction for the api_gateway module.
All consumers (core/service/, adapters/) depend on this API layer.
"""

from ._enums import APIKeyStatus
from ._exceptions import APIKeyError
from ._jwt import verify_jwt_token
from ._models import (
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyListResponse,
    APIKeyQuery,
    APIKeyRecord,
    APIKeyResponse,
    APIKeyUpdate,
    AppAPIKeyCreate,
    BotAPIKeyCreate,
)
from ._permission import (
    APIKeyPermissionChecker,
    check_bot_permission,
    check_permission,
    is_admin,
    parse_bot_entity_id,
)
from ._policy import APIKeyPolicy, parse_policy
from ._protocols import APIKeyService, APIKeyValidator
from ._resource_key import ResourceKeyRecord, ResourceKeyRepository

__all__ = [
    # Admin
    "is_admin",
    # Permission
    "APIKeyPermissionChecker",
    "check_bot_permission",
    "check_permission",
    "parse_bot_entity_id",
    # Policy
    "APIKeyPolicy",
    "parse_policy",
    # Model
    "APIKeyStatus",
    "APIKeyCreate",
    "BotAPIKeyCreate",
    "AppAPIKeyCreate",
    "APIKeyUpdate",
    "APIKeyResponse",
    "APIKeyCreateResponse",
    "APIKeyListResponse",
    "APIKeyQuery",
    # Core Models
    "APIKeyRecord",
    # Protocols / Errors
    "APIKeyError",
    "APIKeyService",
    "APIKeyValidator",
    # Resource Key (re-exported for adapter consumption)
    "ResourceKeyRecord",
    "ResourceKeyRepository",
    # JWT
    "verify_jwt_token",
]
