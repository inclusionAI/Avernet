"""BBS claim HTTP 路由契约测试(FR-PICK-02):POST /api/v1/collaboration/tasks/bbs/claim。

独立 TestClient + 小型 test injector(TaskModule + stub discover),不拉起 singlebox 全栈。
验证:首次 claim 200(返 root_node_id);再次 claim 同任务 409(CAS 输者)。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
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
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def _bbs_task(injector: Injector, task_id: str) -> None:
    """经 injector 取得 TaskGraphService,建图并置 bbs_mode=True。"""
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


def test_claim_route_200_then_409(client):
    c, inj = client
    _bbs_task(inj, "r1")
    r1 = c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": "r1", "bot_id": "botA"})
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["code"] == 200000
    assert body["data"]["root_node_id"] == "r1"
    assert body["data"]["task_id"] == "r1"
    r2 = c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": "r1", "bot_id": "botB"})
    assert r2.status_code == 409, r2.text