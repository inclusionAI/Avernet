"""SQLAlchemy ORM models for bot_management repositories.

These models are used by repository implementations in plugins.
All models share the same Base from core.base.
"""
import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, BigInteger, Integer, String, DateTime, Text, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY".
# BigInteger renders as "BIGINT" in SQLite, which breaks autoincrement.
# with_variant() makes SQLAlchemy use Integer (-> "INTEGER") on SQLite while
# keeping BigInteger (-> "BIGINT") on MySQL/PostgreSQL.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class TemplateModel(Base):
    """SQLAlchemy ORM model for ac_templates table."""
    __tablename__ = "ac_templates"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    bot_id = Column(String(256), nullable=False, index=True, unique=True)
    ext = Column(Text, nullable=True)  # JSON字符串，存储模板配置
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "ext": json.loads(self.ext) if self.ext else None,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


# ============================================================================
# BotRestartLock - 重启幂等锁
# ============================================================================
class BotRestartLockRecord(BaseModel):
    """重启幂等锁业务模型。

    对应 ac_bot_restart_lock 表。锁键为 (env, entity_id, bot_id)，
    由 UNIQUE 约束保证分布式互斥：INSERT 成功即持锁，冲突即被去重。
    """
    id: Optional[int] = Field(default=None, description="主键ID")
    env: str = Field(default="dev", description="环境标识")
    entity_id: str = Field(..., description="实体ID（bot 的 entity_id）")
    bot_id: str = Field(..., description="Bot ID")
    holder_user_id: str = Field(..., description="持锁者工号（触发重启的用户）")
    lock_token: str = Field(..., description="持锁令牌（fencing token，释放时比对）")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")


class BotRestartLockModel(Base):
    """SQLAlchemy ORM model for ac_bot_restart_lock table."""
    __tablename__ = "ac_bot_restart_lock"

    # 主键 - Uses AutoIncrementBigInteger for SQLite compatibility
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)

    # 业务字段
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识: prod/pre/dev")
    entity_id = Column(String(1024), nullable=False, comment="实体ID（bot 的 entity_id）")
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    holder_user_id = Column(String(1024), nullable=False, comment="持锁者工号（触发重启的用户）")
    lock_token = Column(String(256), nullable=False, comment="持锁令牌（fencing token，释放时比对）")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    __table_args__ = (
        # UNIQUE 约束即为幂等锁本体：同一 (env, entity_id, bot_id) 只能存在一行。
        UniqueConstraint("env", "entity_id", "bot_id", name="uk_env_entity_id_bot_id"),
    )

    def to_record(self) -> BotRestartLockRecord:
        """Convert to Pydantic record."""
        return BotRestartLockRecord(
            id=self.id,
            env=self.env,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
            holder_user_id=self.holder_user_id,
            lock_token=self.lock_token,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
