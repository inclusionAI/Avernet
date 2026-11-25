from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskNotFoundError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiError,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotPort,
)


def _root_node(
    task_id: str,
    *,
    session_id: str | None = "sess-1",
    assignee: str | None = "bot-1",
    assignee_owner_id: str | None = "owner-1",
) -> TaskNode:
    """Root 节点(node_id==task_id)替真:session/assignee 落 run_info.extend_props。"""
    node = TaskNode(
        node_id=task_id,
        task_id=task_id,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context("bg", title="T"),
            goal=Goal("O", [AcceptanceCriteria("a1", "d")]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )
    node.run_info.assignee = assignee
    if session_id is not None:
        node.run_info.extend_props["session_id"] = session_id
    if assignee_owner_id is not None:
        node.run_info.extend_props["assignee_owner_id"] = assignee_owner_id
    return node


class _StubService:
    """最小 TaskServiceProtocol stub:router 仅用 get_task_dashboard。"""

    def __init__(self, nodes: list[TaskNode] | None) -> None:
        self._nodes = nodes
        self.dashboard_calls: list[tuple[str, Any]] = []

    def get_task_dashboard(self, task_id, node_id=None) -> TaskExecutionGraph:
        self.dashboard_calls.append((task_id, node_id))
        if self._nodes is None:
            raise TaskNotFoundError(f"task_id={task_id} 图不存在")
        graph = TaskExecutionGraph(run_id=1, loop_round=0, status=Status.RUNNING)
        graph.tasks.extend(self._nodes)
        return graph


class _StubBotPort:
    def __init__(self) -> None:
        self.send_calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def send_message(
        self, *, bot_id: str, message: str, metadata: dict[str, Any],
        message_id: str | None = None,
    ) -> BotSendResult:
        if self.error is not None:
            raise self.error
        self.send_calls.append(
            {
                "bot_id": bot_id,
                "message": message,
                "metadata": metadata,
                "message_id": message_id,
            }
        )
        return BotSendResult(run_id="wb-run-1", session_id="sess-1")


class _WritebackModule(Module):
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


def _client(
    service: _StubService, bot_port: _StubBotPort, *, raise_server_exceptions: bool = True,
) -> TestClient:
    injector = Injector([_WritebackModule(service, bot_port)])
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, injector)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


class TestResultWriteback:
    def test_sends_result_to_root_node_session(self):
        service = _StubService([_root_node("task-1")])
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/result_writeback",
            json={"task_id": "task-1", "message": "任务最终结果:报告已完成"},
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data == {
            "task_id": "task-1",
            "session_id": "sess-1",
            "run_id": "wb-run-1",
        }
        assert service.dashboard_calls == [("task-1", None)]
        assert len(bot_port.send_calls) == 1
        call = bot_port.send_calls[0]
        # 发送目标 = root 节点执行 bot 的复合身份,与 single_bot 派发同口径
        assert call["bot_id"] == compose_bot_identity("bot-1", "owner-1")
        assert call["message"] == "任务最终结果:报告已完成"
        # BaaS 侧按 metadata.session_id 续用原会话
        assert call["metadata"]["session_id"] == "sess-1"

    def test_root_without_session_id_is_rejected_without_sending(self):
        service = _StubService([_root_node("task-1", session_id=None)])
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/result_writeback",
            json={"task_id": "task-1", "message": "done"},
        )

        # TaskStateError → @envelope_errors 映射 409(信封 message 为映射表固定文案)
        assert response.status_code == 409, response.text
        payload = response.json()
        assert payload["code"] == 409000
        assert bot_port.send_calls == []

    def test_graph_without_root_node_is_rejected(self):
        # 无 node_id==task_id 的 root(异常图态)→ 409,不发送
        service = _StubService([])
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/result_writeback",
            json={"task_id": "task-1", "message": "done"},
        )

        assert response.status_code == 409, response.text
        assert response.json()["code"] == 409000
        assert bot_port.send_calls == []

    def test_unknown_task_is_not_found(self):
        service = _StubService(None)
        bot_port = _StubBotPort()
        client = _client(service, bot_port)

        response = client.post(
            "/api/v1/collaboration/tasks/result_writeback",
            json={"task_id": "no-such-task", "message": "done"},
        )

        # TaskNotFoundError → 信封 404(区别于 FastAPI 未路由的 {"detail": ...})
        assert response.status_code == 404, response.text
        payload = response.json()
        assert payload["code"] == 404000
        assert bot_port.send_calls == []

    def test_send_failure_is_not_swallowed(self):
        service = _StubService([_root_node("task-1")])
        bot_port = _StubBotPort()
        bot_port.error = OpenApiError("send_message 业务失败 code=1")
        # 未捕获异常经 raise_server_exceptions=False 以 500 返回(不静默成功)
        client = _client(service, bot_port, raise_server_exceptions=False)

        response = client.post(
            "/api/v1/collaboration/tasks/result_writeback",
            json={"task_id": "task-1", "message": "done"},
        )

        assert response.status_code == 500
