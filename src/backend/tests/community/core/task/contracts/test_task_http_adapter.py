"""Task HTTP adapter 契约测试(Rule 25):execute → dashboard → callback/report 协议。

独立 TestClient + 小型 test injector(仅 TaskModule + BotDiscoverServiceProtocol stub),
不拉起 singlebox 全栈。验证:
- POST /api/task/execute 返 TaskOpResultDTO(success/run_id)
- GET  /api/task/dashboard 返 TaskExecutionGraphDTO(含节点/状态)
- POST /api/task/callback/report 返 {ok:true} 且翻态(N_overview PASS → DONE)

不验真实 plan/dispatch body(已在 test_executor_e2e 覆盖);此测聚焦 HTTP 边界协议正确。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.adapters.http.task.router import router as task_router
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, Status, TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


class _StubDiscoverModule(Module):
    """BotDiscoverServiceProtocol stub:search_by_keyword 返空(端口未激活,不阻断装配)。"""

    @singleton
    @provider
    def discover(self) -> BotDiscoverServiceProtocol:
        class _D:
            def search_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _D()  # type: ignore[return-value]


@pytest.fixture
def client():
    """独立 FastAPI app + test injector(TaskModule + stub discover)。"""
    from agentclaw.community.di.modules.task_module import TaskModule
    injector = Injector([TaskModule(), _StubDiscoverModule()])
    app = FastAPI()
    app.include_router(task_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def _task_info_dict(task_id="t_http") -> dict:
    return {
        "task_spec": {
            "metadata": {"task_id": task_id, "title": "存储尽调", "instruction": "produce DD"},
            "context": {"background": "存储行业", "extend_props": {}},
            "goal": {"objective": "产出尽调报告",
                     "acceptances": [{"id": "ac1", "description": "d1"}]},
        },
        "source_channel_type": "bot",
        "source_channel_id": "owner_bot",
        "execution_config": {"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    }


class TestTaskExecute:
    def test_execute_returns_op_result(self, client):
        c, _ = client
        r = c.post("/api/task/execute", json=_task_info_dict())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["data"]["task_id"] == "t_http"
        assert body["data"]["success"] is True
        assert body["data"]["run_id"] > 0


class TestTaskDashboard:
    def test_dashboard_returns_graph_structure(self, client):
        c, _ = client
        c.post("/api/task/execute", json=_task_info_dict())
        r = c.get("/api/task/dashboard", params={"task_id": "t_http"})
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["status"] in (Status.RUNNING.value, Status.PLANNING.value)
        assert any(n["node_id"] == "t_http" for n in body["tasks"])
        # 根节点 task_spec 字段透传
        root = next(n for n in body["tasks"] if n["node_id"] == "t_http")
        assert root["task_spec"]["metadata"]["task_id"] == "t_http"
        assert root["task_spec"]["goal"]["objective"] == "产出尽调报告"


class TestTaskCallbackReport:
    def test_callback_report_flips_state(self, client):
        """回投 protocol:先 execute(纯内核 stub 路径无端口 → 根 PENDING 不推进),
        再手动 add 一个 RUNNING 子节点 + 回投 PASS → 翻 DONE,验证回投入口可达。"""
        c, inj = client
        # execute 建图(根 PENDING)
        c.post("/api/task/execute", json=_task_info_dict())
        graph_svc = inj.get(TaskGraphService)
        # 手动建一个 RUNNING 子节点(backdoor:直接调 graph_svc),模拟引擎已派发
        from agentclaw.community.core.task.domain.models import TaskNode, RuntimeInfo
        child = TaskNode(
            node_id="N_http", task_id="t_http", status=Status.RUNNING,
            task_spec=TaskSpec(Metadata("t_http", "T", "i"), Context("bg"),
                               Goal("o", [AcceptanceCriteria("a1", "d1")])),
            run_info=RuntimeInfo(run_mode="single_bot", assignee="bot1"),
            node_run_graph=None,  # type: ignore[arg-type]
        )
        graph_svc.add_task_nodes([child], "t_http")  # 子节点以 RUNNING 入图(add 保留状态)
        # 回投 PASS(acceptance 驱动 RUNNING→DONE)
        r = c.post("/api/task/callback/report", json={
            "loop_task_id": "t_http::N_http",
            "workflow_type": "single_bot",
            "result": {"success": True, "data": "ok"},
        })
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {"ok": True}
        # 断言翻态
        g = graph_svc.query_task_dashboard("t_http")
        n = next(n for n in g.tasks if n.node_id == "N_http")
        assert n.status == Status.DONE, f"回投未翻 DONE: {n.status}"


class TestProtocolConformance:
    def test_task_service_protocol_bound(self, client):
        """TaskModule 注册了 TaskServiceProtocol / TaskLoopCallbackProtocol(供 router Injected)。"""
        c, inj = client
        from agentclaw.community.api.task.task_service import TaskServiceProtocol
        from agentclaw.community.api.task.task_loop_callback import TaskLoopCallbackProtocol
        assert isinstance(inj.get(TaskServiceProtocol), TaskServiceProtocol)
        assert isinstance(inj.get(TaskLoopCallbackProtocol), TaskLoopCallbackProtocol)
