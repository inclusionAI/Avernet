"""Authenticated HTTP adapter for the disable-only cron admin Service API."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.cron.schemas import ApiResponse
from agentclaw.community.api.cron_admin_service import CronAdminServiceProtocol
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()
router = APIRouter(prefix="/api/cron/admin", tags=["cron-admin"])


class DisableCronRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_stage: Literal["verify", "online"]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


@router.get("/bots/{bot_id}/tasks", response_model=ApiResponse)
async def list_tasks(
    bot_id: str,
    runtime_stage: Literal["verify", "online"] = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronAdminServiceProtocol = Injected(CronAdminServiceProtocol),
) -> ApiResponse:
    try:
        return ApiResponse(**await service.list_tasks(
            operator_id=user.staffId, bot_id=bot_id, runtime_stage=runtime_stage,
        ))
    except CronRelayError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=exc.error_code)
    except Exception:
        logger.exception("Cron admin list failed")
        return ApiResponse(success=False, message="Cron admin list failed", error_code=500)


@router.post("/bots/{bot_id}/tasks/{task_id}/disable", response_model=ApiResponse)
async def disable_task(
    bot_id: str, task_id: str, request: DisableCronRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CronAdminServiceProtocol = Injected(CronAdminServiceProtocol),
) -> ApiResponse:
    try:
        return ApiResponse(**await service.disable_task(
            operator_id=user.staffId, bot_id=bot_id, task_id=task_id,
            runtime_stage=request.runtime_stage, reason=request.reason,
        ))
    except CronRelayError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=exc.error_code)
    except Exception:
        logger.exception("Cron admin disable failed")
        return ApiResponse(success=False, message="Cron admin disable failed", error_code=500)
