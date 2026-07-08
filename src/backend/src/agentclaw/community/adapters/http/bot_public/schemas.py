"""Bot public API schemas.

HTTP 边界模型，供 FastAPI 做序列化/文档。
只使用 pydantic.BaseModel + Field，不包含任何业务逻辑。
"""
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


# ============================================================================
# 本地枚举/值类型定义（与 core 层保持一致，不依赖 core 层）
# ============================================================================

class BotFriendStatus(StrEnum):
    """好友申请状态枚举"""
    PENDING = "PENDING"        # 待确认（单向添加，等待对方确认）
    ACCEPTED = "ACCEPTED"      # 已成为好友（正常好友关系）
    REJECTED = "REJECTED"      # 已拒绝
    CANCELLED = "CANCELLED"    # 已取消


class ApprovalType(StrEnum):
    """审批类型枚举"""
    AUTO = "AUTO"      # 自动通过
    MANUAL = "MANUAL"  # 需审批


class ApprovalStatus(StrEnum):
    """审批状态枚举"""
    PENDING = "PENDING"      # 待审批
    APPROVED = "APPROVED"    # 已通过
    REJECTED = "REJECTED"    # 已拒绝
    CANCELLED = "CANCELLED"  # 已取消


class BotFriendQueryKey(BaseModel):
    """批量查询好友关系的查询条件"""
    requester_entity_id: str = Field(..., description="申请方实体ID")
    target_entity_id: str = Field(..., description="目标方实体ID")
    target_bot_id: str = Field(..., description="目标Bot ID")


T = TypeVar('T')


# ============================================================================
# Request 模型
# ============================================================================


class CreateFriendRequestRequest(BaseModel):
    """创建好友申请请求"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Owner ID")


# ============================================================================
# Response 模型
# ============================================================================

class BotFriendResponse(BaseModel):
    """Bot好友关系响应模型"""
    id: int | None = Field(default=None, description="主键ID")
    requester_entity_id: str = Field(..., description="申请方实体ID")
    requester_name: str | None = Field(default=None, description="申请人姓名")
    target_entity_id: str = Field(..., description="目标方实体ID")
    target_name: str | None = Field(default=None, description="目标方姓名")
    target_bot_id: str = Field(..., description="目标Bot ID")
    target_owner_name: str | None = Field(default=None, description="被申请Bot的拥有者花名")
    status: str = Field(default=BotFriendStatus.PENDING, description="好友关系状态")
    gmt_create: datetime | None = Field(default=None, description="创建时间")
    gmt_modified: datetime | None = Field(default=None, description="修改时间")
    ext: dict[str, Any] | None = Field(default=None, description="扩展字段")


class BotFriendApprovalResponse(BaseModel):
    """Bot好友审批单响应模型"""
    uuid: str = Field(..., description="审批链接唯一ID")
    approval_type: str = Field(default=ApprovalType.MANUAL, description="审批类型")
    require_approval: bool = Field(default=True, description="是否需要审批")
    approval_url: str | None = Field(default=None, description="审批链接地址")
    request_message: str | None = Field(default=None, description="申请附言")
    approval_time: datetime | None = Field(default=None, description="审批时间")
    reject_reason: str | None = Field(default=None, description="拒绝原因")
    status: str = Field(default=ApprovalStatus.PENDING, description="审批状态")


# ============================================================================
# 公开/取消公开 Bot 请求模型（从 openclawserver 迁移）
# ============================================================================

class BotPublicRequest(BaseModel):
    """公开/取消公开 Bot 请求"""
    public: str = Field(..., description="公开状态: 0=私有, 1=公开")
    permission_owner: str = Field(default="caller", description="权限所有者: caller=调用者, owner=Bot拥有者")
    friend_approval: str = Field(default="0", description="好友审批: 0=无需审批, 1=需要审批")
    owner_id: str | None = Field(default=None, description="可选的Bot所有者ID，用于协作者场景")


class ApiResponse(BaseModel, Generic[T]):
    """统一的 API 响应格式"""
    success: bool
    message: str
    error_code: int = 200
    data: T | None = None
