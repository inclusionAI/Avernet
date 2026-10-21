from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import TaskOpResult
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    BcsClientPort,
    OpenApiBotPort,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    build_benchmark_task_launch_message,
)


class _StubService:
    def __init__(self, result: TaskOpResult | None = None) -> None:
        self.result = result or TaskOpResult(
            task_id="task-1",
            node_id="task-1",
            success=True,
            run_id=7,
            extend_props={
                "orchestration_mode": "relay",
                "root_execution_instruction": "ROOT --> INSTRUCTION",
            },
        )
        self.requests: list[Any] = []

    async def execute(self, request):
        self.requests.append(request)
        return self.result


class _StubBotPort:
    def __init__(self) -> None:
        self.send_calls: list[dict[str, Any]] = []
        self.ensure_calls: list[str] = []
        self.fail_calls: set[int] = set()

    async def ensure_grant(self, bot_id: str) -> None:
        self.ensure_calls.append(bot_id)

    async def send_message(
        self, *, bot_id: str, message: str, metadata: dict[str, Any],
        message_id: str | None = None,
    ) -> BotSendResult:
        call_no = len(self.send_calls) + 1
        self.send_calls.append(
            {
                "bot_id": bot_id,
                "message": message,
                "metadata": metadata,
                "message_id": message_id,
            }
        )
        if call_no in self.fail_calls:
            raise RuntimeError("openapi send failed")
        return BotSendResult(
            run_id=f"openapi-run-{call_no}",
            session_id="benchmark-session-1",
        )


class _StubBcsClient:
    def __init__(self) -> None:
        self.send_calls: list[dict[str, Any]] = []
        self.fail_calls: set[int] = set()

    async def send_message_a2a(
        self,
        *,
        target_bot_id: str,
        target_user_id: str,
        message: str,
        session_id: str | None = None,
        caller_bearer: str | None = None,
        idempotency_key: str | None = None,
    ) -> BotSendResult:
        call_no = len(self.send_calls) + 1
        self.send_calls.append(
            {
                "target_bot_id": target_bot_id,
                "target_user_id": target_user_id,
                "message": message,
                "session_id": session_id,
                "caller_bearer": caller_bearer,
                "idempotency_key": idempotency_key,
            }
        )
        if call_no in self.fail_calls:
            raise RuntimeError("a2a send failed")
        return BotSendResult(
            run_id=f"a2a-run-{call_no}", session_id=session_id
        )

    async def task_callback_url(self) -> str:
        return "http://task-callback"


class _TokenProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_token(self, bot_id: str) -> str | None:
        self.calls.append(bot_id)
        return "caller-token" if bot_id == "proxy-bot:owner-1" else None


class _BenchmarkModule(Module):
    def __init__(
        self,
        service: _StubService,
        bot_port: _StubBotPort,
        bcs_client: _StubBcsClient,
    ) -> None:
        self._service = service
        self._bot_port = bot_port
        self._bcs_client = bcs_client
        self._tokens = _TokenProvider()

    @singleton
    @provider
    def service(self) -> TaskServiceProtocol:
        return self._service  # type: ignore[return-value]

    @singleton
    @provider
    def bot_port(self) -> OpenApiBotPort:
        return self._bot_port  # type: ignore[return-value]

    @singleton
    @provider
    def bcs_client(self) -> BcsClientPort:
        return self._bcs_client  # type: ignore[return-value]

    @singleton
    @provider
    def tokens(self) -> BcsBotTokenProvider:
        return self._tokens  # type: ignore[return-value]


def _client(
    service: _StubService,
    bot_port: _StubBotPort,
    bcs_client: _StubBcsClient,
) -> TestClient:
    injector = Injector([_BenchmarkModule(service, bot_port, bcs_client)])
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, injector)
    return TestClient(app)


def _body(**execution_config: Any) -> dict[str, Any]:
    return {
        "task_spec": {
            "context": {
                "title": "存储行业尽调",
                "background": "AI 基础设施驱动存储行业变化",
                "extend_props": {"deliverables": ["尽调报告"]},
            },
            "goal": {
                "objective": "完成存储行业尽调",
                "acceptances": [{"id": "ac1", "acceptance": "形成投资判断"}],
            },
        },
        "source_type": "bot",
        "owner_user_id": "owner-1",
        "owner_bot_id": "bot-1",
        "execution_config": {
            "task_type": "dynamic",
            "orchestration_mode": "relay",
            **execution_config,
        },
    }


class _AixUIParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str | None, str | None]]) -> None:
        if tag.lower() == "aixui":
            self.attrs = {str(key): value or "" for key, value in attrs if key}


