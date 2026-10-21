"""``AcliOperationStore`` — the ``ac_acli_operation`` rows, one ORM body.

The injected :class:`DatabasePlugin` yields a SQLAlchemy ``Session`` on both
OceanBase (prod) and SQLite (local), so this runs unchanged on either. Every
read is scoped to the current ``env``. Writes raise on failure; nothing here
reports success for a row it did not write.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.repository.models import (
    AcliOperationModel,
    AcliOperationRecord,
)
from agentclaw.community.core.acli_clis.operations import OperationKind
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


class AcliOperationNotRecordedError(LookupError):
    """An update named an operation that has no row."""


@runtime_checkable
class AcliOperationStore(Protocol):
    def create(self, record: AcliOperationRecord) -> None: ...

    def get(self, operation_id: str) -> AcliOperationRecord | None: ...

    def latest(
        self, *, bot_pk: int, stage: PublishStage, kind: OperationKind
    ) -> AcliOperationRecord | None: ...

    def set_engine_operation_id(self, operation_id: str, engine_operation_id: str) -> None: ...


class SqlAcliOperationStore(AcliOperationStore):
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, record: AcliOperationRecord) -> None:
        with self._db.orm_session() as db:
            db.add(AcliOperationModel(
                env=get_current_env(),
                operation_id=record.operation_id,
                task_id=record.task_id,
                kind=record.kind.value,
                action=record.action.value,
                cli_code=record.cli_code,
                bot_pk=record.bot_pk,
                stage=record.stage.value,
                target_operation_id=record.target_operation_id,
                engine_operation_id=record.engine_operation_id,
            ))

    def get(self, operation_id: str) -> AcliOperationRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(AcliOperationModel)
                .filter(
                    AcliOperationModel.env == get_current_env(),
                    AcliOperationModel.operation_id == operation_id,
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def latest(
        self, *, bot_pk: int, stage: PublishStage, kind: OperationKind
    ) -> AcliOperationRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(AcliOperationModel)
                .filter(
                    AcliOperationModel.env == get_current_env(),
                    AcliOperationModel.bot_pk == bot_pk,
                    AcliOperationModel.stage == stage.value,
                    AcliOperationModel.kind == kind.value,
                )
                .order_by(AcliOperationModel.id.desc())
                .first()
            )
            return row.to_record() if row is not None else None

    def set_engine_operation_id(self, operation_id: str, engine_operation_id: str) -> None:
        with self._db.orm_session() as db:
            updated = (
                db.query(AcliOperationModel)
                .filter(
                    AcliOperationModel.env == get_current_env(),
                    AcliOperationModel.operation_id == operation_id,
                )
                .update({AcliOperationModel.engine_operation_id: engine_operation_id})
            )
        if updated != 1:
            raise AcliOperationNotRecordedError(operation_id)


__all__ = [
    "AcliOperationNotRecordedError",
    "AcliOperationStore",
    "SqlAcliOperationStore",
]
