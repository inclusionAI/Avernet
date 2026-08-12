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
from agentclaw.community.core.bot_startup_script.errors import (
    StartupScriptSupersededError,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotStartupScriptRepositoryProtocol,
)
import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


def _script_key(*, env: str, entity_id: str, bot_id: str) -> str:
    """Bounded surrogate for the uniqueness key.

    The logical key is (env, entity_id, bot_id); ``entity_id`` alone is 1024
    utf8mb4 characters, which is 4096 bytes — past InnoDB's 3072-byte index-key
    cap before the other two are counted. Hashing gives a fixed 64-character
    key while the real columns keep their true widths.

    NUL-separated so ``("a", "bc")`` and ``("ab", "c")`` cannot collide by
    concatenation; no id may contain a NUL.

    Every read filters on this rather than on the three columns it is built
    from. That is not a micro-optimisation: once the uniqueness key moved here,
    ``(env, entity_id, bot_id)`` had no index behind it at all, so filtering on
    the surrogate is what keeps a lookup on the one index the table has. It also
    means there is exactly one index to maintain instead of a unique key plus a
    lookup key that could drift apart.
    """
    joined = "\0".join((env, entity_id, bot_id))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


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
                    self._Script.script_key
                    == _script_key(env=env, entity_id=entity_id, bot_id=bot_id)
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
        bot_incarnation: int,
    ) -> BotStartupScriptRecord:
        """写入脚本：存在则整体替换正文，不存在则插入。

        ``bot_incarnation`` is re-stamped on an update as well as an insert: it
        records who this body belongs to, and the row is being handed to the
        writing bot whether or not one was already there. Leaving a previous
        incarnation's stamp in place would make the new owner's own script
        unreadable to it.

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
                bot_incarnation=bot_incarnation,
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
                bot_incarnation=bot_incarnation,
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
        bot_incarnation: int,
    ) -> BotStartupScriptRecord:
        """One read-then-write attempt. Raises ``IntegrityError`` if it races."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Script)
                .filter(
                    self._Script.script_key
                    == _script_key(env=env, entity_id=entity_id, bot_id=bot_id)
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
                    bot_incarnation=bot_incarnation,
                    script_key=_script_key(
                        env=env, entity_id=entity_id, bot_id=bot_id
                    ),
                )
                db.add(row)
            elif row.bot_incarnation > bot_incarnation:
                # A newer bot already owns this key, so this write is stale: its
                # bot was deleted and the identifier handed on while the request
                # was in flight. Overwriting would lose the current owner's
                # script *and* stamp the row back to the dead incarnation, which
                # would then let the stale request's own withdrawal delete it.
                #
                # ``ac_bots.id`` is an autoincrement primary key, so "greater"
                # really does mean "later" — this is an ordering, not a guess.
                raise StartupScriptSupersededError(
                    stored_incarnation=int(row.bot_incarnation),
                    writing_incarnation=int(bot_incarnation),
                )
            else:
                row.script = script
                row.size_bytes = size_bytes
                row.modifier = modifier
                row.bot_incarnation = bot_incarnation
                # Stamped explicitly, not left to the column's ``onupdate``.
                # SQLAlchemy emits no UPDATE at all when every assigned value
                # equals what is already there, so re-submitting an identical
                # script would leave ``gmt_modified`` showing the *earlier*
                # write — and the published contract says every write records
                # who changed it and when. A caller who re-applies the same
                # body has still written.
                #
                # ``func.now()``, not ``datetime.now()``: this column's default
                # and onupdate are both DB time, as is gmt_create and as is
                # every sibling repository that force-touches a timestamp. App
                # time here would give one audit column two clock sources, and
                # a server clock trailing the DB's would let gmt_modified land
                # before gmt_create. A SQL expression also never compares equal
                # to the stored scalar, so it forces the dirty mark just as well.
                row.gmt_modified = func.now()
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
                    self._Script.script_key
                    == _script_key(env=env, entity_id=entity_id, bot_id=bot_id)
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

    def delete_written_by(
        self, *, env: str, entity_id: str, bot_id: str, bot_incarnation: int
    ) -> bool:
        """Delete the row **only if** it is still the one that incarnation wrote.

        This is the withdrawal a write uses to take back its own row, and the
        condition is what stops it taking back somebody else's. Between deciding
        to withdraw and issuing the delete, the identifier can be recreated and
        the new bot can store a script of its own at the same key; an
        unconditional delete would silently destroy it, turning one bot's failed
        write into another bot's data loss.

        Returns ``False`` when there is nothing of ours to remove — either the
        row is gone already or it now belongs to a later incarnation. Both are
        the desired end state, not failures.
        """
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Script)
                .filter(
                    self._Script.script_key
                    == _script_key(env=env, entity_id=entity_id, bot_id=bot_id),
                    self._Script.bot_incarnation == bot_incarnation,
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                "[startup_script.delete_written_by] env=%s, entity_id=%s, "
                "bot_id=%s, bot_incarnation=%s, deleted=%s",
                env,
                entity_id,
                bot_id,
                bot_incarnation,
                deleted,
            )
            return deleted > 0
