"""Tests for direct Todo execution and legacy group compatibility."""

from __future__ import annotations

import uuid

import pytest

from agentclaw.community.core.task.context_collab.executor import (
    ContextCollabExecutor,
    ContextCollabExecutorError,
    ContextCollabResumeRequest,
    ContextCollabRunRequest,
)
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotError,
)


class _RecordingOpenApiBot:
    def __init__(
        self,
        *,
        send_error: Exception | None = None,
        run_error: Exception | None = None,
        send_results: list[BotSendResult] | None = None,
        run_payload: dict | None = None,
    ) -> None:
        self.send_error = send_error
        self.run_error = run_error
        self.send_results = send_results or [
            BotSendResult(run_id="run_1", session_id="session_1")
        ]
        self.run_payload = run_payload or {
            "session_id": "session_1",
            "status": "running",
        }
        self.send_calls: list[dict] = []
        self.get_run_calls: list[str] = []

    @property
    def api_key_prefix(self) -> str:
        return "test"

    async def send_message(self, **kwargs):
        self.send_calls.append(kwargs)
        if self.send_error is not None:
            raise self.send_error
        return self.send_results[len(self.send_calls) - 1]

    async def get_run(self, run_id: str):
        self.get_run_calls.append(run_id)
        if self.run_error is not None:
            raise self.run_error
        return self.run_payload


class _RecordingBcs:
    def __init__(
        self,
        *,
        chat_error: Exception | None = None,
        group_error: Exception | None = None,
        group_payload: dict | None = None,
        chat_result: dict | None = None,
    ) -> None:
        self.chat_error = chat_error
        self.group_error = group_error
        self.group_payload = group_payload or {
            "session": {"session_id": "session_1", "status": "running"}
        }
        self.chat_result = chat_result or {"session_id": "session_1"}
        self.chat_calls: list[tuple[str, dict]] = []
        self.get_group_calls: list[str] = []

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


RUNNER_PATH = (
    "/home/admin/.openclaw/workspace/skills/skills-repo/infra/todo-runner/SKILL.md"
)


def _request(
    *,
    owner_id: str | None = "another_user",
    todo_runner_skill_md_path: str = RUNNER_PATH,
) -> ContextCollabRunRequest:
    return ContextCollabRunRequest(
        collab_context_id="context_1",
        executor_id="bot_1",
        principal_id="user_1",
        executor_owner_id=owner_id,
        objective="Write the report",
        todo_id="todo_1",
        todo_execution_id="todoexec_1",
        todo_execution_token="todo-token-1",
        todo_runner_skill_md_path=todo_runner_skill_md_path,
    )


def _resume_request(
    *,
    group_id: str | None = None,
    todo_runner_skill_md_path: str = RUNNER_PATH,
) -> ContextCollabResumeRequest:
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
        todo_runner_skill_md_path=todo_runner_skill_md_path,
    )


def _executor(
    bot: _RecordingOpenApiBot | None = None,
    *,
    bcs: _RecordingBcs | None = None,
    tokens: _Tokens | None = None,
    backend_url: str = "https://backend.example/",
) -> ContextCollabExecutor:
    return ContextCollabExecutor(
        bot or _RecordingOpenApiBot(),
        bcs=bcs,
        bot_tokens=tokens,
        backend_url=backend_url,
    )


@pytest.mark.asyncio
async def test_run_delivers_directly_to_owner_authorized_bot() -> None:
    bot = _RecordingOpenApiBot()
    executor = _executor(bot)

    result = await executor.run(_request())

    assert result.run_id == "run_1"
    assert result.group_id is None
    assert result.session_id == "session_1"
    assert len(bot.send_calls) == 1
    call = bot.send_calls[0]
    assert call["bot_id"] == "bot_1:another_user"
    assert call["message_id"] == str(
        uuid.uuid5(uuid.NAMESPACE_URL, "avernet-todo-context:context_1")
    )
    assert call["metadata"] == {
        "source": "todo",
        "biz_scene": "todo",
        "biz_task_id": "todo_1",
        "todo_id": "todo_1",
        "collab_context_id": "context_1",
        "todo_execution_id": "todoexec_1",
    }
    assert "todo-token-1" not in repr(call["metadata"])


@pytest.mark.asyncio
async def test_run_message_contains_execution_context_and_runner_path() -> None:
    bot = _RecordingOpenApiBot()
    executor = _executor(bot)

    await executor.run(_request())

    message = bot.send_calls[0]["message"]
    assert "[todo-execute]" in message
    assert "todo_id=todo_1" in message
    assert "todo_execution_id=todoexec_1" in message
    assert "todo_execution_token=todo-token-1" in message
    assert "backend_url=https://backend.example" in message
    assert RUNNER_PATH in message
    assert "/report" not in message
    assert "/complete" not in message
    assert "X-Todo-Execution-Token:" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runner_path, unexpected_root",
    [
        (
            "/home/admin/.aicoding/workspace/skills/skills-repo/infra/todo-runner/SKILL.md",
            "/home/admin/.openclaw/workspace",
        ),
        (
            "/home/admin/.harness/workspace/skills/skills-repo/infra/todo-runner/SKILL.md",
            "/home/admin/.openclaw/workspace",
        ),
    ],
)
async def test_run_uses_executor_supplied_runner_skill_path(
    runner_path: str,
    unexpected_root: str,
) -> None:
    bot = _RecordingOpenApiBot()
    await _executor(bot).run(_request(todo_runner_skill_md_path=runner_path))

    message = bot.send_calls[0]["message"]
    assert message.count(runner_path) == 1
    assert unexpected_root not in message


