"""Bot Publish 模型定义。

包含：
- Pydantic 业务模型（用于 API 层）
- SQLAlchemy ORM 模型（用于数据库持久化）
"""
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from sqlalchemy.sql import func

from agentclaw.community.plugin_api.models import Base
from agentclaw.community.utils.env_utils import get_current_env


# ============================================================================
# 枚举定义
# ============================================================================
class PublishStatus(StrEnum):
    """发布状态枚举"""
    DRAFT = "draft"              # 草稿
    BUILDING = "building"        # 构建中
    BUILT = "built"              # 已构建
    VALIDATE_PUB = "validate_pub"  # 验证发布
    VALIDATING = "validating"     # 验证中
    ONLINE_PUB = "online_pub"     # 上线发布
    SUCCESS = "success"           # 发布成功
    UPGRADED = "upgraded"         # 已升级
    RELEASED = "released"         # 已下线
    FAILED = "failed"             # 发布失败

    @classmethod
    def all(cls) -> list:
        """返回所有状态值列表"""
        return [s.value for s in cls]


def select_stage_bind_id(binding_info: dict, status: str):
    """根据发布状态从 ``ext.binding`` 选择 stage ``bind_id``。

    必须以 status 字段为准来选择，不能假设“有 online 就一定是 online 态”：

    - ``validating``：取 ``verify``
    - ``success``：取 ``online``
    - 其他状态：优先 ``online``，其次 ``verify``

    ``bind_id`` 可能为 0，调用方应以 ``if not bind_id`` 判定“无 stage binding”
    （binding 主键恒 ≥1，故 0 永远不是真实 binding）。
    """
    if status == PublishStatus.VALIDATING.value:
        return binding_info.get("verify")
    if status == PublishStatus.SUCCESS.value:
        return binding_info.get("online")
    # 其他状态优先使用 online，其次 verify
    return binding_info.get("online") or binding_info.get("verify")


# ============================================================================
# Pydantic 业务模型
# ============================================================================
class BotPublishRecord(BaseModel):
    """Bot发布记录业务模型。

    对应 ac_bot_publish 表。
    """
    id: Optional[int] = Field(default=None, description="主键ID")
    source_bot_pk: int = Field(..., description="ac_bots 表主键")
    source_bot_id: str = Field(..., description="源 bot_id，区分数字分身/个人")
    publish_bot_id: str = Field(..., description="发布后的 bot_id = source_bot_id + pub + version")
    name: str = Field(..., description="bot名称")
    description: Optional[str] = Field(default=None, description="描述")
    owner_id: str = Field(..., description="工号")
    owner_name: Optional[str] = Field(default=None, description="花名")
    status: str = Field(default=PublishStatus.DRAFT, description="发布状态")
    version: Optional[int] = Field(default=None, description="版本号")
    last_pub_id: int = Field(default=0, description="上次发布成功id")
    env: str = Field(default="dev", description="环境: prod/pre/dev")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展信息")
    permission_owner: str = Field(..., description="权限归属")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "source_bot_pk": self.source_bot_pk,
            "source_bot_id": self.source_bot_id,
            "publish_bot_id": self.publish_bot_id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "status": self.status,
            "version": self.version,
            "last_pub_id": self.last_pub_id,
            "env": self.env,
            "ext": self.ext,
            "permission_owner": self.permission_owner,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


class BotPublishCreate(BaseModel):
    """创建发布记录请求模型"""
    source_bot_pk: int = Field(..., description="ac_bots 表主键")
    source_bot_id: str = Field(..., description="源 bot_id")
    publish_bot_id: str = Field(..., description="发布后的 bot_id")
    name: str = Field(..., description="bot名称")
    description: Optional[str] = Field(default=None, description="描述")
    owner_id: str = Field(..., description="工号")
    owner_name: Optional[str] = Field(default=None, description="花名")
    status: str = Field(default=PublishStatus.DRAFT, description="发布状态")
    version: Optional[int] = Field(default=None, description="版本号")
    last_pub_id: int = Field(default=0, description="上次发布成功id")
    env: str = Field(default="dev", description="环境")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展信息")
    permission_owner: str = Field(..., description="权限归属")


class BotPublishUpdate(BaseModel):
    """更新发布记录请求模型"""
    status: Optional[str] = Field(default=None, description="发布状态")
    version: Optional[int] = Field(default=None, description="版本号")
    last_pub_id: Optional[int] = Field(default=None, description="上次发布成功id")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展信息")


# ============================================================================
# SQLAlchemy ORM Model
# ============================================================================
class BotPublishModel(Base):
    """SQLAlchemy ORM model for ac_bot_publish table."""
    __tablename__ = "ac_bot_publish"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)

    # 业务字段
    source_bot_pk = Column(Integer, nullable=False, comment="ac_bots 表主键")
    source_bot_id = Column(String(512), nullable=False, comment="源 bot_id")
    publish_bot_id = Column(String(1024), nullable=False, comment="发布后的 bot_id")
    name = Column(String(2048), nullable=False, comment="bot名称")
    description = Column(String(4096), nullable=True, comment="描述")
    owner_id = Column(String(128), nullable=False, comment="工号")
    owner_name = Column(String(128), nullable=True, comment="花名")
    status = Column(String(128), nullable=False, default=PublishStatus.DRAFT, comment="发布状态")
    version = Column(Integer, nullable=True, comment="版本号")
    last_pub_id = Column(Integer, nullable=False, default=0, comment="上次发布成功id")
    env = Column(String(64), nullable=False, default=get_current_env, comment="环境")
    ext = Column(Text, nullable=True, comment="扩展信息 JSON")
    permission_owner = Column(String(64), nullable=False, comment="权限归属")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    __table_args__ = (
        Index(
            "idx_pb_owner_status",
            "publish_bot_id",
            "owner_id",
            "status",
        ),
        Index("idx_owner_name", "owner_name"),
        Index("idx_name", "name"),
        Index("idx_last_pub_id", "last_pub_id"),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "source_bot_pk": self.source_bot_pk,
            "source_bot_id": self.source_bot_id,
            "publish_bot_id": self.publish_bot_id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "status": self.status,
            "version": self.version,
            "last_pub_id": self.last_pub_id,
            "env": self.env,
            "ext": json.loads(self.ext) if self.ext else None,
            "permission_owner": self.permission_owner,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }

    def to_record(self) -> BotPublishRecord:
        """Convert to Pydantic record."""
        return BotPublishRecord(
            id=self.id,
            source_bot_pk=self.source_bot_pk,
            source_bot_id=self.source_bot_id,
            publish_bot_id=self.publish_bot_id,
            name=self.name,
            description=self.description,
            owner_id=self.owner_id,
            owner_name=self.owner_name,
            status=self.status,
            version=self.version,
            last_pub_id=self.last_pub_id,
            env=self.env,
            ext=json.loads(self.ext) if self.ext else None,
            permission_owner=self.permission_owner,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
