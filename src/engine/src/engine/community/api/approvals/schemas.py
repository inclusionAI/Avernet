"""
Approval router HTTP schemas.

HTTP body / response shapes distinct from the plugin-layer
`engine.community.core.approval.models` types. Naming follows the existing session
router convention (`*Body` for request bodies).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ApprovalModeGetBody(BaseModel):
    """获取审批模式请求体"""
    user_id: Optional[str] = None   # 用户 ID，用于路由到正确设备
    session_key: Optional[str] = None


class ApprovalModeSetBody(BaseModel):
    """设置审批模式请求体"""
    user_id: Optional[str] = None   # 用户 ID，用于路由到正确设备
    session_key: str
    mode: str  # "approve", "on-miss", "never"


class ApprovalModeResponse(BaseModel):
    """审批模式响应 — 与通用 ApiResponse 的区别在于失败分支携带 `error` 字段"""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    warning: Optional[str] = None


__all__ = [
    "ApprovalModeGetBody",
    "ApprovalModeSetBody",
    "ApprovalModeResponse",
]
