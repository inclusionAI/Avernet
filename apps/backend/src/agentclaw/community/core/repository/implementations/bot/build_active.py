"""构建活跃守卫 Repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``BotBuildActiveRepositoryProtocol``. The
injected :class:`DatabasePlugin` supplies ``orm_session()`` in both runtimes,
so this single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- The UNIQUE constraint on (env, entity_id, bot_id) is the coordination
  primitive: a build inserts a row, and a second build of the same Bot sees the
  conflict instead of racing on a read-then-write.
- ``mark_active`` mints a ``build_lease``; ``clear`` and ``touch`` are
  compare-by-token so a build whose row was replaced after TTL cannot delete
  or renew its successor's marker.
- ``touch`` refreshes the row from the build's heartbeat, so a build longer
  than the TTL keeps its protection.
- Freshness (``is_active`` / stale replacement in ``mark_active``) is judged on
  the DB clock only, so app/DB clock skew cannot leave a crashed build's
  marker blocking restarts forever.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from injector import inject
from sqlalchemy import DateTime, func, select, type_coerce
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_management.repository.models import (
    BotBuildActiveModel,
)
from agentclaw.community.core.repository.protocols.bot import (
    DEFAULT_BUILD_ACTIVE_TTL_SECONDS,
    BotBuildActiveRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


def _as_naive(dt: datetime) -> datetime:
    """Drop tzinfo so two DB-clock timestamps can always be subtracted.

    ``gmt_create`` and ``func.now()`` share the same DB clock, but a driver may
    return one tz-aware and the other tz-naive.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _is_duplicate_key(error: IntegrityError) -> bool:
    """Distinguish the UNIQUE conflict from other integrity failures.

    SQLite says "UNIQUE constraint failed", MySQL/OceanBase "Duplicate entry".
    A broken constraint or schema must propagate instead of being reported as
    "another build is active" and leaving the build silently unguarded.
    """
    message = str(getattr(error, "orig", None) or error).lower()
    return "unique" in message or "duplicate" in message


class BotBuildActiveRepository(BotBuildActiveRepositoryProtocol):
    """构建活跃守卫 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Active = BotBuildActiveModel

    def _match(self, env: str, entity_id: str, bot_id: str) -> list:
        return [
            self._Active.env == env,
            self._Active.entity_id == entity_id,
            self._Active.bot_id == bot_id,
        ]

    @staticmethod
    def _db_now(db) -> datetime | None:
        # type_coerce ensures both SQLite and MySQL/OceanBase yield a Python
        # datetime for the DB-side "now".
        return db.execute(select(type_coerce(func.now(), DateTime))).scalar()

    def _expire_stale_row(
        self, db, env: str, entity_id: str, bot_id: str, ttl_seconds: int,
    ) -> None:
        """Delete a crashed build's marker so a fresh build can claim the key."""
        row = db.query(self._Active).filter(*self._match(env, entity_id, bot_id)).first()
        if row is None or row.gmt_create is None:
            return
        db_now = self._db_now(db)
        if db_now is None:
            return
        elapsed = (_as_naive(db_now) - _as_naive(row.gmt_create)).total_seconds()
        if elapsed >= ttl_seconds:
            # Re-check freshness inside the DELETE: the owner's heartbeat may
            # renew the row between the SELECT above and this statement.
            cutoff = _as_naive(db_now) - timedelta(seconds=ttl_seconds)
            deleted = (
                db.query(self._Active)
                .filter(
                    *self._match(env, entity_id, bot_id),
                    self._Active.gmt_create <= cutoff,
                )
                .delete(synchronize_session=False)
            )
            if deleted:
                logger.info(
                    "[build_active.mark] replaced stale row, env=%s, entity_id=%s, bot_id=%s, elapsed=%.1fs, ttl=%ss",
                    env, entity_id, bot_id, elapsed, ttl_seconds,
                )

    # ========================================================================
    # Mark / Clear / Touch
    # ========================================================================

    def mark_active(
        self,
        env: str,
        entity_id: str,
        bot_id: str,
        ttl_seconds: int = DEFAULT_BUILD_ACTIVE_TTL_SECONDS,
    ) -> Optional[str]:
        """标记构建开始并返回 fencing token（UNIQUE 冲突即表示已有构建活跃）。"""
        with self._db.orm_session() as db:
            # A crashed build leaves its row behind; replace it once expired so
            # the UNIQUE key does not block every later build of this Bot.
            self._expire_stale_row(db, env, entity_id, bot_id, ttl_seconds)
            token = uuid.uuid4().hex
            row = self._Active(
                env=env, entity_id=entity_id, bot_id=bot_id, build_lease=token,
            )
            db.add(row)
            try:
                db.flush()
                logger.info(
                    "[build_active.mark] marked, env=%s, entity_id=%s, bot_id=%s",
                    env, entity_id, bot_id,
                )
                return token
            except IntegrityError as error:
                db.rollback()
                if not _is_duplicate_key(error):
                    # Not a concurrent-build conflict: let the caller see the
                    # real schema/constraint failure instead of "already active".
                    raise
                logger.info(
                    "[build_active.mark] already active, env=%s, entity_id=%s, bot_id=%s",
                    env, entity_id, bot_id,
                )
                return None

    def clear(self, env: str, entity_id: str, bot_id: str, build_lease: str) -> bool:
        """清理构建标记（比对令牌后硬删除，避免误删接管行）。"""
        with self._db.orm_session() as db:
            result = (
                db.query(self._Active)
                .filter(
                    *self._match(env, entity_id, bot_id),
                    self._Active.build_lease == build_lease,
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                "[build_active.clear] cleared=%s, env=%s, entity_id=%s, bot_id=%s",
                result > 0, env, entity_id, bot_id,
            )
            return result > 0

    def touch(
        self,
        env: str,
        entity_id: str,
        bot_id: str,
        build_lease: str,
    ) -> bool:
        """续租构建标记（仅令牌匹配时刷新 DB 时钟上的 gmt_create）。"""
        with self._db.orm_session() as db:
            db_now = self._db_now(db)
            if db_now is None:
                return False
            result = (
                db.query(self._Active)
                .filter(
                    *self._match(env, entity_id, bot_id),
                    self._Active.build_lease == build_lease,
                )
                .update(
                    {self._Active.gmt_create: db_now, self._Active.gmt_modified: db_now},
                    synchronize_session=False,
                )
            )
            logger.info(
                "[build_active.touch] renewed=%s, env=%s, entity_id=%s, bot_id=%s",
                result > 0, env, entity_id, bot_id,
            )
            return result > 0

    # ========================================================================
    # Query
    # ========================================================================

    def is_active(
        self,
        env: str,
        entity_id: str,
        bot_id: str,
        ttl_seconds: int = DEFAULT_BUILD_ACTIVE_TTL_SECONDS,
    ) -> bool:
        """仅当存在未过期的构建标记时返回 True（TTL 按 DB 时钟判定）。"""
        with self._db.orm_session() as db:
            row = db.query(self._Active).filter(*self._match(env, entity_id, bot_id)).first()
            if row is None or row.gmt_create is None:
                return False
            db_now = self._db_now(db)
            if db_now is None:
                return False
            elapsed = (_as_naive(db_now) - _as_naive(row.gmt_create)).total_seconds()
            active = elapsed < ttl_seconds
            logger.info(
                "[build_active.is_active] active=%s, env=%s, entity_id=%s, bot_id=%s, elapsed=%.1fs, ttl=%ss",
                active, env, entity_id, bot_id, elapsed, ttl_seconds,
            )
            return active