"""
Bot 配置 API
"""
from fastapi import APIRouter, HTTPException

from engine.community.api.bot.schemas import BotConfigRequest
from engine.community.api.response import ApiResponse
from engine.community.shared.credentials import get_credentials_service

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.post("/config")
async def update_bot_config(request: BotConfigRequest) -> ApiResponse:
    # 验证至少传了一个字段
    if request.role is None and request.visibility is None:
        raise HTTPException(status_code=400, detail="role 和 visibility 至少需要传一个")

    # 验证字段值
    if request.role is not None and request.role not in ("OWNER", "CALLER"):
        raise HTTPException(status_code=400, detail="role 必须是 OWNER 或 CALLER")

    if request.visibility is not None and request.visibility not in ("PRIVATE", "PUBLIC"):
        raise HTTPException(status_code=400, detail="visibility 必须是 PRIVATE 或 PUBLIC")

    # 构建更新字典
    updates = {}
    if request.role is not None:
        updates["ROLE"] = request.role
    if request.visibility is not None:
        updates["VISIBILITY"] = request.visibility

    # 更新文件
    service = get_credentials_service()
    service.update_fields(updates)

    # 返回更新后的配置
    creds = service.get_all()
    return ApiResponse(
        success=True,
        data={
            "role": creds.role,
            "visibility": creds.visibility,
        },
        message="更新成功"
    )
