"""Task 4 — BBS 主动触发引擎接线单测(spec §5:engine trigger)。

验证 ``_maybe_propagate_hung`` 命中根 BBS 可恢复态拦截点(``miss_depth_exhausted`` + ``bbs_mode`` + 未 claim)
时,调用 ``_schedule_bbs_notify`` 主动通知 dream-mode bot;以及 ``_schedule_bbs_notify`` 本身的
fire-and-forget 语义(``asyncio.create_task(runner.run_bbs)`` + ``_bg_tasks`` tracking)与端口缺失静默跳过。

可恢复拦截仅对 ``miss_depth_exhausted``;其它 reason(硬死锁)不触发——不在本单测范围(由既有
``test_engine.py`` 覆盖 HUNG 冒泡收口)。
"""
from __future__ import annotations

import asyncio
from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


# ===== domain helpers (mirrors test_engine.py minimal setup) =====
def _task_info(task_id: str = "t1", max_depth: int = 3) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3},
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===== stubs (mirrors test_engine.py) =====
class StubPlanner:
    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None,
                 has_gap_when_empty: bool = True):
        self._factory = factory or (lambda g: [])
        self._has_gap_when_empty = has_gap_when_empty

    async def plan(self, graph, target_node_id: str | None = None) -> PlanResult:
        kids = self._factory(graph)
        return PlanResult(children=kids, has_gap=bool(kids) or self._has_gap_when_empty)


class StubDispatcher:
    def __init__(self, run_mode="single_bot", assignee="bot1", miss=False):
        self.run_mode = run_mode
        self.assignee = assignee
        self.miss = miss

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        out = []
        for n in toDoTaskList:
            if self.miss:
                n.run_info.extend_props["miss_events"] = ["no_bot"]
            else:
                n.run_info.run_mode = self.run_mode
                n.run_info.assignee = self.assignee
            out.append(n)
        return out


class _CaseEngine(ExecutionEngine):
    """测试子类:bot/bcs 留 None(不启 poller 线程),注入 stub planner/dispatcher/runner。"""

    def __init__(self, graph, planner=None, dispatcher=None, runner=None):
        self._case_planner = planner
        self._case_dispatcher = dispatcher
        self._case_runner = runner
        super().__init__(graph)


def _engine(svc, planner=None, dispatcher=None, runner=None):
    return _CaseEngine(
        svc,
        planner=planner or StubPlanner(),
        dispatcher=dispatcher or StubDispatcher(),
        runner=runner,
    )


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


# ===== _schedule_bbs_notify 直测 =====
def test_engine_schedule_bbs_notify_creates_task():
    """_schedule_bbs_notify 经 asyncio.create_task 调度 runner.run_bbs 并登记 _bg_tasks。"""
    # bot/bcs 留 None 构造(不启 poller 线程);再注入 mock 端口避免守卫跳过
    engine = ExecutionEngine(graph=MagicMock(), api_base_url="http://x")
    engine._runner = MagicMock()
    engine._runner.run_bbs = AsyncMock(return_value=None)
    engine._bot = MagicMock()
    engine._bcs = MagicMock()
    fake_g = MagicMock()
    fake_g.task_id = "t1"

    with patch("asyncio.create_task") as mock_create:
        engine._schedule_bbs_notify("t1", fake_g)
        mock_create.assert_called_once()

    # bg 任务登记进 _bg_tasks(由 _on_bg_done 回收)
    assert len(engine._bg_tasks) == 1


def test_engine_schedule_bbs_notify_skips_when_no_runner():
    """无 runner(端口缺失)→ 静默跳过,不抛、不建任务。"""
    engine = ExecutionEngine(graph=MagicMock(), bot=None, bcs=None, api_base_url="")
    engine._runner = None
    # 不抛即可
    engine._schedule_bbs_notify("t1", MagicMock())
    assert engine._bg_tasks == set()


# ===== 可恢复拦截点接线:on_miss → miss_depth_exhausted → _schedule_bbs_notify =====
def test_engine_schedule_bbs_notify_fires_at_recoverable_intercept(svc):
    """on_miss 达 MAX_DEPTH → miss_depth_exhausted → _maybe_propagate_hung 根可恢复态拦截
    (parent-is-root)→ 调用 _schedule_bbs_notify(task_id, graph)。"""
    g = svc.initialize_graph(_task_info("t4", max_depth=1))
    svc.add_task_nodes([_child("c1", "t4")], parent_node_id="t4")
    eng = _engine(svc, planner=StubPlanner(lambda g: []), dispatcher=StubDispatcher(miss=True))

    with patch.object(eng, "_schedule_bbs_notify") as mock_sched:
        _run(eng.on_miss(_patch("t4", "c1", extend_props_patch={"miss_events": ["no_bot"]})))
        mock_sched.assert_called_once()

    # 命中可恢复拦截:根保持 PLANNING(未置图 HUNG)、bbs_mode 已升
    called_args = mock_sched.call_args.args
    assert called_args[0] == "t4"  # task_id
    assert g.extend_props.get("bbs_mode") is True
    assert g.status != Status.HUNG  # 可恢复态不收口图 HUNG


def test_engine_does_not_schedule_bbs_notify_for_non_recoverable_hung(svc):
    """硬死锁 reason(exec_stuck 等)即便 bbs_mode 也不触发 _schedule_bbs_notify——
    走正常 HUNG 冒泡收口(非 miss_depth_exhausted 不进可恢复拦截)。"""
    g = svc.initialize_graph(_task_info("t5", max_depth=1))
    svc.add_task_nodes([_child("c1", "t5")], parent_node_id="t5")
    svc.update_task_node_info(
        _patch("t5", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
    svc.update_task_node_info(_patch("t5", "c1", extend_props_patch={"harness_retries": 2}))
    eng = _engine(svc, planner=StubPlanner(lambda g: []), dispatcher=StubDispatcher())

    with patch.object(eng, "_schedule_bbs_notify") as mock_sched:
        _run(eng.on_harness(_patch("t5", "c1", exec_error="acceptance_fail_retry")))
        mock_sched.assert_not_called()
    # 硬死锁收口:节点 HUNG
    assert svc._get_node(g, "c1").status == Status.HUNG
