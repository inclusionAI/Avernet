"""重启幂等锁 Repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``BotRestartLockRepositoryProtocol``.
The only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- The UNIQUE constraint on (env, entity_id, bot_id) *is* the lock: ``acquire``
  inserts a row and lets the DB arbitrate concurrent callers — exactly one
  INSERT wins, the rest hit ``IntegrityError`` and are treated as "lock held".
- ``release`` hard-deletes the row (no soft delete).
- Staleness (``get_if_stale``) is judged on the DB clock only: both the row's
  ``gmt_create`` and "now" are sourced from the database, so app/DB clock skew
  cannot mis-judge the TTL.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from injector import inject
from sqlalchemy import DateTime, func, select, type_coerce
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_management.repository.models import (
    BotRestartLockModel,
    BotRestartLockRecord,
)
from agentclaw.community.core.repository.protocols.bot import BotRestartLockRepositoryProtocol
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


class BotRestartLockRepository(BotRestartLockRepositoryProtocol):
    """重启幂等锁 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Lock = BotRestartLockModel

    # ========================================================================
    # Acquire / Release
    # ========================================================================

    def acquire(
        self, env: str, entity_id: str, bot_id: str, holder_user_id: str
    ) -> Optional[BotRestartLockRecord]:
        """获取重启锁（INSERT 一行，UNIQUE 冲突即返回 None）。"""
        with self._db.orm_session() as db:
            row = self._Lock(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                holder_user_id=holder_user_id,
                lock_token=uuid.uuid4().hex,
            )
            db.add(row)
            try:
                db.flush()
                db.refresh(row)
                logger.info(
                    "[restart_lock.acquire] acquired, id=%s, env=%s, entity_id=%s, bot_id=%s, holder=%s, token=%s",
                    row.id, env, entity_id, bot_id, holder_user_id, row.lock_token,
                )
                return row.to_record()
            except IntegrityError:
                # 并发冲突：其他请求已持有该 bot 的重启锁。
                # NOTE: deliberately swallowed (return None) rather than
                # re-raised like bot_collab_lock_repository — the Protocol's
                # acquire() contract is Optional[record], so "lock held" is a
                # None return, not an exception the caller must catch. The
                # rollback resets the failed transaction; orm_session()'s
                # subsequent commit on clean exit is then a harmless no-op.
                db.rollback()
                logger.info(
                    "[restart_lock.acquire] already held, env=%s, entity_id=%s, bot_id=%s",
                    env, entity_id, bot_id,
                )
                return None

    def release(
        self, env: str, entity_id: str, bot_id: str, lock_token: str
    ) -> bool:
        """释放重启锁（比对令牌后硬删除）。

        仅当行的 ``lock_token`` 与传入令牌一致时才删除，避免误删他人在本锁
        被回收后重新获取的新锁（stale-reaper 与超时后异步释放两种竞态）。
        """
        with self._db.orm_session() as db:
            result = (
                db.query(self._Lock)
                .filter(
                    self._Lock.env == env,
                    self._Lock.entity_id == entity_id,
                    self._Lock.bot_id == bot_id,
                    self._Lock.lock_token == lock_token,
                )
                .delete(synchronize_session=False)
            )
            if result > 0:
                logger.info(
                    "[restart_lock.release] released, env=%s, entity_id=%s, bot_id=%s, token=%s",
                    env, entity_id, bot_id, lock_token,
                )
            else:
                logger.info(
                    "[restart_lock.release] no-op (token mismatch or already gone), "
                    "env=%s, entity_id=%s, bot_id=%s, token=%s",
                    env, entity_id, bot_id, lock_token,
                )
            return result > 0

    # ========================================================================
    # Query
    # ========================================================================

    def get(
        self, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotRestartLockRecord]:
        """查询重启锁记录。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Lock)
                .filter(
                    self._Lock.env == env,
                    self._Lock.entity_id == entity_id,
                    self._Lock.bot_id == bot_id,
                )
                .first()
            )
            return row.to_record() if row else None

    def get_if_stale(
        self, env: str, entity_id: str, bot_id: str, ttl_seconds: int
    ) -> Optional[BotRestartLockRecord]:
        """仅当锁存在且已超过 ttl_seconds 时返回记录，否则返回 None。

        过期判定完全基于数据库时钟：``gmt_create`` 与 "now" 都取自 DB，
        因此不受应用与数据库之间的时钟漂移影响。
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self._Lock)
                .filter(
                    self._Lock.env == env,
                    self._Lock.entity_id == entity_id,
                    self._Lock.bot_id == bot_id,
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
                    "[restart_lock.get_if_stale] stale lock, env=%s, entity_id=%s, bot_id=%s, elapsed=%.1fs, ttl=%ss",
                    env, entity_id, bot_id, elapsed, ttl_seconds,
                )
                return row.to_record()
            return None
