"""Manifest apply record + serialization lock Repository (OceanBase + SQLite).

Two implementations, one file, because they are two halves of one mechanism: an
apply takes the lock, writes its ``RUNNING`` row, works, and stamps the terminal
row. The only per-environment difference is the injected :class:`DatabasePlugin`
— ``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so these
bodies run unchanged on OceanBase (prod) and SQLite (local).

The lock's behaviour is ``BotRestartLockRepository``'s, verbatim in every part
that matters: the UNIQUE constraint *is* the lock, ``acquire`` lets the database
arbitrate concurrent inserts, ``release`` compares the fencing token before
deleting, and ``get_if_stale`` judges the TTL entirely on the database clock.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from injector import inject
from sqlalchemy import DateTime, func, select, type_coerce
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_config_manifest.repository.apply_models import (
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyLockRecord,
    BotConfigManifestApplyModel,
    BotConfigManifestApplyRecord,
)
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


def _as_naive(dt: datetime) -> datetime:
    """Drop tzinfo so two DB-clock timestamps can always be subtracted.

    ``gmt_create`` and ``func.now()`` share the same DB clock and timezone
    semantics, but a driver may return one tz-aware and the other tz-naive.
    Stripping tzinfo from both is safe and avoids a ``TypeError``. Same helper,
    same reason, as ``restart_lock.py``.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


class BotConfigManifestApplyRepository(BotConfigManifestApplyRepositoryProtocol):
    """apply 记录 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Apply = BotConfigManifestApplyModel

    def _match(self, *, env: str, entity_id: str, bot_id: str) -> list:
        """The filter naming one bot's rows.

        The tenant half of the key is supplied by the guard registered on the
        model, so every query here filters on the other three — the leading
        columns of both indexes after the tenant. One helper rather than the
        clauses repeated per call site, for the reason the manifest repository
        records: reads and writes must address rows identically.
        """
        return [
            self._Apply.env == env,
            self._Apply.entity_id == entity_id,
            self._Apply.bot_id == bot_id,
        ]

    def start(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        apply_id: str,
        trigger: str,
        actor: str,
        report: str,
    ) -> BotConfigManifestApplyRecord:
        """插入 RUNNING 行；apply 开始前调用。"""
        with self._db.orm_session() as db:
            row = self._Apply(
                apply_id=apply_id,
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                trigger=trigger,
                status="RUNNING",
                report=report,
                actor=actor,
                # App time rather than ``func.now()``: this timestamp is also
                # carried in the in-memory report the orchestrator builds, and
                # the two must agree. ``gmt_create`` remains DB time for audit.
                started_at=datetime.now(),
                finished_at=None,
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            logger.info(
                "[manifest_apply.start] running, apply_id=%s, env=%s, "
                "entity_id=%s, bot_id=%s, trigger=%s",
                apply_id,
                env,
                entity_id,
                bot_id,
                trigger,
            )
            return row.to_record()

    def finish(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        apply_id: str,
        status: str,
        report: str,
    ) -> Optional[BotConfigManifestApplyRecord]:
        """写入终态与完整报告；apply 结束时调用（成功或失败）。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Apply)
                .filter(
                    *self._match(env=env, entity_id=entity_id, bot_id=bot_id),
                    self._Apply.apply_id == apply_id,
                )
                .one_or_none()
            )
            if row is None:
                # Nothing to stamp. Returning None rather than raising is
                # deliberate: this runs on a background thread with no caller to
                # catch anything, so a lost row must not become an unhandled
                # exception in a daemon.
                logger.warning(
                    "[manifest_apply.finish] row gone, apply_id=%s, env=%s, "
                    "entity_id=%s, bot_id=%s",
                    apply_id,
                    env,
                    entity_id,
                    bot_id,
                )
                return None
            row.status = status
            row.report = report
            row.finished_at = datetime.now()
            db.flush()
            db.refresh(row)
            logger.info(
                "[manifest_apply.finish] apply_id=%s, env=%s, entity_id=%s, "
                "bot_id=%s, status=%s",
                apply_id,
                env,
                entity_id,
                bot_id,
                status,
            )
            return row.to_record()

    def get(
        self, *, env: str, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[BotConfigManifestApplyRecord]:
        """按 apply_id 读取；**同时**按 bot 键过滤。

        The bot key is part of the filter on purpose: an ``apply_id`` guessed or
        leaked from another bot must not resolve here. The id is the caller's
        handle, never what authorizes the read.
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self._Apply)
                .filter(
                    *self._match(env=env, entity_id=entity_id, bot_id=bot_id),
                    self._Apply.apply_id == apply_id,
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def latest(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestApplyRecord]:
        """最近一次 apply；从未 apply 过返回 None（不是错误）。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Apply)
                .filter(*self._match(env=env, entity_id=entity_id, bot_id=bot_id))
                .order_by(self._Apply.id.desc())
                .first()
            )
            return row.to_record() if row is not None else None


