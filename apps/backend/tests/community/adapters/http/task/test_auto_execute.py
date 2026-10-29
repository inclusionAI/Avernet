from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import TaskOpResult
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotPort,
)


class _StubService:
    def __init__(self, result: TaskOpResult | None = None) -> None:
        self.node_updates: list[dict[str, Any]] = []
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

    async def update_task_node_info(
        self, task_id, node_id, **kwargs
    ):
        self.node_updates.append({"task_id": task_id, "node_id": node_id, **kwargs})
        return TaskOpResult(task_id=task_id, node_id=node_id, success=True)


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
            session_id="auto-session-1",
        )


class _AutoModule(Module):
    def __init__(self, service: _StubService, bot_port: _StubBotPort) -> None:
        self._service = service
        self._bot_port = bot_port

    @singleton
    @provider
    def service(self) -> TaskServiceProtocol:
        return self._service  # type: ignore[return-value]

    @singleton
    @provider
    def bot_port(self) -> OpenApiBotPort:
        return self._bot_port  # type: ignore[return-value]


def _client(service: _StubService, bot_port: _StubBotPort) -> TestClient:
    injector = Injector([_AutoModule(service, bot_port)])
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


class TestAutoExecute:
    def test_sends_launch_in_existing_session(self):
        service = _StubService()
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/auto_execute",
            json=_body(main_session_id="auto-session-1"),
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["task_id"] == "task-1"
        assert data["node_id"] == "task-1"
        assert data["run_id"] == 7
        assert data["success"] is True
        assert data["extend_props"]["auto_session_id"] == "auto-session-1"
        assert data["extend_props"]["auto_run_id"] == "openapi-run-1"
        assert data["extend_props"]["auto_launch_send"] == "ok"

        # No first-stick prompt sent: only one bot_port call (the launch message).
        assert bot_port.ensure_calls == []
        assert len(bot_port.send_calls) == 1
        assert len(service.requests) == 1
        assert service.requests[0].execution_config["main_session_id"] == "auto-session-1"
        assert service.requests[0].execution_config["lifecycle_stage"] == "draft"
        assert service.requests[0].owner_bot_id == "bot-1"
        assert service.requests[0].owner_user_id == "owner-1"
        assert service.node_updates[-1] == {
            "task_id": "task-1",
            "node_id": "task-1",
            "extend_props_patch": {"run_id": "openapi-run-1"},
        }

        call = bot_port.send_calls[0]
        assert call["bot_id"] == "bot-1:owner-1"
        assert call["metadata"] == {
            "benchmark": False,
            "auto": True,
            "session_id": "auto-session-1",
            "sender_options": {"from": "owner-1"},
            "bot_options": {"lifecycle_stage": "draft"},
        }
        # The hidden Relay root instruction is appended as a comment wrapper,
        # and ">-->" inside the payload is zero-width-protected.
        assert "taskPanel.TaskLoopView" in call["message"]
        assert "ROOT --\u200b> INSTRUCTION" in call["message"]
        assert call["message"].endswith("-->")

    def test_centralized_sends_panel_without_hidden_root_instruction(self):
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
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/auto_execute",
            json=_body(main_session_id="auto-session-1"),
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["success"] is True
        assert len(bot_port.send_calls) == 1
        message = bot_port.send_calls[0]["message"]
        assert message.startswith("<AixUI")
        assert "taskPanel.TaskLoopView" in message
        assert "<!--" not in message
        assert service.node_updates[-1]["extend_props_patch"] == {"run_id": "openapi-run-1"}

    def test_missing_session_id_skips_execute_and_send(self):
        service = _StubService()
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        # body() intentionally omits main_session_id.
        response = client.post(
            "/api/v1/collaboration/tasks/auto_execute", json=_body()
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is False
        assert data["task_id"] == ""
        assert "requires main_session_id" in data["message"]
        assert data["extend_props"]["auto_session_id"] == ""
        assert "error: missing session_id" in data["extend_props"]["auto_launch_send"]

        assert service.requests == []
        assert bot_port.send_calls == []

    def test_relay_without_root_instruction_returns_error_without_send(self):
        service = _StubService(
            TaskOpResult(
                task_id="task-relay",
                node_id="task-relay",
                success=True,
                run_id=0,
                extend_props={
                    "orchestration_mode": "relay",
                    "root_execution_instruction": "",
                },
            )
        )
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/auto_execute",
            json=_body(main_session_id="auto-session-1"),
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is False
        assert "relay task creation returned no root execution instruction" in data["message"]
        assert "error: no root execution instruction" in data["extend_props"]["auto_launch_send"]
        assert bot_port.send_calls == []
        # Task was created (service.execute ran) but no run written back.
        assert service.node_updates == []

    def test_send_failure_keeps_task_but_marks_error(self):
        service = _StubService()
        bot_port = _StubBotPort()
        bot_port.fail_calls = {1}
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/auto_execute",
            json=_body(main_session_id="auto-session-1"),
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is False
        assert data["task_id"] == "task-1"
        assert "auto task launch send failed" in data["message"]
        assert "error: openapi send failed" in data["extend_props"]["auto_launch_send"]
        # Without a successful send there is nothing to write back.
        assert service.node_updates == []

    def test_lifecycle_stage_override_wins_over_default(self):
        service = _StubService()
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/auto_execute",
            json=_body(main_session_id="auto-session-1", lifecycle_stage="stable"),
        )

        assert response.status_code == 200, response.text
        assert service.requests[0].execution_config["lifecycle_stage"] == "stable"
        assert bot_port.send_calls[0]["metadata"]["bot_options"] == {
            "lifecycle_stage": "stable"
        }
