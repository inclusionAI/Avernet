"""任务发现 per-bot 分布式锁 Repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``TaskDiscoveryLockRepositoryProtocol``.
The only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- The UNIQUE constraint on (env, bot_id, discovery_date) *is* the lock:
  ``acquire`` inserts a row and lets the DB arbitrate concurrent callers —
  exactly one INSERT wins, the rest hit ``IntegrityError`` and are treated
  as "lock held".
- ``release`` hard-deletes the row (no soft delete).
- Staleness (``get_if_stale``) is judged on the DB clock only: both the row's
  ``gmt_create`` and "now" are sourced from the database, so app/DB clock skew
  cannot mis-judge the TTL.

Structurally identical to ``BotRestartLockRepository``, differing only in the
lock key dimensions (``discovery_date`` replaces ``entity_id``, ``holder``
replaces ``holder_user_id``).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from injector import inject
from sqlalchemy import DateTime, func, select, type_coerce
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.task import (
    TaskDiscoveryLockRepositoryProtocol,
)
from agentclaw.community.core.task.task_discovery.lock_models import (
    TaskDiscoveryLockModel,
    TaskDiscoveryLockRecord,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


def _as_naive(dt: datetime) -> datetime:
    """Drop tzinfo so two DB-clock timestamps can always be subtracted.

    ``gmt_create`` and ``func.now()`` share the same DB clock/timezone, but a
    driver may return one tz-aware and the other tz-naive. Stripping tzinfo
    from both is safe (identical tz semantics) and avoids a TypeError.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


class TaskDiscoveryLockRepository(TaskDiscoveryLockRepositoryProtocol):
    """任务发现 per-bot 分布式锁 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Lock = TaskDiscoveryLockModel

    # ========================================================================
    # Acquire / Release
    # ========================================================================

    def acquire(
        self,
        env: str,
        bot_id: str,
        discovery_date: str,
        holder: str,
    ) -> Optional[TaskDiscoveryLockRecord]:
        """获取发现锁（INSERT 一行，UNIQUE 冲突即返回 None）。"""
        with self._db.orm_session() as db:
            row = self._Lock(
                env=env,
                bot_id=bot_id,
                discovery_date=discovery_date,
                holder=holder,
                lock_token=uuid.uuid4().hex,
            )
            db.add(row)
            try:
                db.flush()
                db.refresh(row)
                logger.info(
                    "[discovery_lock.acquire] acquired, id=%s, env=%s, bot_id=%s, "
                    "date=%s, holder=%s, token=%s",
                    row.id, env, bot_id, discovery_date, holder, row.lock_token,
                )
                return row.to_record()
            except IntegrityError:
                # 并发冲突：其他机器已持有该 bot 当日的发现锁。
                # NOTE: deliberately swallowed (return None) rather than
                # re-raised — the Protocol's acquire() contract is
                # Optional[record], so "lock held" is a None return, not an
                # exception the caller must catch. The rollback resets the
                # failed transaction; orm_session()'s subsequent commit on
                # clean exit is then a harmless no-op.
                db.rollback()
                logger.info(
                    "[discovery_lock.acquire] already held, env=%s, bot_id=%s, "
                    "date=%s",
                    env, bot_id, discovery_date,
                )
                return None

    def release(
        self,
        env: str,
        bot_id: str,
        discovery_date: str,
        lock_token: str,
    ) -> bool:
        """释放发现锁（比对令牌后硬删除）。

        仅当行的 ``lock_token`` 与传入令牌一致时才删除，避免误删他人在本锁
        被回收后重新获取的新锁（stale-reaper 与超时后异步释放两种竞态）。
        """
        with self._db.orm_session() as db:
            result = (
                db.query(self._Lock)
                .filter(
                    self._Lock.env == env,
                    self._Lock.bot_id == bot_id,
                    self._Lock.discovery_date == discovery_date,
                    self._Lock.lock_token == lock_token,
                )
                .delete(synchronize_session=False)
            )
            if result > 0:
                logger.info(
                    "[discovery_lock.release] released, env=%s, bot_id=%s, "
                    "date=%s, token=%s",
                    env, bot_id, discovery_date, lock_token,
                )
            else:
                logger.info(
                    "[discovery_lock.release] no-op (token mismatch or already "
                    "gone), env=%s, bot_id=%s, date=%s, token=%s",
                    env, bot_id, discovery_date, lock_token,
                )
            return result > 0

    # ========================================================================
    # Query
    # ========================================================================

    def get_if_stale(
        self,
        env: str,
        bot_id: str,
        discovery_date: str,
        ttl_seconds: int,
    ) -> Optional[TaskDiscoveryLockRecord]:
        """仅当锁存在且已超过 ttl_seconds 时返回记录，否则返回 None。

        过期判定完全基于数据库时钟：``gmt_create`` 与 "now" 都取自 DB，
        因此不受应用与数据库之间的时钟漂移影响。
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self._Lock)
                .filter(
                    self._Lock.env == env,
                    self._Lock.bot_id == bot_id,
                    self._Lock.discovery_date == discovery_date,
                )
                .first()
            )
            if row is None or row.gmt_create is None:
                return None

            # type_coerce ensures both SQLite and MySQL/OceanBase yield a
            # Python datetime for the DB-side "now".
            db_now = db.execute(
                select(type_coerce(func.now(), DateTime))
            ).scalar()
            if db_now is None:
                return None

            # Both timestamps come from the DB clock with identical timezone
            # semantics, but some drivers return TIMESTAMP columns tz-aware
            # while ``func.now()`` coerces tz-naive. Normalize both to naive so
            # the subtraction can't raise "can't subtract offset-naive and
            # offset-aware datetimes" on any driver.
            elapsed = (_as_naive(db_now) - _as_naive(row.gmt_create)).total_seconds()
            if elapsed >= ttl_seconds:
                logger.info(
                    "[discovery_lock.get_if_stale] stale lock, env=%s, bot_id=%s, "
                    "date=%s, elapsed=%.1fs, ttl=%ss",
                    env, bot_id, discovery_date, elapsed, ttl_seconds,
                )
                return row.to_record()
            return None
