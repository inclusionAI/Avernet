"""API Gateway service package — API Key management and verification services."""

from secbaas.api.api_gateway import (
    APIKeyPermissionChecker,
    APIKeyPolicy,
    APIKeyRecord,
    check_bot_permission,
    check_permission,
    is_admin,
    parse_bot_entity_id,
    parse_policy,
)

from ._key_gen import APIKeyGenerator
from ._key_service import DefaultAPIKeyService
from ._key_validator import DefaultAPIKeyValidator
from ._protocols import APIKeyValidator

__all__ = [
    "APIKeyGenerator",
    "APIKeyPolicy",
    "APIKeyRecord",
    "APIKeyValidator",
    "DefaultAPIKeyService",
    "DefaultAPIKeyValidator",
    "APIKeyPermissionChecker",
    "check_bot_permission",
    "check_permission",
    "is_admin",
    "parse_bot_entity_id",
    "parse_policy",
]
