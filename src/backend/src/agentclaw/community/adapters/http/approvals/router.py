"""
Approval router for session-level approval mode management.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agentclaw.community.adapters.http.approvals.dependencies import get_connection_manager
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class ApprovalModeGetRequest(BaseModel):
    """获取审批模式请求"""
    user_id: str                 # 用户 ID（等于 entity_id）
    entity_type: Optional[str] = "staff"  # 实体类型（staff/proj/team）
    bot_id: Optional[str] = "default"     # Bot ID
    session_key: Optional[str] = None


class ApprovalModeSetRequest(BaseModel):
    """设置审批模式请求"""
    user_id: str                 # 用户 ID（等于 entity_id）
    entity_type: Optional[str] = "staff"  # 实体类型（staff/proj/team）
    bot_id: Optional[str] = "default"     # Bot ID
    session_key: str
    mode: str  # "approve", "on-miss", "never"


class ApprovalModeResponse(BaseModel):
    """审批模式响应"""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


@router.post("/mode/get")
async def get_approval_mode(
    request: ApprovalModeGetRequest,
    manager: Annotated[object, Depends(get_connection_manager)],
    user: AuthenticatedUser = Depends(get_current_user),
) -> ApprovalModeResponse:
    """
    获取会话审批模式。

    Args:
        user_id: 用户 ID，用于路由到正确的设备
        session_key: 会话标识（可选）

    Returns:
        审批模式信息
    """
    try:
        # 使用 DeviceConnectionManager 获取用户专属客户端
        entity_type = request.entity_type or "staff"
        bot_id = request.bot_id or "default"
        client = await manager.get_client(request.user_id, entity_type, bot_id)

        if not client:
            logger.error(f"[get_approval_mode] No device connection for entity: {request.user_id}, type: {entity_type}, bot: {bot_id}")
            return ApprovalModeResponse(
                success=False,
                error=f"No device connection for entity: {request.user_id}, type: {entity_type}, bot: {bot_id}"
            )

        # 通过 WebSocket 发送请求到 Moltis
        result = await client.send_request(
            method="exec.approvals.get",
            params={"sessionKey": request.session_key} if request.session_key else {},
            timeout=10.0,
        )

        if result.ok:
            return ApprovalModeResponse(success=True, data=result.payload)
        else:
            error_msg = result.error.message if result.error else "Unknown error"
            logger.error(f"[get_approval_mode] Moltis error: {error_msg}")
            return ApprovalModeResponse(success=False, error=error_msg)

    except Exception as e:
        logger.exception(f"[get_approval_mode] Error: {e}")
        return ApprovalModeResponse(success=False, error=str(e))


@router.post("/mode/set")
async def set_approval_mode(
    request: ApprovalModeSetRequest,
    manager: Annotated[object, Depends(get_connection_manager)],
    user: AuthenticatedUser = Depends(get_current_user),
) -> ApprovalModeResponse:
    """
    设置会话审批模式。

    Args:
        user_id: 用户 ID，用于路由到正确的设备
        session_key: 会话标识
        mode: 审批模式 ("approve", "on-miss", "never")

    Returns:
        设置结果
    """
    # 越权校验：只允许用户修改自己的审批模式
    if request.user_id != user.staffId:
        logger.warning(f"[set_approval_mode] 权限拒绝: user={user.staffId} 尝试修改 user_id={request.user_id} 的审批模式")
        return ApprovalModeResponse(
            success=False,
            error=f"无权修改用户 {request.user_id} 的审批模式"
        )

    # 验证模式值
    valid_modes = ["approve", "always", "on-miss", "on_miss", "never", "off"]
    if request.mode not in valid_modes:
        return ApprovalModeResponse(
            success=False,
            error=f"Invalid mode: {request.mode}. Valid modes: {valid_modes}"
        )

    try:
        # 使用 DeviceConnectionManager 获取用户专属客户端
        entity_type = request.entity_type or "staff"
        bot_id = request.bot_id or "default"
        client = await manager.get_client(request.user_id, entity_type, bot_id)

        if not client:
            logger.error(f"[set_approval_mode] No device connection for entity: {request.user_id}, type: {entity_type}, bot: {bot_id}")
            return ApprovalModeResponse(
                success=False,
                error=f"No device connection for entity: {request.user_id}, type: {entity_type}, bot: {bot_id}"
            )

        # 通过 WebSocket 发送请求到 Moltis
        result = await client.send_request(
            method="exec.approvals.set",
            params={
                "sessionKey": request.session_key,
                "mode": request.mode,
            },
            timeout=10.0,
        )

        if result.ok:
            return ApprovalModeResponse(
                success=True,
                data={
                    "ok": result.payload.get("ok", True),
                    "mode": request.mode,
                    "sessionKey": request.session_key,
                }
            )
        else:
            error_msg = result.error.message if result.error else "Unknown error"
            logger.error(f"[set_approval_mode] Moltis error: {error_msg}")
            return ApprovalModeResponse(success=False, error=error_msg)

    except Exception as e:
        logger.exception(f"[set_approval_mode] Error: {e}")
        return ApprovalModeResponse(success=False, error=str(e))


@router.get("/modes")
async def list_approval_modes():
    """
    列出所有可用的审批模式。
    """
    return {
        "success": True,
        "data": [
            {
                "value": "approve",
                "label": "谨慎模式",
                "description": "每个操作都需要确认",
            },
            {
                "value": "on-miss",
                "label": "半自动模式",
                "description": "不确定时询问",
            },
            {
                "value": "never",
                "label": "自主模式",
                "description": "完全自动执行",
            },
        ]
    }
