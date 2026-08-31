"""配置清单 Repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotConfigManifestRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- ``upsert`` replaces an existing row's document rather than inserting a second
  one — a bot has at most one manifest, enforced by the UNIQUE constraint on
  ``(avernet_tenant, manifest_key)``.
- ``delete`` hard-deletes (no soft delete): "no row" and "no manifest" are the
  same state, so clearing must not leave a tombstone a later read could find.
- Nothing here parses, normalises or trims the document. It is stored as the
  caller wrote it and returned the same way.
"""
from __future__ import annotations

from typing import Optional

from injector import inject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestModel,
    BotConfigManifestRecord,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest._key import (
    manifest_key,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class BotConfigManifestRepository(BotConfigManifestRepositoryProtocol):
    """配置清单 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Manifest = BotConfigManifestModel

    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestRecord]:
        """读取配置清单行；不存在返回 None（不是错误）。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Manifest)
                .filter(
                    self._Manifest.manifest_key
                    == manifest_key(env=env, entity_id=entity_id, bot_id=bot_id)
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
        document: str,
        size_bytes: int,
        schema_version: int,
        modifier: str,
    ) -> BotConfigManifestRecord:
        """写入配置清单：存在则整体替换文档，不存在则插入。

        Retried once on a duplicate-key conflict. The read-then-insert below is
        not atomic: two first writes for the same key can both see ``None``,
        both insert, and the UNIQUE constraint then fails one of them — a 500 on
        a request that is perfectly valid and should simply have replaced.
        Catching the conflict and going round again turns that into the update
        the loser was always entitled to make.
        """
        try:
            return self._upsert_once(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                document=document,
                size_bytes=size_bytes,
                schema_version=schema_version,
                modifier=modifier,
            )
        except IntegrityError:
            # The row now exists — the racing insert committed first. The retry
            # takes the update branch. A second failure is not a race and is
            # left to propagate.
            logger.info(
                "[config_manifest.upsert] insert lost a race, retrying as an "
                "update: env=%s, entity_id=%s, bot_id=%s",
                env,
                entity_id,
                bot_id,
            )
            return self._upsert_once(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                document=document,
                size_bytes=size_bytes,
                schema_version=schema_version,
                modifier=modifier,
            )

    def _upsert_once(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        document: str,
        size_bytes: int,
        schema_version: int,
        modifier: str,
    ) -> BotConfigManifestRecord:
        """One read-then-write attempt. Raises ``IntegrityError`` if it races."""
        key = manifest_key(env=env, entity_id=entity_id, bot_id=bot_id)
        with self._db.orm_session() as db:
            row = (
                db.query(self._Manifest)
                .filter(self._Manifest.manifest_key == key)
                .one_or_none()
            )
            if row is None:
                row = self._Manifest(
                    env=env,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    document=document,
                    size_bytes=size_bytes,
                    schema_version=schema_version,
                    modifier=modifier,
                    manifest_key=key,
                )
                db.add(row)
            else:
                row.document = document
                row.size_bytes = size_bytes
                row.schema_version = schema_version
                row.modifier = modifier
                # Stamped explicitly, not left to the column's ``onupdate``.
                # SQLAlchemy emits no UPDATE at all when every assigned value
                # equals what is already there, so re-submitting an identical
                # document would leave ``gmt_modified`` showing the *earlier*
                # write — and the published contract says every write records
                # who changed it and when.
                #
                # ``func.now()``, not ``datetime.now()``: this column's default
                # and onupdate are both DB time, as is gmt_create. App time here
                # would give one audit column two clock sources, and a server
                # clock trailing the DB's would let gmt_modified land before
                # gmt_create.
                row.gmt_modified = func.now()
            db.flush()
            db.refresh(row)
            logger.info(
                "[config_manifest.upsert] stored, env=%s, entity_id=%s, "
                "bot_id=%s, size_bytes=%s, schema_version=%s, modifier=%s",
                env,
                entity_id,
                bot_id,
                size_bytes,
                schema_version,
                modifier,
            )
            return row.to_record()

    def delete(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """清除配置清单（硬删除）。不存在时是 no-op，返回 False。"""
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Manifest)
                .filter(
                    self._Manifest.manifest_key
                    == manifest_key(env=env, entity_id=entity_id, bot_id=bot_id)
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                "[config_manifest.delete] env=%s, entity_id=%s, bot_id=%s, deleted=%s",
                env,
                entity_id,
                bot_id,
                deleted,
            )
            return deleted > 0
