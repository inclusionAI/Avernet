"""SQLAlchemy implementation of the Todo persistence contract."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from injector import inject
from sqlalchemy import asc, case, or_

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoAcceptanceModel,
    TodoArtifactModel,
    TodoExecutionModel,
    TodoModel,
    TodoStageInstanceModel,
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
    TODO_STATUS_ORDER,
    TodoPage,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.core.task.todos.stage_outcome_models import (
    TodoStageOutcome,
    TodoStageOutcomeKind,
)
from agentclaw.community.core.task.todos.stage_models import (
    TodoParticipantBinding,
    TodoPendingActionKind,
    TodoStageRecord,
    TodoStageStatus,
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
        return db.query(TodoModel).filter(
            TodoModel.todo_id == todo_id,
            TodoModel.env == get_current_env(),
            TodoModel.avernet_tenant == get_current_avernet_tenant(),
        )

    @classmethod
    def _scoped_query(cls, db):
        return db.query(TodoModel).filter(
            TodoModel.env == get_current_env(),
            TodoModel.avernet_tenant == get_current_avernet_tenant(),
        )

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
                template_id="",
                template_version_id="",
                template_name_snapshot="",
                current_stage_instance_id=None,
                pending_action_kind=None,
                state_reason=None,
                requested_action=None,
                bot_id=bot_id,
                bot_name=bot_name,
                status=TodoStatus.NOT_STARTED.value,
                collab_context_id=None,
                created_by=created_by,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def create_todo_with_stages(
        self,
        *,
        record: TodoRecord,
        participant_bindings: tuple[TodoParticipantBinding, ...],
        stages: tuple[TodoStageRecord, ...],
    ) -> TodoRecord:
        with self._transaction() as db:
            row = TodoModel(
                todo_id=record.todo_id,
                space_id=record.space_id,
                title=record.title,
                description=record.description,
                template_id=record.template_id,
                template_version_id=record.template_version_id,
                template_name_snapshot=record.template_name_snapshot,
                current_stage_instance_id=record.current_stage_instance_id,
                pending_action_kind=record.pending_action_kind,
                state_reason=record.state_reason,
                requested_action=record.requested_action,
                participant_bindings_json=json.dumps(
                    [
                        {
                            "key": binding.key,
                            "label": binding.label,
                            "subject_type": binding.subject_type,
                            "subject_id": binding.subject_id,
                            "subject_name": binding.subject_name,
                        }
                        for binding in participant_bindings
                    ]
                ),
                bot_id=record.bot_id,
                bot_name=record.bot_name,
                status=record.status.value,
                collab_context_id=record.collab_context_id,
                created_by=record.created_by,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            for stage in stages:
                db.add(self._stage_model(todo_id=record.todo_id, stage=stage))
            db.flush()
            db.refresh(row)
            return row.to_record()

    @classmethod
    def _stage_model(cls, *, todo_id: str, stage: TodoStageRecord):
        return TodoStageInstanceModel(
            stage_instance_id=stage.stage_instance_id,
            todo_id=todo_id,
            template_version_id=stage.template_version_id,
            stage_key=stage.stage_key,
            stage_order=stage.stage_order,
            stage_name=stage.stage_name,
            purpose=stage.purpose,
            stage_kind=stage.stage_kind,
            executor_binding_key=stage.executor_binding_key,
            executor_subject_type=stage.executor_subject_type,
            executor_subject_id=stage.executor_subject_id,
            executor_subject_name=stage.executor_subject_name,
            owner_binding_key=stage.owner_binding_key,
            owner_user_id=stage.owner_user_id,
            owner_user_name=stage.owner_user_name,
            completion_gate_json=stage.completion_gate_json,
            status=stage.status.value,
            collab_context_id=stage.collab_context_id,
            pending_action_kind=stage.pending_action_kind.value
            if stage.pending_action_kind is not None
            else None,
            state_reason=stage.state_reason,
            requested_action=stage.requested_action,
            started_at=stage.started_at,
            completed_at=stage.completed_at,
            env=get_current_env(),
            avernet_tenant=get_current_avernet_tenant(),
        )

    def get_todo(self, *, todo_id: str) -> TodoRecord:
        with self._db.orm_session() as db:
            return self._require_todo(db, todo_id=todo_id).to_record()

    def list_todos(
        self,
        *,
        space_id: int,
        keyword: str | None,
        status: TodoStatus | None,
        bot_id: str | None,
        page: int,
        page_size: int,
        order_by_status: bool = True,
    ) -> TodoPage:
        with self._db.orm_session() as db:
            query = self._scoped_query(db).filter(TodoModel.space_id == space_id)
            if keyword is not None:
                pattern = f"%{keyword}%"
                query = query.filter(
                    or_(
                        TodoModel.title.ilike(pattern),
                        TodoModel.description.ilike(pattern),
                        TodoModel.bot_name.ilike(pattern),
                    )
                )
            if status is not None:
                query = query.filter(TodoModel.status == status.value)
            if bot_id is not None:
                query = query.filter(TodoModel.bot_id == bot_id)
            total = query.count()
            order_by = [asc(TodoModel.gmt_create), asc(TodoModel.id)]
            if status is None and order_by_status:
                status_priority = case(
                    *(
                        (TodoModel.status == status_.value, index)
                        for index, status_ in enumerate(TODO_STATUS_ORDER)
                    ),
                    else_=len(TODO_STATUS_ORDER),
                )
                order_by.insert(0, status_priority)
            rows = (
                query.order_by(*order_by)
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return TodoPage(total=total, items=tuple(row.to_record() for row in rows))

    def list_todos_by_status(
        self,
        *,
        status: TodoStatus,
        limit: int,
    ) -> tuple[TodoRecord, ...]:
        with self._db.orm_session() as db:
            rows = (
                self._scoped_query(db)
                .filter(TodoModel.status == status.value)
                .order_by(asc(TodoModel.gmt_create), asc(TodoModel.id))
                .limit(limit)
                .all()
            )
            return tuple(row.to_record() for row in rows)

    def bind_context_if_pending(
        self, *, todo_id: str, collab_context_id: str
    ) -> TodoRecord:
        with self._transaction() as db:
            row = self._require_todo(db, todo_id=todo_id, lock=True)
            if not collab_context_id:
                raise TodoStateConflictError("collab_context_id is required")
            if row.status != TodoStatus.NOT_STARTED.value:
                raise TodoStateConflictError(
                    "Only not-started (legacy pending) Todos can bind a collaboration Context"
                )
            if row.collab_context_id is not None:
                raise TodoStateConflictError("Todo already has a collaboration Context")
            row.collab_context_id = collab_context_id
            db.flush()
            db.refresh(row)
            return row.to_record()

    def transition_status(
        self,
        *,
        todo_id: str,
        expected_status: TodoStatus,
        new_status: TodoStatus,
    ) -> TodoRecord:
        with self._transaction() as db:
            row = self._require_todo(db, todo_id=todo_id, lock=True)
            if row.status != expected_status.value:
                raise TodoStateConflictError(
                    f"Todo status is {row.status}; expected {expected_status.value} (legacy pending compatibility state; expected pending)"
                )
            row.status = new_status.value
            db.flush()
            db.refresh(row)
            return row.to_record()

    def record_acceptance(
        self,
        *,
        todo_id: str,
        acceptance_id: str,
        verdict: AcceptanceVerdict,
        message: str,
        author_id: str,
    ) -> TodoRecord:
        with self._transaction() as db:
            todo = self._require_todo(db, todo_id=todo_id, lock=True)
            decision = (
                db.query(TodoAcceptanceModel)
                .filter(
                    TodoAcceptanceModel.acceptance_id == acceptance_id,
                    TodoAcceptanceModel.env == get_current_env(),
                    TodoAcceptanceModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .with_for_update()
                .one_or_none()
            )
            target_status = (
                TodoStatus.COMPLETED
                if verdict is AcceptanceVerdict.APPROVED
                else TodoStatus.IN_PROGRESS
            )
            if decision is not None:
                if (
                    decision.todo_id != todo_id
                    or decision.verdict != verdict.value
                    or decision.message != message
                    or decision.author_id != author_id
                ):
                    raise TodoStateConflictError(
                        "acceptance_id already belongs to a different decision"
                    )
                if todo.status == target_status.value:
                    return todo.to_record()
                if todo.status != TodoStatus.PENDING_ACTION.value:
                    raise TodoStateConflictError(
                        "Todo state conflicts with the recorded acceptance decision (expected pending_action)"
                    )
                todo.status = target_status.value
                db.flush()
                db.refresh(todo)
                return todo.to_record()

            if todo.status != TodoStatus.PENDING_ACTION.value:
                raise TodoStateConflictError("Todo is not pending_action (legacy pending acceptance)")
            decision = TodoAcceptanceModel(
                acceptance_id=acceptance_id,
                todo_id=todo_id,
                verdict=verdict.value,
                message=message,
                author_id=author_id,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(decision)
            todo.status = target_status.value
            db.flush()
            db.refresh(todo)
            return todo.to_record()

    def bind_stage_context(
        self, *, todo_id: str, stage_instance_id: str, collab_context_id: str
    ) -> TodoRecord:
        with self._transaction() as db:
            todo = self._require_todo(db, todo_id=todo_id, lock=True)
            stage = (
                self._stage_query(
                    db, todo_id=todo_id, stage_instance_id=stage_instance_id
                )
                .with_for_update()
                .one_or_none()
            )
            if stage is None:
                raise TodoNotFoundError(
                    f"Todo stage {stage_instance_id} not found for Todo {todo_id}"
                )
            if not collab_context_id:
                raise TodoStateConflictError("collab_context_id is required")
            if todo.current_stage_instance_id != stage_instance_id:
                raise TodoStateConflictError("Context can only bind to the current Todo stage")
            if todo.status not in {TodoStatus.NOT_STARTED.value, TodoStatus.IN_PROGRESS.value}:
                raise TodoStateConflictError("Context cannot bind after the Todo has stopped")
            if todo.collab_context_id not in {None, collab_context_id}:
                raise TodoStateConflictError("Todo already has a different collaboration Context")
            if stage.collab_context_id not in {None, collab_context_id}:
                raise TodoStateConflictError("Stage already has a different collaboration Context")
            todo.collab_context_id = collab_context_id
            stage.collab_context_id = collab_context_id
            db.flush()
            db.refresh(todo)
            return todo.to_record()

    def resume_todo(self, *, todo_id: str) -> TodoRecord:
        with self._transaction() as db:
            todo = self._require_todo(db, todo_id=todo_id, lock=True)
            if todo.status not in {
                TodoStatus.INTERRUPTED.value,
                TodoStatus.PENDING_ACTION.value,
            }:
                raise TodoStateConflictError(
                    "Only interrupted or pending_action Todos can be resumed"
                )
            if todo.status == TodoStatus.PENDING_ACTION.value and todo.pending_action_kind not in {
                TodoPendingActionKind.BLOCKED.value,
                None,
            }:
                raise TodoStateConflictError(
                    "A planned Human stage must be completed by its assigned Human"
                )

            # A blocked/interrupted templated stage is a new product attempt.
            # Do not reuse the old runtime Context: it may still be in progress
            # or contain the failed attempt's terminal events, and dispatch only
            # starts a Bot run from a newly-created Context. Clearing both
            # bindings also makes the row safe for a scheduler retry after a
            # process crash between the reset and the next dispatch.
            if todo.template_id and todo.current_stage_instance_id:
                stage = (
                    self._stage_query(
                        db,
                        todo_id=todo_id,
                        stage_instance_id=todo.current_stage_instance_id,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if stage is None:
                    raise TodoNotFoundError(
                        f"Todo stage {todo.current_stage_instance_id} not found for Todo {todo_id}"
                    )
                if stage.stage_kind != "bot":
                    raise TodoStateConflictError(
                        "Only a blocked or interrupted Bot stage can be resumed"
                    )
                if stage.status == TodoStageStatus.COMPLETED.value:
                    raise TodoStateConflictError(
                        "A completed Todo stage cannot be resumed"
                    )
                stage.status = TodoStageStatus.IN_PROGRESS.value
                stage.collab_context_id = None
                stage.pending_action_kind = None
                stage.state_reason = "Todo stage resumed"
                stage.requested_action = None
                stage.last_outcome_key = None
                stage.completed_at = None
                stage.started_at = stage.started_at or datetime.now(timezone.utc)
                todo.collab_context_id = None

            todo.status = TodoStatus.IN_PROGRESS.value
            todo.pending_action_kind = None
            todo.state_reason = None
            todo.requested_action = None
            db.flush()
            db.refresh(todo)
            return todo.to_record()

    def cancel_todo(self, *, todo_id: str, reason: str | None = None) -> TodoRecord:
        with self._transaction() as db:
            todo = self._require_todo(db, todo_id=todo_id, lock=True)
            if todo.status in {
                TodoStatus.COMPLETED.value,
                TodoStatus.CANCELLED.value,
            }:
                raise TodoStateConflictError("Completed or cancelled Todos cannot be cancelled")
            todo.status = TodoStatus.CANCELLED.value
            todo.pending_action_kind = None
            todo.state_reason = reason
            todo.requested_action = None
            db.query(TodoStageInstanceModel).filter(
                TodoStageInstanceModel.todo_id == todo_id,
                TodoStageInstanceModel.env == get_current_env(),
                TodoStageInstanceModel.avernet_tenant == get_current_avernet_tenant(),
                TodoStageInstanceModel.status != TodoStageStatus.COMPLETED.value,
            ).update(
                {
                    TodoStageInstanceModel.state_reason: reason or "Todo cancelled",
                    TodoStageInstanceModel.pending_action_kind: None,
                },
                synchronize_session=False,
            )
            db.flush()
            db.refresh(todo)
            return todo.to_record()

    def apply_stage_outcome(self, *, outcome: TodoStageOutcome) -> TodoRecord:
        """Apply stage result and product projection in one transaction."""
        with self._transaction() as db:
            todo = self._require_todo(db, todo_id=outcome.todo_id, lock=True)
            stage = self._stage_query(
                db, todo_id=outcome.todo_id, stage_instance_id=outcome.stage_instance_id
            ).with_for_update().one_or_none()
            if stage is None:
                raise TodoNotFoundError(
                    f"Todo stage {outcome.stage_instance_id} not found for Todo {outcome.todo_id}"
                )
            if todo.status in {TodoStatus.COMPLETED.value, TodoStatus.CANCELLED.value}:
                if outcome.idempotency_key and stage.last_outcome_key == outcome.idempotency_key:
                    return todo.to_record()
                raise TodoStateConflictError("Terminal Todo cannot accept a stage outcome")
            if outcome.idempotency_key and stage.last_outcome_key == outcome.idempotency_key:
                return todo.to_record()
            if todo.current_stage_instance_id != stage.stage_instance_id:
                raise TodoStateConflictError("Stage outcome is not for the current Todo stage")
            if stage.status == TodoStageStatus.COMPLETED.value:
                raise TodoStateConflictError("Todo stage is already completed")
            if outcome.outcome is TodoStageOutcomeKind.COMPLETED:
                self._require_stage_artifacts(db, todo_id=outcome.todo_id, stage=stage)
                stage.status = TodoStageStatus.COMPLETED.value
                stage.completed_at = datetime.now(timezone.utc)
                stage.pending_action_kind = None
                stage.state_reason = outcome.summary or None
                stage.requested_action = None
                stage.last_outcome_key = outcome.idempotency_key
                next_stage = (
                    db.query(TodoStageInstanceModel)
                    .filter(
                        TodoStageInstanceModel.todo_id == outcome.todo_id,
                        TodoStageInstanceModel.env == get_current_env(),
                        TodoStageInstanceModel.avernet_tenant == get_current_avernet_tenant(),
                        TodoStageInstanceModel.stage_order > stage.stage_order,
                    )
                    .order_by(asc(TodoStageInstanceModel.stage_order), asc(TodoStageInstanceModel.id))
                    .with_for_update()
                    .first()
                )
                if next_stage is None:
                    todo.status = TodoStatus.COMPLETED.value
                    todo.current_stage_instance_id = None
                    todo.pending_action_kind = None
                    todo.state_reason = outcome.summary or None
                    todo.requested_action = None
                    todo.collab_context_id = None
                else:
                    next_stage.status = TodoStageStatus.IN_PROGRESS.value
                    next_stage.started_at = datetime.now(timezone.utc)
                    next_stage.pending_action_kind = (
                        TodoPendingActionKind.PLANNED_HUMAN_STAGE.value
                        if next_stage.stage_kind == "human"
                        else None
                    )
                    next_stage.state_reason = (
                        "planned human stage requires action"
                        if next_stage.stage_kind == "human"
                        else None
                    )
                    next_stage.requested_action = None
                    todo.current_stage_instance_id = next_stage.stage_instance_id
                    todo.status = (
                        TodoStatus.PENDING_ACTION.value
                        if next_stage.stage_kind == "human"
                        else TodoStatus.IN_PROGRESS.value
                    )
                    todo.pending_action_kind = next_stage.pending_action_kind
                    todo.state_reason = next_stage.state_reason
                    todo.requested_action = next_stage.requested_action
                    todo.collab_context_id = None
                    todo.bot_id = (
                        next_stage.executor_subject_id
                        if next_stage.stage_kind == "bot"
                        else None
                    )
                    todo.bot_name = (
                        next_stage.executor_subject_name
                        if next_stage.stage_kind == "bot"
                        else None
                    )
            elif outcome.outcome is TodoStageOutcomeKind.BLOCKED:
                stage.status = TodoStageStatus.BLOCKED.value
                stage.pending_action_kind = TodoPendingActionKind.BLOCKED.value
                stage.state_reason = outcome.blocker_reason or outcome.summary
                stage.requested_action = outcome.requested_action
                stage.last_outcome_key = outcome.idempotency_key
                todo.status = TodoStatus.PENDING_ACTION.value
                todo.pending_action_kind = TodoPendingActionKind.BLOCKED.value
                todo.state_reason = stage.state_reason
                todo.requested_action = outcome.requested_action
            elif outcome.outcome is TodoStageOutcomeKind.FAILED:
                stage.status = TodoStageStatus.IN_PROGRESS.value
                stage.pending_action_kind = None
                stage.state_reason = outcome.summary or "stage attempt failed; recovery may retry"
                stage.requested_action = outcome.requested_action
                stage.last_outcome_key = outcome.idempotency_key
                todo.status = TodoStatus.IN_PROGRESS.value
                todo.pending_action_kind = None
                todo.state_reason = stage.state_reason
                todo.requested_action = outcome.requested_action
            else:
                raise TodoStateConflictError("Unsupported Todo stage outcome")
            db.flush()
            db.refresh(todo)
            return todo.to_record()

    @classmethod
    def _stage_query(cls, db, *, todo_id: str, stage_instance_id: str):
        return db.query(TodoStageInstanceModel).filter(
            TodoStageInstanceModel.stage_instance_id == stage_instance_id,
            TodoStageInstanceModel.todo_id == todo_id,
            TodoStageInstanceModel.env == get_current_env(),
            TodoStageInstanceModel.avernet_tenant == get_current_avernet_tenant(),
        )

    @staticmethod
    def _require_stage_artifacts(db, *, todo_id: str, stage) -> None:
        try:
            gate = json.loads(stage.completion_gate_json or "{}")
        except json.JSONDecodeError as exc:
            raise TodoStateConflictError("Stage completion gate is invalid") from exc
        required = gate.get("required_artifacts", [])
        if not required:
            return
        rows = (
            db.query(TodoArtifactModel)
            .join(
                TodoExecutionModel,
                TodoExecutionModel.execution_id == TodoArtifactModel.execution_id,
            )
            .filter(
                TodoArtifactModel.todo_id == todo_id,
                TodoArtifactModel.env == get_current_env(),
                TodoArtifactModel.avernet_tenant == get_current_avernet_tenant(),
                TodoExecutionModel.env == get_current_env(),
                TodoExecutionModel.avernet_tenant == get_current_avernet_tenant(),
                TodoExecutionModel.stage_instance_id == stage.stage_instance_id,
            )
            .all()
        )
        counts: dict[tuple[str, str], int] = {}
        for row in rows:
            try:
                content = json.loads(row.content or "{}")
            except json.JSONDecodeError:
                content = {}
            artifact_type = content.get("artifact_type") or content.get("type") or row.artifact_kind
            artifact_state = content.get("artifact_state") or content.get("state")
            if artifact_state is None and content.get("is_final_artifact"):
                artifact_state = "created"
            key = (str(artifact_type), str(artifact_state or ""))
            counts[key] = counts.get(key, 0) + 1
        for item in required:
            artifact_type = item.get("type") if isinstance(item, dict) else None
            artifact_state = item.get("state") if isinstance(item, dict) else None
            minimum = item.get("min_count", 1) if isinstance(item, dict) else 1
            if counts.get((str(artifact_type), str(artifact_state)), 0) < int(minimum):
                raise TodoStateConflictError(
                    f"Stage requires artifact {artifact_type}:{artifact_state} before completion"
                )

    def list_stages(self, *, todo_id: str) -> tuple[TodoStageRecord, ...]:
        with self._db.orm_session() as db:
            self._require_todo(db, todo_id=todo_id)
            rows = (
                db.query(TodoStageInstanceModel)
                .filter(
                    TodoStageInstanceModel.todo_id == todo_id,
                    TodoStageInstanceModel.env == get_current_env(),
                    TodoStageInstanceModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .order_by(asc(TodoStageInstanceModel.stage_order), asc(TodoStageInstanceModel.id))
                .all()
            )
            return tuple(row.to_record() for row in rows)

    def transition_stage_status(
        self,
        *,
        todo_id: str,
        stage_instance_id: str,
        expected_status: TodoStageStatus,
        new_status: TodoStageStatus,
        collab_context_id: str | None = None,
        state_reason: str | None = None,
        requested_action: str | None = None,
        pending_action_kind: str | None = None,
    ) -> TodoStageRecord:
        with self._transaction() as db:
            self._require_todo(db, todo_id=todo_id)
            row = (
                db.query(TodoStageInstanceModel)
                .filter(
                    TodoStageInstanceModel.stage_instance_id == stage_instance_id,
                    TodoStageInstanceModel.todo_id == todo_id,
                    TodoStageInstanceModel.env == get_current_env(),
                    TodoStageInstanceModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise TodoNotFoundError(
                    f"Todo stage {stage_instance_id} not found for Todo {todo_id}"
                )
            if row.status != expected_status.value:
                raise TodoStateConflictError(
                    "Todo stage status is "
                    f"{row.status}; expected {expected_status.value}"
                )
            row.status = new_status.value
            row.collab_context_id = collab_context_id
            row.state_reason = state_reason
            row.requested_action = requested_action
            if pending_action_kind is not None:
                row.pending_action_kind = pending_action_kind
            db.flush()
            db.refresh(row)
            return row.to_record()
