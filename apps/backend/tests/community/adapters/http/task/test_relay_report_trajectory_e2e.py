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

step 字段映射(旧 ``{step,status,detail}`` → 新七字段,映射约定注记在常量旁):
``action_input = 原始 step 名 + detail 压缩 JSON``、``action_result = 原 status``、
``action_type`` 按 step 阶段取枚举值(plan/dispatch/execute/verify)。
"""
from __future__ import annotations

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
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskExecutionGraph,
    TaskNode,
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
        "action_type": "plan",
        "action_result": "completed",
        "action_input": '{"step": "S1_CONTEXT", "recovery_stage": "fresh", '
                        '"smoke_gate": "checked"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "上下文与恢复阶段确认,烟测门通过", "error_msg": None,
    },
    {
        "action_type": "plan",
        "action_result": "completed",
        "action_input": '{"step": "S2_GAPS", "gaps": ["ac3_remaining", "ac2", '
                        '"ac1_remaining", "ac4"]}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "识别 4 条剩余缺口", "error_msg": None,
    },
    {
        "action_type": "dispatch",
        "action_result": "completed",
        "action_input": '{"step": "S3_CAPABILITY", "decision": "accepted", '
                        '"matched_gaps": ["ac3_remaining", "ac2", '
                        '"ac1_remaining", "ac4"]}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "能力全量匹配缺口,承接执行", "error_msg": None,
    },
    {
        "action_type": "execute",
        "action_result": "completed",
        "action_input": '{"step": "S4A_EXECUTION", "output_summary": '
                        '"完成竞争格局+客户需求两大维度分析,产出阶段二报告"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "阶段二执行完成,正文见 output_patch", "error_msg": None,
    },
    {
        "action_type": "verify",
        "action_result": "ready",
        "action_input": '{"step": "S4B_REPORT", "event_type": "EXECUTION_RESULT"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "EXECUTION_RESULT 就绪,准备上报", "error_msg": None,
    },
]

# PLAN_RESULT(收敛分支)的 5 个 step(原 S5A~S5E),七字段映射:
#   S5A/S5B/S5E=plan、S5C/S5D=dispatch(无下一棒,not_applicable)。
PLAN_STEPS = [
    {
        "action_type": "plan",
        "action_result": "completed",
        "action_input": '{"step": "S5A_REFRESH_GAPS", "refresh_task_gaps": []}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "缺口收敛复核,无残留", "error_msg": None,
    },
    {
        "action_type": "plan",
        "action_result": "completed",
        "action_input": '{"step": "S5B_PLAN_NEXT_NODE", "relay_node": null}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "无下一棒,走任务收敛分支", "error_msg": None,
    },
    {
        "action_type": "dispatch",
        "action_result": "not_applicable",
        "action_input": '{"step": "S5C_SEARCH_CANDIDATES"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "收敛分支无需搜推", "error_msg": None,
    },
    {
        "action_type": "dispatch",
        "action_result": "not_applicable",
        "action_input": '{"step": "S5D_SELECT_EXECUTOR"}',
        "status_from": "RUNNING", "status_to": "RUNNING",
        "boost_reason": "收敛分支无需选执行者", "error_msg": None,
    },
    {
        "action_type": "plan",
        "action_result": "ready",
        "action_input": '{"step": "S5E_REPORT_PLAN", "event_type": '
                        '"PLAN_RESULT", "branch": "closure"}',
        "status_from": "RUNNING", "status_to": "SUCCESS",
        "boost_reason": "PLAN_RESULT 收敛上报就绪,任务完成", "error_msg": None,
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
    assert len(timeline) == len(expected) == 10, (
        f"timeline 数量应为两次上报 step 之和(10),实际 {len(timeline)}:"
        f" {[e['action_type'] + '/' + str(e['action_result']) for e in timeline]}"
    )

    for i, (ev, exp) in enumerate(zip(timeline, expected)):
        assert ev["node_id"] == NODE_ID, f"第 {i} 条 node_id 错误"
        assert ev["action_type"] == exp["action_type"], f"第 {i} 条 action_type 不符"
        assert ev["action_result"] == exp["action_result"], f"第 {i} 条 action_result 不符"
        assert ev["action_input"] == exp["action_input"], f"第 {i} 条 action_input 不符"
        assert ev["status_from"] == exp["status_from"], f"第 {i} 条 status_from 不符"
        assert ev["status_to"] == exp["status_to"], f"第 {i} 条 status_to 不符"
        assert ev["boost_reason"] == exp["boost_reason"], f"第 {i} 条 boost_reason 不符"
        assert ev["error_msg"] is None, f"第 {i} 条 error_msg 应为 null"
        assert ev["attempt"] == 0, f"第 {i} 条 attempt 应为 0"

    # 时间单调:EXECUTION 5 条先于 PLAN 5 条
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