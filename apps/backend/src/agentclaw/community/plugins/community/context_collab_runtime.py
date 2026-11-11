"""Community Context runtime backed by the existing single-Bot OpenAPI port."""

from __future__ import annotations

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotError,
    OpenApiBotPort,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
    ContextCollabRunRequest,
    ContextCollabRunResult,
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

    async def run(
        self, request: ContextCollabRunRequest
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
                message=request.objective,
                metadata={
                    "biz_task_id": request.todo_id,
                    "collab_context_id": request.collab_context_id,
                },
            )
        except OpenApiBotError as exc:
            raise ContextCollabRuntimeError(
                "Context executor delivery failed"
            ) from exc
        return ContextCollabRunResult(
            run_id=result.run_id,
            session_id=result.session_id,
        )


class UnavailableContextCollabRuntimePlugin(ContextCollabRuntimePlugin):
    """Fail-closed runtime used when the deployment has no Bot port."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def run(
        self, request: ContextCollabRunRequest
    ) -> ContextCollabRunResult:
        raise ContextCollabRuntimeError(
            "Context collaboration runtime is unavailable: " + self._reason
        )
