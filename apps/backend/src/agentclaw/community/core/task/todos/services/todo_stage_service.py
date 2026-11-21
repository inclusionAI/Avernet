"""Product lifecycle orchestration for Bot and Human Todo stages."""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.artifact_service_protocol import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import TodoStateConflictError
from agentclaw.community.core.task.todos.models import TodoRecord
from agentclaw.community.core.task.todos.stage_models import TodoStageStatus
from agentclaw.community.core.task.todos.stage_outcome_models import (
    TodoStageOutcome,
    TodoStageOutcomeKind,
)
from agentclaw.community.core.task.todos.stage_outcome_protocol import (
    TodoStageServiceProtocol,
)


class TodoStageService(TodoStageServiceProtocol):
    """Validate actors/credentials, then delegate atomic projection to A."""

    @inject
    def __init__(
        self,
        repository: TodoRepositoryProtocol,
        artifacts: TodoArtifactServiceProtocol,
        space_access: SpaceAccessServiceProtocol,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._space_access = space_access

    def report_bot_outcome(
        self,
        *,
        todo_id: str,
        stage_instance_id: str,
        todo_execution_id: str,
        execution_token: str,
        outcome: TodoStageOutcomeKind | str,
        summary: str,
        blocker_reason: str | None = None,
        requested_action: str | None = None,
        idempotency_key: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> TodoRecord:
        execution = self._artifacts.authorize_execution(
            todo_id=todo_id,
            todo_execution_id=todo_execution_id,
            execution_token=execution_token,
        )
        todo = self._repository.get_todo(todo_id=todo_id)
        stage = self._find_stage(todo_id=todo_id, stage_instance_id=stage_instance_id)
        if stage.stage_kind != "bot":
            raise TodoStateConflictError("Only a Bot stage can accept a Bot outcome")
        if execution.stage_instance_id != stage_instance_id:
            raise TodoStateConflictError(
                "Todo execution does not belong to the reported stage"
            )
        normalized = self._outcome(outcome)
        if (
            stage.status is TodoStageStatus.COMPLETED
            and normalized is TodoStageOutcomeKind.COMPLETED
            and idempotency_key
            and stage.last_outcome_key == idempotency_key
        ):
            return todo
        if todo.current_stage_instance_id != stage_instance_id:
            raise TodoStateConflictError("Stage is not the current Todo stage")
        if stage.status is not TodoStageStatus.IN_PROGRESS:
            raise TodoStateConflictError("Only an active Bot stage can accept an outcome")
        if normalized is TodoStageOutcomeKind.COMPLETED and not summary.strip():
            raise TodoStateConflictError("completed stage outcome requires a summary")
        return self._repository.apply_stage_outcome(
            outcome=TodoStageOutcome(
                todo_id=todo_id,
                stage_instance_id=stage_instance_id,
                outcome=normalized,
                summary=summary,
                execution_id=todo_execution_id,
                idempotency_key=idempotency_key,
                result=dict(result or {}),
                blocker_reason=blocker_reason,
                requested_action=requested_action,
            )
        )

    def complete_human_stage(
        self,
        *,
        todo_id: str,
        stage_instance_id: str,
        actor_id: str,
        summary: str,
        idempotency_key: str,
        result: dict[str, Any] | None = None,
        env: str | None = None,
    ) -> TodoRecord:
        todo = self._repository.get_todo(todo_id=todo_id)
        self._space_access.require_space_member(
            space_id=todo.space_id, user_id=actor_id, env=env
        )
        stage = self._find_stage(todo_id=todo_id, stage_instance_id=stage_instance_id)
        if stage.stage_kind != "human":
            raise TodoStateConflictError("Only a Human stage can be completed by a Human")
        if actor_id != stage.owner_user_id:
            role = self._space_access.get_space_role(
                space_id=todo.space_id, user_id=actor_id
            )
            if role not in {
                SpaceRole.ADMIN,
                SpaceRole.OWNER,
                SpaceRole.ADMINISTRATOR,
            }:
                raise TodoStateConflictError("Only the stage owner or a Space admin can complete it")
        if (
            stage.status is TodoStageStatus.COMPLETED
            and stage.last_outcome_key == idempotency_key
        ):
            return todo
        if todo.current_stage_instance_id != stage_instance_id:
            raise TodoStateConflictError("Stage is not the current Todo stage")
        if stage.status is not TodoStageStatus.IN_PROGRESS:
            raise TodoStateConflictError("Only an active Human stage can be completed")
        if not summary.strip():
            raise TodoStateConflictError("completed stage outcome requires a summary")
        return self._repository.apply_stage_outcome(
            outcome=TodoStageOutcome(
                todo_id=todo_id,
                stage_instance_id=stage_instance_id,
                outcome=TodoStageOutcomeKind.COMPLETED,
                summary=summary,
                idempotency_key=idempotency_key,
                result=dict(result or {}),
            )
        )

    def resume(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord:
        todo = self._repository.get_todo(todo_id=todo_id)
        self._require_operator(todo, actor_id=actor_id, env=env)
        return self._repository.resume_todo(todo_id=todo_id)

    def cancel(
        self,
        *,
        todo_id: str,
        actor_id: str,
        reason: str | None = None,
        env: str | None = None,
    ) -> TodoRecord:
        todo = self._repository.get_todo(todo_id=todo_id)
        self._require_operator(todo, actor_id=actor_id, env=env)
        return self._repository.cancel_todo(todo_id=todo_id, reason=reason)

    def _find_stage(self, *, todo_id: str, stage_instance_id: str):
        for stage in self._repository.list_stages(todo_id=todo_id):
            if stage.stage_instance_id == stage_instance_id:
                return stage
        raise TodoStateConflictError("Todo stage was not found")

    def _current_stage(self, *, todo_id: str, stage_instance_id: str):
        todo = self._repository.get_todo(todo_id=todo_id)
        if todo.current_stage_instance_id != stage_instance_id:
            raise TodoStateConflictError("Stage is not the current Todo stage")
        return self._find_stage(todo_id=todo_id, stage_instance_id=stage_instance_id)

    def _require_operator(self, todo: TodoRecord, *, actor_id: str, env: str | None) -> None:
        self._space_access.require_space_member(
            space_id=todo.space_id, user_id=actor_id, env=env
        )
        if actor_id == todo.created_by:
            return
        if todo.template_id and todo.current_stage_instance_id:
            stage = self._find_stage(
                todo_id=todo.todo_id, stage_instance_id=todo.current_stage_instance_id
            )
            if actor_id == stage.owner_user_id:
                return
        role = self._space_access.get_space_role(
            space_id=todo.space_id, user_id=actor_id
        )
        if role not in {SpaceRole.ADMIN, SpaceRole.OWNER, SpaceRole.ADMINISTRATOR}:
            raise TodoStateConflictError(
                "Todo operation requires its creator, current stage owner, or a Space admin"
            )

    @staticmethod
    def _outcome(value: TodoStageOutcomeKind | str) -> TodoStageOutcomeKind:
        try:
            return value if isinstance(value, TodoStageOutcomeKind) else TodoStageOutcomeKind(value)
        except ValueError as exc:
            raise TodoStateConflictError(f"Unsupported Todo stage outcome {value!r}") from exc
