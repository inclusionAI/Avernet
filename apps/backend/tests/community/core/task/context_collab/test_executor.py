"""Tests for the Context collaboration BCS executor."""

from __future__ import annotations

import uuid

import pytest

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError,
    BcsCreateGroupResult,
)
from agentclaw.community.core.task.context_collab.executor import (
    ContextCollabExecutor,
    ContextCollabExecutorError,
    ContextCollabResumeRequest,
    ContextCollabRunRequest,
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


def _executor(
    bcs: _RecordingBcs,
    *,
    tokens: _Tokens | None = None,
    backend_url: str = "https://backend.example/",
) -> ContextCollabExecutor:
    return ContextCollabExecutor(
        bcs,
        bot_tokens=tokens,
        backend_url=backend_url,
    )


@pytest.mark.asyncio
async def test_run_creates_single_manager_bot_and_human_observer_group() -> None:
    bcs = _RecordingBcs()
    tokens = _Tokens()
    executor = _executor(bcs, tokens=tokens)

    result = await executor.run(_request())

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
async def test_run_context_loads_todo_runner_from_infra_without_symlink() -> None:
    bcs = _RecordingBcs()
    executor = _executor(bcs, tokens=_Tokens())

    await executor.run(_request())

    context = bcs.create_calls[0].context
    assert "[todo-execute]" in context
    assert "todo_id=todo_1" in context
    assert "todo_execution_id=todoexec_1" in context
    assert "todo_execution_token=todo-token-1" in context
    assert "backend_url=https://backend.example" in context
    assert "todo-runner Skill" in context
    assert (
        "/home/admin/.openclaw/workspace/skills/skills-repo/infra/"
        "todo-runner/SKILL.md"
    ) in context
    assert "每次收到 [todo-execute] 或 [todo-resume] 都必须" in context
    assert "不要为 todo-runner 创建软链接" in context
    assert "workspace/skills 顶层激活" in context
    assert "todo-runner skill unavailable" in context
    assert "请严格按照 todo-runner Skill 定义" in context
    assert "/report" not in context
    assert "/artifacts/object-file" not in context
    assert "/complete" not in context
    assert "X-Todo-Execution-Token:" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_id", [None, ""])
async def test_run_rejects_missing_executor_owner_before_bcs(
    owner_id: str | None,
) -> None:
    bcs = _RecordingBcs()
    executor = _executor(bcs, tokens=_Tokens())

    with pytest.raises(ContextCollabExecutorError, match="owner"):
        await executor.run(_request(owner_id=owner_id))

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
    executor = _executor(
        _RecordingBcs(create_result=create_result),
        tokens=_Tokens(),
    )

    with pytest.raises(ContextCollabExecutorError, match="group/session"):
        await executor.run(_request())


@pytest.mark.asyncio
async def test_run_translates_known_bcs_failure() -> None:
    error = BcsClientError("unavailable")
    executor = _executor(_RecordingBcs(create_error=error), tokens=_Tokens())

    with pytest.raises(ContextCollabExecutorError, match="creation") as exc_info:
        await executor.run(_request())

    assert exc_info.value.__cause__ is error


@pytest.mark.asyncio
async def test_run_does_not_hide_unexpected_programming_error() -> None:
    executor = _executor(
        _RecordingBcs(create_error=AssertionError("adapter bug")),
        tokens=_Tokens(),
    )

    with pytest.raises(AssertionError, match="adapter bug"):
        await executor.run(_request())


@pytest.mark.asyncio
async def test_resume_uses_same_session_bot_bearer_and_event_id() -> None:
    bcs = _RecordingBcs()
    executor = _executor(bcs, tokens=_Tokens("session-token"))

    result = await executor.resume(_resume_request())

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
    executor = _executor(bcs, tokens=_Tokens(None))

    with pytest.raises(ContextCollabExecutorError, match="Bot token"):
        await executor.resume(_resume_request())
    with pytest.raises(ContextCollabExecutorError, match="group_id"):
        await executor.resume(_resume_request(group_id=None))

    assert bcs.chat_calls == []


@pytest.mark.asyncio
async def test_resume_translates_known_bcs_failure() -> None:
    error = BcsClientError("unavailable")
    executor = _executor(
        _RecordingBcs(chat_error=error),
        tokens=_Tokens(),
    )

    with pytest.raises(ContextCollabExecutorError, match="resume") as exc_info:
        await executor.resume(_resume_request())

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
    executor = _executor(bcs)

    result = await executor.get_run(
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
    executor = _executor(_RecordingBcs())

    with pytest.raises(ContextCollabExecutorError, match="group_id"):
        await executor.get_run("run_1")
