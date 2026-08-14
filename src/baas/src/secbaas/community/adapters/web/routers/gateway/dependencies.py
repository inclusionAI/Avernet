"""Gateway dependency injection — JWT authentication & context building.

Auth flow:
  1. Extract JWT token from the x-avernet-principal header
  2. Fetch signing secret via SecretStorePlugin, verify JWT signature and expiry
  3. Extract access_key (resource_key) and tenant from JWT payload
  4. Look up baas_resource_key by resource_key + tenant to get record (id, tenant)
  5. Build BotChatContext using resource_key (api_key_prefix = first 8 chars)
  6. Verify resource_key_id + bot_id mapping exists in baas_resource_key_bot_mapping
"""

from dataclasses import dataclass

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Header, HTTPException, status

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    normalize_bot_id,
)
from secbaas.community.api.api_gateway import (
    ResourceKeyRepository,
    verify_jwt_token,
)
from secbaas.community.api.bot_runtime import BotChatContext
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.spi.secret import SecretStorePlugin


@dataclass
class GatewayAuthContext:
    """Auth context after JWT validation."""

    resource_key: str
    resource_key_id: int
    tenant: str
    app: str


def _get_token_from_header(principal: str | None) -> str:
    """Extract the JWT token from the x-avernet-principal header."""
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40101, "message": "Token missing"},
        )

    return principal


@inject
def validate_jwt_token(
    x_avernet_principal: str | None = Header(None, alias="x-avernet-principal"),
    secret_plugin: SecretStorePlugin = Depends(
        Provide[ApplicationContainer.plugins.secret_plugin]
    ),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
    secret_name: str = Depends(
        Provide[ApplicationContainer.config.gateway.jwt.secret_name]
    ),
) -> GatewayAuthContext:
    """Validate JWT token and return auth context.

    After JWT validation, extract access_key (resource_key) and tenant
    from payload, then look up resource_key_id from the database.

    Raises:
        HTTPException 401: token missing/invalid/expired
        HTTPException 403: resource_key not found
    """
    token = _get_token_from_header(x_avernet_principal)

    secret_key = secret_plugin.get_secret(secret_name)

    ok, error_msg, payload = verify_jwt_token(token, secret_key)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40103, "message": error_msg or "Token invalid"},
        )

    principals = payload.get("principals") or []
    if not principals:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40103, "message": "principals missing in token"},
        )

    principal = principals[0]
    resource_key = principal.get("access_key", {}).get("access_key", "")
    tenant = principal.get("tenant", "")
    if not resource_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40103, "message": "access_key missing in token"},
        )
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40103, "message": "tenant missing in token"},
        )

    record = resource_key_repository.get_by_resource_key_and_tenant(
        resource_key, tenant
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 40301, "message": "Resource key not found"},
        )

    return GatewayAuthContext(
        resource_key=resource_key,
        resource_key_id=record.id,
        tenant=record.tenant,
        app=record.app,
    )


def get_bot_chat_context(
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
) -> BotChatContext:
    """Build BotChatContext from GatewayAuthContext.

    app comes from the baas_resource_key record looked up during JWT validation.
    api_key_prefix uses the first 8 chars of resource_key for log tracing.
    """
    return BotChatContext.from_api_key(
        api_key_prefix=auth_ctx.resource_key[:8],
        app_id=auth_ctx.app,
        app_type="app",
        tenant=auth_ctx.tenant,
    )


def check_bot_access(
    bot_id: str,
    auth_ctx: GatewayAuthContext,
    resource_key_repository: ResourceKeyRepository,
) -> str:
    """Verify that resource_key_id + bot_id mapping exists.

    Called inside handlers, not used as a Depends.

    Returns:
        Normalized bot_id.

    Raises:
        HTTPException 400: invalid bot_id format
        HTTPException 403: mapping does not exist
    """
    normalized = normalize_bot_id(bot_id)

    if not resource_key_repository.exists_bot_mapping(
        auth_ctx.resource_key_id, normalized
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": 40302,
                "message": f"Resource key not authorized for bot: {bot_id}",
            },
        )

    return normalized
