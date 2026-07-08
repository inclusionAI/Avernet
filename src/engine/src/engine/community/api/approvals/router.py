"""Approval router for session-level approval mode management.

Routes inbound HTTP requests through the active engine's `ApprovalService`
(`manager.approval`). Engines that don't expose approval-mode policy
(`Engine.approval is None`) cause `check_capability` to 501 the request
before this module reaches into the plugin.
"""
import logging

from fastapi import APIRouter, HTTPException

from engine.community.api.approvals.schemas import (
    ApprovalModeGetBody,
    ApprovalModeResponse,
    ApprovalModeSetBody,
)
from engine.community.api.caps import check_capability
from engine.community.core.approval import (
    ApprovalModeGetRequest,
    ApprovalModeSetRequest,
)
from engine.community.core.engine.capability import Capability
from engine.community.manager import EngineManager

log = logging.getLogger("approval-router")


router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.post("/mode/get")
async def get_approval_mode(body: ApprovalModeGetBody) -> ApprovalModeResponse:
    """
    获取会话审批模式。

    Args:
        user_id: 用户 ID，用于路由到正确的设备
        session_key: 会话标识（可选）

    Returns:
        审批模式信息
    """
    warning = check_capability(Capability.APPROVAL_GET)
    try:
        manager = EngineManager.get_instance()
        result = await manager.approval.get_mode(
            ApprovalModeGetRequest(session_key=body.session_key),
        )
        return ApprovalModeResponse(
            success=True,
            data=result.payload or {"mode": result.mode},
            warning=warning,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"[get_approval_mode] Error: {e}")
        return ApprovalModeResponse(success=False, error=str(e), warning=warning)


@router.post("/mode/set")
async def set_approval_mode(body: ApprovalModeSetBody) -> ApprovalModeResponse:
    """
    设置会话审批模式。

    Args:
        user_id: 用户 ID，用于路由到正确的设备
        session_key: 会话标识
        mode: 审批模式 ("approve", "on-miss", "never")

    Returns:
        设置结果
    """
    # 验证模式值
    valid_modes = ["approve", "always", "on-miss", "on_miss", "never", "off"]
    if body.mode not in valid_modes:
        return ApprovalModeResponse(
            success=False,
            error=f"Invalid mode: {body.mode}. Valid modes: {valid_modes}"
        )

    warning = check_capability(Capability.APPROVAL_SET)
    try:
        manager = EngineManager.get_instance()
        result = await manager.approval.set_mode(
            ApprovalModeSetRequest(session_key=body.session_key, mode=body.mode),
        )
        return ApprovalModeResponse(
            success=True,
            data={
                "ok": result.ok,
                "mode": result.mode,
                "sessionKey": result.session_key,
            },
            warning=warning,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"[set_approval_mode] Error: {e}")
        return ApprovalModeResponse(success=False, error=str(e), warning=warning)


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
