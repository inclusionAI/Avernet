"""``ac_bot_config_managed_files`` repository (prod OceanBase + local SQLite).

One ORM body behind ``BotConfigManagedFilesRepositoryProtocol``; the injected
``DatabasePlugin`` is the only per-environment difference. Same shape as the
manifest repository: an upsert that replaces rather than duplicates (the UNIQUE
key on tenant, env, entity_id, bot_id, category, path_hash enforces it), a hard
delete, and reads that filter on the index's leading columns.
"""
from __future__ import annotations

from typing import Optional

from injector import inject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.core.bot_config_manifest.repository.managed_files_models import (
    BotConfigManagedFileModel,
    ManagedFileRecord,
    managed_path_hash,
)
from agentclaw.community.core.repository.protocols.bot.managed_files import (
    BotConfigManagedFilesRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class BotConfigManagedFilesRepository(BotConfigManagedFilesRepositoryProtocol):
    """平台托管文件索引 Repository 实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Model = BotConfigManagedFileModel

    def _bot(self, *, env: str, entity_id: str, bot_id: str) -> list:
        return [
            self._Model.env == env,
            self._Model.entity_id == entity_id,
            self._Model.bot_id == bot_id,
        ]

    def _one(
        self, *, env: str, entity_id: str, bot_id: str, category: str, rel_path: str
    ) -> list:
        return self._bot(env=env, entity_id=entity_id, bot_id=bot_id) + [
            self._Model.category == category,
            self._Model.path_hash == managed_path_hash(rel_path),
        ]

    def get(
        self, *, env: str, entity_id: str, bot_id: str, category: str, rel_path: str
    ) -> Optional[ManagedFileRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._Model)
                .filter(
                    *self._one(
                        env=env,
                        entity_id=entity_id,
                        bot_id=bot_id,
                        category=category,
                        rel_path=rel_path,
                    )
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
        category: str,
        name: str,
        rel_path: str,
        store_key: str,
        digest: str,
        size_bytes: int,
        apply_id: Optional[str],
    ) -> ManagedFileRecord:
        fields = dict(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            category=category,
            name=name,
            rel_path=rel_path,
            store_key=store_key,
            digest=digest,
            size_bytes=size_bytes,
            apply_id=apply_id,
        )
        try:
            return self._upsert_once(**fields)
        except IntegrityError:
            # A racing first write committed first; the retry takes the update
            # branch, exactly as the manifest repository does.
            logger.info(
                "[managed_files.upsert] insert lost a race, retrying as an update: "
                "bot_id=%s category=%s rel_path=%s",
                bot_id,
                category,
                rel_path,
            )
            return self._upsert_once(**fields)

    def _upsert_once(self, **fields) -> ManagedFileRecord:
        with self._db.orm_session() as db:
            row = (
                db.query(self._Model)
                .filter(
                    *self._one(
                        env=fields["env"],
                        entity_id=fields["entity_id"],
                        bot_id=fields["bot_id"],
                        category=fields["category"],
                        rel_path=fields["rel_path"],
                    )
                )
                .one_or_none()
            )
            if row is None:
                row = self._Model(
                    path_hash=managed_path_hash(fields["rel_path"]), **fields
                )
                db.add(row)
            else:
                row.name = fields["name"]
                row.store_key = fields["store_key"]
                row.digest = fields["digest"]
                row.size_bytes = fields["size_bytes"]
                row.apply_id = fields["apply_id"]
                # Stamped explicitly: SQLAlchemy emits no UPDATE when nothing
                # changed, and an identical re-delivery still happened.
                row.gmt_modified = func.now()
            db.flush()
            db.refresh(row)
            return row.to_record()

    def delete(
        self, *, env: str, entity_id: str, bot_id: str, category: str, rel_path: str
    ) -> bool:
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Model)
                .filter(
                    *self._one(
                        env=env,
                        entity_id=entity_id,
                        bot_id=bot_id,
                        category=category,
                        rel_path=rel_path,
                    )
                )
                .delete(synchronize_session=False)
            )
            return deleted > 0

    def list_by_category(
        self, *, env: str, entity_id: str, bot_id: str, category: str
    ) -> list[ManagedFileRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Model)
                .filter(
                    *self._bot(env=env, entity_id=entity_id, bot_id=bot_id),
                    self._Model.category == category,
                )
                .order_by(self._Model.rel_path)
                .all()
            )
            return [row.to_record() for row in rows]

    def list_all(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> list[ManagedFileRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Model)
                .filter(*self._bot(env=env, entity_id=entity_id, bot_id=bot_id))
                .order_by(self._Model.category, self._Model.rel_path)
                .all()
            )
            return [row.to_record() for row in rows]
