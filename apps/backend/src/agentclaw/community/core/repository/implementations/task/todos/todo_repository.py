"""SQLAlchemy implementation of the Todo persistence contract."""

from __future__ import annotations

import uuid

from injector import inject
from sqlalchemy import asc, or_

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoAcceptanceModel,
    TodoModel,
)
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class TodoRepository(TodoRepositoryProtocol):
    """Persist Todo rows within the current tenant and environment."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    @classmethod
    def _todo_query(cls, db, *, todo_id: str):
        query = db.query(TodoModel).filter(
            TodoModel.todo_id == todo_id,
            TodoModel.env == get_current_env(),
            TodoModel.avernet_tenant == get_current_avernet_tenant(),
        )
        return query

    @classmethod
    def _require_todo(cls, db, *, todo_id: str, lock: bool = False) -> TodoModel:
        query = cls._todo_query(db, todo_id=todo_id)
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise TodoNotFoundError(f"Todo {todo_id} not found")
        return row

    def create_todo(
        self,
        *,
        space_id: int,
        title: str,
        description: str,
        bot_id: str,
        bot_name: str,
        created_by: str,
    ) -> TodoRecord:
        with self._transaction() as db:
            row = TodoModel(
                todo_id=f"todo_{uuid.uuid4().hex}",
                space_id=space_id,
                title=title,
                description=description,
                bot_id=bot_id,
                bot_name=bot_name,
                status=TodoStatus.DRAFT.value,
                collab_context_id=None,
                created_by=created_by,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get_todo(self, *, todo_id: str) -> TodoRecord:
        with self._db.orm_session() as db:
            return self._require_todo(db, todo_id=todo_id).to_record()

    def list_todo_candidates(
        self,
        *,
        space_id: int,
        keyword: str | None,
        bot_id: str | None = None,
    ) -> tuple[TodoRecord, ...]:
        with self._db.orm_session() as db:
            query = db.query(TodoModel).filter(
                TodoModel.space_id == space_id,
                TodoModel.env == get_current_env(),
                TodoModel.avernet_tenant == get_current_avernet_tenant(),
            )
            if keyword is not None:
                pattern = f"%{keyword}%"
                query = query.filter(
                    or_(
                        TodoModel.title.ilike(pattern),
                        TodoModel.description.ilike(pattern),
                        TodoModel.bot_name.ilike(pattern),
                    )
                )
            if bot_id is not None:
                query = query.filter(TodoModel.bot_id == bot_id)
            rows = query.order_by(asc(TodoModel.gmt_create), asc(TodoModel.id)).all()
            return tuple(row.to_record() for row in rows)

    def bind_context_if_draft(
        self, *, todo_id: str, collab_context_id: str
    ) -> TodoRecord:
        with self._transaction() as db:
            row = self._require_todo(db, todo_id=todo_id, lock=True)
            if row.status != TodoStatus.DRAFT.value:
                raise TodoStateConflictError(
                    "Only draft Todos can bind a collaboration Context"
                )
            if not collab_context_id:
                raise TodoStateConflictError("collab_context_id is required")
            if row.collab_context_id is not None:
                raise TodoStateConflictError("Todo already has a collaboration Context")
            row.collab_context_id = collab_context_id
            db.flush()
            db.refresh(row)
            return row.to_record()

    def record_acceptance(
        self,
        *,
        todo_id: str,
        verdict: AcceptanceVerdict,
        message: str,
        author_id: str,
    ) -> TodoRecord:
        with self._transaction() as db:
            todo = self._require_todo(db, todo_id=todo_id, lock=True)
            if todo.status == TodoStatus.COMPLETED.value:
                raise TodoStateConflictError("Completed Todo cannot be accepted again")
            decision = TodoAcceptanceModel(
                acceptance_id=f"acceptance_{uuid.uuid4().hex}",
                todo_id=todo_id,
                verdict=verdict.value,
                message=message,
                author_id=author_id,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(decision)
            todo.status = (
                TodoStatus.COMPLETED.value
                if verdict is AcceptanceVerdict.APPROVED
                else TodoStatus.PENDING_ACCEPTANCE.value
            )
            db.flush()
            db.refresh(todo)
            return todo.to_record()
