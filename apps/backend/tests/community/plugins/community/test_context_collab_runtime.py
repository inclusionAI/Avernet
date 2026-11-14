"""Tests for the production ContextCollab Bot runtime adapter."""

from __future__ import annotations

import pytest

from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotError,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
    ContextCollabResumeRequest,
    ContextCollabRunRequest,
)
from agentclaw.community.plugins.community.context_collab_runtime import (
    CommunityContextCollabRuntimePlugin,
)


class _RecordingOpenApiBot:
    def __init__(
        self,
        error: Exception | None = None,
        *,
        run_payload: dict | None = None,
        get_run_error: Exception | None = None,
    ) -> None:
        self.calls = []
        self.error = error
        self.run_payload = run_payload or {"status": "RUNNING"}
        self.get_run_error = get_run_error
        self.get_run_calls: list[str] = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return BotSendResult(run_id="run_1", session_id="session_1")

    async def get_run(self, run_id: str):
        self.get_run_calls.append(run_id)
        if self.get_run_error is not None:
            raise self.get_run_error
        return self.run_payload


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
                "todo_id": "todo_1",
                "collab_context_id": "context_1",
                "sender_options": {"from": "user_1"},
                "todo_report_path": "/api/v1/collaboration/todos/todo_1/report",
                "todo_upload_path": "/api/v1/collaboration/todos/todo_1/artifacts/object-file",
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
async def test_runtime_resume_reuses_previous_session_and_interaction_id() -> None:
    openapi_bot = _RecordingOpenApiBot()
    runtime = CommunityContextCollabRuntimePlugin(openapi_bot)
    request = ContextCollabResumeRequest(
        collab_context_id="context_1",
        executor_id="bot_1",
        principal_id="user_1",
        todo_id="todo_1",
        session_id="session_previous",
        message="Please revise the implementation",
        interaction_event_id="interaction_1",
        executor_owner_id="another_user",
    )

    result = await runtime.resume(request)

    assert result.run_id == "run_1"
    assert result.session_id == "session_1"
    assert openapi_bot.calls == [
        {
            "bot_id": "bot_1:another_user",
            "message": "Please revise the implementation",
            "metadata": {
                "session_id": "session_previous",
                "biz_task_id": "todo_1",
                "todo_id": "todo_1",
                "collab_context_id": "context_1",
                "interaction_event_id": "interaction_1",
                "sender_options": {"from": "user_1"},
                "todo_report_path": "/api/v1/collaboration/todos/todo_1/report",
                "todo_upload_path": "/api/v1/collaboration/todos/todo_1/artifacts/object-file",
            },
            "message_id": "interaction_1",
        }
    ]


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


@pytest.mark.asyncio
async def test_get_run_normalizes_bot_port_payload() -> None:
    openapi_bot = _RecordingOpenApiBot(
        run_payload={
            "status": " completed ",
            "result": {"content": "done"},
            "error": None,
            "completed_at": "2026-11-12T04:00:00Z",
            "session_id": "session_1",
        }
    )
    runtime = CommunityContextCollabRuntimePlugin(openapi_bot)

    result = await runtime.get_run("run_1")

    assert result.run_id == "run_1"
    assert result.status == "COMPLETED"
    assert result.result == {"content": "done"}
    assert result.error is None
    assert result.completed_at == "2026-11-12T04:00:00Z"
    assert result.session_id == "session_1"
    assert openapi_bot.get_run_calls == ["run_1"]


@pytest.mark.asyncio
async def test_get_run_translates_bot_port_failure() -> None:
    delivery_error = OpenApiBotError("upstream unavailable")
    runtime = CommunityContextCollabRuntimePlugin(
        _RecordingOpenApiBot(get_run_error=delivery_error)
    )

    with pytest.raises(ContextCollabRuntimeError, match="status") as exc_info:
        await runtime.get_run("run_1")

    assert exc_info.value.__cause__ is delivery_error


@pytest.mark.asyncio
async def test_get_run_does_not_hide_unexpected_programming_error() -> None:
    runtime = CommunityContextCollabRuntimePlugin(
        _RecordingOpenApiBot(get_run_error=AssertionError("unexpected adapter bug"))
    )

    with pytest.raises(AssertionError, match="unexpected adapter bug"):
        await runtime.get_run("run_1")
