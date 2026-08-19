"""BBS result HTTP 路由契约测试(FR-PICK-05):POST /openapi/v1/task/bbs/result。

独立 TestClient + 小型 test injector(TaskModule + stub discover),不拉起 singlebox 全栈,
不依赖 SINGLEBOX_TASK_E2E=1。验证:
- claim 持有者 report PASS → 200,scoped 节点 DONE + claim 释放(根收口由框架经 owner 复核,单测无 owner bot
  则不收 DONE,见 live e2e)。
- 非 claim 持有者 report → 409(TaskStateError)。
"""
from __future__ import annotations

import uuid

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

    根 PLANNING via 白盒直改:``query_task_dashboard(task_id)`` 返回 ``_graphs[task_id]`` 同一引用,
    直接置 ``.status = PLANNING``。不可经 ``update_task_node_info(status=PLANNING)``——
    ``PENDING→PLANNING`` 不在 ``_DIRECT_TRANSITIONS`` 会抛 TaskStateError。与 task-5/6 单测同手法。
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
    """对齐 BbsAttachDTO + TaskSpecDTO 的请求体(scoped 子节点 task_id 唯一)。"""
    return {
        "task_id": task_id,
        "parent_node_id": parent_node_id,
        "bot_id": bot_id,
        "task_spec": {
            "metadata": {"task_id": f"bbs-scoped-{uuid.uuid4().hex[:6]}", "title": "s", "instruction": "do"},
            "context": {"background": "", "extend_props": {}},
            "goal": {"objective": "part", "acceptances": [{"id": "a1", "description": "d"}]},
        },
    }


def _result_body(task_id: str, node_id: str, bot_id: str) -> dict:
    """对齐 BbsResultDTO 的请求体(PASS 验收)。收口由框架自判,无 root_verified 字段。"""
    return {
        "task_id": task_id,
        "node_id": node_id,
        "bot_id": bot_id,
        "acceptance_result": {"verdict": "PASS", "acceptances_metric": [], "gaps": []},
    }


@pytest.fixture
def bbs_task_with_claimed_node(client):
    """构造 bbs_mode + 根 PLANNING + claim botA + attach scoped RUNNING 节点(经 HTTP 路由)。

    返回 (TestClient, task_id, node_id, botA)。与 task-4/6 路由测同手法:claim/attach 走 HTTP,
    确保路由端到端可用;根 PLANNING 经白盒直改(PENDING→PLANNING 非法态翻,不可经 update 翻)。
    """
    c, inj = client
    task_id = f"bbs-r8-{uuid.uuid4().hex[:6]}"
    _bbs_task_planning(inj, task_id)
    r_claim = c.post("/openapi/v1/task/bbs/claim", json={"task_id": task_id, "bot_id": "botA"})
    assert r_claim.status_code == 200, r_claim.text
    r_attach = c.post("/openapi/v1/task/bbs/attach", json=_attach_body(task_id, task_id, "botA"))
    assert r_attach.status_code == 200, r_attach.text
    node_id = r_attach.json()["data"]["node_id"]
    assert node_id.startswith("bbs-")
    return c, task_id, node_id, "botA"


def test_result_route_pass_marks_scoped_done_and_releases_claim(bbs_task_with_claimed_node):
    """claim 持有者 report PASS → 200,scoped 节点 DONE + claim 释放。根收口由框架经 owner 复核(单测无
    owner bot → 不收 DONE,见 live e2e)。"""
    c, task_id, node_id, bot = bbs_task_with_claimed_node
    r = c.post("/openapi/v1/task/bbs/result", json=_result_body(task_id, node_id, bot))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 200000
    assert body["data"] == {"ok": True}
    # scoped DONE + claim 释放
    d = c.get("/openapi/v1/task/dashboard", params={"task_id": task_id}).json()["data"]
    nodes = {t["node_id"]: t for t in d["tasks"]}
    assert nodes[node_id]["status"] == "DONE"
    root = nodes[task_id]
    assert (root["run_info"]["extend_props"] or {}).get("bbs_owner") is None


def test_result_route_non_owner_409(bbs_task_with_claimed_node):
    """非 claim 持有者 report → 409(TaskStateError;owner 校验抛,不释放他卡)。"""
    c, task_id, node_id, bot = bbs_task_with_claimed_node
    r = c.post("/openapi/v1/task/bbs/result", json=_result_body(task_id, node_id, "botOTHER"))
    assert r.status_code == 409, r.text
