"""验收测试:relay 上报 → /callback/report 两次上报(含 trace.steps)→
GET /trajectory 应只返回两次上报的 trace step 轨迹事件。

链路全真(HTTP 入口 + 协议解析 + 图事务 + 写时发射 + 表读回 + DTO);轨迹表用**内存
repo**替真。

语义(2026-10-15 验收约定):
* relay 轨迹的**唯一事实源 = Bot 上报的 trace.steps**——上报时逐条转
  ``task_trajectory_events`` 行(七字段),读取原样拼回;
* 高层动作(apply 记录的 execute/verify/plan/transition)不再落轨迹表;
* 顺序:protocol 要求 EXECUTION_RESULT 先于 PLAN_RESULT(PLAN 无已收执行回执会被
  409 拒),故本测试先发 EXECUTION(样例里的第二次上报),再发 PLAN(验收语义里
  的第一次上报)——两次都成功落账后,timeline 应严格等于 10 条 step(5+5),
  顺序 = EXECUTION steps → PLAN steps。

step 字段映射(2026-10-16 语义复核):``action_type`` = step 码本身(S1_CONTEXT 等,
轨迹主事件标识);``action_input`` = 该步骤输入;``action_result`` = 该步骤实际输出
或产出摘要;``boost_reason`` = 执行过程证据：为什么推进、为什么选择等等维度信息;``error_msg`` = 失败原因;
``status_from/status_to`` = 本步骤前后节点状态。
"""
from __future__ import annotations

import json
from dataclasses import replace as dataclasses_replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.task.router import router as task_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.api.task.task_context_service import (
    TaskContextServiceProtocol,
)
from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextService,
)
from agentclaw.community.core.task.task_center.task_service_relay import (
    TaskServiceRelayMixin,
)
from agentclaw.community.core.task.task_context.relay_report import time as _relay_report_time  # noqa: F401
import agentclaw.community.core.task.task_context.relay_report as relay_report_mod
import agentclaw.community.core.task.task_center.task_service_relay as task_service_relay_mod
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
    Context as SpecContext,
    Goal,
    RuntimeInfo,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    epoch_ms_to_storage_datetime,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
)

TASK_ID = "8cea4ede-c8c0-481c-9879-5eff29ed48e2"
NODE_ID = "relay-ee785eb1"
GENERATION = "gen-ee785eb1"
EXECUTION_EVENT_ID = "0a5ee59b-1820-4459-b6a7-05612046a4e6"
PLAN_EVENT_ID = "fa239f79-e8f8-4fa6-b437-da3e8650d73f"

# EXECUTION_RESULT 的 5 个 step(原 S1~S4B),七字段映射:
#   S1/S2=plan(上下文/缺口,规划前置)、S3=dispatch(承接能力决策)、
#   S4A=execute(实际执行)、S4B=verify(执行回投就绪)。
EXECUTION_STEPS = [
    {
        "action_type": "S1_CONTEXT",
        "action_input": '{"context_endpoint": "/context", "recovery_stage": "fresh"}',
        "action_result": '{"status": "completed", "context_recovered": true, "key_context_summary": "根验收与前序事实已恢复"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "S1 context API 返回有效 TaskContext,烟测门通过", "error_msg": None,
    },
    {
        "action_type": "S2_GAPS",
        "action_input": '{"root_acceptances": ["ac1", "ac2", "ac3", "ac4"], "all_done_output": []}',
        "action_result": '{"status": "completed", "task_gaps": ["ac3_remaining", "ac2", "ac1_remaining", "ac4"]}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "S2 对照根验收与前序事实,识别 4 条剩余缺口", "error_msg": None,
    },
    {
        "action_type": "S3_CAPABILITY",
        "action_input": '{"task_gaps": ["ac3_remaining", "ac2", "ac1_remaining", "ac4"], "identity_evidence": "任务需求助手可产出研究报告"}',
        "action_result": '{"status": "completed", "decision": "accepted", "actual_goal": "完成竞争格局与客户需求分析"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "IDENTITY/AGENTS 职责与当前缺口匹配,承接执行", "error_msg": None,
    },
    {
        "action_type": "S4A_EXECUTION",
        "action_input": '{"actual_goal": "完成竞争格局与客户需求分析", "execution_plan": "读取前序事实并产出阶段二报告"}',
        "action_result": '{"status": "completed", "output_summary": "完成竞争格局+客户需求两大维度分析,产出阶段二报告", "artifacts": ["report-stage-2.md"]}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "关键执行过程完成,产物通过本棒局部验收", "error_msg": None,
    },
    {
        "action_type": "S4B_REPORT",
        "action_input": '{"event_type": "EXECUTION_RESULT", "event_id": "0a5ee59b-1820-4459-b6a7-05612046a4e6"}',
        "action_result": '{"status": "completed", "data_status": "APPLIED", "http_status": 200}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "服务端返回 APPLIED,执行结果已接纳", "error_msg": None,
    },
]

