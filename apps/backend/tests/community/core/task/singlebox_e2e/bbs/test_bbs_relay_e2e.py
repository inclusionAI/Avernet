"""BBS relay E2E(singlebox 奢验)— spec §6 场景 C/B/D/G。

gated by ``SINGLEBOX_TASK_E2E=1``。与同目录 ``test_task_integration_e2e.py`` 一致用 singlebox e2e 门,
但 BBS relay 机制(C/B/D/G)不依赖跨引擎内容 skill,故全程经 **HTTP facade + 真实 app DI** 驱动
(``TestClient`` over ``task_router`` + ``Injector([TaskModule(), _StubDiscoverModule()])``,
范本 ``test_bbs_{claim,attach,result}_route.py`` 同手法),用 ``bot_id`` 字符串驱动,不建真实 bot。
SSOT 播种经 injector 取 ``TaskGraphService`` 白盒建图/置 bbs_mode/根 PLANNING(与 route 测同手法:
``PENDING→PLANNING`` 不在 ``_DIRECT_TRANSITIONS``,不可经 ``update_task_node_info`` 翻态)。

场景:
- C(claim race):两 bot 同 ``bbs/claim`` 同一 bbs 任务 → 恰一 200、一 409(CAS 输者)。
- B(FAIL-discard relay):botA claim→attach→result(FAIL+gaps)→**scoped 节点被删**(丢弃本次接力尝试,
  不留 FAILED/checkpoint)+ 释放;botB claim→attach(新 scoped 节点)→result(PASS)→scoped SUCCESS + 释放。
  FAIL 即作废本次接力尝试,下段从零做起(不再"部分交棒 checkpoint")。
- D(crash lease):botA claim+attach 后不 result(模拟崩溃)→ harness SLA 到期清根 bbs_owner +
  scoped 标终态 FAILED(非 PENDING 重派)→ botB claim 接管并完成接力。
  D 以服务级 ``TaskHarness``(注入假时钟 + 短 SLA,范本 ``test_bbs_harness_expire.py``)直写同一
  ``TaskGraphService`` SSOT,再经 HTTP facade 断言图态;避免 wall-clock 等待,确定性强。
- G(graph-level HUNG skip):任务图级 HUNG(root_stuck)→ bot ``claim`` 仍可(仅校验 bbs_mode),
  但 ``attach`` 应 409(根 HUNG 非 ``_DELEGATABLE_PARENT`` + ``_assert_add_trigger`` a/b/c/d 均不满足);
  bot 不会把 RUNNING 节点挂到 HUNG 图上。
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router
from agentclaw.community.adapters.http.task.router import router as task_internal_router
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
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_harness.harness import TaskHarness

pytestmark = pytest.mark.skipif(
    os.environ.get("SINGLEBOX_TASK_E2E") != "1",
    reason="需 SINGLEBOX_TASK_E2E=1 singlebox 环境(E2E relay 奢验)",
)


class _StubDiscoverModule(Module):
    """BotDiscover/BotPublic 服务端口 stub:search 返空(端口未激活,不阻断装配)。与 route 测同。"""

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


class _Clock:
    """可手动推进的时钟(单测定确定性;范本 test_bbs_harness_expire)。"""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


def _task_info(task_id: str, *, execution_config: dict | None = None) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config=dict(execution_config) if execution_config else {},
    )


def _seed_bbs_planning(injector: Injector, task_id: str, *, execution_config: dict | None = None) -> None:
    """建图 + 置 bbs_mode=True + 根 PENDING→PLANNING(可委托态)。

    根 PLANNING via 白盒直改:``query_task_dashboard`` 返回 ``_graphs[task_id]`` 同一引用,直接置
    ``.status = PLANNING``。不可经 ``update_task_node_info(status=PLANNING)``——``PENDING→PLANNING``
    不在 ``_DIRECT_TRANSITIONS`` 会抛 TaskStateError。与 task-5/6/8 路由测同手法。
    execution_config 支持 SLA_TIMEOUT / BBS_MAX_DEPTH 注入(供 D harness SLA / B 深度闸)。
    """
    graph_svc = injector.get(TaskGraphService)
    graph_svc.initialize_graph(_task_info(task_id, execution_config=execution_config))
    graph_svc.update_task_graph_info(task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    graph = graph_svc.query_task_dashboard(task_id)
    next(n for n in graph.tasks if n.node_id == task_id).status = Status.PLANNING


def _seed_bbs_hung(injector: Injector, task_id: str) -> None:
    """建图 + bbs_mode + 图级 HUNG + 根 HUNG(白盒直改)。模拟图级 root_stuck:claim 仍可(仅校验
    bbs_mode,不看根/图态),attach 应被拒(根 HUNG 非 _DELEGATABLE_PARENT;_assert_add_trigger
    a/b/c/d 均不满足)。"""
    graph_svc = injector.get(TaskGraphService)
    graph_svc.initialize_graph(_task_info(task_id))
    graph_svc.update_task_graph_info(
        task_id,
        TaskGraphPatch(status=Status.HUNG, extend_props_patch={"bbs_mode": True, "hung_reason": "root_stuck"}),
    )
    graph = graph_svc.query_task_dashboard(task_id)
    next(n for n in graph.tasks if n.node_id == task_id).status = Status.HUNG


def _attach_body(task_id: str, parent_node_id: str, bot_id: str) -> dict:
    """对齐 BbsAttachDTO + TaskSpecDTO 的请求体(scoped 子节点 task_id 每次唯一)。"""
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


def _result_body(
    task_id: str, node_id: str, bot_id: str, *,
    verdict: str = "DONE", gaps: list[str] | None = None,
    output_patch: dict | None = None,
) -> dict:
    """对齐 BbsResultDTO 的请求体。FAIL 强制要求 gaps 非空(验收 skill 契约)。收口由框架自判,无 root_verified。"""
    return {
        "task_id": task_id,
        "node_id": node_id,
        "bot_id": bot_id,
        "acceptance_result": {"verdict": verdict, "acceptances_metric": [], "gaps": gaps or []},
        "output_patch": output_patch,
    }


def _dashboard_tasks(c: TestClient, task_id: str) -> tuple[dict, dict[str, dict]]:
    """GET /openapi/v1/collaboration/tasks/dashboard → (graph_data, {node_id: node_dto})。"""
    data = c.get("/openapi/v1/collaboration/tasks/dashboard", params={"task_id": task_id}).json()["data"]
    return data, {n["node_id"]: n for n in data["tasks"]}


def _root_owner(nodes: dict[str, dict], task_id: str):
    """根节点 extend_props.bbs_owner(None 表示已释放)。"""
    return (nodes[task_id]["run_info"]["extend_props"] or {}).get("bbs_owner")


@pytest.fixture
def client():
    """独立 FastAPI app + test injector(TaskModule + stub discover)。返回 (TestClient, injector)。

    经 TestClient 驱动 HTTP facade 真实 DI(TaskService → ExecutionEngine → TaskGraphService,
    bbs relay 全程 collector-free,不依赖 bot/bcs/discover 端口);经 injector 取 TaskGraphService
    做 SSOT 白盒播种。范本:test_bbs_{claim,attach,result}_route.py(同手法)。
    """
    from agentclaw.community.di.modules.task_module import TaskModule

    injector = Injector([TaskModule(), _StubDiscoverModule()])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def test_c_two_bots_claim_same_bbs_task_exactly_one_wins(client):
    """场景 C:两 bot 同时 POST /bbs/claim 同一 bbs 任务 → 恰一 200、一 409(CAS 输者)。"""
    c, inj = client
    task_id = f"bbs-c-{uuid.uuid4().hex[:6]}"
    _seed_bbs_planning(inj, task_id)
    r1 = c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botA"})
    r2 = c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botB"})
    assert {r1.status_code, r2.status_code} == {200, 409}, f"{r1.status_code}/{r2.status_code} {r1.text} {r2.text}"
    win = r1 if r1.status_code == 200 else r2
    assert win.json()["data"]["root_node_id"] == task_id


def test_b_fail_deletes_scoped_then_next_bot_relays_fresh(client):
    """场景 B:botA claim→attach→result(FAIL+gaps)→**scoped 节点被删**(丢弃本次接力尝试,不留
    FAILED/checkpoint)+ 释放;botB claim→attach(新 scoped 节点)→result(PASS)→scoped SUCCESS + 释放。
    FAIL 即作废本次尝试,下段从零做起(不再"部分交棒 checkpoint")。"""
    c, inj = client
    task_id = f"bbs-b-{uuid.uuid4().hex[:6]}"
    _seed_bbs_planning(inj, task_id, execution_config={"BBS_MAX_DEPTH": 5})

    # botA 接力第一段:claim → attach → result(FAIL+gaps+output_patch checkpoint)→ 释放 claim
    assert c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botA"}).status_code == 200
    r_attach_a = c.post("/api/v1/collaboration/tasks/bbs/attach", json=_attach_body(task_id, task_id, "botA"))
    assert r_attach_a.status_code == 200, r_attach_a.text
    node_a = r_attach_a.json()["data"]["node_id"]
    assert node_a.startswith("bbs-")
    r_fail = c.post("/api/v1/collaboration/tasks/bbs/result", json=_result_body(
        task_id, node_a, "botA", verdict="FAILED", gaps=["need_data"], output_patch={"progress": 30}))
    assert r_fail.status_code == 200, r_fail.text

    # botA 已释放 claim + scoped 节点被删(FAIL→丢弃本次尝试,不翻 FAILED、不 fold checkpoint)
    d1, nodes1 = _dashboard_tasks(c, task_id)
    assert node_a not in nodes1, "FAIL → scoped 节点应被删除,不留 FAILED/checkpoint"
    assert _root_owner(nodes1, task_id) is None, "botA result 后 claim 应已释放"
    assert d1["extend_props"].get("bbs_relay_count") == 1, "botA attach 后 relay_count 应 +1(删节点不回扣)"

    # botB 接力第二段:claim(owner 已清,CAS 成功)→ attach 新 scoped 节点(接力不重做,新 node_id)
    assert c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botB"}).status_code == 200
    r_attach_b = c.post("/api/v1/collaboration/tasks/bbs/attach", json=_attach_body(task_id, task_id, "botB"))
    assert r_attach_b.status_code == 200, r_attach_b.text
    node_b = r_attach_b.json()["data"]["node_id"]
    assert node_b != node_a, "接力应挂新 scoped 节点,不重做 botA 段"
    # botB 续做:PASS → 本 scoped SUCCESS + claim 释放(根收口由框架经 owner 复核自判,非 bot 声明;单测无 owner
    # bot→不收图 SUCCESS,见 natual live 测)。这里验接力机制:接力不重做 + checkpoint 留存 + claim 释放。
    r_pass = c.post("/api/v1/collaboration/tasks/bbs/result", json=_result_body(
        task_id, node_b, "botB", verdict="DONE"))
    assert r_pass.status_code == 200, r_pass.text

    # 终局:botA scoped 已删(FAIL 丢弃,不重做);botB scoped SUCCESS;claim 已释放。
    # (根是否 DONE 由框架复核根 gap 自判;in-process 无 owner bot 不收口,断言略,见 natual live 测)
    d2, nodes2 = _dashboard_tasks(c, task_id)
    assert node_a not in nodes2, "botA scoped 应仍被删(FAIL 丢弃,不重做)"
    assert nodes2[node_b]["status"] == "SUCCESS"
    assert _root_owner(nodes2, task_id) is None, "result 后 claim 应已释放"


def test_d_crash_lease_relay(client):
    """场景 D:botA claim+attach 后不 result(模拟崩溃)→ harness SLA 到期清根 bbs_owner + scoped
    标终态 FAILED(bbs_lease_expired,非 PENDING 重派)→ botB claim 接管并完成接力。

    D 以服务级 ``TaskHarness``(注入假时钟 + 短 SLA,范本 ``test_bbs_harness_expire.py``)直写同一
    ``TaskGraphService`` SSOT,再经 HTTP facade 断言图态;避免 wall-clock 等待 daemon 巡检,确定性强。
    DI 内置 harness(breal 时钟,未 register 本 task)不触及本图,互不干扰。"""
    c, inj = client
    task_id = f"bbs-d-{uuid.uuid4().hex[:6]}"
    _seed_bbs_planning(inj, task_id, execution_config={"SLA_TIMEOUT": 10, "BBS_MAX_DEPTH": 5})

    # botA claim + attach,不 result(模拟崩溃)
    assert c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botA"}).status_code == 200
    r_attach = c.post("/api/v1/collaboration/tasks/bbs/attach", json=_attach_body(task_id, task_id, "botA"))
    assert r_attach.status_code == 200, r_attach.text
    node_a = r_attach.json()["data"]["node_id"]

    # 服务级 harness(假时钟 + 短 SLA)直写同一 graph_svc SSOT
    graph_svc = inj.get(TaskGraphService)
    clock = _Clock(0.0)
    recorder: list = []
    harness = TaskHarness(
        graph_svc, recorder.append,
        clock=clock, sleep=lambda *_: None,
        default_sla_timeout=10.0, default_pending_timeout=10.0, interval=0,
    )
    harness.register(task_id)
    harness._poll_once()      # t=0:首见 RUNNING bbs 节点 → 记时 t0=0(本轮不判)
    clock.advance(11.0)       # t=11 > SLA=10 → lease 到期
    harness._poll_once()      # bbs 到期分支:scoped→FAILED(bbs_lease_expired)+ 清根 owner;不重派

    # 经 HTTP facade 断言图态(bbs_owner 清空 + scoped FAILED 终态,非 PENDING 重派)
    _, nodes = _dashboard_tasks(c, task_id)
    assert _root_owner(nodes, task_id) is None, "harness 到期应清根 bbs_owner 释放接力所有权"
    assert nodes[node_a]["status"] == "FAILED", "scoped 节点应标终态 FAILED(非 PENDING 重派)"
    assert (nodes[node_a]["run_info"]["acceptance_result"] or {})["gaps"] == ["bbs_lease_expired"]
    # harness 不经 on_harness_fn 重派 bbs 节点(recorder 不含 node_a 的 PENDING 复位)
    assert not any(getattr(p, "node_id", None) == node_a for p in recorder), (
        f"bbs 节点不应经 on_harness_fn 重派(标终态不重派): {recorder}")

    # botB claim 接管(owner 已清 → CAS 成功)并完成接力段 → 本 scoped SUCCESS + claim 释放
    # (根收口由框架自判;in-process 无 owner bot 不收图 SUCCESS,见 natual live 测)
    assert c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botB"}).status_code == 200
    r_attach_b = c.post("/api/v1/collaboration/tasks/bbs/attach", json=_attach_body(task_id, task_id, "botB"))
    assert r_attach_b.status_code == 200, r_attach_b.text
    node_b = r_attach_b.json()["data"]["node_id"]
    assert c.post("/api/v1/collaboration/tasks/bbs/result", json=_result_body(
        task_id, node_b, "botB", verdict="DONE")).status_code == 200
    _, nodes_done = _dashboard_tasks(c, task_id)
    assert nodes_done[node_b]["status"] == "SUCCESS"
    assert _root_owner(nodes_done, task_id) is None


def test_g_graph_hung_attach_rejected(client):
    """场景 G:任务图级 HUNG(root_stuck)→ bot ``claim`` 仍可(仅校验 bbs_mode,CAS 真),但 ``attach``
    应 409(根 HUNG 非 _DELEGATABLE_PARENT;_assert_add_trigger a/b/c/d 均不满足 → GraphIntegrityError)。
    bot 不会把 RUNNING 节点挂到 HUNG 图上(图中无 bbs- scoped 节点)。"""
    c, inj = client
    task_id = f"bbs-g-{uuid.uuid4().hex[:6]}"
    _seed_bbs_hung(inj, task_id)

    # claim 仍 200(claim 仅校验 bbs_mode + CAS,不看根/图态)
    r_claim = c.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": task_id, "bot_id": "botA"})
    assert r_claim.status_code == 200, r_claim.text

    # attach 应 409(根 HUNG 非 _DELEGATABLE_PARENT;_assert_add_trigger a/b/c/d 均不满足)
    r_attach = c.post("/api/v1/collaboration/tasks/bbs/attach", json=_attach_body(task_id, task_id, "botA"))
    assert r_attach.status_code == 409, r_attach.text

    # bot 未把任何 RUNNING 节点挂到 HUNG 图上:图仍 HUNG,仅根,无 scoped 子节点附着
    # (task_id 与 scoped node_id 前缀均可能含 'bbs-',故按"无非根节点"判定,不按前缀)
    d, nodes = _dashboard_tasks(c, task_id)
    assert d["status"] == "HUNG"
    assert len(d["tasks"]) == 1, "HUNG 图不应被挂入 scoped 节点"
    assert all(n["node_id"] == task_id for n in d["tasks"]), "HUNG 图不应被挂入 scoped 子节点"
    assert nodes[task_id]["status"] == "HUNG"
