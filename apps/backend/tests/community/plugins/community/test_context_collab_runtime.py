"""Tests for the production ContextCollab Bot runtime adapter."""

from __future__ import annotations

import pytest

from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotError,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
    ContextCollabRunRequest,
)
from agentclaw.community.plugins.community.context_collab_runtime import (
    CommunityContextCollabRuntimePlugin,
)


class _RecordingOpenApiBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return BotSendResult(run_id="run_1", session_id="session_1")


def _request(*, owner_id: str | None = "another_user") -> ContextCollabRunRequest:
    return ContextCollabRunRequest(
        collab_context_id="context_1",
        executor_id="bot_1",
        principal_id="user_1",
        executor_owner_id=owner_id,
        objective="Write the report",
        todo_id="todo_1",
    )


@pytest.mark.asyncio
async def test_runtime_addresses_bot_by_real_owner_not_todo_principal() -> None:
    openapi_bot = _RecordingOpenApiBot()
    runtime = CommunityContextCollabRuntimePlugin(openapi_bot)

    result = await runtime.run(_request())

    assert result.run_id == "run_1"
    assert result.session_id == "session_1"
    assert openapi_bot.calls == [
        {
            "bot_id": "bot_1:another_user",
            "message": "Write the report",
            "metadata": {
                "biz_task_id": "todo_1",
                "collab_context_id": "context_1",
            },
        }
    ]
    assert openapi_bot.calls[0]["bot_id"] != "bot_1:user_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_id", [None, ""])
async def test_runtime_rejects_missing_owner_without_invoking_bot(
    owner_id: str | None,
) -> None:
    openapi_bot = _RecordingOpenApiBot()
    runtime = CommunityContextCollabRuntimePlugin(openapi_bot)

    with pytest.raises(ContextCollabRuntimeError, match="owner"):
        await runtime.run(_request(owner_id=owner_id))

    assert openapi_bot.calls == []


@pytest.mark.asyncio
async def test_runtime_translates_bot_port_failure() -> None:
    delivery_error = OpenApiBotError("upstream unavailable")
    openapi_bot = _RecordingOpenApiBot(delivery_error)
    runtime = CommunityContextCollabRuntimePlugin(openapi_bot)

    with pytest.raises(ContextCollabRuntimeError, match="delivery") as exc_info:
        await runtime.run(_request())

    assert exc_info.value.__cause__ is delivery_error
    assert len(openapi_bot.calls) == 1


@pytest.mark.asyncio
async def test_runtime_does_not_hide_unexpected_programming_error() -> None:
    programming_error = AssertionError("unexpected adapter bug")
    runtime = CommunityContextCollabRuntimePlugin(
        _RecordingOpenApiBot(programming_error)
    )

    with pytest.raises(AssertionError, match="unexpected adapter bug"):
        await runtime.run(_request())
