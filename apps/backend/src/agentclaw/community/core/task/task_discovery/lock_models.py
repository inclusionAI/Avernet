"""任务发现 per-bot 分布式锁的领域记录与 ORM 模型。

锁键为 ``(env, bot_id, discovery_date)``，由 UNIQUE 约束保证分布式互斥：
INSERT 成功即持锁，冲突即被去重。与 ``BotRestartLockModel`` 同构，差异仅在
锁键维度（discovery_date 替代 entity_id）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = Integer  # SQLite-friendly; matches task/repository/models.py


@dataclass
class TaskDiscoveryLockRecord:
    """任务发现锁业务记录。

    对应 ``ac_task_discovery_lock`` 表。锁键为 (env, bot_id, discovery_date)，
    由 UNIQUE 约束保证分布式互斥：INSERT 成功即持锁，冲突即被去重。

    Attributes:
        id:              主键 ID。
        env:             环境标识。
        bot_id:          Bot ID。
        discovery_date:  发现日期 (YYYY-MM-DD)。
        holder:          持锁者标识 (HOSTNAME 或 socket.gethostname)。
        lock_token:      持锁令牌（fencing token，释放时比对）。
        gmt_create:      创建时间。
        gmt_modified:    修改时间。
    """

    id: Optional[int] = None
    env: str = ""
    bot_id: str = ""
    discovery_date: str = ""
    holder: str = ""
    lock_token: str = ""
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


class TaskDiscoveryLockModel(Base):
    """SQLAlchemy ORM model for ``ac_task_discovery_lock`` table."""

    __tablename__ = "ac_task_discovery_lock"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="环境标识: prod/pre/dev",
    )
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    discovery_date = Column(
        String(10), nullable=False, comment="发现日期 YYYY-MM-DD"
    )
    holder = Column(
        String(256), nullable=False, comment="持锁者 (hostname)"
    )
    lock_token = Column(
        String(256), nullable=False, comment="持锁令牌（fencing token，释放时比对）"
    )
    gmt_create = Column(
        DateTime, default=func.now(), nullable=False, comment="创建时间"
    )
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="修改时间",
    )

    __table_args__ = (
        # UNIQUE 约束即为锁本体：同一 (env, bot_id, discovery_date) 只能存在一行。
        UniqueConstraint(
            "env", "bot_id", "discovery_date",
            name="uk_env_bot_id_discovery_date",
        ),
    )

    def to_record(self) -> TaskDiscoveryLockRecord:
        """Convert to dataclass record."""
        return TaskDiscoveryLockRecord(
            id=self.id,
            env=self.env,
            bot_id=self.bot_id,
            discovery_date=self.discovery_date,
            holder=self.holder,
            lock_token=self.lock_token,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
