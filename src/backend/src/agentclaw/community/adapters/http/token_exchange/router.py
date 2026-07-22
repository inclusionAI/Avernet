import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.caller_credential import (
    CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CallerCredentialError,
    CallerRuntimeUpdater,
    CallerTokenProvider,
)
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
    CallerIdentityStage,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityAmbiguousError,
    CallerIdentityPermissionError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin
from agentclaw.community.utils.env_utils import get_current_env

router = APIRouter()
logger = get_logger()


def _cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
    }


def _success_iam_response(iam_token: str, headers: dict[str, str]) -> Response:
    return JSONResponse(
        content={"success": True, "iam_token": iam_token},
        headers=headers,
    )


def _request_injector(request: Request):
    return getattr(getattr(request, "app", None), "state", None).injector


def _get_optional_dependency(
    request: Request,
    dependency: Any,
    error_code: str,
):
    try:
        return _request_injector(request).get(dependency)
    except Exception as exc:
        logger.warning("caller_token_dependency_unavailable code=%s", error_code)
        raise CallerCredentialError(error_code) from exc


def _build_auth_context(request: Request) -> AuthRequestContext:
    return AuthRequestContext(
        cookies=dict(request.cookies),
        headers={k: v for k, v in request.headers.items()},
        query_params=dict(request.query_params),
        base_url=str(request.base_url),
    )


async def _resolve_current_user(request: Request):
    auth_plugin = _get_optional_dependency(
        request,
        AuthPlugin,
        CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
    )
    identity = await auth_plugin.resolve_user_from_request(_build_auth_context(request))
    return AuthenticatedUser(
        id=identity.id,
        staffId=identity.staffId,
        operatorName=identity.operatorName,
        nickName=identity.nickName,
        tenantId=identity.tenantId,
    )


def _caller_error_status(code: str) -> int:
    if code == CALLER_CREDENTIAL_REQUEST_INVALID:
        return 400
    if code == CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE:
        return 503
    return 502


@router.options("/api/v1/token/exchange")
async def token_exchange_options(request: Request):
    origin = request.headers.get("origin", "*")
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Cookie",
            "Access-Control-Allow-Credentials": "true",
        },
    )


@router.post("/api/v1/token/exchange")
async def token_exchange(
    request: Request,
    plugin: TokenExchangePlugin = Injected(TokenExchangePlugin),
) -> Response:
    headers = _cors_headers(request)
    # Plugin owns the per-runtime policy: Local returns a mock; Prod
    # reads IAM_TOKEN and calls Buservice. Missing cookie / upstream
    # failures raise DomainError subclasses mapped to 400/500 by the
    # global handler in api/app.py.
    content = await plugin.exchange_from_request(request)
    return JSONResponse(content=content, headers=headers)


@router.options("/api/v1/token/iam")
async def get_iam_token_options(request: Request):
    origin = request.headers.get("origin", "*")
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Cookie",
            "Access-Control-Allow-Credentials": "true",
        },
    )


