"""CLI 工具 Repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotCliToolRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- ``upsert`` replaces an existing row of the same name rather than inserting a
  second one — a command name is unique per bot, enforced by the UNIQUE
  constraint on ``(avernet_tenant, tool_key)``.
- ``delete`` hard-deletes: a removed tool must leave no tombstone a later read
  could mistake for an installed one.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence

from injector import inject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    BotCliToolModel,
    BotCliToolRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCliToolRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


def _tool_key(*, env: str, entity_id: str, bot_id: str, name: str) -> str:
    """Bounded surrogate for the uniqueness key.

    The logical key is (env, entity_id, bot_id, name); ``entity_id`` alone is
    1024 utf8mb4 characters, which is 4096 bytes — past InnoDB's 3072-byte
    index-key cap before the other three are counted. Hashing gives a fixed
    64-character key while the real columns keep their true widths.

    **Length-prefixed, not delimiter-joined**, for the reason
    ``_script_key`` records: a separator only disambiguates while it cannot
    occur inside a component, and nothing enforces that for caller-supplied
    ``bot_id`` / ``entity_id``. Length prefixes make the encoding injective
    whatever the components contain.
    """
    joined = "".join(
        f"{len(part)}:{part}" for part in (env, entity_id, bot_id, name)
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class BotCliToolRepository(BotCliToolRepositoryProtocol):
    """CLI 工具 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Tool = BotCliToolModel

    # ── reads ────────────────────────────────────────────────────────────

    def get(
        self, *, env: str, entity_id: str, bot_id: str, name: str
    ) -> Optional[BotCliToolRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._Tool)
                .filter(
                    self._Tool.tool_key
                    == _tool_key(
                        env=env, entity_id=entity_id, bot_id=bot_id, name=name
                    )
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def list(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Sequence[BotCliToolRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Tool)
                .filter(
                    self._Tool.env == env,
                    self._Tool.entity_id == entity_id,
                    self._Tool.bot_id == bot_id,
                )
                .all()
            )
            # Sorted here, not with ``ORDER BY name``. SQL ordering is
            # collation-dependent — SQLite is BINARY, OceanBase's default is
            # case-insensitive — so ``Zip`` and ``aws`` come back in opposite
            # orders in tests and in prod. The composed artifact's ref list is
            # built from this sequence and its byte-identity is asserted, so a
            # collation difference would pass CI and diverge in production.
            # Python compares by code point, which is the same everywhere.
            return sorted(
                (row.to_record() for row in rows), key=lambda r: r.name
            )

    # ── writes ───────────────────────────────────────────────────────────

    def upsert(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        name: str,
        source: str,
        digest: str,
        subpath: Optional[str],
        md5: str,
        size_bytes: int,
        version: Optional[str],
        oss_key: str,
        installed_by: str,
        modifier: str,
    ) -> BotCliToolRecord:
        """写入工具行：存在则整体替换，不存在则插入。

        Retried once on a duplicate-key conflict, for the reason
        ``BotStartupScriptRepository.upsert`` records: the read-then-insert is
        not atomic, so two first writes for the same key can both see ``None``
        and the UNIQUE constraint then fails one of them. Catching the conflict
        and going round again turns that into the update the loser was always
        entitled to make.
        """
        fields = dict(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            name=name,
            source=source,
            digest=digest,
            subpath=subpath,
            md5=md5,
            size_bytes=size_bytes,
            version=version,
            oss_key=oss_key,
            installed_by=installed_by,
            modifier=modifier,
        )
        try:
            return self._upsert_once(**fields)
        except IntegrityError as exc:
            # Most often a concurrent first write for the same key: the
            # read-then-insert below is not atomic, so two callers can both see
            # ``None`` and the UNIQUE constraint fails one of them. The retry
            # takes the update branch that loser was always entitled to.
            #
            # It is *not* only that — a NOT NULL violation raises the same
            # class — so the log names the ambiguity rather than asserting a
            # race, and a second failure propagates untouched.
            logger.info(
                "[cli_tool.upsert] integrity conflict (likely a concurrent "
                "insert); retrying once as an update: env=%s, bot_id=%s, "
                "name=%s, error=%s",
                env,
                bot_id,
                name,
                exc.__class__.__name__,
            )
            return self._upsert_once(**fields)

    def _upsert_once(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        name: str,
        source: str,
        digest: str,
        subpath: Optional[str],
        md5: str,
        size_bytes: int,
        version: Optional[str],
        oss_key: str,
        installed_by: str,
        modifier: str,
    ) -> BotCliToolRecord:
        """One read-then-write attempt. Raises ``IntegrityError`` if it races."""
        key = _tool_key(env=env, entity_id=entity_id, bot_id=bot_id, name=name)
        with self._db.orm_session() as db:
            row = (
                db.query(self._Tool)
                .filter(self._Tool.tool_key == key)
                .one_or_none()
            )
            if row is None:
                row = self._Tool(
                    env=env,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    name=name,
                    tool_key=key,
                )
                db.add(row)
            row.source = source
            row.digest = digest
            row.subpath = subpath
            row.md5 = md5
            row.size_bytes = size_bytes
            row.version = version
            row.oss_key = oss_key
            row.installed_by = installed_by
            row.modifier = modifier
            # Force the timestamp even when nothing else changed. SQLAlchemy
            # emits no UPDATE at all when every assigned value equals what is
            # already there, so ``onupdate`` never fires and a re-install or a
            # re-applied manifest would leave the audit columns showing the
            # *previous* write. A SQL expression never compares equal to the
            # stored scalar, so it forces the dirty mark; DB time also keeps
            # gmt_create and gmt_modified on one clock.
            row.gmt_modified = func.now()
            db.flush()
            db.refresh(row)
            record = row.to_record()
        logger.info(
            "[cli_tool.upsert] stored, env=%s, bot_id=%s, name=%s, "
            "size_bytes=%s, installed_by=%s, modifier=%s",
            env,
            bot_id,
            name,
            size_bytes,
            installed_by,
            modifier,
        )
        return record

    def delete(self, *, env: str, entity_id: str, bot_id: str, name: str) -> bool:
        with self._db.orm_session() as db:
            removed = (
                db.query(self._Tool)
                .filter(
                    self._Tool.tool_key
                    == _tool_key(
                        env=env, entity_id=entity_id, bot_id=bot_id, name=name
                    )
                )
                .delete(synchronize_session=False)
            )
        logger.info(
            "[cli_tool.delete] env=%s, bot_id=%s, name=%s, deleted=%s",
            env,
            bot_id,
            name,
            removed,
        )
        return bool(removed)

    def delete_all(self, *, env: str, entity_id: str, bot_id: str) -> Sequence[str]:
        """Delete every row for the bot; return the ``oss_key``s that were on
        them.

        Returning the keys rather than a count is what stops the objects being
        orphaned: ``oss_key`` lives only on these rows, so a caller that
        deleted first and asked later could never enumerate what to clean up.
        """
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Tool)
                .filter(
                    self._Tool.env == env,
                    self._Tool.entity_id == entity_id,
                    self._Tool.bot_id == bot_id,
                )
                .all()
            )
            keys = [row.oss_key for row in rows]
            for row in rows:
                db.delete(row)
        logger.info(
            "[cli_tool.delete_all] env=%s, bot_id=%s, deleted=%s",
            env,
            bot_id,
            len(keys),
        )
        return keys
