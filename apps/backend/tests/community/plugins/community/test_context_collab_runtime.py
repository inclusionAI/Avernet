"""Tests for the production BCS Context collaboration runtime adapter."""

from __future__ import annotations

import uuid

import pytest

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError,
    BcsCreateGroupResult,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabResumeRequest,
    ContextCollabRunRequest,
    ContextCollabRuntimeError,
)
from agentclaw.community.plugins.community.context_collab_runtime import (
    CommunityContextCollabRuntimePlugin,
)


class _RecordingBcs:
    def __init__(
        self,
        *,
        create_error: Exception | None = None,
        chat_error: Exception | None = None,
        group_error: Exception | None = None,
        create_result: BcsCreateGroupResult | None = None,
        group_payload: dict | None = None,
        chat_result: dict | None = None,
    ) -> None:
        self.create_error = create_error
        self.chat_error = chat_error
        self.group_error = group_error
        self.create_result = create_result or BcsCreateGroupResult(
            group_id="group_1",
            session_id="session_1",
        )
        self.group_payload = group_payload or {
            "session": {"session_id": "session_1", "status": "running"}
        }
        self.chat_result = chat_result or {"session_id": "session_1"}
        self.create_calls = []
        self.chat_calls = []
        self.get_group_calls: list[str] = []

    async def create_group(self, request):
        self.create_calls.append(request)
        if self.create_error is not None:
            raise self.create_error
        return self.create_result

    async def send_session_chat(self, session_id, **kwargs):
        self.chat_calls.append((session_id, kwargs))
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat_result

    async def get_group(self, group_id):
        self.get_group_calls.append(group_id)
        if self.group_error is not None:
            raise self.group_error
        return self.group_payload


class _Tokens:
    def __init__(self, token: str | None = "bot-token") -> None:
        self.token = token
        self.calls: list[str] = []

    def get_token(self, bot_uuid: str) -> str | None:
        self.calls.append(bot_uuid)
        return self.token


def _request(*, owner_id: str | None = "another_user") -> ContextCollabRunRequest:
    return ContextCollabRunRequest(
        collab_context_id="context_1",
        executor_id="bot_1",
        principal_id="user_1",
        executor_owner_id=owner_id,
        objective="Write the report",
        todo_id="todo_1",
        todo_execution_id="todoexec_1",
        todo_execution_token="todo-token-1",
    )


def _resume_request(*, group_id: str | None = "group_1") -> ContextCollabResumeRequest:
    return ContextCollabResumeRequest(
        collab_context_id="context_1",
        executor_id="bot_1",
        principal_id="user_1",
        todo_id="todo_1",
        session_id="session_1",
        group_id=group_id,
        message="Please revise the implementation",
        interaction_event_id="interaction_1",
        executor_owner_id="another_user",
        todo_execution_id="todoexec_2",
        todo_execution_token="todo-token-2",
    )


def _runtime(
    bcs: _RecordingBcs,
    *,
    tokens: _Tokens | None = None,
    backend_url: str = "https://backend.example/",
) -> CommunityContextCollabRuntimePlugin:
    return CommunityContextCollabRuntimePlugin(
        bcs,
        bot_tokens=tokens,
        backend_url=backend_url,
    )


@pytest.mark.asyncio
async def test_run_creates_single_manager_bot_and_human_observer_group() -> None:
    bcs = _RecordingBcs()
    tokens = _Tokens()
    runtime = _runtime(bcs, tokens=tokens)

    result = await runtime.run(_request())

    assert result.run_id is None
    assert result.group_id == "group_1"
    assert result.session_id == "session_1"
    assert len(bcs.create_calls) == 1
    request = bcs.create_calls[0]
    assert request.group_strategy == "manager_worker"
    assert request.driver_bot == "bot_1:another_user"
    assert request.master_bot == "bot_1:another_user"
    assert request.participants == [
        {"bot_uuid": "bot_1:another_user", "role": "manager"},
        {
            "bot_uuid": "human_user_1",
            "bot_name": "user_1",
            "role": "observer",
        },
    ]
    assert all(item["bot_uuid"] != "default" for item in request.participants)
    assert request.routing_policy == {"default_bot_final_delivery": "inject_observers"}
    assert request.caller_bot_token == "bot-token"
    assert request.idempotency_key == str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "avernet-todo-context:context_1",
        )
    )
    assert tokens.calls == ["bot_1:another_user"]


