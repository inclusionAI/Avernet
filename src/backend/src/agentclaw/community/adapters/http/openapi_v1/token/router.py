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
from agentclaw.community.adapters.http.openapi_v1.token.schemas import (
    CallerIdentityReady,
    IamToken,
)
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


token_router = APIRouter(prefix="/openapi/v1/org/user", tags=["org-user"], route_class=PublicAPIRoute)
caller_identity_router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}", tags=["Caller identity"],
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


@token_router.get(
    "/iam-token",
    response_model=Envelope[IamToken],
    dependencies=[Depends(refuse_app_only_caller)],
)
@envelope_errors
async def get_iam_token(
    request: Request,
    user_id: UserIdDep,
    service: CallerIamTokenServiceProtocol = Injected(CallerIamTokenServiceProtocol),
) -> Envelope[IamToken]:
    """Return the signed-in user's IAM token for the first-party chat client."""
    del user_id  # Identity equality is enforced by UserIdDep before this handler.
    result = await service.get_iam_token(
        iam_token=request.cookies.get("IAM_TOKEN") or "",
        auth_request=_auth_request(request),
        bot_id=None,
        stage=CoreCallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=False,
    )
    _raise_for_error(result.error)
    return envelope(IamToken(iam_token=result.iam_token), request)


@caller_identity_router.post(
    "/caller-identity",
    response_model=Envelope[CallerIdentityReady],
    dependencies=[Depends(refuse_app_only_caller)],
)
@envelope_errors
async def prepare_caller_identity(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    stage: RuntimeStage = Query(
        default=RuntimeStage.DRAFT,
        description="Bot runtime stage whose Caller identity is prepared.",
    ),
    publish_id: int | None = Query(
        default=None,
        description="Published release identifier when preparing a published runtime.",
    ),
    entity_id: str | None = Query(
        default=None,
        description="Entity identifier used to resolve the Caller credential.",
    ),
    service: CallerIamTokenServiceProtocol = Injected(CallerIamTokenServiceProtocol),
) -> Envelope[CallerIdentityReady]:
    """Prepare the caller credential required by this bot's chat runtime."""
    del user_id  # The service resolves the same user from the authenticated request.
    result = await service.get_iam_token(
        iam_token=request.cookies.get("IAM_TOKEN") or "",
        auth_request=_auth_request(request),
        bot_id=bot_id,
        stage=CoreCallerIdentityStage(stage.value),
        publish_id=publish_id,
        entity_id=entity_id,
        is_test_exchange=False,
    )
    _raise_for_error(result.error)
    return envelope(CallerIdentityReady(bot_id=bot_id, stage=stage), request)


__all__ = ["caller_identity_router", "token_router"]
