"""Todo runtime orchestration over authoritative L4 Work and Event services.

Preserves direct Bot and historical BCS run/resume behavior from dev. Runtime
invocation and SQL CAS remain separate failure domains. No projection is written
back as authority, and no runtime operations are added to the L4 service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
    ContextCollabStatusProjection,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    resource_bindings_from_initial_event,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.executor import (
    ContextCollabExecutor,
    ContextCollabRunRequest,
    ContextCollabResumeRequest,
)
from agentclaw.community.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TodoContextRunResult:
    collab_context_id: str
    run_id: str | None
    session_id: str | None
    context_version: int
    current_status: str
    group_id: str | None = None


def _required_str(value: object, field: str) -> str:
    """必填字符串校验;空串/非字符串抛 ``ContextCollabValidationError``。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{field} must be a non-empty string, got: {value!r}"
        )
    return value


def _previous_runtime_handles(
    events: Sequence[EventRecord],
) -> tuple[str | None, str | None, str | None]:
    """Find the latest real executor handles from a previous run.

    Returns ``(run_id, group_id, session_id)``. Direct Bot executions carry a
    run id, while BCS manager-worker executions carry a group and session. The
    two handle kinds stay distinct so a group id is never persisted as a fake
    run id.
    """
    for event in reversed(events):
        if event.event_kind not in {"task.started", "task.completed"}:
            continue
        if not isinstance(event.payload, dict):
            continue
        run_id = _payload_identifier(event.payload, "run_id")
        group_id = _payload_identifier(event.payload, "group_id")
        session_id = _payload_identifier(event.payload, "session_id")
        if run_id or group_id or session_id:
            return run_id, group_id, session_id
    return None, None, None


def _payload_identifier(payload: dict, field: str) -> str | None:
    value = payload.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _runtime_handle_payload(
    *,
    run_id: str | None,
    group_id: str | None,
    session_id: str | None,
) -> dict[str, object]:
    """Build an event payload from real, non-empty runtime handles."""
    payload: dict[str, object] = {}
    if run_id:
        payload["run_id"] = run_id
    if group_id:
        payload["group_id"] = group_id
    if session_id:
        payload["session_id"] = session_id
    return payload


