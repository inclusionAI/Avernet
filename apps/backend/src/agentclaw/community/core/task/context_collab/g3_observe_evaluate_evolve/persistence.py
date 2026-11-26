"""Append-only G3 SQL records, independent of L4 Task/Collet/Event revisions."""

import json
from datetime import datetime, timezone

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.metrics import (
    MetricDefinition,
    metric_storage_key,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    TelemetryContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, allow_nan=False)


class TelemetryRecordModel(Base):
    __tablename__ = "ac_context_collab_telemetry"

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    record_kind = Column(String(32), nullable=False)
    record_id = Column(String(128), nullable=False)
    # Global metric definitions have no Collet/subject/context; these are null,
    # not fabricated references. Samples/observations/outcomes require all three.
    collet_id = Column(String(128), nullable=True)
    subject_type = Column(String(32), nullable=True)
    subject_id = Column(String(128), nullable=True)
    record_data = Column(
        Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False
    )
    telemetry_context = Column(
        Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True
    )
    recorded_by = Column(String(2048), nullable=False)
    recorded_at = Column(DateTime, nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    __table_args__ = (
        Index(
            "uk_context_collab_telemetry",
            "avernet_tenant",
            "env",
            "record_kind",
            "record_id",
            unique=True,
        ),
        Index(
            "idx_context_collab_telemetry_subject",
            "avernet_tenant",
            "env",
            "collet_id",
            "subject_type",
            "subject_id",
            "id",
        ),
    )


register_avernet_tenant_guard(TelemetryRecordModel)


class TelemetryRepository:
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def append(
        self,
        kind: str,
        record_id: str,
        data: dict,
        *,
        context: TelemetryContext | None,
        subject_ref: ObjectRef | None,
        actor: IdentityRef,
    ) -> None:
        if kind != "MetricDefinition" and (
            context is None or context.collet_ref is None or subject_ref is None
        ):
            raise ValueError(
                "scoped telemetry storage requires Collet, subject and context"
            )
        transactional = getattr(self._db, "transactional_orm_session", None)
        with (
            transactional()
            if transactional is not None
            else self._db.orm_session() as db
        ):
            db.add(
                TelemetryRecordModel(
                    record_kind=kind,
                    record_id=record_id,
                    collet_id=context.collet_ref.object_id
                    if context and context.collet_ref
                    else None,
                    subject_type=subject_ref.object_type if subject_ref else None,
                    subject_id=subject_ref.object_id if subject_ref else None,
                    record_data=_json(data),
                    telemetry_context=_json(context.to_dict()) if context else None,
                    recorded_by=_json(actor.to_dict()),
                    recorded_at=datetime.now(timezone.utc),
                    env=get_current_env(),
                )
            )
            db.flush()  # Storage errors propagate. Never return a false receipt.

    def get_metric(self, metric_ref: ObjectRef) -> MetricDefinition | None:
        with self._db.orm_session() as db:
            row = (
                db.query(TelemetryRecordModel)
                .filter(
                    TelemetryRecordModel.env == get_current_env(),
                    TelemetryRecordModel.record_kind == "MetricDefinition",
                    TelemetryRecordModel.record_id == metric_storage_key(metric_ref),
                )
                .one_or_none()
            )
            if row is None:
                return None
            data = json.loads(row.record_data)
            if data["reference"] != metric_ref.to_dict():
                raise ValueError("stored metric identity does not match its reference")
            return MetricDefinition.from_dict(data["definition"])

    def list_records(
        self, kind: str, collet_ref: ObjectRef, subject_ref: ObjectRef
    ) -> tuple[dict, ...]:
        with self._db.orm_session() as db:
            rows = (
                db.query(TelemetryRecordModel)
                .filter(
                    TelemetryRecordModel.env == get_current_env(),
                    TelemetryRecordModel.record_kind == kind,
                    TelemetryRecordModel.collet_id == collet_ref.object_id,
                    TelemetryRecordModel.subject_type == subject_ref.object_type,
                    TelemetryRecordModel.subject_id == subject_ref.object_id,
                )
                .order_by(TelemetryRecordModel.id.asc())
                .all()
            )
            return tuple(json.loads(row.record_data) for row in rows)
