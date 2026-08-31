"""Bot config manifest Repository (prod OceanBase + local SQLite).

One ORM implementation behind ``BotConfigManifestRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior mirrors ``BotStartupScriptRepository`` on purpose —— same key, same
collision domain, same lifecycle:

- ``upsert`` whole-replaces an existing row's document rather than inserting a
  second one; PUT is all-or-nothing at the document level (validation happens
  above this layer);
- ``delete`` hard-deletes: "no row" and "no declaration" are the same state.
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestModel,
    BotConfigManifestRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
)
import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


def _manifest_key(*, env: str, entity_id: str, bot_id: str) -> str:
    """Bounded surrogate for the uniqueness key.

    The logical key is (env, entity_id, bot_id); ``entity_id`` alone is 1024
    utf8mb4 characters, which is 4096 bytes — past InnoDB's 3072-byte index-key
    cap before the other two are counted. Hashing gives a fixed 64-character
    key while the real columns keep their true widths.

    **Length-prefixed, not delimiter-joined** — identical reasoning to
    ``_script_key`` (see ac_bot_startup_script): a separator only disambiguates
    while the separator cannot occur inside a component, and nothing enforces
    that for caller-supplied ``entity_id``/``bot_id``. Prefixing each component
    with its length is injective for *every* input, so the key stops depending
    on an invariant nobody upholds. Two bots that concatenate alike must get
    two rows: each would otherwise execute the other's declared script.

    Every read filters on this rather than on the three columns it is built
    from — the surrogate is the table's only index.
    """
    joined = "".join(f"{len(part)}:{part}" for part in (env, entity_id, bot_id))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class BotConfigManifestRepository(
    BotConfigManifestRepositoryProtocol,
):
    """配置清单文档仓储实现。"""

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
        """读取文档行；不存在返回 None（不是错误——服务层转空文档）。"""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Manifest)
                .filter(
                    self._Manifest.manifest_key
                    == _manifest_key(env=env, entity_id=entity_id, bot_id=bot_id)
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
        schema_version: int,
        document: str,
        size_bytes: int,
        modifier: str,
    ) -> BotConfigManifestRecord:
        """写入文档：存在则整体替换，不存在则插入。

        Retried once on a duplicate-key conflict — the read-then-insert is not
        atomic, and a racing first-write pair would otherwise surface as a 500
        on a perfectly valid replace. Same pattern, same reason as
        ``BotStartupScriptRepository.upsert``.
        """
        try:
            return self._upsert_once(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                schema_version=schema_version,
                document=document,
                size_bytes=size_bytes,
                modifier=modifier,
            )
        except IntegrityError:
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
                schema_version=schema_version,
                document=document,
                size_bytes=size_bytes,
                modifier=modifier,
            )

    def _upsert_once(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        schema_version: int,
        document: str,
        size_bytes: int,
        modifier: str,
    ) -> BotConfigManifestRecord:
        """One read-then-write attempt. Raises ``IntegrityError`` if it races."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Manifest)
                .filter(
                    self._Manifest.manifest_key
                    == _manifest_key(env=env, entity_id=entity_id, bot_id=bot_id)
                )
                .one_or_none()
            )
            if row is None:
                row = self._Manifest(
                    env=env,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    schema_version=schema_version,
                    document=document,
                    size_bytes=size_bytes,
                    modifier=modifier,
                    manifest_key=_manifest_key(
                        env=env, entity_id=entity_id, bot_id=bot_id
                    ),
                )
                db.add(row)
            else:
                row.schema_version = schema_version
                row.document = document
                row.size_bytes = size_bytes
                row.modifier = modifier
                # Stamped explicitly (never left to onupdate) so an identical
                # re-PUT still records who wrote and when — the audit contract
                # says every write is a write. ``func.now()``, not app time:
                # one clock source for both gmt_* columns.
                row.gmt_modified = func.now()
            db.flush()
            db.refresh(row)
            logger.info(
                "[config_manifest.upsert] stored, env=%s, entity_id=%s, bot_id=%s, "
                "schema_version=%s, size_bytes=%s, modifier=%s",
                env,
                entity_id,
                bot_id,
                schema_version,
                size_bytes,
                modifier,
            )
            return row.to_record()

    def delete(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """移除声明行（硬删除）。不存在时是 no-op，返回 False。"""
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Manifest)
                .filter(
                    self._Manifest.manifest_key
                    == _manifest_key(env=env, entity_id=entity_id, bot_id=bot_id)
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