PLAN_STEPS = [
    {
        "action_type": "S5A_REFRESH_GAPS",
        "action_input": '{"root_acceptances": ["ac1", "ac2", "ac3", "ac4"], "accepted_execution_output": "阶段二报告"}',
        "action_result": '{"status": "completed", "refresh_task_gaps": []}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "纳入本棒已接纳事实后,缺口收敛复核无残留", "error_msg": None,
    },
    {
        "action_type": "S5B_PLAN_NEXT_NODE",
        "action_input": '{"refresh_task_gaps": []}',
        "action_result": '{"status": "completed", "next_node": null, "closure": true}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "无剩余缺口,走任务收敛分支", "error_msg": None,
    },
    {
        "action_type": "S5C_SEARCH_CANDIDATES",
        "action_input": '{"search_required": false, "reason": "closure"}',
        "action_result": '{"status": "not_applicable", "search": null, "candidates": []}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "收敛分支没有下一棒,不触发搜索", "error_msg": None,
    },
    {
        "action_type": "S5D_SELECT_EXECUTOR",
        "action_input": '{"candidates": [], "search_required": false}',
        "action_result": '{"status": "not_applicable", "selected_executor": null, "run_mode": null}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "收敛分支没有下一棒,无需选执行者", "error_msg": None,
    },
    {
        "action_type": "S5E_REPORT_PLAN",
        "action_input": '{"event_type": "PLAN_RESULT", "branch": "closure", "event_id": "fa239f79-e8f8-4fa6-b437-da3e8650d73f"}',
        "action_result": '{"status": "completed", "data_status": "APPLIED", "task_completed": true}',
        "status_from": "RUNNING", "status_to": "SUCCESS",
        "boost_reason": "服务端返回 APPLIED 且 task_completed=true,根任务收敛", "error_msg": None,
    },
]

EXECUTION_BODY = {
    "task_id": TASK_ID,
    "node_id": NODE_ID,
    "event_id": EXECUTION_EVENT_ID,
    "relay_token": GENERATION,
    "event_type": "EXECUTION_RESULT",
    "event": {
        "task_id": None,
        "node_id": None,
        "execution_decision": "ACCEPTED",
        "actual_goal": {
            "objective": "完成存储行业尽调报告阶段二:竞争格局深度分析与客户需求维度分析",
            "acceptances": [
                {"id": "ac2", "description": "明确最值得跟踪的细分赛道、公司类型和核心变量"},
                {"id": "ac3_remaining", "description": "提供竞争格局、客户需求两大维度的系统分析"},
                {"id": "ac1_remaining", "description": "明确存储行业当前是否具备中短期投资价值"},
                {"id": "ac4", "description": "至少形成5条核心投资判断"},
            ],
        },
        "output_patch": {
            "result": "已完成存储行业尽调报告阶段二,报告路径: 存储行业尽调报告_阶段二.md",
        },
        "local_acceptance_result": {
            "verdict": "DONE",
            "done_items": [
                {"id": "ac2", "passed": True, "summary": "8条细分赛道排序与12项核心变量矩阵"},
                {"id": "ac3_remaining", "passed": True, "summary": "竞争格局与客户需求双维度系统分析"},
                {"id": "ac1_remaining", "passed": True, "summary": "最终结论:具备结构性投资价值"},
                {"id": "ac4", "passed": True, "summary": "6条核心投资判断"},
            ],
            "gap_items": [],
        },
        "failure_reason": None,
    },
    "trace": {"steps": EXECUTION_STEPS},
}

