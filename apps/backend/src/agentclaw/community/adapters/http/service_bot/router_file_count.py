"""Read-only current-runtime file-count HTTP adaptation."""
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot.schemas import ApiResponse
from agentclaw.community.api.file_count_service import FileCountServiceProtocol
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.di import Injected
from agentclaw.community.kernel.file_count import FileCountError, FileCountQuery
from agentclaw.community.log import get_logger

logger = get_logger()
router = APIRouter(prefix="/api/service-bot/publish/ops", tags=["service-bot-ops"])


@router.get("/file-count", response_model=ApiResponse)
async def query_file_count(
    bot_id: str = Query(..., min_length=1, max_length=255),
    entity_id: str = Query(..., min_length=1, max_length=255),
    stage: Literal["draft", "verify", "online"] = Query(...),
    path: str = Query(..., min_length=1, max_length=4096, pattern="^[^\\x00]+$"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: FileCountServiceProtocol = Injected(FileCountServiceProtocol),
) -> ApiResponse:
    query = FileCountQuery(bot_id, entity_id, stage, path, str(uuid4()))
    try:
        result = await service.query(query, user.staffId, is_admin=user.staffId in super_admin())
        return ApiResponse(success=result["success"], data=result,
                           error_code=200 if result["success"] else 502)
    except FileCountError as exc:
        return ApiResponse(success=False, message=exc.code,
                           error_code=403 if exc.code == "permission_denied" else 409)
    except Exception:
        logger.warning("backend.file_count.http_failure request_id=%s error_code=operation_failed",
                       query.request_id)
        return ApiResponse(success=False, message="file_count_failed", error_code=500)
