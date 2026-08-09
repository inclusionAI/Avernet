"""协作操作日志 Repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``BotCollabLogRepositoryProtocol`` Protocol.
The only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- Uses ORM for all database operations
- gmt_create/gmt_modified are DB server defaults (func.now())
- Indexes on (bot_id, owner_id, env, gmt_create) and (operator_id, env, gmt_create)
"""
from __future__ import annotations

from typing import Any, Dict, List

from injector import inject

from agentclaw.community.core.bot_collaborator.models import (
    BotCollabLogRecord,
    BotCollabLogModel,
)
from agentclaw.community.core.repository.protocols.bot import BotCollabLogRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class BotCollabLogRepository(
    BotCollabLogRepositoryProtocol,
):
    """协作操作日志 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Log = BotCollabLogModel

    # ========================================================================
    # Insert Operations
    # ========================================================================

    def insert(self, data: Dict[str, Any]) -> BotCollabLogRecord:
        """创建协作操作日志记录。"""
        with self._db.orm_session() as db:
            row = self._Log(
                bot_id=data["bot_id"],
                owner_id=data["owner_id"],
                detail=data["detail"],
                operator_id=data["operator_id"],
                env=data.get("env", get_current_env()),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            logger.info(
                "[insert] inserted collab log id=%s, bot_id=%s, operator_id=%s",
                row.id, row.bot_id, row.operator_id
            )
            return row.to_record()

    # ========================================================================
    # Query Operations
    # ========================================================================

    def list_by_bot(
        self,
        bot_id: str,
        owner_id: str,
        limit: int = 100,
    ) -> List[BotCollabLogRecord]:
        """按Bot查询协作操作日志列表。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Log)
                .filter(
                    self._Log.bot_id == bot_id,
                    self._Log.owner_id == owner_id,
                    self._Log.env == env,
                )
                .order_by(self._Log.gmt_create.desc())
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_operator(
        self,
        operator_id: str,
        limit: int = 100,
    ) -> List[BotCollabLogRecord]:
        """按操作者查询协作操作日志列表。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Log)
                .filter(
                    self._Log.operator_id == operator_id,
                    self._Log.env == env,
                )
                .order_by(self._Log.gmt_create.desc())
                .limit(limit)
                .all()
            )
            return [r.to_record() for r in rows]