PLAN_BODY = {
    "task_id": TASK_ID,
    "node_id": NODE_ID,
    "event_id": PLAN_EVENT_ID,
    "relay_token": GENERATION,
    "event_type": "PLAN_RESULT",
    "event": None,
    "failure_reason": None,
    "task_acceptance_result": {
        "verdict": "DONE",
        "done_items": [
            {"id": "ac1", "passed": True, "summary": "中短期结构性投资价值结论"},
            {"id": "ac2", "passed": True, "summary": "细分赛道优先级与公司类型"},
            {"id": "ac3", "passed": True, "summary": "四大维度系统分析覆盖"},
            {"id": "ac4", "passed": True, "summary": "6条核心投资判断"},
        ],
        "gap_items": [],
    },
    "trace": {"steps": PLAN_STEPS},
}


class _Clock:
    """可控时钟:两次上报隔离毫秒区间,保证 timeline 顺序确定。"""

    def __init__(self, start_ms: int = 1_789_000_000_000) -> None:
        self.ms = start_ms

    def time(self) -> float:
        return self.ms / 1000

    def advance_ms(self, n: int) -> None:
        self.ms += n


class _InMemoryTrajectoryRepo:
    """task_trajectory_events / task_trajectory 的内存替真(append-only 行 +
    head 行),判定顺序 ``(gmt_create, id)`` 与真仓储一致。"""

    def __init__(self) -> None:
        self.events: list[TrajectoryEventRecord] = []
        self._seq = 0
        self.heads: dict[str, object] = {}

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self._seq += 1
        record = dataclasses_replace(record, id=self._seq)
        self.events.append(record)
        return record

    def list_events_by_task(self, task_id: str) -> list[TrajectoryEventRecord]:
        rows = [r for r in self.events if r.task_id == task_id]
        return sorted(rows, key=lambda r: (r.gmt_create, r.id))

    def list_head(self, task_id: str):
        return self.heads.get(task_id)

    def upsert_head(self, task_id: str):
        if task_id not in self.heads:
            dt = epoch_ms_to_storage_datetime(0)
            self.heads[task_id] = SimpleNamespace(
                analysis=None, gmt_create=dt, gmt_modified=dt,
            )
        return self.heads[task_id]

    def backfill_analysis(self, task_id: str, analysis_json: str) -> None:  # pragma: no cover
        raise AssertionError("验收场景不走分析回填")


class _Analyzer:
    async def analyze(self, *args, **kwargs):  # pragma: no cover - 纯读场景
        raise AssertionError("验收场景不触发分析")


class _CallbackStub:
    """W1 relay 审计落库替真:记录 ingest 行,验证上报>task_callback 链路。"""

    def __init__(self) -> None:
        self.ingested: list = []

    async def ingest(self, data) -> None:
        self.ingested.append(data)


class _RelayService(TaskServiceRelayMixin):
    """真实 relay 上报服务:真实图事务(TaskGraphService + planted relay graph)
    + 真实轨迹发射门面;仅 task_info 仓储等重依赖替真。"""

    def __init__(self, graph: TaskGraphService, tcs, callback: _CallbackStub):
        self._graph = graph
        self._task_context_service = tcs
        self.callback = callback

    def get_task_dashboard(self, task_id: str, node_id=None):
        return self._graph.query_task_dashboard(task_id, node_id)


def _relay_graph() -> TaskGraphService:
    graph = TaskExecutionGraph(
        task_id=TASK_ID,
        run_id=1,
        loop_round=1,
        status=Status.RUNNING,
        extend_props={
            "execution_config": {
                "orchestration_mode": "relay", "MAX_LOOP": 3,
            },
            "runtime_profile": {"allowed_run_modes": ["single_bot"]},
        },
    )
    graph.tasks.append(TaskNode(
        node_id=NODE_ID,
        task_id=TASK_ID,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            SpecContext(title="阶段二"), Goal("完成阶段二分析", []),
        ),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-holder",
            extend_props={"relay_token": GENERATION},
        ),
        node_run_graph=graph,
    ))
    service = TaskGraphService()
    service._graphs[TASK_ID] = graph
    return service


