"""Unified File repository (prod OceanBase + local SQLite).

One ORM implementation behind ``FileRepositoryProtocol`` for the ``ac_file``
table (teclaw workspace-file metadata). The only per-environment difference is
the injected :class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs on OceanBase (prod) and
SQLite (local) — mirroring ``ResourceRepository``.
"""
from __future__ import annotations

from typing import List, Optional

from injector import inject

from agentclaw.community.core.files.models import FileRecord
from agentclaw.community.core.files.repository.protocol import FileRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()

_FIELDS = (
    "bot_id", "entity_id", "entity_type", "engine_type", "env", "path", "name",
    "parent_path", "size", "mime_type", "source", "created_by", "user_id",
)


class FileRepository(FileRepositoryProtocol):
    """Unified ORM-backed ``FileRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.plugin_api.models import FileModel

        self._db = db
        self.Model = FileModel

    @staticmethod
    def _to_record(row) -> FileRecord:
        return FileRecord(
            id=row.id,
            bot_id=row.bot_id,
            entity_id=row.entity_id,
            entity_type=row.entity_type,
            engine_type=row.engine_type,
            env=row.env,
            path=row.path,
            name=row.name,
            parent_path=row.parent_path,
            size=row.size or 0,
            mime_type=row.mime_type,
            source=row.source,
            created_by=row.created_by,
            user_id=row.user_id,
            gmt_create=row.gmt_create,
            gmt_modified=row.gmt_modified,
        )

    def create(self, data: dict) -> FileRecord:
        row = self.Model(**{k: data.get(k) for k in _FIELDS if k in data})
        with self._db.orm_session() as db:
            db.add(row)
            db.flush()  # populate autoincrement id before the session closes
            return self._to_record(row)

    def get_by_path(
        self, *, bot_id: str, env: str, path: str
    ) -> Optional[FileRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.env == env,
                    self.Model.path == path,
                )
                .order_by(self.Model.id.desc())
                .first()
            )
            return self._to_record(row) if row else None

    def list_by_path_prefix(
        self, *, bot_id: str, env: str, prefix: str
    ) -> List[FileRecord]:
        # Escape LIKE wildcards so a dir name with '_' or '%' (e.g. "docs_v2/")
        # only matches its own subtree, not "docsXv2/...". Use '!' as the escape
        # char (never a path char issue once doubled) rather than backslash, which
        # MySQL/OceanBase ignores in LIKE under NO_BACKSLASH_ESCAPES mode.
        escaped = (
            prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        )
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.env == env,
                    self.Model.path.like(f"{escaped}%", escape="!"),
                )
                .order_by(self.Model.path.asc())
                .all()
            )
            return [self._to_record(r) for r in rows]

    def list_by_bot(
        self, *, bot_id: str, env: str, engine_type: Optional[str] = None
    ) -> List[FileRecord]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id, self.Model.env == env
            )
            # Scope to the bot's current engine so a bot that switched away from
            # teclaw doesn't surface its stale teclaw-era rows (whose OSS path is
            # under the OLD engine dir) into the new engine's compose.
            if engine_type is not None:
                query = query.filter(self.Model.engine_type == engine_type)
            rows = query.order_by(self.Model.path.asc()).all()
            return [self._to_record(r) for r in rows]

    def delete(self, file_id: int) -> bool:
        with self._db.orm_session() as db:
            deleted = (
                db.query(self.Model)
                .filter(self.Model.id == file_id)
                .delete(synchronize_session=False)
            )
            return bool(deleted)
