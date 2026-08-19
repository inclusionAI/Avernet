"""BBS attach HTTP 路由契约测试(FR-PICK-04):POST /openapi/v1/task/bbs/attach。

独立 TestClient + 小型 test injector(TaskModule + stub discover),不拉起 singlebox 全栈,
不依赖 SINGLEBOX_TASK_E2E=1。验证:claim 持有者 attach 200(返 bbs- node_id);
非持有者 attach 409(TaskStateError)。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    Status,
    TaskGraphPatch,
    TaskInfo,
    TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


class _StubDiscoverModule(Module):
    """BotDiscover/BotPublic 服务端口 stub:search 返空(端口未激活,不阻断装配)。"""

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


@pytest.fixture
def client():
    """独立 FastAPI app + test injector(TaskModule + stub discover)。返回 (TestClient, injector)。"""
    from agentclaw.community.di.modules.task_module import TaskModule

    injector = Injector([TaskModule(), _StubDiscoverModule()])
    app = FastAPI()
    app.include_router(task_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def _bbs_task_planning(injector: Injector, task_id: str) -> None:
    """经 injector 取得 TaskGraphService,建图、置 bbs_mode=True、根 PENDING→PLANNING(可委托态)。

    根 PLANNING via 白盒直改:``query_task_dashboard(node_id=None)`` 返回 ``_graphs[task_id]``
    同一引用,直接置 ``.status = PLANNING``。不可经 ``update_task_node_info(status=PLANNING)``——
    ``PENDING→PLANNING`` 不在 ``_DIRECT_TRANSITIONS`` 会抛 TaskStateError。与 task-5 单测同手法。
    """
    graph_svc = injector.get(TaskGraphService)
    graph_svc.initialize_graph(TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
        execution_config={},
    ))
    graph_svc.update_task_graph_info(task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    graph = graph_svc.query_task_dashboard(task_id)
    root = next(n for n in graph.tasks if n.node_id == task_id)
    root.status = Status.PLANNING


def _attach_body(task_id: str, parent_node_id: str, bot_id: str) -> dict:
    """对齐 BbsAttachDTO + TaskSpecDTO 的请求体。"""
    return {
        "task_id": task_id,
        "parent_node_id": parent_node_id,
        "bot_id": bot_id,
        "task_spec": {
            "metadata": {"task_id": f"bbs-scoped-{task_id}", "title": "s", "instruction": "do"},
            "context": {"background": "", "extend_props": {}},
            "goal": {"objective": "part", "acceptances": [{"id": "a1", "description": "d"}]},
        },
    }


def test_attach_route_creates_node(client):
    """claim 持有者 attach → 200,data.node_id 以 'bbs-' 开头。"""
    c, inj = client
    _bbs_task_planning(inj, "x1")
    r_claim = c.post("/openapi/v1/task/bbs/claim", json={"task_id": "x1", "bot_id": "botA"})
    assert r_claim.status_code == 200, r_claim.text
    r = c.post("/openapi/v1/task/bbs/attach", json=_attach_body("x1", "x1", "botA"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["node_id"].startswith("bbs-")
    assert body["data"]["task_id"] == "x1"


def test_attach_route_non_owner_409(client):
    """非 claim 持有者 attach → 409(TaskStateError)。"""
    c, inj = client
    _bbs_task_planning(inj, "x2")
    r_claim = c.post("/openapi/v1/task/bbs/claim", json={"task_id": "x2", "bot_id": "botA"})
    assert r_claim.status_code == 200, r_claim.text
    r = c.post("/openapi/v1/task/bbs/attach", json=_attach_body("x2", "x2", "botB"))
    assert r.status_code == 409, r.text