class _RealModules(Module):
    def __init__(self, service: _RelayService, tcs: TaskContextService) -> None:
        self._service = service
        self._tcs = tcs

    @singleton
    @provider
    def task_service(self) -> TaskServiceProtocol:
        return self._service

    @singleton
    @provider
    def task_context_service(self) -> TaskContextServiceProtocol:
        return self._tcs

    @singleton
    @provider
    def callback_auth(self) -> CallbackAuthenticator:
        return SimpleNamespace()  # type: ignore[return-value]  relay 分支不校验签名

    @singleton
    @provider
    def callback_registry(self) -> CallbackCorrelationRegistry:
        return SimpleNamespace()  # type: ignore[return-value]

    @singleton
    @provider
    def callback_enricher(self) -> CallbackDataEnricher:
        return SimpleNamespace()  # type: ignore[return-value]

    @singleton
    @provider
    def artifact_repo(self) -> "TaskArtifactRepositoryProtocol":
        class _EmptyArtifactRepo:
            def list_by_task(self, task_id, *, limit=200):
                return []

            def list_by_task_node(self, task_id, node_id, *, limit=100):
                return []

        return _EmptyArtifactRepo()


def _build_client(service: _RelayService, tcs) -> TestClient:
    injector = injector_app(service, tcs)
    app = FastAPI()
    app.include_router(task_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "acceptance"}
    attach_injector(app, injector)
    return TestClient(app)


def injector_app(service, tcs):
    from injector import Injector
    return Injector([_RealModules(service, tcs)])


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_two_relay_reports_project_step_timeline(monkeypatch) -> None:
    """两次上报(EXECUTION→PLAN)→ timeline 恰为 10 条 step 行,顺序与字段一致。"""
    repo = _InMemoryTrajectoryRepo()
    graph = _relay_graph()
    clock = _Clock()
    monkeypatch.setattr(relay_report_mod, "time", SimpleNamespace(time=clock.time))
    monkeypatch.setattr(task_service_relay_mod, "time", SimpleNamespace(time=clock.time))

    trajectory = TaskTrajectoryService(
        TaskTrajectoryAssembler(repo), repo, _Analyzer(), None, graph=graph,
    )
    tcs = TaskContextService(trajectory)
    service = _RelayService(graph, tcs, _CallbackStub())
    client = _build_client(service, tcs)

    # ── 第一次上报:EXECUTION_RESULT(协议要求先于 PLAN;正文为验收样例的第二份)──
    resp_exec = client.post(
        "/api/v1/collaboration/tasks/callback/report", json=EXECUTION_BODY,
    )
    assert resp_exec.status_code == 200, resp_exec.text
    assert resp_exec.json()["data"]["status"] == "APPLIED"

    # ── 第二次上报:PLAN_RESULT(收敛分支,即验收样例的第一份)──
    clock.advance_ms(50)
    resp_plan = client.post(
        "/api/v1/collaboration/tasks/callback/report", json=PLAN_BODY,
    )
    assert resp_plan.status_code == 200, resp_plan.text
    assert resp_plan.json()["data"]["status"] == "APPLIED"
    assert resp_plan.json()["data"]["task_completed"] is True

    # ── 轨迹查询:timeline == 两次上报 step 之和(5+5),顺序 = 上报先后 ──
    resp_traj = client.get(
        "/api/v1/collaboration/tasks/trajectory", params={"task_id": TASK_ID},
    )
    assert resp_traj.status_code == 200, resp_traj.text
    timeline = resp_traj.json()["data"]["timeline"]

    expected = EXECUTION_STEPS + PLAN_STEPS
    steps = [ev for ev in timeline if ev["action_type"] != "task_result_display"]
    assert len(steps) == len(expected) == 10, (
        f"step 数量应为两次上报 step 之和(10),实际 {len(steps)}:"
        f" {[e['action_type'] + '/' + str(e['action_result']) for e in steps]}"
    )
    virtuals = [ev for ev in timeline if ev["action_type"] == "task_result_display"]
    assert len(virtuals) == 1, "该子任务应恰有一条虚拟展示事件"
    assert virtuals[0]["output"] is not None
    assert virtuals[0]["actual_run_mode"] == "single_bot"
    assert virtuals[0]["gmt_create"] == steps[-1]["gmt_create"] + 1

    for i, (ev, exp) in enumerate(zip(steps, expected)):
        assert ev["node_id"] == NODE_ID, f"第 {i} 条 node_id 错误"
        assert ev["action_type"] == exp["action_type"], f"第 {i} 条 action_type 不符"
        assert ev["action_result"] == exp["action_result"], f"第 {i} 条 action_result 不符"
        assert ev["action_input"] == exp["action_input"], f"第 {i} 条 action_input 不符"
        assert ev["status_from"] == exp["status_from"], f"第 {i} 条 status_from 不符"
        assert ev["status_to"] == exp["status_to"], f"第 {i} 条 status_to 不符"
        assert ev["boost_reason"] == exp["boost_reason"], f"第 {i} 条 boost_reason 不符"
        assert ev["error_msg"] is None, f"第 {i} 条 error_msg 应为 null"
        assert ev["attempt"] == 0, f"第 {i} 条 attempt 应为 0"

    # 时间单调:EXECUTION 5 条先于 PLAN 5 条(虚拟事件 gmt=末位+1,天然有序)
    stamps = [ev["gmt_create"] for ev in timeline]
    assert stamps == sorted(stamps), "timeline 必须按 gmt_create 升序"

    # ── W1 顺带断言:两次上报各自落了一条 relay 审计行,token 已脱敏 ──
    assert len(service.callback.ingested) == 2
    for ing in service.callback.ingested:
        payload = ing.data
        assert payload["workflow_source"] == "relay"
        assert payload["_raw_callback_body"]["relay_token"] == "***"
    audit_ids = [ing.data["loop_task_id"].split("::")[0] for ing in service.callback.ingested]
    assert set(audit_ids) == {EXECUTION_EVENT_ID, PLAN_EVENT_ID}


