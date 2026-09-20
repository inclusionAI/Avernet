"""Authenticated employee UI queries; platform-to-platform auth is a separate adapter."""
import asyncio
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_management.schemas import ApiResponse
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.digital_employee_service import DigitalEmployeeServiceProtocol
from agentclaw.community.core.bot_collaborator.interceptor import CollaboratorPermissionInterceptor, with_interceptors
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError, DigitalEmployeeSettings
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.digital_employee import DigitalEmployeePlatformError

router = APIRouter(prefix="/api/digital-employee", tags=["digital-employee"])


class PermissionQuery(BaseModel):
    owner_id: str
    mcp_codes: list[str] = Field(min_length=1, max_length=100)


@router.get("/settings", response_model=ApiResponse)
def settings(
    user: AuthenticatedUser = Depends(get_current_user),
    config: DigitalEmployeeSettings = Injected(DigitalEmployeeSettings),
) -> ApiResponse:
    return ApiResponse(success=True, data={"enabled": config.enabled, "site_url": config.site_url if config.enabled else ""},
                       message="OK" if config.enabled else "数字员工接入未启用，请联系管理员补齐配置")


@router.post("/bots/{bot_id}/mcp-permissions/query", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(bot_id="$bot_id", owner_id="$req.owner_id"))
async def query_permissions(
    bot_id: str, req: PermissionQuery,
    user: AuthenticatedUser = Depends(get_current_user),
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    service: DigitalEmployeeServiceProtocol = Injected(DigitalEmployeeServiceProtocol),
) -> ApiResponse:
    bot = await asyncio.to_thread(bots.get_bot, bot_id, req.owner_id)
    try:
        result = await asyncio.to_thread(service.query_permissions, bot["id"], req.mcp_codes)
    except (DigitalEmployeeError, DigitalEmployeePlatformError) as exc:
        return ApiResponse(success=False, error_code=409, message=str(exc))
    return ApiResponse(success=True, data=result)
