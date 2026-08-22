"""Task HTTP adapter 契约测试(Rule 25):execute → dashboard → callback/report 协议。

独立 TestClient + 小型 test injector(仅 TaskModule + BotDiscoverServiceProtocol stub),
不拉起 singlebox 全栈。验证:
- POST /openapi/v1/collaboration/tasks/execute 返 TaskOpResultDTO(success/run_id)
- GET  /openapi/v1/collaboration/tasks/dashboard 返 TaskExecutionGraphDTO(含节点/状态)
- POST /api/v1/collaboration/tasks/callback/report 返 {ok:true} 且翻态(N_overview PASS → DONE)

不验真实 plan/dispatch body(已在 test_executor_e2e 覆盖);此测聚焦 HTTP 边界协议正确。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.repository.protocols.task import TaskInfoRepositoryProtocol
from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router
from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, Status, TaskInfo, TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


class _StubDiscoverModule(Module):
    """BotDiscover/BotPublic 服务端口 stub:search 返空(端口未激活,不阻断装配)。

    TaskModule.task_service 依赖 BotDiscoverServiceProtocol + BotPublicServiceProtocol(非 singlebox
    走 ``default`` 分支,bot_public 未实际使用但 DI 仍需绑定,否则 injector 直实例化 Protocol 抛 TypeError)。"""

    @singleton
    @provider
    def discover(self) -> BotDiscoverServiceProtocol:
        class _D:
            def search_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _D()  # type: ignore[return-value]

    @singleton
    @provider
    def bot_public(self) -> BotPublicServiceProtocol:
        class _B:
            def search_public_bots_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _B()  # type: ignore[return-value]

    @provider
    def task_info_repo(self) -> TaskInfoRepositoryProtocol:
        # HTTP 契约测聚焦边界协议,不验持久化;facade 构造需 protocol 绑定 → None 跳过 persist。
        return None  # type: ignore[return-value]


@pytest.fixture
def client():
    """独立 FastAPI app + test injector(TaskModule + stub discover)。"""
    from agentclaw.community.di.modules.task_module import TaskModule
    injector = Injector([TaskModule(), _StubDiscoverModule()])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def _task_info_dict() -> dict:
    """新扁平契约(TaskInfoRequestDTO):task_id 服务端生成,不在请求体。"""
    return {
        "task_spec": {
            "metadata": {"title": "存储尽调", "instruction": "produce DD"},
            "context": {"background": "存储行业", "extend_props": {}},
            "goal": {"objective": "产出尽调报告",
                     "acceptances": [{"id": "ac1", "acceptance": "d1"}]},
        },
        "source_type": "bot",
        "owner_user_id": "owner_user",
        "owner_bot_id": "owner_bot",
        "execution_config": {"task_type": "dynamic", "MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    }


def _execute_and_get_id(c) -> str:
    """POST execute → 返回服务端生成的 task_id(契约:task_id 不在请求体,服务端 uuid4)。"""
    r = c.post("/openapi/v1/collaboration/tasks/execute", json=_task_info_dict())
    assert r.status_code == 200, r.text
    return r.json()["data"]["task_id"]


class TestTaskExecute:
    def test_execute_returns_op_result(self, client):
        c, _ = client
        r = c.post("/openapi/v1/collaboration/tasks/execute", json=_task_info_dict())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 200000
        assert isinstance(body["data"]["task_id"], str) and body["data"]["task_id"]
        assert body["data"]["success"] is True
        assert body["data"]["run_id"] > 0


class TestTaskDashboard:
    def test_dashboard_returns_graph_structure(self, client):
        c, _ = client
        task_id = _execute_and_get_id(c)
        r = c.get("/openapi/v1/collaboration/tasks/dashboard", params={"task_id": task_id})
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        # stub 路径无 owner bot → 无法规划 → 根 gap 拆不出 → 图 HUNG(语义正确:无规划端口不假 done)
        assert body["status"] == Status.HUNG.value
        assert any(n["node_id"] == task_id for n in body["tasks"])
        # 根节点 task_spec 字段透传(task_id 服务端回填进 metadata)
        root = next(n for n in body["tasks"] if n["node_id"] == task_id)
        assert root["task_spec"]["metadata"]["task_id"] == task_id
        assert root["task_spec"]["goal"]["objective"] == "产出尽调报告"
        # include_action_log 默认关:action_log 不返回(空),避免常规查询 payload 膨胀
        assert root["run_info"]["action_log"] == []

    def test_dashboard_include_action_log_populates(self, client):
        c, _ = client
        task_id = _execute_and_get_id(c)
        r = c.get("/openapi/v1/collaboration/tasks/dashboard",
                  params={"task_id": task_id, "include_action_log": "true"})
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        root = next(n for n in body["tasks"] if n["node_id"] == task_id)
        # 根经历 plan(无规划端口 has_gap=T,children=[])→ HUNG:至少 1 条 plan + 1 条 transition
        actions = [e["action"] for e in root["run_info"]["action_log"]]
        assert "plan" in actions
        assert "transition" in actions
        # 示例事件 payload 含全量字段
        plan_ev = next(e for e in root["run_info"]["action_log"] if e["action"] == "plan")
        assert "children" in plan_ev["payload"] and "has_gap" in plan_ev["payload"]
        assert plan_ev["seq"] >= 1 and plan_ev["ts"] > 0


class TestTaskCallbackReport:
    def test_callback_report_flips_state(self, client):
        """回投 protocol:经 graph_svc 建图(根 PENDING)→ 手动 add 一个 RUNNING 子节点(
        模拟引擎已派发)→ POST /callback/report 回投 PASS → 翻 DONE,验证回投 HTTP 端点可达。"""
        c, inj = client
        graph_svc = inj.get(TaskGraphService)
        # 经 graph_svc 建图(根 PENDING),不走 execute(execute 会驱动引擎在 stub 路径把图推到 DONE,
        # 致 add_task_nodes 触发条件 a 失效)。本测聚焦 HTTP 回投端点,非引擎规划逻辑。
        graph_svc.initialize_graph(TaskInfo(
            task_spec=TaskSpec(Metadata("t_http", "T", "i"), Context("bg"),
                               Goal("o", [AcceptanceCriteria("a1", "d1")])),
            source_type="bot", owner_bot_id="owner_bot", execution_config={}))
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
        r = c.post("/api/v1/collaboration/tasks/callback/report", json={
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