@pytest.mark.asyncio
async def test_run_fails_before_delivery_without_runner_path_or_owner() -> None:
    bot = _RecordingOpenApiBot()
    executor = _executor(bot)

    with pytest.raises(ContextCollabExecutorError, match="runner Skill path"):
        await executor.run(_request(todo_runner_skill_md_path=""))
    with pytest.raises(ContextCollabExecutorError, match="owner"):
        await executor.run(_request(owner_id=None))

    assert bot.send_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        BotSendResult(run_id="", session_id="session_1"),
        BotSendResult(run_id="run_1", session_id=None),
    ],
)
async def test_run_fails_closed_without_real_run_and_session_handles(
    result: BotSendResult,
) -> None:
    executor = _executor(_RecordingOpenApiBot(send_results=[result]))

    with pytest.raises(ContextCollabExecutorError, match="run/session"):
        await executor.run(_request())


@pytest.mark.asyncio
async def test_run_translates_known_openapi_failure_only() -> None:
    known = OpenApiBotError("unavailable")
    with pytest.raises(ContextCollabExecutorError, match="direct delivery") as exc_info:
        await _executor(_RecordingOpenApiBot(send_error=known)).run(_request())
    assert exc_info.value.__cause__ is known

    with pytest.raises(AssertionError, match="adapter bug"):
        await _executor(
            _RecordingOpenApiBot(send_error=AssertionError("adapter bug"))
        ).run(_request())


@pytest.mark.asyncio
async def test_direct_resume_reuses_session_and_starts_new_run() -> None:
    bot = _RecordingOpenApiBot(
        send_results=[BotSendResult(run_id="run_2", session_id="session_1")]
    )
    result = await _executor(bot).resume(_resume_request())

    assert result.run_id == "run_2"
    assert result.group_id is None
    assert result.session_id == "session_1"
    call = bot.send_calls[0]
    assert call["bot_id"] == "bot_1:another_user"
    assert call["message_id"] == "interaction_1"
    assert call["metadata"]["session_id"] == "session_1"
    assert call["metadata"]["resumed_from_event_id"] == "interaction_1"
    assert "[todo-resume]" in call["message"]
    assert "本轮修订要求：Please revise the implementation" in call["message"]


@pytest.mark.asyncio
async def test_direct_resume_fails_closed_on_missing_run_or_changed_session() -> None:
    with pytest.raises(ContextCollabExecutorError, match="no run handle"):
        await _executor(
            _RecordingOpenApiBot(
                send_results=[BotSendResult(run_id="", session_id="session_1")]
            )
        ).resume(_resume_request())

    with pytest.raises(ContextCollabExecutorError, match="different session"):
        await _executor(
            _RecordingOpenApiBot(
                send_results=[BotSendResult(run_id="run_2", session_id="session_2")]
            )
        ).resume(_resume_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire_status,expected",
    [("active", "RUNNING"), ("completed", "COMPLETED"), ("aborted", "FAILED")],
)
async def test_get_direct_run_normalizes_status(
    wire_status: str, expected: str
) -> None:
    bot = _RecordingOpenApiBot(
        run_payload={
            "session_id": "session_1",
            "status": wire_status,
            "result": {"content": "done"},
            "error": "failed" if expected == "FAILED" else None,
            "completed_at": "2026-11-12T04:00:00Z",
        }
    )
    result = await _executor(bot).get_run("run_1", session_id="session_1")

    assert result.run_id == "run_1"
    assert result.group_id is None
    assert result.status == expected
    assert result.result == {"content": "done"}
    assert result.session_id == "session_1"
    assert bot.get_run_calls == ["run_1"]


@pytest.mark.asyncio
async def test_legacy_group_resume_and_status_remain_supported() -> None:
    bcs = _RecordingBcs(
        group_payload={
            "session": {
                "session_id": "session_1",
                "status": "completed",
                "output": {"content": "done"},
                "completed_at": "2026-11-12T04:00:00Z",
            }
        }
    )
    tokens = _Tokens("session-token")
    executor = _executor(bcs=bcs, tokens=tokens)

    resumed = await executor.resume(_resume_request(group_id="group_1"))
    status = await executor.get_run(group_id="group_1", session_id="session_1")

    assert resumed.run_id is None
    assert resumed.group_id == "group_1"
    assert resumed.session_id == "session_1"
    assert bcs.chat_calls[0][1]["caller_bearer"] == "session-token"
    assert bcs.chat_calls[0][1]["idempotency_key"] == "interaction_1"
    assert tokens.calls == ["bot_1:another_user"]
    assert status.status == "COMPLETED"
    assert status.result == {"content": "done"}
    assert bcs.get_group_calls == ["group_1"]


@pytest.mark.asyncio
async def test_legacy_group_requires_compatibility_ports() -> None:
    executor = _executor()

    with pytest.raises(ContextCollabExecutorError, match="Legacy BCS"):
        await executor.resume(_resume_request(group_id="group_1"))
    with pytest.raises(ContextCollabExecutorError, match="Legacy BCS"):
        await executor.get_run(group_id="group_1", session_id="session_1")


@pytest.mark.asyncio
async def test_legacy_group_translates_known_bcs_failure() -> None:
    error = BcsClientError("unavailable")
    executor = _executor(bcs=_RecordingBcs(chat_error=error), tokens=_Tokens())

    with pytest.raises(
        ContextCollabExecutorError, match="legacy session resume"
    ) as exc:
        await executor.resume(_resume_request(group_id="group_1"))
    assert exc.value.__cause__ is error