@router.get("/api/v1/token/iam")
async def get_iam_token(
    request: Request,
    bot_id: str | None = Query(default=None),
    stage: CallerIdentityStage = Query(default=CallerIdentityStage.DRAFT),
    publish_id: int | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    is_test_exchange: bool = False,
) -> Response:
    headers = _cors_headers(request)
    iam_token = request.cookies.get("IAM_TOKEN") or ""
    if not iam_token:
        return JSONResponse(
            content={"success": False, "error": "IAM_TOKEN cookie not found"},
            status_code=400,
            headers=headers,
        )
    if is_test_exchange and not bot_id:
        logger.warning("caller_test_exchange_rejected reason=bot_id_missing")
        return JSONResponse(
            content={"success": False, "error": CALLER_CREDENTIAL_REQUEST_INVALID},
            status_code=400,
            headers=headers,
        )
    if is_test_exchange and get_current_env() == "prod":
        logger.warning("caller_test_exchange_rejected reason=production_environment")
        return JSONResponse(
            content={"success": False, "error": CallerIdentityPermissionError().detail},
            status_code=403,
            headers=headers,
        )
    if not bot_id:
        return _success_iam_response(iam_token, headers)

    try:
        caller_identity = _get_optional_dependency(
            request,
            CallerIdentityServiceProtocol,
            CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
        )
    except CallerCredentialError:
        logger.warning(
            "caller_token_context_unavailable bot_id=%s stage=%s "
            "reason=identity_service_missing",
            bot_id,
            stage.value,
        )
        return _success_iam_response(iam_token, headers)
    try:
        token_context = await asyncio.to_thread(
            caller_identity.get_iam_token_context,
            bot_id=bot_id,
            stage=stage,
            publish_id=publish_id,
            entity_id=entity_id,
            is_test_exchange=is_test_exchange,
        )
    except CallerIdentityAmbiguousError as exc:
        logger.warning(
            "caller_token_context_ambiguous bot_id=%s stage=%s test_exchange=%s",
            bot_id,
            stage.value,
            is_test_exchange,
        )
        return JSONResponse(
            content={"success": False, "error": exc.detail},
            status_code=409,
            headers=headers,
        )
    except Exception:
        logger.warning(
            "caller_token_context_unavailable bot_id=%s stage=%s test_exchange=%s",
            bot_id,
            stage.value,
            is_test_exchange,
        )
        return _success_iam_response(iam_token, headers)
    if not token_context.should_exchange_caller_token:
        logger.info(
            "caller_token_exchange_skipped bot_id=%s stage=%s call_type=%s "
            "test_exchange=%s",
            bot_id,
            stage.value,
            token_context.bot_call_type.value,
            is_test_exchange,
        )
        return _success_iam_response(iam_token, headers)
    if not token_context.owner_id:
        logger.warning(
            "caller_token_exchange_failed bot_id=%s stage=%s reason=owner_missing "
            "test_exchange=%s",
            bot_id,
            stage.value,
            is_test_exchange,
        )
        return JSONResponse(
            content={"success": False, "error": CALLER_CREDENTIAL_REQUEST_INVALID},
            status_code=400,
            headers=headers,
        )

    try:
        current_user = await _resolve_current_user(request)
        if is_test_exchange and current_user.staffId != token_context.owner_id:
            logger.warning(
                "caller_test_exchange_rejected bot_id=%s stage=%s reason=not_owner",
                bot_id,
                stage.value,
            )
            return JSONResponse(
                content={
                    "success": False,
                    "error": CallerIdentityPermissionError().detail,
                },
                status_code=403,
                headers=headers,
            )
        passport = _get_optional_dependency(
            request,
            PassportPlugin,
            CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
        )
        provider = _get_optional_dependency(
            request,
            CallerTokenProvider,
            CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
        )
        updater = _get_optional_dependency(
            request,
            CallerRuntimeUpdater,
            CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
        )
        exchange_kwargs = {
            "iam_token": iam_token,
            "caller_user_id": current_user.staffId,
            "bot_id": bot_id,
            "owner_user_id": token_context.owner_id,
            "passport": passport,
            "token_provider": provider,
            "runtime_updater": updater,
            "stage": stage.value,
            "publish_id": publish_id,
            "entity_id": entity_id,
            "binding_id": token_context.binding_id,
        }
        if is_test_exchange:
            exchange_kwargs["is_test_exchange"] = True
        await asyncio.to_thread(
            caller_identity.exchange_caller_identity,
            **exchange_kwargs,
        )
    except CallerCredentialError as exc:
        logger.warning(
            "caller_token_exchange_failed bot_id=%s stage=%s code=%s test_exchange=%s",
            bot_id,
            stage.value,
            exc.code,
            is_test_exchange,
        )
        return JSONResponse(
            content={"success": False, "error": exc.code},
            status_code=_caller_error_status(exc.code),
            headers=headers,
        )
    except Exception:
        logger.warning(
            "caller_runtime_update_failed bot_id=%s stage=%s test_exchange=%s",
            bot_id,
            stage.value,
            is_test_exchange,
        )
        return JSONResponse(
            content={"success": False, "error": CALLER_OUTBOUND_UPDATE_FAILED},
            status_code=502,
            headers=headers,
        )

    logger.info(
        "caller_token_exchange_succeeded bot_id=%s stage=%s test_exchange=%s",
        bot_id,
        stage.value,
        is_test_exchange,
    )
    # COSEC: the Caller credential is written to BaaS only. Returning it to
    # the browser would turn a server-side runtime credential into an API secret.
    return _success_iam_response(iam_token, headers)
