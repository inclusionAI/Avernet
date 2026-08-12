"""启动脚本 Repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotStartupScriptRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- ``upsert`` replaces the body of an existing row rather than inserting a
  second one — a bot has at most one script, enforced by the UNIQUE constraint
  on ``(env, entity_id, bot_id)``.
- ``delete`` hard-deletes (no soft delete): "no row" and "no script" are the
  same state, so clearing must not leave a tombstone a later read could find.
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.bot_startup_script.repository.models import (
    BotStartupScriptModel,
    BotStartupScriptRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotStartupScriptRepositoryProtocol,
)
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


class BotStartupScriptRepository(
    BotStartupScriptRepositoryProtocol,
):
    """启动脚本 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Script = BotStartupScriptModel

    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotStartupScriptRecord]:
        """读取脚本行；不存在返回 None（不是错误）。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Script)
                .filter(
                    self._Script.env == env,
                    self._Script.entity_id == entity_id,
                    self._Script.bot_id == bot_id,
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def upsert(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        script: str,
        size_bytes: int,
        modifier: str,
    ) -> BotStartupScriptRecord:
        """写入脚本：存在则整体替换正文，不存在则插入。

        Retried once on a duplicate-key conflict. The read-then-insert below is
        not atomic: two first writes for the same key can both see ``None``,
        both insert, and the UNIQUE constraint then fails one of them — a 500
        on a request that is perfectly valid and should simply have replaced.
        Catching the conflict and going round again turns that into the update
        the loser was always entitled to make.
        """
        try:
            return self._upsert_once(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                script=script,
                size_bytes=size_bytes,
                modifier=modifier,
            )
        except IntegrityError:
            # The row now exists — the racing insert committed first. The retry
            # takes the update branch. A second failure is not a race and is
            # left to propagate.
            logger.info(
                "[startup_script.upsert] insert lost a race, retrying as an "
                "update: env=%s, entity_id=%s, bot_id=%s",
                env,
                entity_id,
                bot_id,
            )
            return self._upsert_once(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                script=script,
                size_bytes=size_bytes,
                modifier=modifier,
            )

    def _upsert_once(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        script: str,
        size_bytes: int,
        modifier: str,
    ) -> BotStartupScriptRecord:
        """One read-then-write attempt. Raises ``IntegrityError`` if it races."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Script)
                .filter(
                    self._Script.env == env,
                    self._Script.entity_id == entity_id,
                    self._Script.bot_id == bot_id,
                )
                .one_or_none()
            )
            if row is None:
                row = self._Script(
                    env=env,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    script=script,
                    size_bytes=size_bytes,
                    modifier=modifier,
                )
                db.add(row)
            else:
                row.script = script
                row.size_bytes = size_bytes
                row.modifier = modifier
                # Stamped explicitly, not left to the column's ``onupdate``.
                # SQLAlchemy emits no UPDATE at all when every assigned value
                # equals what is already there, so re-submitting an identical
                # script would leave ``gmt_modified`` showing the *earlier*
                # write — and the published contract says every write records
                # who changed it and when. A caller who re-applies the same
                # body has still written.
                row.gmt_modified = datetime.now()
            db.flush()
            db.refresh(row)
            logger.info(
                "[startup_script.upsert] stored, env=%s, entity_id=%s, bot_id=%s, "
                "size_bytes=%s, modifier=%s",
                env,
                entity_id,
                bot_id,
                size_bytes,
                modifier,
            )
            return row.to_record()

    def delete(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """清除脚本（硬删除）。不存在时是 no-op，返回 False。"""
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Script)
                .filter(
                    self._Script.env == env,
                    self._Script.entity_id == entity_id,
                    self._Script.bot_id == bot_id,
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                "[startup_script.delete] env=%s, entity_id=%s, bot_id=%s, deleted=%s",
                env,
                entity_id,
                bot_id,
                deleted,
            )
            return deleted > 0
