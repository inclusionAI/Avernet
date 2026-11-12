"""Community Context runtime backed by the existing single-Bot OpenAPI port."""

from __future__ import annotations

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotError,
    OpenApiBotPort,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
    ContextCollabResumeRequest,
    ContextCollabRunRequest,
    ContextCollabRunResult,
    ContextCollabRunStatus,
    ContextCollabRuntimePlugin,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl


@plugin_impl(
    mode=Mode.PROD,
    rationale="reuse the deployment-owned single-Bot OpenAPI runtime",
)
class CommunityContextCollabRuntimePlugin(ContextCollabRuntimePlugin):
    """Invoke the Context executor while keeping lifecycle policy in Core."""

    def __init__(self, openapi_bot: OpenApiBotPort) -> None:
        self._openapi_bot = openapi_bot

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        if not request.executor_owner_id:
            raise ContextCollabRuntimeError(
                "Context executor owner is required for Bot runtime addressing"
            )
        try:
            result = await self._openapi_bot.send_message(
                bot_id=compose_bot_identity(
                    request.executor_id, request.executor_owner_id
                ),
                message=request.objective,
                metadata=_runtime_metadata(request),
            )
        except OpenApiBotError as exc:
            raise ContextCollabRuntimeError("Context executor delivery failed") from exc
        return ContextCollabRunResult(
            run_id=result.run_id,
            session_id=result.session_id,
        )

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        if not request.executor_owner_id:
            raise ContextCollabRuntimeError(
                "Context executor owner is required for Bot runtime addressing"
            )
        try:
            result = await self._openapi_bot.send_message(
                bot_id=compose_bot_identity(
                    request.executor_id, request.executor_owner_id
                ),
                message=request.message,
                metadata=_runtime_metadata(request),
                message_id=request.interaction_event_id,
            )
        except OpenApiBotError as exc:
            raise ContextCollabRuntimeError(
                "Context executor delivery failed"
            ) from exc
        return ContextCollabRunResult(
            run_id=result.run_id,
            session_id=result.session_id or request.session_id,
        )

    async def get_run(self, run_id: str) -> ContextCollabRunStatus:
        try:
            payload = await self._openapi_bot.get_run(run_id)
        except OpenApiBotError as exc:
            raise ContextCollabRuntimeError(
                "Context executor status lookup failed"
            ) from exc
        return ContextCollabRunStatus(
            run_id=run_id,
            status=str(payload.get("status") or "").strip().upper(),
            result=payload.get("result"),
            error=payload.get("error"),
            completed_at=payload.get("completed_at"),
            session_id=payload.get("session_id"),
        )


def _runtime_metadata(request: ContextCollabRunRequest | ContextCollabResumeRequest) -> dict[str, str]:
    metadata = {
        "biz_task_id": request.todo_id,
        "todo_id": request.todo_id,
        "collab_context_id": request.collab_context_id,
        "todo_report_path": f"/api/v1/collaboration/todos/{request.todo_id}/report",
        "todo_upload_path": f"/api/v1/collaboration/todos/{request.todo_id}/artifacts/object-file",
    }
    if isinstance(request, ContextCollabResumeRequest):
        metadata["session_id"] = request.session_id
        metadata["interaction_event_id"] = request.interaction_event_id
    if request.todo_execution_id:
        metadata["todo_execution_id"] = request.todo_execution_id
    if request.todo_execution_token:
        metadata["todo_execution_token"] = request.todo_execution_token
    return metadata


class UnavailableContextCollabRuntimePlugin(ContextCollabRuntimePlugin):
    """Fail-closed runtime used when the deployment has no Bot port."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        raise ContextCollabRuntimeError(
            "Context collaboration runtime is unavailable: " + self._reason
        )

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        raise ContextCollabRuntimeError(
            "Context collaboration runtime is unavailable: " + self._reason
        )

    async def get_run(self, run_id: str) -> ContextCollabRunStatus:
        raise ContextCollabRuntimeError(
            "Context collaboration runtime is unavailable: " + self._reason
        )
