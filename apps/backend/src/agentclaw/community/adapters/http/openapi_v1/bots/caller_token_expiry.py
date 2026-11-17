"""Super-admin-only correction of one existing Caller expiry ledger."""
from __future__ import annotations

import time
from typing import Annotated

from fastapi import Depends, Path, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope, USER_SCOPED_403
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
    """Correct one Caller instance expiry using the expected current value."""

    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(min_length=1, max_length=128, description="Entity that owns the addressed Bot instance.")
    binding_id: int = Field(gt=0, description="Current dedicated Caller instance binding identifier.")
    expires_at: AwareDatetime = Field(description="Corrected expiry with an explicit timezone; past values are allowed.")
    expected_expires_at: AwareDatetime | None = Field(..., description="Expected current expiry with timezone, or null if no expiry is recorded.")
    reason: str = Field(min_length=1, max_length=256, description="Administrative reason for this correction.")


class CallerExpiryCorrectionResponse(BaseModel):
    """Confirmed Caller instance expiry correction."""

    instance_id: int = Field(description="Corrected Caller instance identifier.")
    bot_id: str = Field(description="Addressed Bot identifier.")
    entity_id: str = Field(description="Entity that owns the addressed Bot instance.")
    caller_user_id: str = Field(description="User identity of the target Caller.")
    binding_id: int = Field(description="Dedicated Caller instance binding identifier.")
    previous_expires_at: AwareDatetime | None = Field(description="Previously recorded expiry in UTC, or null if absent.")
    expires_at: AwareDatetime = Field(description="Corrected expiry in UTC.")


@router.patch(
    "/{bot_id}/caller-instances/{caller_user_id}/token-expiry",
    response_model=Envelope[CallerExpiryCorrectionResponse],
    responses=USER_SCOPED_403,
    dependencies=[Depends(refuse_app_only_caller)],
)
@envelope_errors
async def correct_caller_token_expiry(
    bot_id: BotIdPath, caller_user_id: Annotated[str, Path(min_length=1, max_length=128, description="User identity of the Caller whose instance expiry is corrected.")],
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
