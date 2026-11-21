from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from agentclaw.community.core.repository.implementations.task.todos.todo_repository import (
    TodoRepository,
)
from agentclaw.community.core.task.todos.errors import TodoStateConflictError
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.stage_models import (
    TodoPendingActionKind,
    TodoStageRecord,
    TodoStageStatus,
)
from agentclaw.community.core.task.todos.stage_outcome_models import (
    TodoStageOutcome,
    TodoStageOutcomeKind,
)

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def _stage(
    *,
    todo_id: str,
    stage_id: str,
    order: int,
    kind: str,
    status: TodoStageStatus,
    owner: str = "owner_1",
    gate: dict | None = None,
) -> TodoStageRecord:
    return TodoStageRecord(
        stage_instance_id=stage_id,
        todo_id=todo_id,
        template_version_id="todo_tplv_1",
        stage_key=f"stage_{order}",
        stage_order=order,
        stage_name=f"Stage {order}",
        purpose=f"Purpose {order}",
        stage_kind=kind,
        executor_binding_key=f"executor_{order}",
        executor_subject_type=kind,
        executor_subject_id=f"{kind}_{order}",
        executor_subject_name=f"{kind.title()} {order}",
        owner_binding_key="owner",
        owner_user_id=owner,
        owner_user_name=owner,
        completion_gate_json=json.dumps(gate or {"required_artifacts": []}),
        status=status,
        collab_context_id=None,
        pending_action_kind=(
            TodoPendingActionKind.PLANNED_HUMAN_STAGE
            if kind == "human" and status is TodoStageStatus.IN_PROGRESS
            else None
        ),
        state_reason=(
            "planned human stage requires action"
            if kind == "human" and status is TodoStageStatus.IN_PROGRESS
            else None
        ),
        requested_action=None,
        started_at=NOW if status is TodoStageStatus.IN_PROGRESS else None,
        completed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _todo(todo_id: str, stage_id: str, status: TodoStatus) -> TodoRecord:
    return TodoRecord(
        todo_id=todo_id,
        space_id=7,
        title="Lifecycle test",
        description="Exercise stage projection",
        bot_id="bot_0",
        bot_name="Bot 0",
        status=status,
        collab_context_id=None,
        created_by="creator_1",
        created_at=NOW,
        updated_at=NOW,
        template_id="todo_tpl_1",
        template_version_id="todo_tplv_1",
        template_name_snapshot="Lifecycle",
        current_stage_instance_id=stage_id,
    )


def _create(repo: TodoRepository, *, kinds: tuple[str, ...]) -> TodoRecord:
    todo_id = f"todo_{uuid4().hex}"
    stages = tuple(
        _stage(
            todo_id=todo_id,
            stage_id=f"{todo_id}_stage_{index}",
            order=index,
            kind=kind,
            status=(
                TodoStageStatus.IN_PROGRESS
                if index == 0
                else TodoStageStatus.NOT_STARTED
            ),
        )
        for index, kind in enumerate(kinds)
    )
    todo = _todo(todo_id, stages[0].stage_instance_id, TodoStatus.IN_PROGRESS)
    return repo.create_todo_with_stages(
        record=todo,
        participant_bindings=(),
        stages=stages,
    )


def _outcome(
    todo: TodoRecord,
    stage_id: str,
    kind: TodoStageOutcomeKind,
    *,
    key: str = "outcome_1",
    **kwargs,
) -> TodoStageOutcome:
    return TodoStageOutcome(
        todo_id=todo.todo_id,
        stage_instance_id=stage_id,
        outcome=kind,
        summary="stage result",
        execution_id="execution_1",
        idempotency_key=key,
        **kwargs,
    )


def test_bot_to_bot_keeps_todo_in_progress_and_activates_next_stage(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("bot", "bot"))
    first, second = repo.list_stages(todo_id=todo.todo_id)

    updated = repo.apply_stage_outcome(
        outcome=_outcome(todo, first.stage_instance_id, TodoStageOutcomeKind.COMPLETED)
    )

    assert updated.status is TodoStatus.IN_PROGRESS
    assert updated.current_stage_instance_id == second.stage_instance_id
    assert repo.get_todo(todo_id=todo.todo_id).status is TodoStatus.IN_PROGRESS
    assert repo.list_stages(todo_id=todo.todo_id)[1].status is TodoStageStatus.IN_PROGRESS


def test_bot_to_human_enters_planned_human_pending_action(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("bot", "human"))
    first, second = repo.list_stages(todo_id=todo.todo_id)

    updated = repo.apply_stage_outcome(
        outcome=_outcome(todo, first.stage_instance_id, TodoStageOutcomeKind.COMPLETED)
    )

    assert updated.status is TodoStatus.PENDING_ACTION
    assert updated.pending_action_kind == TodoPendingActionKind.PLANNED_HUMAN_STAGE.value
    assert updated.current_stage_instance_id == second.stage_instance_id
    assert repo.list_stages(todo_id=todo.todo_id)[1].pending_action_kind is TodoPendingActionKind.PLANNED_HUMAN_STAGE


def test_human_to_bot_completion_keeps_todo_in_progress(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("human", "bot"))
    first, second = repo.list_stages(todo_id=todo.todo_id)
    # The helper starts a first Human stage in progress, matching template expansion.

    updated = repo.apply_stage_outcome(
        outcome=_outcome(todo, first.stage_instance_id, TodoStageOutcomeKind.COMPLETED)
    )

    assert updated.status is TodoStatus.IN_PROGRESS
    assert updated.current_stage_instance_id == second.stage_instance_id
    assert repo.list_stages(todo_id=todo.todo_id)[1].status is TodoStageStatus.IN_PROGRESS


def test_blocked_is_pending_action_and_resume_reactivates_same_bot_stage(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("bot",))
    stage = repo.list_stages(todo_id=todo.todo_id)[0]

    blocked = repo.apply_stage_outcome(
        outcome=_outcome(
            todo,
            stage.stage_instance_id,
            TodoStageOutcomeKind.BLOCKED,
            blocker_reason="Needs a decision",
            requested_action="Ask the owner",
        )
    )
    assert blocked.status is TodoStatus.PENDING_ACTION
    assert blocked.pending_action_kind == TodoPendingActionKind.BLOCKED.value
    assert blocked.state_reason == "Needs a decision"
    assert blocked.requested_action == "Ask the owner"

    resumed = repo.resume_todo(todo_id=todo.todo_id)
    assert resumed.status is TodoStatus.IN_PROGRESS
    assert resumed.current_stage_instance_id == stage.stage_instance_id
    resumed_stage = repo.list_stages(todo_id=todo.todo_id)[0]
    assert resumed_stage.status is TodoStageStatus.IN_PROGRESS
    assert resumed_stage.pending_action_kind is None


def test_failed_is_attempt_result_and_does_not_leave_in_progress(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("bot",))
    stage = repo.list_stages(todo_id=todo.todo_id)[0]

    failed = repo.apply_stage_outcome(
        outcome=_outcome(
            todo,
            stage.stage_instance_id,
            TodoStageOutcomeKind.FAILED,
            requested_action="Retry automatically",
        )
    )

    assert failed.status is TodoStatus.IN_PROGRESS
    assert failed.pending_action_kind is None
    assert failed.requested_action == "Retry automatically"
    assert repo.list_stages(todo_id=todo.todo_id)[0].status is TodoStageStatus.IN_PROGRESS


def test_interrupted_can_resume_but_terminal_states_cannot(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("bot",))
    interrupted = repo.transition_status(
        todo_id=todo.todo_id,
        expected_status=TodoStatus.IN_PROGRESS,
        new_status=TodoStatus.INTERRUPTED,
    )
    assert repo.resume_todo(todo_id=interrupted.todo_id).status is TodoStatus.IN_PROGRESS

    completed_todo = _create(repo, kinds=("bot",))
    stage = repo.list_stages(todo_id=completed_todo.todo_id)[0]
    repo.apply_stage_outcome(
        outcome=_outcome(completed_todo, stage.stage_instance_id, TodoStageOutcomeKind.COMPLETED)
    )
    with pytest.raises(TodoStateConflictError, match="Only interrupted or pending_action"):
        repo.resume_todo(todo_id=completed_todo.todo_id)

    cancelled = _create(repo, kinds=("bot",))
    repo.cancel_todo(todo_id=cancelled.todo_id, reason="No longer needed")
    with pytest.raises(TodoStateConflictError, match="Only interrupted or pending_action"):
        repo.resume_todo(todo_id=cancelled.todo_id)


def test_stage_outcome_is_idempotent_and_stale_stage_is_rejected(db) -> None:
    repo = TodoRepository(db)
    todo = _create(repo, kinds=("bot", "bot"))
    first, second = repo.list_stages(todo_id=todo.todo_id)
    outcome = _outcome(todo, first.stage_instance_id, TodoStageOutcomeKind.COMPLETED)

    first_result = repo.apply_stage_outcome(outcome=outcome)
    replay = repo.apply_stage_outcome(outcome=outcome)
    assert replay == first_result

    with pytest.raises(TodoStateConflictError, match="not for the current"):
        repo.apply_stage_outcome(
            outcome=_outcome(
                todo,
                first.stage_instance_id,
                TodoStageOutcomeKind.FAILED,
                key="different_key",
            )
        )
