"""Token exchange HTTP boundary; Caller policy lives in application services."""

import time
import uuid

from agentclaw.community.core.caller_identity.boundary_logging import redact_boundary
from agentclaw.community.log import get_logger

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse

from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.api.caller_credential import (
    CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
    CALLER_CREDENTIAL_REQUEST_INVALID,
)
from agentclaw.community.api.caller_identity_service import CallerIdentityStage
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth import AuthRequestContext
from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin


router = APIRouter()
logger = get_logger()


def _cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
    }


def _auth_request(request: Request) -> AuthRequestContext:
    return AuthRequestContext(
        cookies=dict(request.cookies),
        headers=dict(request.headers),
        query_params=dict(request.query_params),
        base_url=str(request.base_url),
    )


def _caller_error_status(error: str) -> int:
    if error == CALLER_CREDENTIAL_REQUEST_INVALID or error == "IAM_TOKEN cookie not found":
        return 400
    if error == CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE:
        return 503
    if error == "CALLER_IDENTITY_AMBIGUOUS":
        return 409
    if error == "CALLER_IDENTITY_FORBIDDEN":
        return 403
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
    return JSONResponse(
        content=await plugin.exchange_from_request(request),
        headers=_cors_headers(request),
    )


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
    service: CallerIamTokenServiceProtocol = Injected(CallerIamTokenServiceProtocol),
    session_key: str | None = Query(default=None, alias="sessionKey"),
) -> Response:
    # Query defaults are also used by direct adapter tests; only strings are data.
    session_key = session_key if isinstance(session_key, str) else None
    fields = {"system": "backend", "direction": "inbound", "operation": "caller_iam",
              "operation_id": uuid.uuid4().hex, "method": "GET", "route": "/api/v1/token/iam",
              "request_id": request.headers.get("x-request-id"), "bot_id": bot_id,
              "stage": stage.value, "publish_id": publish_id, "entity_id": entity_id,
              "is_test_exchange": is_test_exchange,
              "session_key": "[REDACTED]" if session_key is not None else None}
    started_at = time.monotonic()
    logger.info("caller_iam_request_received fields=%s", fields)
    try:
        session_kwargs = {"session_key": session_key} if session_key is not None else {}
        result = await service.get_iam_token(
            iam_token=request.cookies.get("IAM_TOKEN") or "",
            auth_request=_auth_request(request),
            bot_id=bot_id,
            stage=stage,
            publish_id=publish_id,
            entity_id=entity_id,
            is_test_exchange=is_test_exchange,
            **session_kwargs,
        )
    except Exception as exc:
        logger.warning("caller_iam_response_failed fields=%s duration_ms=%.1f status_code=%s error_type=%s error_message=caller_iam_failed",
                       fields, (time.monotonic() - started_at) * 1000, getattr(exc, "status_code", 500), type(exc).__name__)
        # COSEC: transport errors may contain the original credential-bearing URL.
        if isinstance(exc, StarletteHTTPException):
            raise HTTPException(status_code=exc.status_code, detail="Caller IAM request failed", headers=exc.headers) from None
        raise HTTPException(status_code=500, detail="Caller IAM request failed") from None
    content = {"success": result.error is None}
    if result.error is None:
        content["iam_token"] = result.iam_token
    else:
        content["error"] = result.error
    logger.info("%s fields=%s status_code=%s duration_ms=%.1f response=%s",
                "caller_iam_response_succeeded" if result.error is None else "caller_iam_response_failed",
                fields, 200 if result.error is None else _caller_error_status(result.error),
                (time.monotonic() - started_at) * 1000, redact_boundary(content, secrets=(request.cookies.get("IAM_TOKEN") or "", session_key or "")))
    return JSONResponse(
        content=content,
        status_code=200 if result.error is None else _caller_error_status(result.error),
        headers=_cors_headers(request),
    )
