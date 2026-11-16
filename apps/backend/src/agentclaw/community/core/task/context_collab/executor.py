"""Execute Todo collaboration Contexts through the existing Task BCS client."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError,
    BcsCreateGroupRequest,
)
from agentclaw.community.core.task.task_runner.client.ports import BcsClientPort
from agentclaw.community.core.task.todos.todo_execution_prompt import (
    format_todo_execution_instruction,
)

_HUMAN_OBSERVER_ROUTING_POLICY = {"default_bot_final_delivery": "inject_observers"}
_RUNNING_STATUSES = {"created", "pending", "active", "running", "in_progress"}
_COMPLETED_STATUSES = {"completed", "done", "succeeded", "success"}
_FAILED_STATUSES = {"failed", "error", "aborted", "cancelled", "canceled"}


class ContextCollabExecutorError(RuntimeError):
    """The configured Context executor could not execute the request."""


@dataclass(frozen=True)
class ContextCollabRunRequest:
    """Input for starting the executor bound to a collaboration Context."""

    collab_context_id: str
    executor_id: str
    principal_id: str
    objective: str
    todo_id: str
    executor_owner_id: str | None = None
    todo_execution_id: str | None = None
    todo_execution_token: str | None = None


@dataclass(frozen=True)
class ContextCollabResumeRequest:
    """Input for continuing the previous executor session."""

    collab_context_id: str
    executor_id: str
    principal_id: str
    todo_id: str
    session_id: str
    message: str
    interaction_event_id: str
    executor_owner_id: str | None = None
    todo_execution_id: str | None = None
    todo_execution_token: str | None = None
    group_id: str | None = None


@dataclass(frozen=True)
class ContextCollabRunResult:
    """External execution handles recorded on the next ``task.started`` event."""

    run_id: str | None
    session_id: str | None
    group_id: str | None = None


@dataclass(frozen=True)
class ContextCollabRunStatus:
    """Snapshot of one external executor run or BCS group session."""

    run_id: str | None
    status: str
    result: object | None
    error: object | None
    completed_at: str | None
    session_id: str | None
    group_id: str | None = None


class ContextCollabExecutor:
    """Run one selected Todo Bot as BCS manager with its creator as observer.

    This is a Task-core execution component, not a deployment Plugin. It reuses
    Task's existing BCS port, request DTO, HTTP adapter binding, and Bot-token
    provider without changing ``TaskExecutor``.
    """

    def __init__(
        self,
        bcs: BcsClientPort,
        *,
        bot_tokens: BcsBotTokenProvider | None = None,
        backend_url: str = "",
    ) -> None:
        self._bcs = bcs
        self._bot_tokens = bot_tokens
        self._backend_url = backend_url

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        driver = self._driver_identity(request)
        message = self._delivery_message(
            request=request,
            instruction=request.objective,
            resume=False,
        )
        token = self._bot_token(driver)
        group_request = BcsCreateGroupRequest(
            driver_bot=driver,
            master_bot=driver,
            participants=[
                {"bot_uuid": driver, "role": "manager"},
                {
                    "bot_uuid": f"human_{request.principal_id}",
                    "bot_name": request.principal_id,
                    "role": "observer",
                },
            ],
            group_strategy="manager_worker",
            context=message,
            routing_policy=dict(_HUMAN_OBSERVER_ROUTING_POLICY),
            label=f"Todo {request.todo_id}",
            caller_bot_token=token,
            idempotency_key=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"avernet-todo-context:{request.collab_context_id}",
                )
            ),
        )
        try:
            result = await self._bcs.create_group(group_request)
        except BcsClientError as exc:
            raise ContextCollabExecutorError(
                "Context executor group creation failed"
            ) from exc
        group_id = str(result.group_id or "").strip()
        session_id = str(result.session_id or "").strip()
        if not group_id or not session_id:
            raise ContextCollabExecutorError(
                "Context executor group creation returned no group/session handle"
            )
        return ContextCollabRunResult(
            run_id=None,
            group_id=group_id,
            session_id=session_id,
        )

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        driver = self._driver_identity(request)
        group_id = str(request.group_id or "").strip()
        if not group_id:
            raise ContextCollabExecutorError(
                "Context executor group_id is required for session resume"
            )
        token = self._bot_token(driver)
        if not token:
            raise ContextCollabExecutorError(
                "Context executor Bot token is required for BCS session resume"
            )
        message = self._delivery_message(
            request=request,
            instruction=request.message,
            resume=True,
        )
        try:
            result = await self._bcs.send_session_chat(
                request.session_id,
                message=message,
                caller_bearer=token,
                idempotency_key=request.interaction_event_id,
            )
        except BcsClientError as exc:
            raise ContextCollabExecutorError(
                "Context executor session resume failed"
            ) from exc
        returned_session_id = str(result.get("session_id") or "").strip()
        if returned_session_id and returned_session_id != request.session_id:
            raise ContextCollabExecutorError(
                "Context executor session resume returned a different session"
            )
        return ContextCollabRunResult(
            run_id=None,
            group_id=group_id,
            session_id=request.session_id,
        )

    async def get_run(
        self,
        run_id: str | None = None,
        *,
        group_id: str | None = None,
        session_id: str | None = None,
    ) -> ContextCollabRunStatus:
        if run_id is not None or not group_id:
            raise ContextCollabExecutorError(
                "BCS Context status lookup requires group_id and no run_id"
            )
        try:
            group = await self._bcs.get_group(group_id)
        except BcsClientError as exc:
            raise ContextCollabExecutorError(
                "Context executor group status lookup failed"
            ) from exc
        session = group.get("session") or {}
        if not isinstance(session, dict):
            raise ContextCollabExecutorError(
                "Context executor group returned an invalid session snapshot"
            )
        returned_session_id = str(session.get("session_id") or session_id or "").strip()
        if session_id and returned_session_id and returned_session_id != session_id:
            raise ContextCollabExecutorError(
                "Context executor group returned a different session"
            )
        return ContextCollabRunStatus(
            run_id=None,
            group_id=group_id,
            status=_normalize_group_status(session.get("status")),
            result=session.get("output"),
            error=session.get("error_message") or session.get("error"),
            completed_at=session.get("completed_at"),
            session_id=returned_session_id or None,
        )

    def _driver_identity(
        self, request: ContextCollabRunRequest | ContextCollabResumeRequest
    ) -> str:
        if not request.executor_owner_id:
            raise ContextCollabExecutorError(
                "Context executor owner is required for BCS Bot addressing"
            )
        return compose_bot_identity(
            request.executor_id,
            request.executor_owner_id,
        )

    def _bot_token(self, driver: str) -> str | None:
        if self._bot_tokens is None:
            return None
        return self._bot_tokens.get_token(driver)

    def _delivery_message(
        self,
        *,
        request: ContextCollabRunRequest | ContextCollabResumeRequest,
        instruction: str,
        resume: bool,
    ) -> str:
        execution_id = request.todo_execution_id
        execution_token = request.todo_execution_token
        if bool(execution_id) != bool(execution_token):
            raise ContextCollabExecutorError(
                "Todo execution ID and token must be provided together"
            )
        if not execution_id or not execution_token:
            return instruction
        backend_url = self._backend_url.strip().rstrip("/")
        if not backend_url:
            raise ContextCollabExecutorError(
                "Backend URL is required for Todo execution delivery"
            )
        return format_todo_execution_instruction(
            todo_id=request.todo_id,
            todo_execution_id=execution_id,
            todo_execution_token=execution_token,
            backend_url=backend_url,
            instruction=instruction,
            resume=resume,
        )


def _normalize_group_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _RUNNING_STATUSES:
        return "RUNNING"
    if normalized in _COMPLETED_STATUSES:
        return "COMPLETED"
    if normalized in _FAILED_STATUSES:
        return "FAILED"
    return normalized.upper()


class UnavailableContextCollabExecutor:
    """Fail closed when this deployment has no Task BCS port."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        del request
        raise ContextCollabExecutorError(
            "Context collaboration executor is unavailable: " + self._reason
        )

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        del request
        raise ContextCollabExecutorError(
            "Context collaboration executor is unavailable: " + self._reason
        )

    async def get_run(
        self,
        run_id: str | None = None,
        *,
        group_id: str | None = None,
        session_id: str | None = None,
    ) -> ContextCollabRunStatus:
        del run_id, group_id, session_id
        raise ContextCollabExecutorError(
            "Context collaboration executor is unavailable: " + self._reason
        )