class BotConfigManifestApplyLockRepository(
    BotConfigManifestApplyLockRepositoryProtocol
):
    """apply 串行锁 Repository 实现（形态沿用 ac_bot_restart_lock）。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Lock = BotConfigManifestApplyLockModel

    def _match(self, *, env: str, entity_id: str, bot_id: str) -> list:
        return [
            self._Lock.env == env,
            self._Lock.entity_id == entity_id,
            self._Lock.bot_id == bot_id,
        ]

    def acquire(
        self, *, env: str, entity_id: str, bot_id: str, holder_user_id: str
    ) -> Optional[BotConfigManifestApplyLockRecord]:
        """获取 apply 锁（INSERT 一行，UNIQUE 冲突即返回 None）。"""
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
                    "[manifest_apply_lock.acquire] acquired, env=%s, "
                    "entity_id=%s, bot_id=%s, holder=%s",
                    env,
                    entity_id,
                    bot_id,
                    holder_user_id,
                )
                return row.to_record()
            except IntegrityError:
                # Another apply holds it. Swallowed into a ``None`` return
                # rather than re-raised, because the Protocol's contract is
                # ``Optional[record]`` — "held" is a value, not an exception the
                # caller must catch. The rollback resets the failed transaction.
                db.rollback()
                logger.info(
                    "[manifest_apply_lock.acquire] already held, env=%s, "
                    "entity_id=%s, bot_id=%s",
                    env,
                    entity_id,
                    bot_id,
                )
                return None

    def release(
        self, *, env: str, entity_id: str, bot_id: str, lock_token: str
    ) -> bool:
        """释放锁（比对令牌后硬删除）。

        The token comparison is what stops this deleting a *later* holder's
        lock: if this apply's lock was reaped as stale and another apply took a
        fresh one, the tokens differ and the delete is a no-op.
        """
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Lock)
                .filter(
                    *self._match(env=env, entity_id=entity_id, bot_id=bot_id),
                    self._Lock.lock_token == lock_token,
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                "[manifest_apply_lock.release] env=%s, entity_id=%s, bot_id=%s, "
                "released=%s",
                env,
                entity_id,
                bot_id,
                deleted > 0,
            )
            return deleted > 0

    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestApplyLockRecord]:
        """查询锁记录。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Lock)
                .filter(*self._match(env=env, entity_id=entity_id, bot_id=bot_id))
                .first()
            )
            return row.to_record() if row is not None else None

    def get_if_stale(
        self, *, env: str, entity_id: str, bot_id: str, ttl_seconds: int
    ) -> Optional[BotConfigManifestApplyLockRecord]:
        """仅当锁存在且已超过 ttl_seconds 时返回记录，否则 None。

        判定完全基于数据库时钟：行的 ``gmt_create`` 与 "now" 都取自 DB，
        因此不受应用与数据库之间的时钟漂移影响。
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self._Lock)
                .filter(*self._match(env=env, entity_id=entity_id, bot_id=bot_id))
                .first()
            )
            if row is None or row.gmt_create is None:
                return None

            # type_coerce ensures both SQLite and MySQL/OceanBase yield a Python
            # datetime for the DB-side "now".
            db_now = db.execute(select(type_coerce(func.now(), DateTime))).scalar()
            if db_now is None:
                return None

            elapsed = (_as_naive(db_now) - _as_naive(row.gmt_create)).total_seconds()
            if elapsed >= ttl_seconds:
                logger.info(
                    "[manifest_apply_lock.get_if_stale] stale, env=%s, "
                    "entity_id=%s, bot_id=%s, elapsed=%.1fs, ttl=%ss",
                    env,
                    entity_id,
                    bot_id,
                    elapsed,
                    ttl_seconds,
                )
                return row.to_record()
            return None