@pytest.mark.asyncio
async def test_run_context_contains_complete_protocol_without_default_skill_dependency() -> (
    None
):
    bcs = _RecordingBcs()
    runtime = _runtime(bcs, tokens=_Tokens())

    await runtime.run(_request())

    context = bcs.create_calls[0].context
    assert "[todo-execute]" in context
    assert "todo_id=todo_1" in context
    assert "todo_execution_id=todoexec_1" in context
    assert (
        "POST https://backend.example/api/v1/collaboration/todos/todo_1/report"
        in context
    )
    assert "不依赖、也不得假定已默认加载 Todo Skill" in context
    assert "按需加载机制" in context
    assert "软链接" in context


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_id", [None, ""])
async def test_run_rejects_missing_executor_owner_before_bcs(
    owner_id: str | None,
) -> None:
    bcs = _RecordingBcs()
    runtime = _runtime(bcs, tokens=_Tokens())

    with pytest.raises(ContextCollabRuntimeError, match="owner"):
        await runtime.run(_request(owner_id=owner_id))

    assert bcs.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_result",
    [
        BcsCreateGroupResult(group_id="", session_id="session_1"),
        BcsCreateGroupResult(group_id="group_1", session_id=None),
    ],
)
async def test_run_fails_closed_without_real_group_session_handle(
    create_result: BcsCreateGroupResult,
) -> None:
    runtime = _runtime(
        _RecordingBcs(create_result=create_result),
        tokens=_Tokens(),
    )

    with pytest.raises(ContextCollabRuntimeError, match="group/session"):
        await runtime.run(_request())


@pytest.mark.asyncio
async def test_run_translates_known_bcs_failure() -> None:
    error = BcsClientError("unavailable")
    runtime = _runtime(_RecordingBcs(create_error=error), tokens=_Tokens())

    with pytest.raises(ContextCollabRuntimeError, match="creation") as exc_info:
        await runtime.run(_request())

    assert exc_info.value.__cause__ is error


@pytest.mark.asyncio
async def test_run_does_not_hide_unexpected_programming_error() -> None:
    runtime = _runtime(
        _RecordingBcs(create_error=AssertionError("adapter bug")),
        tokens=_Tokens(),
    )

    with pytest.raises(AssertionError, match="adapter bug"):
        await runtime.run(_request())


@pytest.mark.asyncio
async def test_resume_uses_same_session_bot_bearer_and_event_id() -> None:
    bcs = _RecordingBcs()
    runtime = _runtime(bcs, tokens=_Tokens("session-token"))

    result = await runtime.resume(_resume_request())

    assert result.run_id is None
    assert result.group_id == "group_1"
    assert result.session_id == "session_1"
    assert bcs.create_calls == []
    assert len(bcs.chat_calls) == 1
    session_id, call = bcs.chat_calls[0]
    assert session_id == "session_1"
    assert call["caller_bearer"] == "session-token"
    assert call["idempotency_key"] == "interaction_1"
    assert "[todo-resume]" in call["message"]
    assert "本轮修订要求：Please revise the implementation" in call["message"]
    assert "todo_execution_id=todoexec_2" in call["message"]


@pytest.mark.asyncio
async def test_resume_requires_existing_group_and_bot_token() -> None:
    bcs = _RecordingBcs()
    runtime = _runtime(bcs, tokens=_Tokens(None))

    with pytest.raises(ContextCollabRuntimeError, match="Bot token"):
        await runtime.resume(_resume_request())
    with pytest.raises(ContextCollabRuntimeError, match="group_id"):
        await runtime.resume(_resume_request(group_id=None))

    assert bcs.chat_calls == []


@pytest.mark.asyncio
async def test_resume_translates_known_bcs_failure() -> None:
    error = BcsClientError("unavailable")
    runtime = _runtime(
        _RecordingBcs(chat_error=error),
        tokens=_Tokens(),
    )

    with pytest.raises(ContextCollabRuntimeError, match="resume") as exc_info:
        await runtime.resume(_resume_request())

    assert exc_info.value.__cause__ is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire_status,expected",
    [
        ("active", "RUNNING"),
        ("completed", "COMPLETED"),
        ("aborted", "FAILED"),
    ],
)
async def test_get_group_status_normalizes_session_state(
    wire_status: str,
    expected: str,
) -> None:
    bcs = _RecordingBcs(
        group_payload={
            "session": {
                "session_id": "session_1",
                "status": wire_status,
                "output": {"content": "done"},
                "error_message": "failed" if expected == "FAILED" else None,
                "completed_at": "2026-11-12T04:00:00Z",
            }
        }
    )
    runtime = _runtime(bcs)

    result = await runtime.get_run(
        group_id="group_1",
        session_id="session_1",
    )

    assert result.run_id is None
    assert result.group_id == "group_1"
    assert result.session_id == "session_1"
    assert result.status == expected
    assert result.result == {"content": "done"}
    assert result.completed_at == "2026-11-12T04:00:00Z"
    assert bcs.get_group_calls == ["group_1"]


@pytest.mark.asyncio
async def test_get_group_status_rejects_direct_run_handle() -> None:
    runtime = _runtime(_RecordingBcs())

    with pytest.raises(ContextCollabRuntimeError, match="group_id"):
        await runtime.get_run("run_1")
