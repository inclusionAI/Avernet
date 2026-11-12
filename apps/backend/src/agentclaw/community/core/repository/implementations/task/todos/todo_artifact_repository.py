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
    ) -> TodoExecutionRecord:
        with self._transaction() as db:
            todo = self._scope(db.query(TodoModel), TodoModel).filter(
                TodoModel.todo_id == todo_id
            ).one_or_none()
            if todo is None:
                raise TodoNotFoundError(f"Todo {todo_id} not found")
            self._scope(db.query(TodoExecutionModel), TodoExecutionModel).filter(
                TodoExecutionModel.todo_id == todo_id,
                TodoExecutionModel.status == TodoExecutionStatus.ACTIVE.value,
            ).update({TodoExecutionModel.status: TodoExecutionStatus.REPLACED.value})
            row = TodoExecutionModel(
                execution_id=f"todoexec_{uuid.uuid4().hex}",
                todo_id=todo_id,
                collab_context_id=collab_context_id,
                run_id=None,
                token_hash=token_hash,
                status=TodoExecutionStatus.ACTIVE.value,
                executor_id=executor_id,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get_execution(self, *, execution_id: str) -> TodoExecutionRecord:
        with self._db.orm_session() as db:
            row = self._scope(db.query(TodoExecutionModel), TodoExecutionModel).filter(
                TodoExecutionModel.execution_id == execution_id
            ).one_or_none()
            if row is None:
                raise TodoNotFoundError(f"Todo execution {execution_id} not found")
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