# ---------------------------------------------------------------------------
# 多子任务接力链:每棒一对上报(EXECUTION→PLAN),PLAN 分「带下一棒」与「收敛」两种
# ---------------------------------------------------------------------------


def _execution_body(node_id: str, event_id: str, token: str) -> dict:
    body = json.loads(json.dumps(EXECUTION_BODY, ensure_ascii=False))
    body.update(node_id=node_id, event_id=event_id, relay_token=token)
    return body


def _plan_next_body(node_id: str, event_id: str, token: str, assignee: str) -> dict:
    """PLAN_RESULT 的「规划下一棒」分支:event 带未落图节点规格,无 task_acceptance。"""
    return {
        "task_id": TASK_ID,
        "node_id": node_id,
        "event_id": event_id,
        "relay_token": token,
        "event_type": "PLAN_RESULT",
        "trace": {"steps": PLAN_STEPS},
        "event": {
            "task_spec": {
                "context": {"title": "下一棒", "background": "承接上一棒继续推进"},
                "goal": {
                    "objective": "承接上一棒剩余缺口并完成收尾",
                    "acceptances": [{"id": "ac1", "description": "补齐剩余缺口"}],
                },
            },
            "run_info": {"run_mode": "single_bot", "assignee": assignee},
        },
    }


def _closure_body(node_id: str, event_id: str, token: str) -> dict:
    """PLAN_RESULT 的「任务收敛」分支:event=None + task_acceptance_result(DONE)。"""
    body = json.loads(json.dumps(PLAN_BODY, ensure_ascii=False))
    body.update(node_id=node_id, event_id=event_id, relay_token=token)
    return body


def _chain_expected(node_ids: list[str]) -> list[tuple[str, dict]]:
    """期望 timeline:每棒按上报顺序贡献 EXECUTION_STEPS(5) + PLAN_STEPS(5)。"""
    expected: list[tuple[str, dict]] = []
    for node_id in node_ids:
        expected.extend((node_id, step) for step in EXECUTION_STEPS)
        expected.extend((node_id, step) for step in PLAN_STEPS)
    return expected


