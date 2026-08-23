"""OpenAPI migration of the legacy IAM-token and Caller preparation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.errors import (
    CallerIdentityConflictError,
    CallerIdentityForbiddenError,
    CallerIdentityInvalidError,
    CallerIdentityOpenApiError,
    IamTokenUnavailableError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.token.schemas import IamToken
from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityStage as CoreCallerIdentityStage,
)
from agentclaw.community.api.caller_credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth import AuthRequestContext
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


token_router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["Bot IAM token"],
    route_class=PublicAPIRoute,
)


def _auth_request(request: Request) -> AuthRequestContext:
    return AuthRequestContext(
        cookies=dict(request.cookies),
        headers=dict(request.headers),
        query_params=dict(request.query_params),
        base_url=str(request.base_url),
    )


def _raise_for_error(error: str | None) -> None:
    if error is None:
        return
    if error == "IAM_TOKEN cookie not found":
        raise IamTokenUnavailableError
    if error == CALLER_CREDENTIAL_REQUEST_INVALID:
        raise CallerIdentityInvalidError(error)
    if error == "CALLER_IDENTITY_FORBIDDEN":
        raise CallerIdentityForbiddenError(error)
    if error == "CALLER_IDENTITY_AMBIGUOUS":
        raise CallerIdentityConflictError(error)
    raise CallerIdentityOpenApiError(error)


@token_router.post(
    "/iam-token",
    response_model=Envelope[IamToken],
    dependencies=[Depends(refuse_app_only_caller)],
)
@envelope_errors
async def get_bot_iam_token(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    stage: RuntimeStage = Query(
        default=RuntimeStage.DRAFT,
        description="Bot runtime stage whose Caller identity may be prepared.",
    ),
    entity_id: str | None = Query(
        default=None,
        description="Entity identifier used to resolve the Bot unambiguously.",
    ),
    service: CallerIamTokenServiceProtocol = Injected(CallerIamTokenServiceProtocol),
) -> Envelope[IamToken]:
    """Return the IAM token and prepare Caller identity when the Bot requires it."""
    normalized_entity_id = (entity_id or "").strip() or None
    # Every user owns a Bot named ``default``.  When the browser has no explicit
    # chat target yet, scope that otherwise-ambiguous identifier to the verified
    # user instead of attempting a tenant-wide unique lookup.
    if bot_id == "default" and normalized_entity_id is None:
        normalized_entity_id = user_id
    result = await service.get_iam_token(
        iam_token=request.cookies.get("IAM_TOKEN") or "",
        auth_request=_auth_request(request),
        bot_id=bot_id,
        stage=CoreCallerIdentityStage(stage.value),
        # Published runtime selection is server-owned.  The runtime updater
        # resolves the live release for ``stage`` from the exact Bot row.
        publish_id=None,
        entity_id=normalized_entity_id,
        is_test_exchange=False,
    )
    _raise_for_error(result.error)
    return envelope(IamToken(iam_token=result.iam_token), request)


__all__ = ["token_router"]
