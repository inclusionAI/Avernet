"""``TaskInfoRepositoryProtocol`` implementation for the ``task_info`` table."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional, Sequence

from injector import inject
from sqlalchemy import func, or_

from agentclaw.community.core.repository.json_expression import JsonText, SafeJsonDocument
from agentclaw.community.core.repository.protocols.task import TaskInfoRepositoryProtocol
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.models import TaskInfoModel
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.utils.app_utils import get_current_executor_app
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.plugin_api.database import DatabasePlugin


class TaskInfoRepository(TaskInfoRepositoryProtocol):
    """Unified ORM implementation for ``task_info`` (runs on SQLite + OceanBase)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = TaskInfoModel

    @staticmethod
    def _to_row(record: TaskInfoRecord) -> TaskInfoModel:
        return TaskInfoModel(
            task_id=record.task_id,
            intake_session_id=record.intake_session_id,
            source_type=record.source_type,
            owner_user_id=record.owner_user_id,
            owner_bot_id=record.owner_bot_id,
            execution_config=(
                json.dumps(record.execution_config)
                if record.execution_config is not None
                else None
            ),
            task_spec=json.dumps(record.task_spec),
            status=record.status.value,
            graph_run_id=record.graph_run_id,
            env=record.env,
            executor_app=record.executor_app,
            graph_loop_round=record.graph_loop_round,
            graph_output=json.dumps(record.graph_output) if record.graph_output is not None else None,
            graph_extend_props=(
                json.dumps(record.graph_extend_props)
                if record.graph_extend_props is not None
                else None
            ),
            graph_version=record.graph_version,
            lease_owner=record.lease_owner,
            lease_until=record.lease_until,
            heartbeat_at=record.heartbeat_at,
        )

    def insert(self, record: TaskInfoRecord) -> TaskInfoRecord:
        with self._db.orm_session() as db:
            row = self._to_row(record)
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    @staticmethod
    def _require_read_scope(row: TaskInfoModel) -> None:
        """Scope reads to the deployment environment only.

        ``executor_app`` records which application owns durable execution; it must
        not prevent the presentation/read side from showing another application's
        task in the same environment.
        """
        env = get_current_env()
        if row.env != env:
            raise TaskStateError(
                f"task environment mismatch: task={row.env}, current={env}"
            )

    @staticmethod
    def _require_write_scope(row: TaskInfoModel) -> None:
        TaskInfoRepository._require_read_scope(row)
        executor_app = get_current_executor_app()
        if row.executor_app != executor_app:
            raise TaskStateError(
                "task executor app mismatch: "
                f"task={row.executor_app}, current={executor_app}"
            )

    def get(self, task_id: str) -> Optional[TaskInfoRecord]:
        with self._db.orm_session() as db:
            row = db.query(self._model).filter(self._model.task_id == task_id).first()
            if row is None:
                return None
            self._require_read_scope(row)
            return row.to_record()

    def get_by_intake_session(self, session_id: str) -> Optional[TaskInfoRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.intake_session_id == session_id)
                .first()
            )
            if row is None:
                return None
            self._require_read_scope(row)
            return row.to_record()

    def update_status(self, task_id: str, status: Status) -> bool:
        with self._db.orm_session() as db:
            row = (
                db.query(self._model)
                .filter(self._model.task_id == task_id)
                .with_for_update()
                .first()
            )
            if row is None:
                return False
            self._require_write_scope(row)
            row.status = status.value
            db.flush()
            return True

    @staticmethod
    def _keyword_filter(keyword: Optional[str]):
        """Build one case-insensitive substring filter over list-search fields.

        A blank keyword disables the filter. The searchable fields are task_id,
        owner_bot_id, and the user-facing task summary text in TaskSpec.
        """
        normalized = str(keyword or "").strip()
        if not normalized:
            return None
        safe_task_spec = SafeJsonDocument(TaskInfoModel.task_spec)
        field_values = (
            TaskInfoModel.task_id,
            TaskInfoModel.owner_bot_id,
            JsonText(safe_task_spec, "$.context.title"),
            JsonText(safe_task_spec, "$.goal.objective"),
        )
        return or_(
            *(
                func.lower(value).contains(normalized, autoescape=True)
                for value in field_values
            )
        )

    def list_records(
        self,
        status: Optional[Sequence[Status]] = None,
        *,
        owner_user_id: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            keyword_filter = self._keyword_filter(keyword)
            q = db.query(self._model).filter(
                self._model.env == get_current_env(),
            )
            # status 是运行时态集合:空/None 不过滤,非空按 SQL IN 过滤。
            if status:
                q = q.filter(self._model.status.in_([s.value for s in status]))
            if owner_user_id is not None:
                q = q.filter(self._model.owner_user_id == owner_user_id)
            if keyword_filter is not None:
                q = q.filter(keyword_filter)
            rows = q.order_by(
                self._model.gmt_modified.desc(),
                self._model.id.desc(),
            ).all()
            return [row.to_record() for row in rows]

    def list_records_page(
        self,
        status: Optional[Sequence[Status]] = None,
        *,
        owner_user_id: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskInfoRecord], int]:
        with self._db.orm_session() as db:
            keyword_filter = self._keyword_filter(keyword)
            q = db.query(self._model).filter(
                self._model.env == get_current_env(),
            )
            # status 是运行时态集合:空/None 不过滤,非空按 SQL IN 过滤。
            if status:
                q = q.filter(self._model.status.in_([s.value for s in status]))
            if owner_user_id is not None:
                q = q.filter(self._model.owner_user_id == owner_user_id)
            if keyword_filter is not None:
                q = q.filter(keyword_filter)
            total = q.count()
            rows = (
                q.order_by(
                    self._model.gmt_modified.desc(),
                    self._model.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return [row.to_record() for row in rows], total

    def list_by_status(
        self,
        status: Status | Sequence[Status],
        *,
        gmt_modified_since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[TaskInfoRecord]:
        with self._db.orm_session() as db:
            statuses = [status] if isinstance(status, Status) else list(status)
            q = db.query(self._model).filter(
                self._model.env == get_current_env(),
                self._model.status.in_([item.value for item in statuses]),
            )
            if gmt_modified_since is not None:
                q = q.filter(self._model.gmt_modified >= gmt_modified_since)
            rows = q.order_by(self._model.gmt_modified.desc()).limit(limit).all()
            return [r.to_record() for r in rows]
