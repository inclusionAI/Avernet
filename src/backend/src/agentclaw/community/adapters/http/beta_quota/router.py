"""Beta Quota API Router."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.beta_quota.schemas import AdjustQuotaRequest, ApiResponse
from agentclaw.community.api.beta_quota_service import BetaQuotaServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.utils import env_utils


router = APIRouter(prefix="/api/v1/beta/quota", tags=["beta-quota"])


@router.get("", response_model=ApiResponse[dict])
async def get_quota(
    user: AuthenticatedUser = Depends(get_current_user),
    service: BetaQuotaServiceProtocol = Injected(BetaQuotaServiceProtocol),
):
    """查询内测剩余名额。"""
    try:
        data = service.get_quota(env_utils.get_current_env())
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40401, data=None)
    return ApiResponse(success=True, message="OK", error_code=200, data=data)


@router.post("/adjust", response_model=ApiResponse[dict])
async def adjust_quota(
    req: AdjustQuotaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BetaQuotaServiceProtocol = Injected(BetaQuotaServiceProtocol),
):
    """按增量调整内测剩余名额。"""
    try:
        data = service.adjust_quota(env_utils.get_current_env(), req.delta, user.staffId)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=40001, data=None)
    return ApiResponse(success=True, message="OK", error_code=200, data=data)
