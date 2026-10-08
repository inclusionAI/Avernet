"""Task 4 — BBS 主动触发引擎接线单测(spec §5:engine trigger)。

验证 ``_maybe_propagate_hung`` 将根节点置为 HUNG 后进入 BBS 可恢复态(``miss_depth_exhausted`` + ``bbs_mode`` + 未 claim),
并调用 ``_schedule_bbs_notify`` 主动通知 claim-enabled bot;以及 ``_schedule_bbs_notify`` 本身的
durable-loop fire-and-forget 语义(``asyncio.run_coroutine_threadsafe`` + ``_bg_tasks`` tracking)与端口缺失静默跳过。

BBS 调度仍由根 HUNG 后的统一入口负责;本文件覆盖 miss 与 harness 两类触发。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
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
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner


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
def test_engine_schedule_bbs_notify_submits_to_durable_loop():
    """BBS submission is independent of the caller's short-lived Harness loop."""
    engine = ExecutionEngine(graph=MagicMock(), api_base_url="http://x")
    engine._runner = MagicMock()
    engine._runner.start_run = AsyncMock(return_value=[True])
    engine._bot = MagicMock()
    engine._bcs = MagicMock()
    fake_g = MagicMock()
    fake_g.task_id = "t1"
    root = _child("t1", "t1")
    root.run_info.run_mode = "single_bot"
    root.run_info.assignee = "original-bot"
    root.run_info.extend_props["business"] = {"version": 2}
    fake_g.tasks = [root]
    durable_loop = MagicMock()
    durable_future = concurrent.futures.Future()

    def submit(coro, loop):
        assert loop is durable_loop
        coro.close()
        return durable_future

    with patch.object(engine, "_ensure_bbs_loop", return_value=durable_loop), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=submit) as mock_submit:
        engine._schedule_bbs_notify("t1", fake_g)
        mock_submit.assert_called_once()
        assert len(engine._bg_tasks) == 1
        submitted = engine._runner.start_run.call_args.args[0][0]
        assert submitted is not root
        assert submitted.run_info is not root.run_info
        assert submitted.run_info.run_mode == "bbs"
        assert submitted.run_info.assignee == "original-bot"
        assert submitted.run_info.extend_props == {"business": {"version": 2}}
        assert submitted.run_info.extend_props is not root.run_info.extend_props
        assert root.run_info.run_mode == "single_bot"
        durable_future.set_result(None)

    assert engine._bg_tasks == set()


def test_engine_schedule_bbs_notify_skips_when_root_missing(caplog):
    engine = ExecutionEngine(graph=MagicMock(), api_base_url="http://x")
    engine._runner = MagicMock()
    fake_g = MagicMock()
    fake_g.tasks = [_child("child", "t1")]

    engine._schedule_bbs_notify("t1", fake_g)

    engine._runner.start_run.assert_not_called()
    assert "root missing" in caplog.text


def test_engine_background_false_result_is_visible(caplog):
    engine = ExecutionEngine(graph=MagicMock(), api_base_url="http://x")
    future = concurrent.futures.Future()
    future._bbs_task_id = "t1"
    engine._bg_tasks.add(future)
    future.set_result([False])

    with caplog.at_level("ERROR"):
        engine._on_bg_done(future)

    assert future not in engine._bg_tasks
    assert "background start_run 投递失败" in caplog.text


def test_engine_bbs_survives_caller_loop_shutdown():
    """A caller driven by asyncio.run cannot cancel the durable BBS coroutine."""
    engine = ExecutionEngine(graph=MagicMock(), api_base_url="http://x")
    started = threading.Event()
    finished = threading.Event()

    async def start_run(nodes):
        assert len(nodes) == 1
        assert nodes[0].run_info.run_mode == "bbs"
        started.set()
        await asyncio.sleep(0)
        finished.set()

    engine._runner = MagicMock()
    engine._runner.start_run = start_run
    engine._bot = MagicMock()
    engine._bcs = MagicMock()
    fake_g = MagicMock()
    fake_g.task_id = "t-harness-loop"
    fake_g.tasks = [_child("t-harness-loop", "t-harness-loop")]

    async def schedule_from_short_lived_loop():
        engine._schedule_bbs_notify(fake_g.task_id, fake_g)

    asyncio.run(schedule_from_short_lived_loop())

    assert started.wait(1.0)
    assert finished.wait(1.0)


