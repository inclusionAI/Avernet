"""Internal task reads: signed internal application or existing user login.

The gateway owns credential authentication and signing. This adapter only reuses
its existing backend verifier; it does not mint callers or trust identity hints.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    resolve_caller,
)
from agentclaw.community.core.gateway_principal import PrincipalType, VerifiedCaller
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT


async def task_read_actor(
    request: Request,
    auth_plugin: AuthPlugin = Injected(AuthPlugin),
) -> AuthenticatedUser | VerifiedCaller:
    """Fail closed on supplied service credentials; never downgrade to cookies.

    Internal routes serve the internal tenant. Accepting an external app here
    would run under that default scope, so tenant equality is mandatory.
    """
    if PRINCIPAL_HEADER.lower() in request.headers:
        caller = resolve_caller(request)
        if caller is None or caller.tenant != DEFAULT_AVERNET_TENANT:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if any(principal.type == PrincipalType.APP for principal in caller.principals):
            return caller
        # A gateway-forwarded browser user is not a service caller. Preserve
        # the existing cookie authentication, but only after successful verify.
        if not caller.has_user:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return await get_current_user(request, auth_plugin)
