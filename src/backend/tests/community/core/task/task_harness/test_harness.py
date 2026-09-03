"""M5 TaskHarness 单测(对齐 tasks.md T5.1)。

真实 TaskGraphService 构图;注入可控 clock + 记录型 on_harness_fn。覆盖:
首见记时不复位、超时复位 PENDING patch、未超时不动、复位经编排核 on_harness 端到端重投、register 未登记不巡检、
run_poll_loop stop_event 停止、不抢正向(不直接写 HUNG)。
"""
from __future__ import annotations

import threading

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.domain.models import AcceptanceResult, AcceptanceVerdict
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_harness.harness import TaskHarness


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


class _Clock:
    """可手动推进的时钟(单测定确定性)。"""

    def __init__(self, start: float = 0.0):
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


def _dispatch_running(svc: TaskGraphService, graph, node_id: str, parent: str = "t1",
                      run_mode: str = "single_bot", assignee: str = "bot1") -> None:
    svc.add_task_nodes([_child(node_id)], parent_node_id=parent)
    svc.update_task_node_info(_patch("t1", node_id, status=Status.RUNNING, run_mode=run_mode, assignee=assignee))


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


@pytest.fixture
def graph(svc: TaskGraphService):
    return svc.initialize_graph(_task_info())


class Recorder:
    def __init__(self):
        self.patches: list[TaskNodePatch] = []

    def __call__(self, patch: TaskNodePatch):
        self.patches.append(patch)


class TestHarnessOnlyPollsExecutionLeaves:
    def test_running_parent_with_children_is_not_reset(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        root = svc._get_node(graph, "t1")
        # 构造历史遗留的“结构父节点仍为单 Bot RUNNING”状态。
        root.status = Status.RUNNING
        root.run_info.run_mode = "single_bot"
        root.run_info.assignee = "bot1"

        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=5.0)
        h.register("t1")
        assert h._poll_once() == []
        clock.advance(10.0)

        assert h._poll_once() == []
        assert root.status == Status.RUNNING
        assert rec.patches == []


class TestBbsActualRunMode:
    def test_actual_bbs_override_uses_bbs_lease_expiry_path(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch(
            "t1", "c1", status=Status.RUNNING, run_mode="coop_group", assignee="bot1",
            extend_props_patch={"actual_run_mode": "bbs"},
        ))
        svc.update_task_node_info(_patch(
            "t1", "t1", extend_props_patch={"bbs_owner": "bot1"},
        ))

        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=5.0)
        h.register("t1")
        h._poll_once()
        clock.advance(10.0)
        assert h._poll_once() == []
        assert svc._get_node(graph, "c1").status == Status.DONE
        assert svc._get_node(graph, "t1").run_info.extend_props.get("bbs_owner") is None
        assert rec.patches == []


class TestRetryTimerReset:
    def test_harness_retry_starts_a_fresh_sla_clock(self, svc, graph):
        _dispatch_running(svc, graph, "c1", run_mode="coop_group", assignee="group1")
        clock = _Clock(0.0)

        def retry(patch):
            svc.update_task_node_info(_patch("t1", "c1", status=Status.PENDING))
            svc.update_task_node_info(_patch(
                "t1", "c1", status=Status.RUNNING, run_mode="coop_group", assignee="group1"
            ))

        h = TaskHarness(
            svc, retry, clock=clock, default_sla_timeout=600.0, interval=0
        )
        h.register("t1")
        h._poll_once()
        clock.advance(901.0)
        assert len(h._poll_once()) == 1

        # The retry callback put the node back into RUNNING. The next poll is
        # the first observation of the new attempt, not another timeout.
        clock.advance(1.0)
        assert h._poll_once() == []


