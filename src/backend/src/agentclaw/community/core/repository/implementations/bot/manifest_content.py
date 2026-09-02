"""溯源日志 Repository (prod OceanBase + local SQLite).

One ORM implementation behind ``ManifestContentRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- ``add`` is insert-only — the log is append-only by design (see the DDL);
  there is no upsert and no delete on this table, and adding one is a policy
  change that belongs in the DDL's retention comment first.
- ``records_for`` answers the audit read: one bot's receipts, newest first.
  The tenant half of the filter is not written out — the guard registered on
  the model confines every query to the request's tenant, exactly like W1's
  manifest repository.
- Nothing here derives or normalises: the record is stored as the service
  decided it, so the audit log is the service's decision, verbatim.
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.bot_config_manifest.content.models import (
    ManifestContentModel,
    StoredContentRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    DEFAULT_RECORD_LIMIT,
    ManifestContentRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class ManifestContentRepository(ManifestContentRepositoryProtocol):
    """manifest 内容溯源仓库实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Manifest = ManifestContentModel

    def add(self, record: StoredContentRecord) -> StoredContentRecord:
        """Append one provenance row; returns the stored record with its id."""
        with self._db.orm_session() as session:
            row = self._Manifest(
                env=record.env,
                entity_id=record.entity_id,
                bot_id=record.bot_id,
                digest=record.digest,
                source_url=record.source_url,
                fetched_url=record.fetched_url,
                credential_name=record.credential_name,
                content_type=record.content_type,
                size_bytes=record.size_bytes,
                fetched_at=record.fetched_at,
                apply_id=record.apply_id,
                category=record.category,
                entry_identity=record.entry_identity,
                modifier=record.modifier,
            )
            session.add(row)
            session.flush()
            return row.to_record()

    def records_for(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        limit: int = DEFAULT_RECORD_LIMIT,
    ) -> list[StoredContentRecord]:
        """One bot's receipts, newest first — the audit read.

        A non-positive ``limit`` raises: an audit read that answers "no
        receipts" is a claim about the bot, and a caller whose page math went
        negative must be told it asked something broken, not told the bot
        never fetched. (And a *negative LIMIT* would still mean "unbounded"
        on SQLite — see the protocol docstring — so refusing it also keeps
        the bound honest on every dialect.)
        """
        if limit <= 0:
            raise ValueError(
                f"records_for limit must be a positive page, got {limit}"
            )
        with self._db.orm_session() as session:
            rows = (
                session.query(self._Manifest)
                .filter(
                    self._Manifest.env == env,
                    self._Manifest.entity_id == entity_id,
                    self._Manifest.bot_id == bot_id,
                )
                # gmt_create leading on idx_tenant_env_entity_bot keeps the
                # sort on the index; id breaks the same-second tie.
                .order_by(
                    self._Manifest.gmt_create.desc(), self._Manifest.id.desc()
                )
                .limit(limit)
                .all()
            )
            return [row.to_record() for row in rows]

    def latest_for(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        source_url: str,
    ) -> Optional[StoredContentRecord]:
        """The newest receipt for one bot and one source URL, or ``None``.

        Same ordering as ``records_for`` (gmt_create leading on the index, id
        breaking the same-second tie) with the source as an exact-equality
        filter. Unbounded on purpose: the per-source lookup answers for one
        URL, so the question's own shape is the bound. The tenant half of the
        filter is the model guard's, as everywhere else here.
        """
        with self._db.orm_session() as session:
            row = (
                session.query(self._Manifest)
                .filter(
                    self._Manifest.env == env,
                    self._Manifest.entity_id == entity_id,
                    self._Manifest.bot_id == bot_id,
                    self._Manifest.source_url == source_url,
                )
                .order_by(
                    self._Manifest.gmt_create.desc(), self._Manifest.id.desc()
                )
                .first()
            )
            return row.to_record() if row is not None else None
