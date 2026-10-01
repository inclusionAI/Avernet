"""
Bot好友关系申请审批模型。

场景：
- Bot申请与另一个Bot建立好友关系
- 两种审批方式：需要审批 / 直接通过
"""
import json
from datetime import datetime
from typing import Any, Dict, Optional

from enum import StrEnum
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func

# 使用 plugins.models.Base 以与 db.py init_db() 中的 Base 保持一致
from agentclaw.community.plugin_api.models import Base
from agentclaw.community.utils.env_utils import get_current_env


class FriendRequestStatus(StrEnum):
    """好友申请状态枚举"""
    PENDING = "PENDING"         # 待审批
    APPROVED = "APPROVED"       # 已通过
    REJECTED = "REJECTED"       # 已拒绝
    CANCELLED = "CANCELLED"     # 已取消


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


# ============================================================================
# Pydantic Business Model
# ============================================================================
class BotFriendRequest(BaseModel):
    """
    Bot好友申请业务模型。

    记录Bot之间的好友关系申请和审批流程。
    """
    # 主键（数据库层自动生成）
    id: Optional[int] = Field(default=None, description="主键ID")

    # 申请方信息
    requester_entity_id: str = Field(..., description="申请方实体ID (e.g., staff_xxx)")
    requester_name: Optional[str] = Field(default=None, description="申请人姓名")

    # 目标方信息
    target_entity_id: str = Field(..., description="目标方实体ID (e.g., staff_xxx)")
    target_name: Optional[str] = Field(default=None, description="目标方姓名")
    target_bot_id: str = Field(..., description="目标Bot ID（被申请方）")
    target_owner_name: Optional[str] = Field(default=None, description="被申请Bot的拥有者花名")

    # 状态
    status: str = Field(default=BotFriendStatus.PENDING, description="好友关系状态")

    # 时间戳
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")

    # 扩展字段
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展字段，记录好友申请历史记录")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "requester_entity_id": self.requester_entity_id,
            "requester_name": self.requester_name,
            "target_entity_id": self.target_entity_id,
            "target_name": self.target_name,
            "target_bot_id": self.target_bot_id,
            "target_owner_name": self.target_owner_name,
            "status": self.status,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "ext": self.ext,
        }


class BotFriendRequestCreate(BaseModel):
    """创建好友申请请求模型"""
    requester_entity_id: str = Field(..., description="申请方实体ID")
    requester_name: Optional[str] = Field(default=None, description="申请人姓名")
    target_entity_id: str = Field(..., description="目标方实体ID")
    target_name: Optional[str] = Field(default=None, description="目标方姓名")
    target_bot_id: str = Field(..., description="目标Bot ID")
    target_owner_name: Optional[str] = Field(default=None, description="被申请Bot的拥有者花名")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展字段")


class BotFriendRequestUpdate(BaseModel):
    """更新好友申请请求模型"""
    status: Optional[str] = Field(default=None, description="好友关系状态")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展字段")


class BotFriendQueryKey(BaseModel):
    """批量查询好友关系的查询条件"""
    requester_entity_id: str = Field(..., description="申请方实体ID")
    target_entity_id: str = Field(..., description="目标方实体ID")
    target_bot_id: str = Field(..., description="目标Bot ID")


# ============================================================================
# SQLAlchemy ORM Model (for database)
# ============================================================================
class BotFriendModel(Base):
    """SQLAlchemy ORM model for ac_bot_friend table."""
    __tablename__ = "ac_bot_friend"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)

    # 申请方信息
    requester_entity_id = Column(String(1024), nullable=False, comment="申请方实体ID")
    requester_name = Column(String(1024), nullable=True, comment="申请人姓名")

    # 目标方信息
    target_entity_id = Column(String(1024), nullable=False, comment="目标方实体ID")
    target_name = Column(String(1024), nullable=True, comment="目标方姓名")
    target_bot_id = Column(String(256), nullable=False, comment="目标Bot ID（被申请方）")
    target_owner_name = Column(String(1024), nullable=True, comment="被申请Bot的拥有者花名")

    # 状态
    status = Column(String(20), default=BotFriendStatus.PENDING, nullable=False, comment="好友关系状态")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    # 扩展字段与环境
    ext = Column(Text, nullable=True, comment="扩展字段，记录好友申请历史记录")
    env = Column(String(20), default=get_current_env, nullable=False, comment="环境标识")

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "requester_entity_id": self.requester_entity_id,
            "requester_name": self.requester_name,
            "target_entity_id": self.target_entity_id,
            "target_name": self.target_name,
            "target_bot_id": self.target_bot_id,
            "target_owner_name": self.target_owner_name,
            "status": self.status,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "ext": json.loads(self.ext) if self.ext else None,
            "env": self.env,
        }


# ============================================================================
# Bot好友审批单模型（存储在 BotFriendModel.ext 字段中）
# ============================================================================
class BotFriendApproval(BaseModel):
    """
    Bot好友审批单模型。

    记录好友申请的审批流程信息，存储在 BotFriendModel 的 ext 扩展字段中。
    """
    # 审批单唯一标识
    uuid: str = Field(..., description="审批链接唯一ID")

    # 审批类型
    approval_type: str = Field(default=ApprovalType.MANUAL, description="审批类型: AUTO=自动通过, MANUAL=需审批")
    require_approval: bool = Field(default=True, description="是否需要审批")

    # 审批链接
    approval_url: Optional[str] = Field(default=None, description="审批链接地址")

    # 申请信息
    request_message: Optional[str] = Field(default=None, description="申请附言/申请理由")

    # 审批结果
    approval_time: Optional[datetime] = Field(default=None, description="审批时间")
    reject_reason: Optional[str] = Field(default=None, description="拒绝原因")

    # 审批状态
    status: str = Field(default=ApprovalStatus.PENDING, description="审批状态")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization (stored in ext field)."""
        return {
            "uuid": self.uuid,
            "approval_type": self.approval_type,
            "require_approval": self.require_approval,
            "approval_url": self.approval_url,
            "request_message": self.request_message,
            "approval_time": self.approval_time.isoformat() if self.approval_time else None,
            "reject_reason": self.reject_reason,
            "status": self.status,
        }