class TodoContextRuntime:
    """Fixed Todo-owned execution orchestration, assembled by TaskTodoBindings."""

    def __init__(
        self,
        service: ContextServiceProtocol,
        repository: ContextCollabRepositoryProtocol,
        executor: ContextCollabExecutor,
    ) -> None:
        self._service = service
        self._repository = repository
        self._executor = executor

    @staticmethod
    def _authorize_record(record: ContextStatusRecord, actor: ActorContext) -> None:
        if record.task is None or actor.actor != record.task.principal_ref:
            raise ContextCollabValidationError(
                "Todo runtime requires its Task principal"
            )
        if record.creation_facts.get("product_kind") != "team_todo":
            raise ContextCollabValidationError(
                "Todo runtime requires a team_todo Context"
            )

    async def run_context(
        self,
        collab_context_id: str,
        context: ActorContext,
        *,
        todo_execution_id: str | None = None,
        todo_execution_token: str | None = None,
        todo_runner_skill_md_path: str | None = None,
    ) -> TodoContextRunResult:
        """Start the bound executor, then append the authoritative start event.

        Runtime invocation and event persistence intentionally remain two
        independent operations in this phase. A concurrent append after the
        pre-read is rejected by the repository's expected-version check rather
        than overwriting the newer Context state.
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        self._authorize_record(status, context)
        collet = status.collet
        task = status.task
        if task is None:
            raise ContextCollabValidationError(
                "collaboration context cannot run without an L4 Task"
            )
        if task.status != "created":
            raise ContextCollabValidationError(
                "collaboration context can run only from status 'created', "
                f"got {task.status!r}"
            )
        executor = task.executor_ref
        if executor is None:
            raise ContextCollabValidationError(
                "collaboration context cannot run without an executor_ref"
            )
        todo_refs = [
            ref
            for ref in tuple(
                binding.resource_ref
                for binding in resource_bindings_from_initial_event(status.events[0])
            )
            if ref.resource_type == "todo"
        ]
        if len(todo_refs) != 1:
            raise ContextCollabValidationError(
                "collaboration context run requires exactly one todo resource_ref"
            )

        runtime_result = await self._executor.run(
            ContextCollabRunRequest(
                collab_context_id=collab_context_id,
                executor_id=executor.agent_id,
                principal_id=task.principal_ref.principal_id,
                objective=task.objective,
                todo_id=todo_refs[0].resource_id,
                executor_owner_id=status.creation_facts.get("executor_owner_id"),
                todo_execution_id=todo_execution_id,
                todo_execution_token=todo_execution_token,
                todo_runner_skill_md_path=todo_runner_skill_md_path,
            )
        )
        if bool(runtime_result.run_id) == bool(runtime_result.group_id):
            raise ContextCollabValidationError(
                "Context executor must return exactly one of run_id or group_id"
            )
        if runtime_result.group_id and not runtime_result.session_id:
            raise ContextCollabValidationError(
                "BCS Context executor must return session_id with group_id"
            )
        payload = _runtime_handle_payload(
            run_id=runtime_result.run_id,
            group_id=runtime_result.group_id,
            session_id=runtime_result.session_id,
        )
        executor_actor = PrincipalRef("bot", executor.agent_id)
        accepted = self._service.append_event(
            collab_context_id,
            EventAppendCommand(
                expected_version=collet.version,
                event_kind="task.started",
                primitive_type="Task",
                actor_ref=executor_actor,
                parent_event=status.events[-1].event_id if status.events else None,
                visibility="workspace",
                payload=payload,
            ),
            ActorContext(actor=executor_actor, source=context.source),
        )
        if accepted.current_status is None:
            raise ContextCollabValidationError(
                "collaboration context run did not produce a current status"
            )
        return TodoContextRunResult(
            collab_context_id=collab_context_id,
            run_id=runtime_result.run_id,
            session_id=runtime_result.session_id,
            context_version=accepted.context_version,
            current_status=accepted.current_status,
            group_id=runtime_result.group_id,
        )

    async def resume_context(
        self,
        collab_context_id: str,
        *,
        triggered_by_event_id: str,
        context: ActorContext,
        todo_execution_id: str | None = None,
        todo_execution_token: str | None = None,
        todo_runner_skill_md_path: str | None = None,
    ) -> TodoContextRunResult:
        """Continue the previous executor session from durable Human work.

        ``user_interaction.posted`` and ``task.rejected`` are durable input
        facts. This use case starts a new executor run in the previous session
        and appends ``task.started``, moving the Context from ``done`` back to
        ``in_progress``.
        """
        normalized_trigger_id = _required_str(
            triggered_by_event_id, "triggered_by_event_id"
        )
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        self._authorize_record(status, context)
        collet = status.collet
        task = status.task
        if task is None:
            raise ContextCollabValidationError(
                "collaboration context cannot resume without an L4 Task"
            )
        if task.status != "done":
            raise ContextCollabValidationError(
                "collaboration context can resume only from status 'done', "
                f"got {task.status!r}"
            )
        trigger_index = next(
            (
                index
                for index, event in enumerate(status.events)
                if event.event_id == normalized_trigger_id
            ),
            None,
        )
        if trigger_index is None:
            raise ContextCollabValidationError(
                "collaboration context resume requires an existing Human-work event"
            )
        trigger = status.events[trigger_index]
        if trigger.event_kind not in {"user_interaction.posted", "task.rejected"}:
            raise ContextCollabValidationError(
                "collaboration context can resume only from "
                "user_interaction.posted or task.rejected"
            )
        message = (
            trigger.payload.get("content")
            if isinstance(trigger.payload, dict)
            else None
        )
        if not isinstance(message, str) or not message.strip():
            raise ContextCollabValidationError(
                "collaboration context resume requires a non-empty Human-work message"
            )
        previous_run_id, group_id, session_id = _previous_runtime_handles(
            status.events[:trigger_index]
        )
        if session_id is None:
            raise ContextCollabValidationError(
                "collaboration context resume requires the previous executor session_id"
            )
        executor = task.executor_ref
        if executor is None:
            raise ContextCollabValidationError(
                "collaboration context cannot resume without an executor_ref"
            )
        todo_ref = next(
            (
                ref
                for ref in tuple(
                    binding.resource_ref
                    for binding in resource_bindings_from_initial_event(
                        status.events[0]
                    )
                )
                if ref.resource_type == "todo"
            ),
            None,
        )
        if todo_ref is None:
            raise ContextCollabValidationError(
                "collaboration context resume requires one todo resource_ref"
            )

        runtime_result = await self._executor.resume(
            ContextCollabResumeRequest(
                collab_context_id=collab_context_id,
                executor_id=executor.agent_id,
                principal_id=task.principal_ref.principal_id,
                todo_id=todo_ref.resource_id,
                session_id=session_id,
                message=message,
                interaction_event_id=trigger.event_id,
                executor_owner_id=status.creation_facts.get("executor_owner_id"),
                todo_execution_id=todo_execution_id,
                todo_execution_token=todo_execution_token,
                todo_runner_skill_md_path=todo_runner_skill_md_path,
                group_id=group_id,
            )
        )
        runtime_session_id = runtime_result.session_id or session_id
        if runtime_session_id != session_id:
            raise ContextCollabValidationError(
                "Context executor returned a different session; refusing to resume"
            )
        runtime_group_id = runtime_result.group_id or group_id
        if runtime_group_id != group_id:
            raise ContextCollabValidationError(
                "Context executor returned a different group; refusing to resume"
            )
        if group_id is None:
            if previous_run_id is None:
                raise ContextCollabValidationError(
                    "collaboration context resume requires a previous runtime handle"
                )
            if not runtime_result.run_id:
                raise ContextCollabValidationError(
                    "direct Context resume returned no new run_id"
                )
            if runtime_result.run_id == previous_run_id:
                raise ContextCollabValidationError(
                    "direct Context resume returned the previous run_id"
                )
        elif runtime_result.run_id is not None:
            raise ContextCollabValidationError(
                "BCS Context resume returned an unexpected direct run_id"
            )
        executor_actor = PrincipalRef("bot", executor.agent_id)
        started_payload = _runtime_handle_payload(
            run_id=runtime_result.run_id,
            group_id=runtime_group_id,
            session_id=session_id,
        )
        started_payload.update(
            {
                "resumed": True,
                "triggered_by_event_id": trigger.event_id,
            }
        )
        accepted = self._service.append_event(
            collab_context_id,
            EventAppendCommand(
                expected_version=collet.version,
                event_kind="task.started",
                primitive_type="Task",
                actor_ref=executor_actor,
                parent_event=status.events[-1].event_id if status.events else None,
                visibility="workspace",
                payload=started_payload,
            ),
            ActorContext(actor=executor_actor, source=context.source),
        )
        if accepted.current_status is None:
            raise ContextCollabValidationError(
                "collaboration context resume did not produce a current status"
            )
        if accepted.current_status != "in_progress":
            raise ContextCollabValidationError(
                "collaboration context resume did not produce status "
                f"'in_progress', got {accepted.current_status!r}"
            )
        return TodoContextRunResult(
            collab_context_id=collab_context_id,
            run_id=runtime_result.run_id,
            session_id=session_id,
            context_version=accepted.context_version,
            current_status=accepted.current_status,
            group_id=runtime_group_id,
        )

    async def refresh_run(
        self,
        collab_context_id: str,
        context: ActorContext,
    ) -> ContextCollabStatusProjection:
        """Project the executor's real run state into the Context lifecycle.

        Runtime reads are side-effect free. Only a confirmed ``COMPLETED`` run
        appends ``task.completed``; non-terminal and failed terminal states leave
        the Context ``in_progress`` for later inspection or recovery.
        """
        status = self._repository.get_current_status(collab_context_id)
        if status is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        self._authorize_record(status, context)
        collet = status.collet
        task = status.task
        if task is None:
            raise ContextCollabValidationError(
                "collaboration context cannot refresh without an L4 Task"
            )
        if task.status == "done":
            return self._service.get_status(
                collab_context_id, trunk_events=False, context=context
            )
        if task.status != "in_progress":
            raise ContextCollabValidationError(
                "collaboration context can refresh only from status "
                f"'in_progress', got {task.status!r}"
            )

        started_event = next(
            (
                event
                for event in reversed(status.events)
                if event.event_kind == "task.started"
            ),
            None,
        )
        payload = started_event.payload if started_event is not None else None
        run_id = (
            _payload_identifier(payload, "run_id")
            if isinstance(payload, dict)
            else None
        )
        group_id = (
            _payload_identifier(payload, "group_id")
            if isinstance(payload, dict)
            else None
        )
        session_id = (
            _payload_identifier(payload, "session_id")
            if isinstance(payload, dict)
            else None
        )
        if bool(run_id) == bool(group_id):
            raise ContextCollabValidationError(
                "in_progress collaboration context requires a task.started event "
                "with exactly one non-empty run_id or group_id"
            )
        if group_id and not session_id:
            raise ContextCollabValidationError(
                "in_progress BCS collaboration context requires task.started session_id"
            )

        runtime_status = await self._executor.get_run(
            run_id,
            group_id=group_id,
            session_id=session_id,
        )
        if runtime_status.status in {"PENDING", "RUNNING"}:
            return self._service.get_status(
                collab_context_id, trunk_events=False, context=context
            )
        if runtime_status.status != "COMPLETED":
            logger.warning(
                "Context executor run did not complete successfully: "
                "collab_context_id=%s run_id=%s group_id=%s status=%s error=%r",
                collab_context_id,
                run_id,
                group_id,
                runtime_status.status,
                runtime_status.error,
            )
            return self._service.get_status(
                collab_context_id, trunk_events=False, context=context
            )

        executor = task.executor_ref
        if executor is None:
            raise ContextCollabValidationError(
                "collaboration context cannot complete without an executor_ref"
            )
        completion_payload = _runtime_handle_payload(
            run_id=run_id,
            group_id=group_id,
            session_id=runtime_status.session_id or session_id,
        )
        if runtime_status.result is not None:
            completion_payload["result"] = runtime_status.result
        if runtime_status.completed_at is not None:
            completion_payload["completed_at"] = runtime_status.completed_at
        executor_actor = PrincipalRef("bot", executor.agent_id)
        try:
            self._service.append_event(
                collab_context_id,
                EventAppendCommand(
                    expected_version=collet.version,
                    event_kind="task.completed",
                    primitive_type="Task",
                    actor_ref=executor_actor,
                    parent_event=status.events[-1].event_id if status.events else None,
                    visibility="workspace",
                    payload=completion_payload,
                ),
                ActorContext(actor=executor_actor, source=context.source),
            )
        except ContextCollabVersionConflict:
            concurrent = self._repository.get_current_status(collab_context_id)
            if (
                concurrent is not None
                and concurrent.task is not None
                and concurrent.task.status == "done"
            ):
                return self._service.get_status(
                    collab_context_id, trunk_events=False, context=context
                )
            raise

        completed = self._repository.get_current_status(collab_context_id)
        if completed is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        return self._service.get_status(
            collab_context_id, trunk_events=False, context=context
        )
