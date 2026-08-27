"""DiscoveredTask ORM model — 持久化已发现任务数据。

将原 SqliteTaskReader 的 discovered_tasks 表迁移为 SQLAlchemy ORM，
通过 DatabasePlugin.orm_session() 访问：
  - corp 环境走 OceanBase
  - local/singlebox 环境走 SQLite 内存库

与 lock_models.py 同构，遵循 task_discovery 域的 ORM 约定。
"""
from __future__ import annotations

import json

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.task_discovery.models import DiscoveredTask

AutoIncrementBigInteger = Integer  # SQLite-friendly; matches task/repository/models.py


class DiscoveredTaskModel(Base):
    """SQLAlchemy ORM model for ``ac_discovered_tasks`` table.

    对应原 ``discovered_tasks`` SQLite 表，字段与 ``DiscoveredTask`` 领域模型一一映射。
    ``acceptances`` 以 JSON 文本存储，读取时反序列化。
    """

    __tablename__ = "ac_discovered_tasks"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    task_id = Column(String(256), nullable=False, unique=True, comment="唯一标识")
    bot_id = Column(String(256), nullable=False, comment="Bot ID")
    owner_id = Column(String(256), nullable=False, comment="Bot 所有者 ID")
    dt = Column(String(10), nullable=False, comment="日期 YYYY-MM-DD")
    title = Column(String(512), nullable=False, comment="任务标题")
    instruction = Column(Text, nullable=True, comment="核心执行指令")
    background = Column(Text, nullable=True, comment="背景信息")
    discovery_basis = Column(Text, nullable=True, comment="挖掘依据")
    priority = Column(
        String(20), nullable=False, default="medium", comment="优先级 high/medium/low"
    )
    discovered_at = Column(String(64), nullable=True, comment="发现时间戳")
    status = Column(
        String(32),
        nullable=False,
        default="pending_confirmation",
        comment="当前状态",
    )
    objective = Column(Text, nullable=True, comment="任务目标")
    acceptances = Column(Text, nullable=True, comment="验收标准 JSON array")
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
        Index(
            "idx_ac_discovered_tasks_bot_owner_dt",
            "bot_id",
            "owner_id",
            "dt",
        ),
    )

    def to_domain(self) -> DiscoveredTask:
        """Convert ORM row to domain dataclass.

        ``acceptances`` 字段为 JSON 文本，解析失败时防御性回退为空列表。
        """
        try:
            acceptances = json.loads(self.acceptances) if self.acceptances else []
        except (TypeError, ValueError):
            acceptances = []
        return DiscoveredTask(
            task_id=self.task_id,
            bot_id=self.bot_id,
            owner_id=self.owner_id,
            dt=self.dt,
            title=self.title,
            instruction=self.instruction or "",
            background=self.background or "",
            discovery_basis=self.discovery_basis or "",
            priority=self.priority or "medium",
            discovered_at=self.discovered_at,
            status=self.status or "pending_confirmation",
            objective=self.objective or "",
            acceptances=acceptances if isinstance(acceptances, list) else [],
        )


__all__ = ["DiscoveredTaskModel"]
