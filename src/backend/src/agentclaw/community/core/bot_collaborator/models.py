"""Bot 协作者模型定义。

包含：
- Pydantic 业务模型（用于 API 层）
- SQLAlchemy ORM 模型（用于数据库持久化）
- 权限级别枚举
"""
from datetime import datetime
from enum import Enum, IntEnum
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.env_utils import get_current_env


# ============================================================================
# StrEnum 兼容类（Python 3.11+ 内置，这里提供兼容）
# ============================================================================
class StrEnum(str, Enum):
    """String enum base class for Python < 3.11 compatibility."""
    pass


class CollaboratorRole(StrEnum):
    """协作者角色枚举。

    继承自 str 和 Enum，便于序列化和比较。
    """
    ADMIN = "admin"  # 管理员 - 可编辑、发布、取消公开、管理协作者
    MEMBER = "member"  # 成员 - 仅可编辑内容


class PermissionLevel(IntEnum):
    """权限级别枚举。

    数值越大权限越高，便于权限比较。
    """
    NONE = 0  # 无权限
    MEMBER = 1  # 成员权限（编辑内容）
    ADMIN = 2  # 管理员权限（发布、管理协作者）
    OWNER = 3  # 拥有者权限（完整权限）


# ============================================================================
# Pydantic 业务模型
# ============================================================================
class CollaboratorRecord(BaseModel):
    """Bot 协作者记录业务模型。

    对应 ac_bot_collaborator 表。
    """
    id: Optional[int] = Field(default=None, description="主键ID")
    bot_pk: int = Field(..., description="Bot主键，关联ac_bots.id")
    bot_id: str = Field(..., description="Bot ID，冗余存储")
    owner_id: str = Field(..., description="Bot拥有者工号，冗余存储")
    user_id: str = Field(..., description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: str = Field(default=CollaboratorRole.ADMIN, description="角色：admin/member")
    operator_id: str = Field(..., description="操作者工号")
    env: str = Field(default="dev", description="环境标识")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "bot_pk": self.bot_pk,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "role": self.role,
            "operator_id": self.operator_id,
            "env": self.env,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


class CollaboratorCreate(BaseModel):
    """创建协作者请求模型。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot拥有者工号")
    user_id: str = Field(..., description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: str = Field(default=CollaboratorRole.ADMIN, description="角色：admin/member")


class CollaboratorUpdate(BaseModel):
    """更新协作者请求模型。"""
    id: int = Field(..., description="协作者记录ID")
    user_id: Optional[str] = Field(default=None, description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: Optional[str] = Field(default=None, description="角色：admin/member")


# ============================================================================
# SQLAlchemy ORM Model
# ============================================================================
class BotCollaboratorModel(Base):
    """SQLAlchemy ORM model for ac_bot_collaborator table."""
    __tablename__ = "ac_bot_collaborator"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)

    # 业务字段
    bot_pk = Column(Integer, nullable=False, comment="Bot主键，关联ac_bots.id")
    bot_id = Column(String(256), nullable=False, comment="Bot ID，冗余存储")
    owner_id = Column(String(256), nullable=False, comment="Bot拥有者工号，冗余存储")
    user_id = Column(String(256), nullable=False, comment="协作者工号")
    user_name = Column(String(100), nullable=True, comment="协作者花名")
    role = Column(String(32), nullable=False, default=CollaboratorRole.ADMIN, comment="角色：admin/member")
    operator_id = Column(String(256), nullable=False, comment="操作者工号")
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    __table_args__ = (
        # 唯一索引：防止重复添加同一用户
        UniqueConstraint("bot_pk", "user_id", "env", name="uk_bot_pk_user_env"),
        # 普通索引：按用户查询参与的Bot
        Index("idx_user_env_botpk", "user_id", "env", "bot_pk"),
        # 普通索引：获取协作者列表
        Index("idx_bot_id_owner_env", "bot_id", "owner_id", "env"),
    )

    def to_record(self) -> CollaboratorRecord:
        """Convert to Pydantic record."""
        return CollaboratorRecord(
            id=self.id,
            bot_pk=self.bot_pk,
            bot_id=self.bot_id,
            owner_id=self.owner_id,
            user_id=self.user_id,
            user_name=self.user_name,
            role=self.role,
            operator_id=self.operator_id,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# ============================================================================
# BotCollabLog - 协作操作日志
# ============================================================================
class BotCollabLogRecord(BaseModel):
    """协作操作日志业务模型。

    对应 ac_bot_collab_log 表。
    """
    id: Optional[int] = Field(default=None, description="主键ID")
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot拥有者工号")
    detail: str = Field(..., description="操作详情")
    operator_id: str = Field(..., description="操作者工号")
    env: str = Field(default="dev", description="环境标识")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotCollabLogCreate(BaseModel):
    """创建协作操作日志请求模型。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot拥有者工号")
    detail: str = Field(..., description="操作详情")
    operator_id: str = Field(..., description="操作者工号")


class BotCollabLogModel(Base):
    """SQLAlchemy ORM model for ac_bot_collab_log table."""
    __tablename__ = "ac_bot_collab_log"

    # 主键 - Uses AutoIncrementBigInteger for SQLite compatibility
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)

    # 业务字段
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    owner_id = Column(String(256), nullable=False, comment="Bot拥有者工号")
    detail = Column(Text, nullable=False, comment="操作详情")
    operator_id = Column(String(256), nullable=False, comment="操作者工号")
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    __table_args__ = (
        Index("idx_bot_id_owner_env_created", "bot_id", "owner_id", "env", "gmt_create"),
        Index("idx_operator_env_created", "operator_id", "env", "gmt_create"),
    )

    def to_record(self) -> BotCollabLogRecord:
        """Convert to Pydantic record."""
        return BotCollabLogRecord(
            id=self.id,
            bot_id=self.bot_id,
            owner_id=self.owner_id,
            detail=self.detail,
            operator_id=self.operator_id,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# ============================================================================
# BotCollabLock - 协作锁
# ============================================================================
class BotCollabLockRecord(BaseModel):
    """协作锁业务模型。

    对应 ac_bot_collab_lock 表。
    """
    id: Optional[int] = Field(default=None, description="主键ID")
    lock_key: str = Field(..., description="锁键")
    holder_user_id: str = Field(..., description="持锁者工号")
    env: str = Field(default="dev", description="环境标识")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotCollabLockCreate(BaseModel):
    """创建协作锁请求模型。"""
    lock_key: str = Field(..., description="锁键")
    holder_user_id: str = Field(..., description="持锁者工号")


class LockInfoResult(BaseModel):
    """锁信息查询结果。

    包含锁记录、持有者名字、是否有协作者和持有者是否是 owner。
    """
    lock: Optional[BotCollabLockRecord] = Field(default=None, description="锁记录，未锁定时为 None")
    holder_name: Optional[str] = Field(default=None, description="锁持有者名字")
    has_collaborators: bool = Field(default=False, description="Bot 是否有协作者")
    is_owner: bool = Field(default=False, description="锁持有者是否是 Bot owner")


class SessionLockInfoResult(BaseModel):
    """会话锁信息查询结果（coding 应用会话级锁）。

    与 LockInfoResult 不同：不依赖"是否有协作者"短路，直接按 lock_key 查锁，
    并标记当前用户是否为持有者（is_mine）。
    """
    locked: bool = Field(default=False, description="是否已被锁定")
    lock: Optional[BotCollabLockRecord] = Field(default=None, description="锁记录，未锁定时为 None")
    holder_name: Optional[str] = Field(default=None, description="锁持有者名字")
    is_mine: bool = Field(default=False, description="当前用户是否持有该锁")


class BotCollabLockModel(Base):
    """SQLAlchemy ORM model for ac_bot_collab_lock table."""
    __tablename__ = "ac_bot_collab_lock"

    # 主键 - Uses AutoIncrementBigInteger for SQLite compatibility
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)

    # 业务字段
    lock_key = Column(String(256), nullable=False, comment="锁键")
    holder_user_id = Column(String(256), nullable=False, comment="持锁者工号")
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    __table_args__ = (
        UniqueConstraint("lock_key", "env", name="uk_lockkey_env"),
        Index("idx_holder_user_env", "holder_user_id", "env"),
    )

    def to_record(self) -> BotCollabLockRecord:
        """Convert to Pydantic record."""
        return BotCollabLockRecord(
            id=self.id,
            lock_key=self.lock_key,
            holder_user_id=self.holder_user_id,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
