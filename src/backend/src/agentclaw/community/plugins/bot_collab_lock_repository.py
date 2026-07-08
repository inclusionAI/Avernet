"""协作锁 Repository (prod ZDAS + local SQLite).

One ORM implementation behind the ``BotCollabLockRepositoryProtocol`` Protocol.
The only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- Uses ORM for all database operations
- gmt_create/gmt_modified are DB server defaults (func.now())
- Unique constraint on (lock_key, env) to prevent duplicate locks
"""
from __future__ import annotations

from typing import List, Optional

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_collaborator.models import (
    BotCollabLockRecord,
    BotCollabLockModel,
)
from agentclaw.community.core.bot_collaborator.repository.protocol import BotCollabLockRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class BotCollabLockRepository(BotCollabLockRepositoryProtocol):
    """协作锁 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Lock = BotCollabLockModel

    # ========================================================================
    # Insert Operations
    # ========================================================================

    def acquire(self, lock_key: str, holder_user_id: str) -> BotCollabLockRecord:
        """获取协作锁。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            row = self._Lock(
                lock_key=lock_key,
                holder_user_id=holder_user_id,
                env=env,
            )
            db.add(row)
            try:
                db.flush()
                db.refresh(row)
                logger.info(
                    "[acquire] acquired lock, id=%s, lock_key=%s, holder=%s",
                    row.id, row.lock_key, row.holder_user_id
                )
                return row.to_record()
            except IntegrityError:
                logger.warning(
                    "[acquire] lock already exists, lock_key=%s, env=%s",
                    lock_key, env
                )
                raise

    # ========================================================================
    # Query Operations
    # ========================================================================

    def get_by_key(self, lock_key: str) -> Optional[BotCollabLockRecord]:
        """根据锁键查询锁记录。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            row = (
                db.query(self._Lock)
                .filter(
                    self._Lock.lock_key == lock_key,
                    self._Lock.env == env,
                )
                .first()
            )
            return row.to_record() if row else None

    def is_locked(self, lock_key: str) -> bool:
        """检查锁是否存在。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            exists = (
                db.query(self._Lock.id)
                .filter(
                    self._Lock.lock_key == lock_key,
                    self._Lock.env == env,
                )
                .first()
            )
            return exists is not None

    def list_by_holder(
        self,
        holder_user_id: str,
    ) -> List[BotCollabLockRecord]:
        """获取用户持有的所有锁。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Lock)
                .filter(
                    self._Lock.holder_user_id == holder_user_id,
                    self._Lock.env == env,
                )
                .order_by(self._Lock.gmt_create.desc())
                .all()
            )
            return [r.to_record() for r in rows]

    # ========================================================================
    # Delete Operations
    # ========================================================================

    def release(self, lock_key: str) -> bool:
        """释放协作锁。"""
        env = get_current_env()
        with self._db.orm_session() as db:
            result = db.query(self._Lock).filter(
                self._Lock.lock_key == lock_key,
                self._Lock.env == env,
            ).delete(synchronize_session=False)

            if result > 0:
                logger.info("[release] released lock, lock_key=%s", lock_key)
            return result > 0
