"""Versioned SQL persistence for the single TaskIntakeResult state object."""

from __future__ import annotations

import json
from typing import Protocol

from injector import inject

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, IntakeTargetType, TaskIntakeResult, TaskIntakeStatus,
)
from agentclaw.community.core.task.domain.models import TaskSourceType
from agentclaw.community.core.task.repository.models import TaskIntakeSessionModel
from agentclaw.community.core.task.repository.serializers import task_spec_from_dict
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskIntakeRepositoryProtocol(Protocol):
    def create(self, result: TaskIntakeResult) -> TaskIntakeResult: ...
    def get(self, session_id: str) -> TaskIntakeResult | None: ...
    def save(self, previous: TaskIntakeResult, updated: TaskIntakeResult) -> TaskIntakeResult: ...


def _encode(result: TaskIntakeResult) -> str:
    return json.dumps({
        "session_id": result.session_id,
        "owner_user_id": result.owner_user_id,
        "source_type": result.source_type.value,
        "status": result.status.value,
        "target": (
            {"target_type": result.target.target_type.value, "target_id": result.target.target_id}
            if result.target else None
        ),
        "task_spec": result.task_spec.to_dict() if result.task_spec else None,
        "missing_fields": result.missing_fields,
        "questions": result.questions,
        "create_time_ms": result.create_time_ms,
        "update_time_ms": result.update_time_ms,
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode(raw: str) -> TaskIntakeResult:
    data = json.loads(raw)
    target = data.get("target")
    spec = data.get("task_spec")
    return TaskIntakeResult(
        session_id=data["session_id"],
        owner_user_id=data["owner_user_id"],
        source_type=TaskSourceType(data["source_type"]),
        status=TaskIntakeStatus(data["status"]),
        target=(IntakeTarget(IntakeTargetType(target["target_type"]), target["target_id"])
                if target else None),
        task_spec=task_spec_from_dict(spec) if spec else None,
        missing_fields=list(data.get("missing_fields") or []),
        questions=list(data.get("questions") or []),
        create_time_ms=int(data.get("create_time_ms") or 0),
        update_time_ms=int(data.get("update_time_ms") or 0),
    )


class TaskIntakeRepository(TaskIntakeRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, result: TaskIntakeResult) -> TaskIntakeResult:
        with self._db.transactional_orm_session() as db:
            db.add(TaskIntakeSessionModel(
                session_id=result.session_id,
                owner_user_id=result.owner_user_id,
                status=result.status.value,
                result_json=_encode(result),
                version=0,
            ))
            db.flush()
        return result

    def get(self, session_id: str) -> TaskIntakeResult | None:
        with self._db.orm_session() as db:
            row = db.get(TaskIntakeSessionModel, session_id)
            return _decode(row.result_json) if row else None

    def save(self, previous: TaskIntakeResult, updated: TaskIntakeResult) -> TaskIntakeResult:
        if previous.session_id != updated.session_id:
            raise TaskStateError("intake session identity cannot change")
        with self._db.transactional_orm_session() as db:
            count = (
                db.query(TaskIntakeSessionModel)
                .filter(
                    TaskIntakeSessionModel.session_id == previous.session_id,
                    TaskIntakeSessionModel.result_json == _encode(previous),
                )
                .update({
                    "result_json": _encode(updated),
                    "status": updated.status.value,
                    "version": TaskIntakeSessionModel.version + 1,
                }, synchronize_session=False)
            )
            if count != 1:
                raise TaskStateError("intake session changed concurrently")
        return updated