def _assert_step_timeline(
    timeline: list[dict],
    expected: list[tuple[str, dict]],
    *,
    node_ids: list[str] | None = None,
) -> None:
    """timeline 与期望逐条对齐:数量、(node_id, step) 顺序、七字段一致性、时间单调。

    2026-10-16 起每个有素材(产出/模态/会话)的子任务在自身 timeline 末尾多一条
    ``task_result_display`` 虚拟事件——step 序列比对将其剥离后进行;虚拟事件由
    ``node_ids`` 单独断言(每子任务恰好一条,携 output/actual_run_mode)。"""
    steps = [ev for ev in timeline if ev["action_type"] != "task_result_display"]
    virtuals = [ev for ev in timeline if ev["action_type"] == "task_result_display"]
    assert len(steps) == len(expected), (
        f"step 数量应为 {len(expected)},实际 {len(steps)}:"
        f" {[e['action_type'] for e in steps]}"
    )
    for i, (ev, (node_id, step)) in enumerate(zip(steps, expected)):
        assert ev["node_id"] == node_id, f"第 {i} 条 node_id 错误"
        assert ev["action_type"] == step["action_type"], f"第 {i} 条 action_type 不符"
        assert ev["action_result"] == step["action_result"], f"第 {i} 条 action_result 不符"
        assert ev["action_input"] == step["action_input"], f"第 {i} 条 action_input 不符"
        assert ev["status_from"] == step["status_from"], f"第 {i} 条 status_from 不符"
        assert ev["status_to"] == step["status_to"], f"第 {i} 条 status_to 不符"
        assert ev["boost_reason"] == step["boost_reason"], f"第 {i} 条 boost_reason 不符"
        assert ev["error_msg"] is None, f"第 {i} 条 error_msg 应为 null"
    stamps = [ev["gmt_create"] for ev in timeline]
    assert stamps == sorted(stamps), "timeline 必须按 gmt_create 升序"
    if node_ids is not None:
        # 每个有素材的子任务恰好一条虚拟展示事件,携带产出与真实模态
        by_node = {}
        for ev in virtuals:
            by_node.setdefault(ev["node_id"], []).append(ev)
        assert set(by_node) == set(node_ids), f"虚拟事件节点归属: {sorted(by_node)}"
        for node_id, evs in by_node.items():
            assert len(evs) == 1, f"{node_id} 应恰好 1 条虚拟事件,实际 {len(evs)}"
            assert evs[0]["action_result"] == "display"
            assert evs[0]["output"] is not None, "虚拟事件应携带该子任务当前产出"
            assert evs[0]["actual_run_mode"] == "single_bot"
            assert evs[0]["session_msgs"] is None, "本场景未拉会话明细(纯读默认)"