class TestBenchmarkExecute:
    def test_openapi_branch_executes_in_the_created_benchmark_session(self):
        service = _StubService()
        bot_port = _StubBotPort()
        client = _client(service, bot_port, _StubBcsClient())

        response = client.post(
            "/api/v1/collaboration/tasks/benchmark_execute", json=_body()
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["task_id"] == "task-1"
        assert data["node_id"] == "task-1"
        assert data["run_id"] == 7
        assert data["success"] is True
        assert data["extend_props"]["benchmark_session_id"] == "benchmark-session-1"
        assert data["extend_props"]["benchmark_launch_run_id"] == "openapi-run-2"

        assert len(service.requests) == 1
        assert service.requests[0].execution_config["main_session_id"] == "benchmark-session-1"
        assert service.requests[0].execution_config["lifecycle_stage"] == "draft"
        assert service.requests[0].owner_bot_id == "bot-1"
        assert service.requests[0].owner_user_id == "owner-1"

        assert bot_port.ensure_calls == ["bot-1:owner-1"]
        assert len(bot_port.send_calls) == 2
        assert bot_port.send_calls[0]["metadata"] == {
            "benchmark": True,
            "sender_options": {"from": "owner-1"},
            "bot_options": {"lifecycle_stage": "draft"},
        }
        assert bot_port.send_calls[1]["metadata"] == {
            "benchmark": True,
            "session_id": "benchmark-session-1",
            "sender_options": {"from": "owner-1"},
            "bot_options": {"lifecycle_stage": "draft"},
        }
        launch = bot_port.send_calls[1]["message"]
        assert "taskPanel.TaskLoopView" in launch
        assert "ROOT --\u200b> INSTRUCTION" in launch
        assert launch.endswith("-->")

        parser = _AixUIParser()
        parser.feed(launch)
        assert parser.attrs["component"] == "taskPanel.TaskLoopView"
        assert parser.attrs["params"] == (
            '{"taskId":"task-1","apiBaseUrl":"","bcsBaseUrl":"","userId":"owner-1"}'
        )

    def test_a2a_branch_rebinds_owner_and_reuses_one_session(self):
        service = _StubService()
        bot_port = _StubBotPort()
        bcs_client = _StubBcsClient()
        client = _client(service, bot_port, bcs_client)

        response = client.post(
            "/api/v1/collaboration/tasks/benchmark_execute",
            json={
                **_body(
                    target_bot_id="target-bot",
                    target_user_id="target-user",
                ),
                "owner_bot_id": "proxy-bot",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is True
        assert data["task_id"] == "task-1"
        assert data["extend_props"]["benchmark_channel"] == "a2a"
        assert data["extend_props"]["benchmark_launch_run_id"] == "a2a-run-2"

        assert bot_port.send_calls == []
        assert len(bcs_client.send_calls) == 2
        first, second = bcs_client.send_calls
        assert first["target_bot_id"] == "target-bot"
        assert first["target_user_id"] == "target-user"
        assert first["caller_bearer"] == "caller-token"
        assert second["session_id"] == first["session_id"]
        assert second["caller_bearer"] == "caller-token"
        assert "ROOT --\u200b> INSTRUCTION" in second["message"]

        assert len(service.requests) == 1
        assert service.requests[0].owner_bot_id == "target-bot"
        assert service.requests[0].owner_user_id == "target-user"
        assert service.requests[0].execution_config["main_session_id"] == first["session_id"]
        assert "lifecycle_stage" not in service.requests[0].execution_config

    def test_centralized_task_sends_panel_without_fake_root_instruction(self):
        service = _StubService(
            TaskOpResult(
                task_id="task-2",
                node_id="task-2",
                success=True,
                run_id=8,
                extend_props={"orchestration_mode": "centralized"},
            )
        )
        bot_port = _StubBotPort()
        client = _client(service, bot_port, _StubBcsClient())

        response = client.post(
            "/api/v1/collaboration/tasks/benchmark_execute",
            json=_body(main_session_id="caller-session"),
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["success"] is True
        assert len(bot_port.send_calls) == 2
        assert bot_port.send_calls[1]["message"].startswith("<AixUI")
        assert "<!--" not in bot_port.send_calls[1]["message"]

    def test_execute_failure_does_not_send_a_launch_message(self):
        service = _StubService(
            TaskOpResult(
                task_id="task-failed",
                success=False,
                run_id=0,
                error="persist failed",
                extend_props={},
            )
        )
        bot_port = _StubBotPort()
        client = _client(service, bot_port, _StubBcsClient())

        response = client.post(
            "/api/v1/collaboration/tasks/benchmark_execute", json=_body()
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is False
        assert data["message"] == "persist failed"
        assert data["extend_props"]["benchmark_send"] == "ok"
        assert len(bot_port.send_calls) == 1

    def test_launch_failure_is_not_reported_as_success(self):
        service = _StubService()
        bot_port = _StubBotPort()
        bot_port.fail_calls = {2}
        client = _client(service, bot_port, _StubBcsClient())

        response = client.post(
            "/api/v1/collaboration/tasks/benchmark_execute", json=_body()
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is False
        assert "benchmark task launch send failed" in data["message"]
        assert "openapi send failed" in data["extend_props"]["benchmark_launch_send"]
        assert data["task_id"] == "task-1"

    def test_prompt_failure_does_not_create_task(self):
        service = _StubService()
        bot_port = _StubBotPort()
        bot_port.fail_calls = {1}
        client = _client(service, bot_port, _StubBcsClient())

        response = client.post(
            "/api/v1/collaboration/tasks/benchmark_execute", json=_body()
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is False
        assert data["task_id"] == ""
        assert service.requests == []
        assert "benchmark prompt send failed" in data["message"]

    def test_hidden_root_instruction_cannot_close_the_comment_early(self):
        message = build_benchmark_task_launch_message(
            task_id="task-1",
            owner_user_id="owner-1",
            task_spec={
                "context": {"title": "存储行业'尽调\"`"},
                "goal": {"objective": "完成尽调"},
            },
            root_execution_instruction="prefix --> --!> suffix",
        )

        # The only legal comment close is the final terminator appended by the helper.
        assert re.findall(r"--!?>", message) == ["-->"]
        assert "--\u200b>" in message
        assert "--\u200b!>" in message
