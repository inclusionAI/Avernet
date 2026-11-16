"""Super-admin-only correction of one existing Caller expiry ledger."""
from __future__ import annotations

import time
from typing import Annotated

from fastapi import Depends, Path, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal, require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep, caller_owner_id, refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.openapi_v1.response_envelope import error_response
from agentclaw.community.api.caller_expiry_correction_service import (
    CallerExpiryCorrectionError, CallerExpiryCorrectionServiceProtocol,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .router import router

logger = get_logger()


class CallerExpiryCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(min_length=1, max_length=128)
    binding_id: int = Field(gt=0)
    expires_at: AwareDatetime
    expected_expires_at: AwareDatetime | None = Field(...)
    reason: str = Field(min_length=1, max_length=256)


class CallerExpiryCorrectionResponse(BaseModel):
    instance_id: int
    bot_id: str
    entity_id: str
    caller_user_id: str
    binding_id: int
    previous_expires_at: AwareDatetime | None
    expires_at: AwareDatetime


@router.patch(
    "/{bot_id}/caller-instances/{caller_user_id}/token-expiry",
    response_model=Envelope[CallerExpiryCorrectionResponse],
    dependencies=[Depends(refuse_app_only_caller)],
)
@envelope_errors
async def correct_caller_token_expiry(
    bot_id: BotIdPath, caller_user_id: Annotated[str, Path(min_length=1, max_length=128)],
    body: CallerExpiryCorrectionRequest,
    request: Request, user_id: UserIdDep,
    principal: Annotated[Principal, Depends(require_principal)],
    service: CallerExpiryCorrectionServiceProtocol = Injected(CallerExpiryCorrectionServiceProtocol),
):
    # COSEC: the verified human selects the actor; the path names only the target Caller.
    actor_id = caller_owner_id(principal)
    started = time.monotonic()
    fields = {"system": "backend", "operation": "caller_expiry_correction", "method": "PATCH",
              "actor_id": actor_id, "bot_id": bot_id, "caller_user_id": caller_user_id,
              "entity_id": body.entity_id, "binding_id": body.binding_id,
              "expires_at": body.expires_at.isoformat(),
              "expected_expires_at": body.expected_expires_at.isoformat() if body.expected_expires_at else None}
    logger.info("caller_expiry_correction_requested fields=%s", fields)
    try:
        result = service.correct(
            actor_id=actor_id, bot_id=bot_id, entity_id=body.entity_id, caller_user_id=caller_user_id,
            binding_id=body.binding_id, expires_at=body.expires_at,
            expected_expires_at=body.expected_expires_at, reason=body.reason,
        )
    except (CallerExpiryCorrectionError, ValueError) as exc:
        code = exc.code if isinstance(exc, CallerExpiryCorrectionError) else str(exc)
        if code not in {"caller_expiry_forbidden", "caller_expiry_not_found", "caller_expiry_unsupported",
                        "caller_expiry_binding_changed", "caller_token_expiry_invalid",
                        "caller_instance_changed", "caller_token_expiry_conflict"}:
            code = "caller_expiry_correction_failed"
        status = {"caller_expiry_forbidden": 403, "caller_expiry_not_found": 404,
                  "caller_token_expiry_invalid": 422, "caller_expiry_correction_failed": 500}.get(code, 409)
        logger.warning("caller_expiry_correction_failed fields=%s status=%s error_code=%s duration_ms=%.1f",
                       fields, status, code, (time.monotonic() - started) * 1000)
        return error_response(status, code, request)
    except Exception as exc:
        logger.warning("caller_expiry_correction_failed fields=%s status=500 error_type=%s duration_ms=%.1f",
                       fields, type(exc).__name__, (time.monotonic() - started) * 1000)
        return error_response(500, "caller_expiry_correction_failed", request)
    logger.info("caller_expiry_correction_succeeded fields=%s result=%s status=200 duration_ms=%.1f",
                fields, result, (time.monotonic() - started) * 1000)
    return envelope(CallerExpiryCorrectionResponse.model_validate(result), request)