def _report_cycle(
    client: TestClient,
    clock: _Clock,
    graph: TaskGraphService,
    node_id: str,
    token: str,
    *,
    event_prefix: str,
    next_assignee: str | None,
) -> tuple[str, str] | None:
    """一棒的完整上报闭环:EXECUTION → PLAN(带下一棒或收敛)。

    ``next_assignee=None`` 走收敛分支(任务完成);否则规划下一棒并交付接管
    (直接图翻 RUNNING 模拟 dispatch 投递,免去 delivery intent 基建),返回
    新棒 node_id。每次上报间推进假时钟,保证时间线顺序确定。"""
    clock.advance_ms(10)
    resp = client.post(
        "/api/v1/collaboration/tasks/callback/report",
        json=_execution_body(node_id, f"{event_prefix}-exec", token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "APPLIED"

    clock.advance_ms(10)
    if next_assignee is None:
        resp = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json=_closure_body(node_id, f"{event_prefix}-plan", token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["task_completed"] is True
        return None

    resp = client.post(
        "/api/v1/collaboration/tasks/callback/report",
        json=_plan_next_body(
            node_id, f"{event_prefix}-plan", token, next_assignee,
        ),
    )
    assert resp.status_code == 200, resp.text
    child_id = resp.json()["data"]["target_node_id"]
    assert child_id
    # 交付接管(模拟 dispatch_node 的 PENDING→RUNNING 翻转 + 取新棒 token)
    graph.update_task_node_info(TaskNodePatch(
        task_id=TASK_ID, node_id=child_id, status=Status.RUNNING,
    ))
    child = next(
        n for n in graph.query_task_dashboard(TASK_ID).tasks
        if n.node_id == child_id
    )
    return child_id, child.run_info.extend_props["relay_token"]


def _wire_chain(graph: TaskGraphService, clock: _Clock, monkeypatch):
    """装配链路测试的整套 DI(内存轨迹表 + 真实 service/门面 + 假时钟)。"""
    from agentclaw.community.core.task.task_center import task_service_relay as tsr_mod
    repo = _InMemoryTrajectoryRepo()
    trajectory = TaskTrajectoryService(
        TaskTrajectoryAssembler(repo), repo, _Analyzer(), None, graph=graph,
    )
    tcs = TaskContextService(trajectory)
    service = _RelayService(graph, tcs, _CallbackStub())
    monkeypatch.setattr(relay_report_mod, "time", SimpleNamespace(time=clock.time))
    monkeypatch.setattr(tsr_mod, "time", SimpleNamespace(time=clock.time))
    client = _build_client(service, tcs)
    return repo, tcs, service, client


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_two_subtask_relay_chain_projects_step_timeline(monkeypatch) -> None:
    """两个子任务(两棒接力):棒1 执行+规划下一棒 → 棒2 执行+收敛。
    timeline 应恰为 2×10 条,棒序内 EXECUTION 5 步 + PLAN 5 步,棒间按上报先后。"""
    graph = _relay_graph()
    clock = _Clock()
    _, _, service, client = _wire_chain(graph, clock, monkeypatch)

    # ── 棒1(relay-ee785eb1):执行 → 规划下一棒 ──
    dispatched = _report_cycle(
        client, clock, graph, NODE_ID, GENERATION,
        event_prefix="r1", next_assignee="bot-second",
    )
    child_id, child_token = dispatched

    # ── 注入一条系统侧事件(模拟 harness reset):按验收规则应嵌挂到棒1 id 最近
    #    的前一主事件(S5E)的 system_logs,顶层数量不变。注:发射偏移不推进假时钟,
    #    须先把时钟推过棒1全部步骤区间,注入时刻才落在 S5E 之后 ──
    clock.advance_ms(10)
    service._task_context_service.emit_trajectory_event(
        TASK_ID, NODE_ID, "reset",
        action_result="sla_timeout",
        boost_reason="harness 巡检复位(注入演示)",
        now_ms=clock.ms,
    )

    # ── 棒2:执行 → 收敛(任务完成)──
    _report_cycle(
        client, clock, graph, child_id, child_token,
        event_prefix="r2", next_assignee=None,
    )

    resp = client.get(
        "/api/v1/collaboration/tasks/trajectory", params={"task_id": TASK_ID},
    )
    assert resp.status_code == 200, resp.text
    timeline = resp.json()["data"]["timeline"]
    _assert_step_timeline(timeline, _chain_expected([NODE_ID, child_id]),
                         node_ids=[NODE_ID, child_id])

    # 棒间归属核验:1 号棒 10 条 step、2 号棒 10 条 step(reset 已嵌挂不占顶层);
    # 每棒另各有一条 task_result_display 虚拟展示事件 → 顶层各 11 条
    per_node = {}
    for ev in timeline:
        if ev["action_type"] != "task_result_display":
            per_node[ev["node_id"]] = per_node.get(ev["node_id"], 0) + 1
    assert per_node == {NODE_ID: 10, child_id: 10}

    # 系统事件嵌挂:reset(id 在棒1 S5E 之后)挂最近前主事件 S5E 的 system_logs
    host = next(ev for ev in timeline
                if ev["node_id"] == NODE_ID and ev["action_type"] == "S5E_REPORT_PLAN")
    assert host["system_logs"] is not None
    assert [sub["action_type"] for sub in host["system_logs"]] == ["reset"]
    assert host["system_logs"][0]["action_result"] == "sla_timeout"
    # 其余主事件无嵌挂
    assert all(ev.get("system_logs") is None for ev in timeline if ev is not host)

    # W1:4 次上报各一条审计行
    assert len(service.callback.ingested) == 4


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_three_subtask_relay_chain_projects_step_timeline(monkeypatch) -> None:
    """三个子任务(三棒接力):棒1→棒2 均规划下一棒,棒3 执行+收敛。timeline=3×10。"""
    graph = _relay_graph()
    clock = _Clock()
    _, _, service, client = _wire_chain(graph, clock, monkeypatch)

    chain_nodes: list[str] = []
    node_id, token, seq = NODE_ID, GENERATION, 1
    while True:
        is_last = len(chain_nodes) == 2  # 棒1、棒2 之后,棒3 走收敛
        result = _report_cycle(
            client, clock, graph, node_id, token,
            event_prefix=f"n{seq}", next_assignee=None if is_last else f"bot-{seq + 1}",
        )
        chain_nodes.append(node_id)
        if is_last:
            break
        node_id, token = result
        seq += 1

    assert len(chain_nodes) == 3
    resp = client.get(
        "/api/v1/collaboration/tasks/trajectory", params={"task_id": TASK_ID},
    )
    assert resp.status_code == 200, resp.text
    timeline = resp.json()["data"]["timeline"]
    _assert_step_timeline(timeline, _chain_expected(chain_nodes),
                         node_ids=chain_nodes)

    # 最后一棒收敛:图终态 DONE、根任务完成
    topology = graph.query_task_dashboard(TASK_ID)
    assert topology.status == Status.DONE
    assert len(service.callback.ingested) == 6
