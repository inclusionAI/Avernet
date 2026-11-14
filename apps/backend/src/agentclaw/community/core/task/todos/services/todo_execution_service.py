"""Automatic Todo dispatch and runtime-status projection orchestration."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.artifact_service_protocol import (
    TodoArtifactServiceProtocol,
)
from agentclaw.community.core.task.todos.bot_scope import (
    require_todo_bot_owner_id,
    resolve_todo_bot,
)
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.execution_protocol import (
    TodoExecutionServiceProtocol,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACCEPTANCE_ID_LENGTH,
    MAX_ACCEPTANCE_TARGETS,
    MAX_ACTOR_ID_LENGTH,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_EVENT_ID_LENGTH,
    MAX_MESSAGE_LENGTH,
    AcceptanceVerdict,
    TodoAcceptanceTargetKind,
    TodoRecord,
    TodoStatus,
)
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
    TodoCollaborationEvent,
    TodoCollaborationPlugin,
)

_CONTEXT_CREATED = "created"
_CONTEXT_IN_PROGRESS = "in_progress"
_CONTEXT_DONE = "done"
_TASK_STARTED_EVENT = "task.started"
_TASK_COMPLETED_EVENT = "task.completed"
_INTERACTION_EVENT = "user_interaction.posted"
_TASK_ACCEPTED_EVENT = "task.accepted"
_TASK_REJECTED_EVENT = "task.rejected"
_HUMAN_WORK_EVENTS = {_INTERACTION_EVENT, _TASK_REJECTED_EVENT}


def _unstarted_human_work(
    context: TodoCollaborationContext,
) -> TodoCollaborationEvent | None:
    """Return durable Human work that is still awaiting a resumed run."""
    for event in reversed(context.events):
        if event.event_type == _TASK_STARTED_EVENT:
            return None
        if event.event_type in _HUMAN_WORK_EVENTS:
            return event
    return None


def _valid_acceptance_targets(
    *,
    verdict: AcceptanceVerdict,
    targets: object,
) -> bool:
    if not isinstance(targets, list) or len(targets) > MAX_ACCEPTANCE_TARGETS:
        return False
    if verdict is AcceptanceVerdict.APPROVED:
        return not targets
    if not targets:
        return False

    seen: set[tuple[str, str]] = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"kind", "id", "floor"}:
            return False
        kind = target.get("kind")
        target_id = target.get("id")
        floor = target.get("floor")
        if kind not in {
            TodoAcceptanceTargetKind.EVENT.value,
            TodoAcceptanceTargetKind.ARTIFACT.value,
        }:
            return False
        max_length = (
            MAX_EVENT_ID_LENGTH
            if kind == TodoAcceptanceTargetKind.EVENT.value
            else MAX_ARTIFACT_ID_LENGTH
        )
        if (
            not isinstance(target_id, str)
            or target_id != target_id.strip()
            or not target_id
            or len(target_id) > max_length
            or isinstance(floor, bool)
            or not isinstance(floor, int)
            or floor < 1
            or (kind, target_id) in seen
        ):
            return False
        seen.add((kind, target_id))
    return True


def _acceptance_decision(
    *,
    todo_id: str,
    responsible_actor_id: str,
    event: TodoCollaborationEvent,
) -> tuple[str, AcceptanceVerdict, str, str] | None:
    if event.event_type not in {_TASK_ACCEPTED_EVENT, _TASK_REJECTED_EVENT}:
        return None
    payload = event.payload
    decision_id = payload.get("decision_id")
    event_todo_id = payload.get("todo_id")
    verdict_raw = payload.get("verdict")
    message = payload.get("content")
    if (
        event.actor_type != "human"
        or event.actor_id != responsible_actor_id
        or payload.get("author_id") != event.actor_id
        or not isinstance(event.actor_id, str)
        or not event.actor_id
        or len(event.actor_id) > MAX_ACTOR_ID_LENGTH
        or not isinstance(decision_id, str)
        or decision_id != decision_id.strip()
        or not decision_id
        or len(decision_id) > MAX_ACCEPTANCE_ID_LENGTH
        or event_todo_id != todo_id
        or not isinstance(message, str)
        or message != message.strip()
        or len(message) > MAX_MESSAGE_LENGTH
    ):
        return None
    expected_verdict = (
        AcceptanceVerdict.APPROVED
        if event.event_type == _TASK_ACCEPTED_EVENT
        else AcceptanceVerdict.REJECTED
    )
    if verdict_raw != expected_verdict.value:
        return None
    if expected_verdict is AcceptanceVerdict.REJECTED and not message:
        return None
    if not _valid_acceptance_targets(
        verdict=expected_verdict,
        targets=payload.get("targets"),
    ):
        return None
    return decision_id, expected_verdict, message, event.actor_id


class TodoExecutionService(TodoExecutionServiceProtocol):
    """Bridge A-owned Todo state to B-owned Context execution state."""

    @inject
    def __init__(
        self,
        repository: TodoRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        bot_repository: BotRepository,
        collaboration: TodoCollaborationPlugin,
        artifacts: TodoArtifactServiceProtocol,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._bot_repository = bot_repository
        self._collaboration = collaboration
        self._artifacts = artifacts

    async def dispatch_pending(
        self,
        *,
        todo_id: str,
        env: str | None = None,
    ) -> TodoRecord:
        """Create/bind/run one pending Todo, idempotently across schedulers."""

        todo = self._repository.get_todo(todo_id=todo_id)
        if todo.status is not TodoStatus.PENDING:
            return todo

        space, _member = self._space_access.require_space_member(
            space_id=todo.space_id,
            user_id=todo.created_by,
            env=env,
        )
        bot = resolve_todo_bot(
            bot_repository=self._bot_repository,
            space=space,
            actor_id=todo.created_by,
            bot_id=todo.bot_id,
        )
        owner_id = require_todo_bot_owner_id(bot)

        context = await self._bound_context(
            todo=todo,
            environment=space.env,
            owner_id=owner_id,
        )
        if context.status == _CONTEXT_CREATED:
            credential = self._artifacts.issue_execution(
                todo_id=todo.todo_id,
                collab_context_id=context.collab_context_id,
                executor_id=todo.bot_id,
            )
            context = await self._collaboration.run_context(
                collab_context_id=context.collab_context_id,
                actor_id=todo.created_by,
                todo_execution_id=credential.execution_id,
                todo_execution_token=credential.token,
            )
            if context.run_id:
                self._artifacts.bind_run_id(
                    execution_id=credential.execution_id,
                    run_id=context.run_id,
                )
            if context.status not in {_CONTEXT_IN_PROGRESS, _CONTEXT_DONE}:
                raise TodoStateConflictError(
                    "Todo Context remained in unexpected state after run: "
                    f"{context.status}"
                )
        elif context.status not in {_CONTEXT_IN_PROGRESS, _CONTEXT_DONE}:
            raise TodoStateConflictError(
                f"Pending Todo has inconsistent Context state {context.status}"
            )

        return self._transition_or_reload(
            todo_id=todo.todo_id,
            expected=TodoStatus.PENDING,
            target=TodoStatus.IN_PROGRESS,
        )

    async def resume_claimed_interaction(self, *, todo_id: str) -> TodoRecord:
        """Resume one claimed Human-work item when the scheduler finds it durable.

        A claim is represented by A as ``pending_acceptance -> in_progress`` and
        by B as the latest ``user_interaction.posted`` or ``task.rejected``
        event. A later ``task.started`` event makes the operation a no-op,
        which makes retries safe even if scheduler ticks overlap.
        """
        todo = self._repository.get_todo(todo_id=todo_id)
        if todo.status is not TodoStatus.IN_PROGRESS:
            return todo
        if not todo.collab_context_id:
            raise TodoNotFoundError("Interacting Todo collaboration Context is missing")
        context = self._collaboration.get_context(
            collab_context_id=todo.collab_context_id,
            actor_id=todo.created_by,
        )
        trigger = _unstarted_human_work(context)
        if trigger is None:
            if context.status != _CONTEXT_IN_PROGRESS:
                raise TodoStateConflictError(
                    f"Interacting Todo has inconsistent Context state {context.status}"
                )
            return todo
        if context.status == _CONTEXT_IN_PROGRESS:
            return todo
        if context.status != _CONTEXT_DONE:
            raise TodoStateConflictError(
                f"Interacting Todo has inconsistent Context state {context.status}"
            )
        credential = self._artifacts.issue_execution(
            todo_id=todo.todo_id,
            collab_context_id=todo.collab_context_id,
            executor_id=todo.bot_id,
        )
        context = await self._collaboration.resume_context(
            collab_context_id=todo.collab_context_id,
            actor_id=todo.created_by,
            triggered_by_event_id=trigger.event_id,
            todo_execution_id=credential.execution_id,
            todo_execution_token=credential.token,
        )
        if context.run_id:
            self._artifacts.bind_run_id(
                execution_id=credential.execution_id,
                run_id=context.run_id,
            )
        if context.status != _CONTEXT_IN_PROGRESS:
            raise TodoStateConflictError(
                f"Context resume returned unexpected state {context.status}"
            )
        return self._repository.get_todo(todo_id=todo.todo_id)

    async def reconcile_pending_acceptance(self, *, todo_id: str) -> TodoRecord:
        """Repair a B-first acceptance whose A-side projection did not commit."""

        todo = self._repository.get_todo(todo_id=todo_id)
        if todo.status is not TodoStatus.PENDING_ACCEPTANCE:
            return todo
        if not todo.collab_context_id:
            raise TodoNotFoundError(
                "Pending-acceptance Todo collaboration Context is missing"
            )
        context = self._collaboration.get_context(
            collab_context_id=todo.collab_context_id,
            actor_id=todo.created_by,
        )
        current_round: list[TodoCollaborationEvent] = []
        for event in reversed(context.events):
            if event.event_type == _TASK_COMPLETED_EVENT:
                break
            current_round.append(event)
        for event in reversed(current_round):
            decision = _acceptance_decision(
                todo_id=todo.todo_id,
                responsible_actor_id=todo.created_by,
                event=event,
            )
            if decision is None:
                continue
            decision_id, verdict, message, author_id = decision
            return self._repository.record_acceptance(
                todo_id=todo.todo_id,
                acceptance_id=decision_id,
                verdict=verdict,
                message=message,
                author_id=author_id,
            )
        return todo

    async def refresh_in_progress(self, *, todo_id: str) -> TodoRecord:
        """Project a completed B Context into A's pending-acceptance state."""

        todo = self._repository.get_todo(todo_id=todo_id)
        if todo.status is not TodoStatus.IN_PROGRESS:
            return todo
        if not todo.collab_context_id:
            raise TodoNotFoundError("In-progress Todo collaboration Context is missing")

        context = self._collaboration.get_context(
            collab_context_id=todo.collab_context_id,
            actor_id=todo.created_by,
        )
        if context.status == _CONTEXT_DONE:
            if _unstarted_human_work(context) is not None:
                return todo
            return self._mark_pending_acceptance(todo)
        if context.status != _CONTEXT_IN_PROGRESS:
            raise TodoStateConflictError(
                f"In-progress Todo has inconsistent Context state {context.status}"
            )

        context = await self._collaboration.refresh_context_run(
            collab_context_id=todo.collab_context_id,
            actor_id=todo.created_by,
        )
        if context.status == _CONTEXT_DONE:
            return self._mark_pending_acceptance(todo)
        if context.status == _CONTEXT_IN_PROGRESS:
            return todo
        raise TodoStateConflictError(
            f"Context refresh returned unexpected state {context.status}"
        )

    async def _bound_context(
        self,
        *,
        todo: TodoRecord,
        environment: str,
        owner_id: str,
    ) -> TodoCollaborationContext:
        if todo.collab_context_id:
            return self._collaboration.get_context(
                collab_context_id=todo.collab_context_id,
                actor_id=todo.created_by,
            )

        created = self._collaboration.create_context(
            TodoCollaborationContextRequest(
                space_id=str(todo.space_id),
                environment=environment,
                principal_id=todo.created_by,
                product_kind="team_todo",
                workflow_kind="team_todo_execution",
                method_kind="Single",
                executor_id=todo.bot_id,
                executor_owner_id=owner_id,
                objective=todo.description or todo.title,
                todo_id=todo.todo_id,
            )
        )
        try:
            self._repository.bind_context_if_pending(
                todo_id=todo.todo_id,
                collab_context_id=created.collab_context_id,
            )
        except TodoStateConflictError:
            winner = self._repository.get_todo(todo_id=todo.todo_id)
            if not winner.collab_context_id:
                raise
            return self._collaboration.get_context(
                collab_context_id=winner.collab_context_id,
                actor_id=winner.created_by,
            )
        return created

    def _mark_pending_acceptance(self, todo: TodoRecord) -> TodoRecord:
        return self._transition_or_reload(
            todo_id=todo.todo_id,
            expected=TodoStatus.IN_PROGRESS,
            target=TodoStatus.PENDING_ACCEPTANCE,
        )

    def _transition_or_reload(
        self,
        *,
        todo_id: str,
        expected: TodoStatus,
        target: TodoStatus,
    ) -> TodoRecord:
        try:
            return self._repository.transition_status(
                todo_id=todo_id,
                expected_status=expected,
                new_status=target,
            )
        except TodoStateConflictError:
            current = self._repository.get_todo(todo_id=todo_id)
            accepted_successors = {
                TodoStatus.PENDING: {
                    TodoStatus.IN_PROGRESS,
                    TodoStatus.PENDING_ACCEPTANCE,
                    TodoStatus.COMPLETED,
                },
                TodoStatus.IN_PROGRESS: {
                    TodoStatus.PENDING_ACCEPTANCE,
                    TodoStatus.COMPLETED,
                },
            }
            if current.status not in accepted_successors.get(expected, set()):
                raise TodoStateConflictError(
                    "Todo status changed to an invalid successor during transition: "
                    f"{expected.value} -> {current.status.value}"
                )
            return current


__all__ = ["TodoExecutionService"]
