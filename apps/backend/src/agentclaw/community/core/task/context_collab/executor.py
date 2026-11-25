"""Execute Todo collaboration Contexts through Task runtime ports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsClientPort,
    OpenApiBotError,
    OpenApiBotPort,
)
from agentclaw.community.core.task.todos.todo_execution_prompt import (
    format_todo_execution_instruction,
)

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
    todo_runner_skill_md_path: str | None = None


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
    todo_runner_skill_md_path: str | None = None
    group_id: str | None = None


@dataclass(frozen=True)
class ContextCollabRunResult:
    """External execution handles recorded on the next ``task.started`` event."""

    run_id: str | None
    session_id: str | None
    group_id: str | None = None


@dataclass(frozen=True)
class ContextCollabRunStatus:
    """Snapshot of one external executor run or legacy BCS group session."""

    run_id: str | None
    status: str
    result: object | None
    error: object | None
    completed_at: str | None
    session_id: str | None
    group_id: str | None = None


class ContextCollabExecutor:
    """Run new single-Bot Todos directly while preserving legacy BCS handles.

    New Todo runs use Task's existing ``OpenApiBotPort`` and therefore depend
    on the owner-authorized BaaS Bot identity rather than public collaboration
    visibility or a synthetic one-Bot group. Historical Contexts that already
    persisted ``group_id + session_id`` remain refreshable/resumable through
    the optional BCS compatibility ports.
    """

    def __init__(
        self,
        openapi_bot: OpenApiBotPort,
        *,
        bcs: BcsClientPort | None = None,
        bot_tokens: BcsBotTokenProvider | None = None,
        backend_url: str = "",
    ) -> None:
        self._openapi_bot = openapi_bot
        self._bcs = bcs
        self._bot_tokens = bot_tokens
        self._backend_url = backend_url

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        bot_id = self._driver_identity(request)
        message = self._delivery_message(
            request=request,
            instruction=request.objective,
            resume=False,
        )
        try:
            result = await self._openapi_bot.send_message(
                bot_id=bot_id,
                message=message,
                metadata=self._runtime_metadata(request),
                message_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"avernet-todo-context:{request.collab_context_id}",
                    )
                ),
            )
        except OpenApiBotError as exc:
            raise ContextCollabExecutorError(
                "Context executor direct delivery failed"
            ) from exc
        run_id = str(result.run_id or "").strip()
        session_id = str(result.session_id or "").strip()
        if not run_id or not session_id:
            raise ContextCollabExecutorError(
                "Context executor direct delivery returned no run/session handle"
            )
        return ContextCollabRunResult(
            run_id=run_id,
            group_id=None,
            session_id=session_id,
        )

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        if request.group_id:
            return await self._resume_legacy_group(request)

        bot_id = self._driver_identity(request)
        message = self._delivery_message(
            request=request,
            instruction=request.message,
            resume=True,
        )
        metadata = self._runtime_metadata(request)
        metadata.update(
            {
                "session_id": request.session_id,
                "resumed_from_event_id": request.interaction_event_id,
            }
        )
        try:
            result = await self._openapi_bot.send_message(
                bot_id=bot_id,
                message=message,
                metadata=metadata,
                message_id=request.interaction_event_id,
            )
        except OpenApiBotError as exc:
            raise ContextCollabExecutorError(
                "Context executor direct session resume failed"
            ) from exc
        run_id = str(result.run_id or "").strip()
        returned_session_id = str(result.session_id or "").strip()
        if not run_id:
            raise ContextCollabExecutorError(
                "Context executor direct session resume returned no run handle"
            )
        if returned_session_id != request.session_id:
            raise ContextCollabExecutorError(
                "Context executor direct session resume returned a different session"
            )
        return ContextCollabRunResult(
            run_id=run_id,
            group_id=None,
            session_id=request.session_id,
        )

    async def get_run(
        self,
        run_id: str | None = None,
        *,
        group_id: str | None = None,
        session_id: str | None = None,
    ) -> ContextCollabRunStatus:
        if bool(run_id) == bool(group_id):
            raise ContextCollabExecutorError(
                "Context status lookup requires exactly one run_id or group_id"
            )
        if group_id:
            return await self._get_legacy_group_run(
                group_id=group_id,
                session_id=session_id,
            )

        normalized_run_id = str(run_id or "").strip()
        try:
            run = await self._openapi_bot.get_run(normalized_run_id)
        except OpenApiBotError as exc:
            raise ContextCollabExecutorError(
                "Context executor direct status lookup failed"
            ) from exc
        returned_session_id = str(run.get("session_id") or session_id or "").strip()
        if session_id and returned_session_id and returned_session_id != session_id:
            raise ContextCollabExecutorError(
                "Context executor direct status returned a different session"
            )
        return ContextCollabRunStatus(
            run_id=normalized_run_id,
            group_id=None,
            status=_normalize_runtime_status(run.get("status")),
            result=run.get("result"),
            error=run.get("error"),
            completed_at=_optional_string(run.get("completed_at")),
            session_id=returned_session_id or None,
        )

    async def _resume_legacy_group(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        bcs = self._require_legacy_bcs()
        driver = self._driver_identity(request)
        token = self._bot_token(driver)
        if not token:
            raise ContextCollabExecutorError(
                "Context executor Bot token is required for legacy BCS session resume"
            )
        message = self._delivery_message(
            request=request,
            instruction=request.message,
            resume=True,
        )
        try:
            result = await bcs.send_session_chat(
                request.session_id,
                message=message,
                caller_bearer=token,
                idempotency_key=request.interaction_event_id,
            )
        except BcsClientError as exc:
            raise ContextCollabExecutorError(
                "Context executor legacy session resume failed"
            ) from exc
        returned_session_id = str(result.get("session_id") or "").strip()
        if returned_session_id and returned_session_id != request.session_id:
            raise ContextCollabExecutorError(
                "Context executor legacy session resume returned a different session"
            )
        return ContextCollabRunResult(
            run_id=None,
            group_id=request.group_id,
            session_id=request.session_id,
        )

    async def _get_legacy_group_run(
        self,
        *,
        group_id: str,
        session_id: str | None,
    ) -> ContextCollabRunStatus:
        bcs = self._require_legacy_bcs()
        try:
            group = await bcs.get_group(group_id)
        except BcsClientError as exc:
            raise ContextCollabExecutorError(
                "Context executor legacy group status lookup failed"
            ) from exc
        session = group.get("session") or {}
        if not isinstance(session, dict):
            raise ContextCollabExecutorError(
                "Context executor legacy group returned an invalid session snapshot"
            )
        returned_session_id = str(session.get("session_id") or session_id or "").strip()
        if session_id and returned_session_id and returned_session_id != session_id:
            raise ContextCollabExecutorError(
                "Context executor legacy group returned a different session"
            )
        return ContextCollabRunStatus(
            run_id=None,
            group_id=group_id,
            status=_normalize_runtime_status(session.get("status")),
            result=session.get("output"),
            error=session.get("error_message") or session.get("error"),
            completed_at=_optional_string(session.get("completed_at")),
            session_id=returned_session_id or None,
        )

    def _require_legacy_bcs(self) -> BcsClientPort:
        if self._bcs is None:
            raise ContextCollabExecutorError(
                "Legacy BCS Context runtime is unavailable"
            )
        return self._bcs

    def _driver_identity(
        self, request: ContextCollabRunRequest | ContextCollabResumeRequest
    ) -> str:
        if not request.executor_owner_id:
            raise ContextCollabExecutorError(
                "Context executor owner is required for Bot addressing"
            )
        return compose_bot_identity(
            request.executor_id,
            request.executor_owner_id,
        )

    def _bot_token(self, driver: str) -> str | None:
        if self._bot_tokens is None:
            return None
        return self._bot_tokens.get_token(driver)

    @staticmethod
    def _runtime_metadata(
        request: ContextCollabRunRequest | ContextCollabResumeRequest,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "source": "todo",
            "biz_scene": "todo",
            "biz_task_id": request.todo_id,
            "todo_id": request.todo_id,
            "collab_context_id": request.collab_context_id,
        }
        if request.todo_execution_id:
            metadata["todo_execution_id"] = request.todo_execution_id
        return metadata

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
        runner_path = request.todo_runner_skill_md_path
        if not runner_path:
            raise ContextCollabExecutorError(
                "Todo runner Skill path is required for execution delivery"
            )
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
            todo_runner_skill_md_path=runner_path,
        )


def _normalize_runtime_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _RUNNING_STATUSES:
        return "RUNNING"
    if normalized in _COMPLETED_STATUSES:
        return "COMPLETED"
    if normalized in _FAILED_STATUSES:
        return "FAILED"
    return normalized.upper()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


class UnavailableContextCollabExecutor:
    """Fail closed when this deployment has no Task Open API Bot port."""

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