class TestCoopGroupTimeout:
    def test_coop_group_default_timeout_is_twelve_minutes(self, svc, graph):
        _dispatch_running(
            svc, graph, "c1", run_mode="coop_group", assignee="group1"
        )
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=600.0, interval=0
        )
        h.register("t1")
        h._poll_once()
        clock.advance(899.0)
        assert h._poll_once() == []
        clock.advance(2.0)
        resets = h._poll_once()
        assert len(resets) == 1
        assert resets[0].node_id == "c1"

    def test_task_sla_override_still_wins_for_coop_group(self, svc, graph):
        graph.extend_props["execution_config"]["SLA_TIMEOUT"] = 30.0
        _dispatch_running(
            svc, graph, "c1", run_mode="coop_group", assignee="group1"
        )
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=600.0)
        h.register("t1")
        h._poll_once()
        clock.advance(31.0)
        assert len(h._poll_once()) == 1


class TestPollOnce:
    def test_first_sight_records_no_reset(self, svc, graph):
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=5.0)
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        resets = h._poll_once()
        assert resets == []  # 首见:记时,不复位
        assert rec.patches == []
        assert ("t1", "c1") in h._dispatched_at

    def test_timeout_resets_to_pending(self, svc, graph):
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=5.0)
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        h._poll_once()  # 记时 t=0
        clock.advance(10.0)  # t=10 > sla=5
        resets = h._poll_once()
        assert len(resets) == 1
        assert resets[0].status == Status.PENDING
        assert resets[0].node_id == "c1"
        assert rec.patches[0].extend_props_patch.get("harness_reset") == "timeout"

    def test_not_timed_out_untouched(self, svc, graph):
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=5.0)
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        h._poll_once()  # t=0
        clock.advance(2.0)  # t=2 < sla=5
        assert h._poll_once() == []
        assert rec.patches == []
        assert svc._get_node(graph, "c1").status == Status.RUNNING

    def test_sla_from_execution_config(self, svc, graph):
        graph.extend_props["execution_config"]["SLA_TIMEOUT"] = 1.0
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock)  # default 30, 但 config 覆盖为 1
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        h._poll_once()  # t=0 记时
        clock.advance(2.0)  # t=2 > config sla=1
        assert len(h._poll_once()) == 1

    def test_reset_evicts_dispatched_at_after_done(self, svc, graph):
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=1.0)
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        h._poll_once()  # 记时
        clock.advance(5.0)
        h._poll_once()  # 复位 c1
        # 模拟节点已 DONE(不再 RUNNING):c1 被 reset 后由编排核重投,这里手动标 DONE
        svc.update_task_node_info(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE)))
        h._poll_once()  # 无 RUNNING → 淘汰记时项
        assert ("t1", "c1") not in h._dispatched_at


class TestRegister:
    def test_unregistered_not_polled(self, svc, graph):
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=1.0)
        _dispatch_running(svc, graph, "c1")
        clock.advance(100.0)
        assert h._poll_once() == []  # 未 register("t1") → 不巡检


class TestNoopFn:
    def test_no_on_harness_returns_empty(self, svc, graph):
        clock = _Clock(0.0)
        h = TaskHarness(svc, clock=clock, default_sla_timeout=1.0)  # 无 on_harness_fn
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        h._poll_once()
        clock.advance(100.0)
        assert h._poll_once() == []  # 无 on_harness_fn → 不复位


class TestDoesNotWriteHung:
    def test_reset_is_pending_not_hung(self, svc, graph):
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=1.0)
        h.register("t1")
        _dispatch_running(svc, graph, "c1")
        h._poll_once()
        clock.advance(100.0)
        resets = h._poll_once()
        assert resets[0].status == Status.PENDING  # 复位 PENDING,不写 HUNG


class TestRunPollLoop:
    def test_stops_on_event(self, svc, graph):
        calls = {"n": 0}

        def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 3:
                stop.set()

        stop = threading.Event()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, sleep=fake_sleep, interval=0.01)
        h.register("t1")
        h.run_poll_loop(stop)
        assert stop.is_set()
        assert calls["n"] >= 3
