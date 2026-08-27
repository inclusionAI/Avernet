"""Authentication gate shared by JWT-authenticated ordinary HTTP routes."""

from __future__ import annotations

from starlette.requests import HTTPConnection

from agentclaw.community.adapters.http.openapi_v1.dependencies import resolve_caller
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.core.gateway_principal import VerifiedCaller


async def require_gateway_user(connection: HTTPConnection) -> VerifiedCaller:
    """Return the verified end user carried by ``X-Avernet-Principal``.

    The caller may reach Backend directly; trust comes from the JWT signature,
    not from requiring a physical Gateway hop. Tenant is deliberately not read
    or used by these ordinary HTTP routes.
    """
    caller = resolve_caller(connection)
    if caller is None or not caller.has_user:
        raise MissingPrincipalError("verified user principal required")
    return caller
