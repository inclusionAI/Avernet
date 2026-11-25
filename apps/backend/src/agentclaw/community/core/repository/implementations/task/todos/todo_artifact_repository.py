"""SQLAlchemy persistence for Todo execution credentials and artifacts."""

from __future__ import annotations

import json
import uuid

from injector import inject
from sqlalchemy import asc

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoArtifactModel,
    TodoExecutionModel,
    TodoModel,
)
from agentclaw.community.core.repository.protocols.task.todo_artifacts import (
    TodoArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
    TodoExecutionRecord,
    TodoExecutionStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoArtifactNotFoundError,
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class TodoArtifactRepository(TodoArtifactRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    @staticmethod
    def _scope(query, model):
        return query.filter(
            model.env == get_current_env(),
            model.avernet_tenant == get_current_avernet_tenant(),
        )

    def create_execution(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        token_hash: str,
        executor_id: str,
        stage_instance_id: str | None = None,
    ) -> TodoExecutionRecord:
        with self._transaction() as db:
            todo = (
                self._scope(db.query(TodoModel), TodoModel)
                .filter(TodoModel.todo_id == todo_id)
                .with_for_update()
                .one_or_none()
            )
            if todo is None:
                raise TodoNotFoundError(f"Todo {todo_id} not found")
            if todo.collab_context_id != collab_context_id:
                raise TodoStateConflictError(
                    "Todo execution Context does not match the current bound Context"
                )

            stage_filter = (
                TodoExecutionModel.stage_instance_id.is_(None)
                if stage_instance_id is None
                else TodoExecutionModel.stage_instance_id == stage_instance_id
            )
            prior_rows = (
                self._scope(db.query(TodoExecutionModel), TodoExecutionModel)
                .filter(
                    TodoExecutionModel.todo_id == todo_id,
                    stage_filter,
                )
                .order_by(
                    TodoExecutionModel.generation.desc(),
                    TodoExecutionModel.id.desc(),
                )
                .with_for_update()
                .all()
            )
            generation = (
                max((int(row.generation or 1) for row in prior_rows), default=0) + 1
            )
            execution_id = f"todoexec_{uuid.uuid4().hex}"
            replacement_source = (
                prior_rows[0]
                if prior_rows and prior_rows[0].replaced_by_execution_id is None
                else None
            )
            for prior in prior_rows:
                if not prior.is_current:
                    continue
                prior.is_current = False
                if prior.status == TodoExecutionStatus.ACTIVE.value:
                    prior.status = TodoExecutionStatus.REPLACED.value
            if replacement_source is not None:
                replacement_source.replaced_by_execution_id = execution_id

            row = TodoExecutionModel(
                execution_id=execution_id,
                todo_id=todo_id,
                collab_context_id=collab_context_id,
                run_id=None,
                token_hash=token_hash,
                status=TodoExecutionStatus.ACTIVE.value,
                executor_id=executor_id,
                stage_instance_id=stage_instance_id,
                generation=generation,
                is_current=True,
                invalidated_by_interaction_id=None,
                replaced_by_execution_id=None,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def complete_active_execution(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
    ) -> None:
        with self._transaction() as db:
            self._scope(db.query(TodoExecutionModel), TodoExecutionModel).filter(
                TodoExecutionModel.todo_id == todo_id,
                TodoExecutionModel.collab_context_id == collab_context_id,
                TodoExecutionModel.status == TodoExecutionStatus.ACTIVE.value,
                TodoExecutionModel.is_current.is_(True),
            ).update(
                {TodoExecutionModel.status: TodoExecutionStatus.COMPLETED.value}
            )

    def get_execution(self, *, execution_id: str) -> TodoExecutionRecord:
        with self._db.orm_session() as db:
            row = self._scope(db.query(TodoExecutionModel), TodoExecutionModel).filter(
                TodoExecutionModel.execution_id == execution_id
            ).one_or_none()
            if row is None:
                raise TodoNotFoundError(f"Todo execution {execution_id} not found")
            return row.to_record()

    def list_executions(self, *, todo_id: str) -> tuple[TodoExecutionRecord, ...]:
        with self._db.orm_session() as db:
            rows = (
                self._scope(db.query(TodoExecutionModel), TodoExecutionModel)
                .filter(TodoExecutionModel.todo_id == todo_id)
                .order_by(
                    asc(TodoExecutionModel.gmt_create),
                    asc(TodoExecutionModel.id),
                )
                .all()
            )
            return tuple(row.to_record() for row in rows)

    def get_current_execution(
        self, *, todo_id: str, stage_instance_id: str
    ) -> TodoExecutionRecord:
        with self._db.orm_session() as db:
            row = (
                self._scope(db.query(TodoExecutionModel), TodoExecutionModel)
                .filter(
                    TodoExecutionModel.todo_id == todo_id,
                    TodoExecutionModel.stage_instance_id == stage_instance_id,
                    TodoExecutionModel.is_current.is_(True),
                )
                .order_by(
                    TodoExecutionModel.generation.desc(),
                    TodoExecutionModel.id.desc(),
                )
                .first()
            )
            if row is None:
                raise TodoNotFoundError(
                    f"Current Todo execution for stage {stage_instance_id} not found"
                )
            return row.to_record()

    def bind_run_id(self, *, execution_id: str, run_id: str) -> TodoExecutionRecord:
        with self._transaction() as db:
            row = self._scope(db.query(TodoExecutionModel), TodoExecutionModel).filter(
                TodoExecutionModel.execution_id == execution_id
            ).with_for_update().one_or_none()
            if row is None:
                raise TodoNotFoundError(f"Todo execution {execution_id} not found")
            row.run_id = run_id
            db.flush()
            db.refresh(row)
            return row.to_record()

    def create_artifact(
        self,
        *,
        todo_id: str,
        collab_context_id: str,
        execution_id: str,
        artifact_name: str,
        artifact_kind: TodoArtifactKind,
        content: dict[str, object],
        content_hash: str,
        creator: str,
    ) -> TodoArtifactRecord:
        with self._transaction() as db:
            existing = self._scope(db.query(TodoArtifactModel), TodoArtifactModel).filter(
                TodoArtifactModel.todo_id == todo_id,
                TodoArtifactModel.execution_id == execution_id,
                TodoArtifactModel.content_hash == content_hash,
            ).one_or_none()
            if existing is not None:
                return existing.to_record()
            row = TodoArtifactModel(
                artifact_id=f"todoartifact_{uuid.uuid4().hex}",
                todo_id=todo_id,
                collab_context_id=collab_context_id,
                execution_id=execution_id,
                artifact_name=artifact_name,
                artifact_kind=artifact_kind.value,
                content=json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                content_hash=content_hash,
                creator=creator,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def list_artifacts(self, *, todo_id: str) -> tuple[TodoArtifactRecord, ...]:
        with self._db.orm_session() as db:
            rows = self._scope(db.query(TodoArtifactModel), TodoArtifactModel).filter(
                TodoArtifactModel.todo_id == todo_id
            ).order_by(asc(TodoArtifactModel.gmt_create), asc(TodoArtifactModel.id)).all()
            return tuple(row.to_record() for row in rows)

    def get_artifact(self, *, todo_id: str, artifact_id: str) -> TodoArtifactRecord:
        with self._db.orm_session() as db:
            row = self._scope(db.query(TodoArtifactModel), TodoArtifactModel).filter(
                TodoArtifactModel.todo_id == todo_id,
                TodoArtifactModel.artifact_id == artifact_id,
            ).one_or_none()
            if row is None:
                raise TodoArtifactNotFoundError(
                    f"Todo artifact {artifact_id} not found under {todo_id}"
                )
            return row.to_record()