def test_engine_bbs_schedule_reaches_notify_through_start_run():
    root = _child("t1", "t1")
    fake_g = TaskExecutionGraph(
        run_id=1,
        loop_round=1,
        status=Status.HUNG,
        tasks=[root],
        task_id="t1",
    )
    graph = MagicMock()
    graph.query_task_dashboard.return_value = fake_g
    executor = TaskExecutor(
        bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
        formatter=None, context=None, sink=None, poller=None,
        graph=graph, api_base_url="http://x",
    )
    engine = ExecutionEngine(graph=graph, api_base_url="http://x")
    engine._runner = TaskRunner(graph, execution_backend=executor)
    durable_future = concurrent.futures.Future()
    submitted = {}

    def submit(coro, _loop):
        submitted["coro"] = coro
        return durable_future

    with patch.object(engine, "_ensure_bbs_loop", return_value=MagicMock()), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=submit), \
         patch(
             "agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor.notify",
             new_callable=AsyncMock,
         ) as notify:
        engine._schedule_bbs_notify("t1", fake_g)
        result = _run(submitted["coro"])
        durable_future.set_result(result)

    assert result == [True]
    notify.assert_awaited_once()


def test_engine_schedule_bbs_notify_skips_when_no_runner():
    """无 runner(端口缺失)→ 静默跳过,不抛、不建任务。"""
    engine = ExecutionEngine(graph=MagicMock(), bot=None, bcs=None, api_base_url="")
    engine._runner = None
    # 不抛即可
    engine._schedule_bbs_notify("t1", MagicMock())
    assert engine._bg_tasks == set()


# ===== 根 HUNG 接线:on_miss → miss_depth_exhausted → _schedule_bbs_notify =====
def test_engine_schedule_bbs_notify_fires_at_recoverable_intercept(svc):
    """on_miss 达 MAX_DEPTH → miss_depth_exhausted → 根 HUNG → 调用 _schedule_bbs_notify。"""
    g = svc.initialize_graph(_task_info("t4", max_depth=1))
    svc.add_task_nodes([_child("c1", "t4")], parent_node_id="t4")
    eng = _engine(svc, planner=StubPlanner(lambda g: []), dispatcher=StubDispatcher(miss=True))

    with patch.object(eng, "_schedule_bbs_notify") as mock_sched:
        _run(eng.on_miss(_patch("t4", "c1", extend_props_patch={"miss_events": ["no_bot"]})))
        mock_sched.assert_called_once()

    # 命中统一根 HUNG→BBS 路径:根先置 HUNG,bbs_mode 已升。
    called_args = mock_sched.call_args.args
    assert called_args[0] == "t4"  # task_id
    assert g.extend_props.get("bbs_mode") is True
    assert g.tasks[0].status == Status.HUNG
    assert g.status != Status.HUNG  # graph.status 仍是进行态镜像，根 HUNG 是 BBS 可恢复入口


def test_engine_harness_exhausted_schedules_bbs_recoverable(svc):
    """harness 耗尽(exec_stuck)→节点 HUNG 冒泡到根→根 HUNG 升 BBS 可恢复态。

    exec_stuck 属可恢复 reason:_maybe_propagate_hung 在根处不限 reason(根 HUNG + bbs_mode +
    未 claim → _schedule_bbs_notify),与"根/图 HUNG 升 BBS"设计一致(harness→bbs 回收默认开)。
    """
    g = svc.initialize_graph(_task_info("t5", max_depth=1))
    svc.add_task_nodes([_child("c1", "t5")], parent_node_id="t5")
    svc.update_task_node_info(
        _patch("t5", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
    svc.update_task_node_info(_patch("t5", "c1", extend_props_patch={"harness_retries": 3}))
    eng = _engine(svc, planner=StubPlanner(lambda g: []), dispatcher=StubDispatcher())

    with patch.object(eng, "_schedule_bbs_notify") as mock_sched:
        _run(eng.on_harness(_patch("t5", "c1", exec_error="exec_failed_retry")))
        mock_sched.assert_called_once()  # exec_stuck 可恢复 → 升 BBS
    # 节点仍 HUNG(exec_stuck 收口)
    assert svc._get_node(g, "c1").status == Status.HUNG
