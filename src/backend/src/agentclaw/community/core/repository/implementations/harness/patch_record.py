"""Unified HarnessPatchRecord repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``HarnessPatchRecordRepository``
Protocol. The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

The off-Protocol ``update_preview`` method that both legacy twins
shipped is dropped — its only references were within the twins
themselves; no production caller existed at the time of unification.
"""
from __future__ import annotations

import json
from typing import Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.harness.models import (
    Layer,
    PatchOperation,
    PatchRecord,
    PatchStatus,
    PatchTarget,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.harness import HarnessPatchRecordRepository


logger = get_logger()


class HarnessPatchRecordRepository(
    HarnessPatchRecordRepository,
):
    """Unified ORM ``HarnessPatchRecordRepository`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.harness.sqlite_models import (
            HarnessPatchRecordModel,
        )

        self._db = db
        self.Model = HarnessPatchRecordModel

    @staticmethod
    def _row_to_domain(row) -> PatchRecord:
        target = PatchTarget()
        if row.target:
            try:
                target = PatchTarget(**json.loads(row.target))
            except Exception:
                pass

        operations: list[PatchOperation] = []
        if row.operations:
            try:
                operations = [
                    PatchOperation(**op) for op in json.loads(row.operations)
                ]
            except Exception:
                pass

        layer_val = Layer.L1
        if row.layer:
            try:
                layer_val = Layer(row.layer)
            except ValueError:
                pass

        status_val = PatchStatus.PLANNED
        if row.status:
            try:
                status_val = PatchStatus(row.status)
            except ValueError:
                pass

        return PatchRecord(
            id=row.id,
            bot_id=row.bot_id,
            entity_id=row.entity_id or "",
            patch_id=row.patch_id or 0,
            layer=layer_val,
            target=target,
            status=status_val,
            operations=operations,
            preview_diff=row.preview_diff,
            backup_content=row.backup_content,
            backup_path=row.backup_path,
            backup_checksum=row.backup_checksum,
            applied_by=row.applied_by or "",
            rolled_back_at=row.rolled_back_at,
            failed_reason=row.failed_reason,
            env=row.env,
            gmt_create=row.gmt_create,
            gmt_modified=row.gmt_modified,
            bot_publish_id=row.bot_publish_id,
        )

    def create(self, record: PatchRecord) -> int:
        with self._db.orm_session() as db:
            row = self.Model(
                bot_id=record.bot_id,
                entity_id=record.entity_id,
                patch_id=record.patch_id,
                layer=record.layer.value
                if hasattr(record.layer, "value")
                else str(record.layer),
                target=json.dumps(
                    {
                        "files": record.target.files,
                        "sections": record.target.sections,
                    }
                ),
                status=record.status.value
                if hasattr(record.status, "value")
                else str(record.status),
                operations=json.dumps(
                    [
                        {
                            "op": op.op,
                            "target": op.target,
                            "template": op.template,
                            "detail": op.detail,
                        }
                        for op in record.operations
                    ]
                ),
                preview_diff=record.preview_diff,
                backup_path=record.backup_path,
                backup_content=record.backup_content,
                backup_checksum=record.backup_checksum,
                applied_by=record.applied_by or "",
                rolled_back_at=record.rolled_back_at,
                failed_reason=record.failed_reason,
                env=record.env,
                bot_publish_id=record.bot_publish_id,
            )
            db.add(row)
            db.flush()  # populates row.id without ending the session
            record.id = row.id
            return row.id

    def get_by_id(self, record_id: int) -> Optional[PatchRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == record_id)
                .first()
            )
            return self._row_to_domain(row) if row else None

    def list_by_bot(
        self, bot_id: str, entity_id: str, status: str | None = None
    ) -> list[PatchRecord]:
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.entity_id == entity_id,
                self.Model.env == env,
            )
            if status:
                query = query.filter(self.Model.status == status)
            rows = query.order_by(self.Model.gmt_create.desc()).all()
            return [self._row_to_domain(r) for r in rows]

    def get_by_patch_id(self, patch_id: int) -> Optional[PatchRecord]:
        """Fetch by patch_id (latest record if multiple)."""
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.patch_id == patch_id)
                .order_by(self.Model.gmt_create.desc(), self.Model.id.desc())
                .first()
            )
            return self._row_to_domain(row) if row else None

    def update_status(
        self,
        record_id: int,
        status: PatchStatus,
        failed_reason: str | None = None,
    ) -> None:
        # Single blind UPDATE — prod parity (prod twin: one UPDATE,
        # no-op when id is missing). mysqlconnector sets
        # CLIENT_FOUND_ROWS so behavior is identical on both backends.
        status_val = (
            status.value if hasattr(status, "value") else str(status)
        )
        values: dict = {
            self.Model.status: status_val,
            self.Model.gmt_modified: func.now(),
        }
        if failed_reason is not None:
            values[self.Model.failed_reason] = failed_reason
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == record_id
            ).update(values, synchronize_session=False)
