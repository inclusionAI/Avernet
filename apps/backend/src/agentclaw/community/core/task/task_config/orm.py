"""ORM model for ``task_config`` — typed config store keyed by (config_name, env).

Spec: ``apps/backend/specs/2026-11-02-task-config/spec.md``. ``config_value`` is
stored as JSON text and interpreted by ``config_type`` (single_string|bool|set);
the repository persists pre-serialized JSON text, the service encodes/validates.

Mirrors the task-domain ORM idiom (``core/task/repository/models.py``): ``Base``
from ``core/base``, ``AutoIncrementBigInteger`` so SQLite autoincrements (plain
``BigInteger`` would render BIGINT and break SQLite's INTEGER-PK autoincrement),
and a callable ``get_current_env`` default on ``env`` (not a ``"prod"`` literal —
a literal fallback would silently route a missed insert into prod, the
spec's worst anti-goal). DB-level timestamps use ``server_default=func.now()``
(the proven convention in caller_identity / spaces) so a Core ``insert()``
upsert path populates them without an ORM flush.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env

# SQLite autoincrements only on INTEGER PRIMARY KEY; BigInteger renders BIGINT
# and breaks SQLite autoincrement. with_variant keeps BIGINT on MySQL/OceanBase
# (matching core/task/repository/models.py).
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class TaskConfigModel(Base):
    """ORM for ``task_config`` — one typed config row per (config_name, env)."""

    __tablename__ = "task_config"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    config_name = Column(String(128), nullable=False)
    config_type = Column(String(32), nullable=False)
    config_value = Column(Text, nullable=False)
    env = Column(String(32), nullable=False, default=get_current_env)
    creator = Column(String(128), nullable=True)
    modifier = Column(String(128), nullable=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modify = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("config_name", "env", name="uk_task_config_name_env"),
        Index("idx_task_config_env", "env"),
    )
