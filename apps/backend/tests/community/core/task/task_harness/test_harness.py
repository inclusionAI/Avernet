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
from agentclaw.community.core.task.task_harness.relay_runtime_policy import resolve_relay_resume_step
from agentclaw.community.core.task.task_harness.relay_session_recovery import (
    RelaySessionPreconditionError,
)
from agentclaw.community.core.task.task_harness.runtime_probe import (
    RuntimeProbeResult,
)


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(task_id=task_id,
        task_spec=TaskSpec(

            context=Context(background="bg", title="T"),
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


class _ScriptedProbe:
    """Return a queue of internal probe observations."""

    def __init__(self, *observations: RuntimeProbeResult):
        self.observations = list(observations)
        self.calls = 0

    async def probe(self, node, previous_cursor=None):
        index = min(self.calls, len(self.observations) - 1)
        self.calls += 1
        return self.observations[index]


class _FakeRelayRecovery:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.nodes = []
        self.resume_steps = []

    async def resume(self, node, *, resume_step=None):
        if self.error is not None:
            raise self.error
        self.nodes.append(node)
        self.resume_steps.append(resume_step)
        from agentclaw.community.core.task.task_harness.relay_session_recovery import (
            RelayResumeResult,
        )
        return RelayResumeResult(
            channel="openapi_bot_session",
            session_id=str(node.run_info.extend_props.get("session_id") or "s1"),
            dispatch_handle="r-resume",
        )


def _runtime_result(state: str, *, progressed: bool = False, **kwargs):
    return RuntimeProbeResult(state=state, progressed=progressed, **kwargs)


class TestHarnessOnlyPollsExecutionLeaves:
    def test_relay_root_is_not_reset(self, svc, graph):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        root = svc._get_node(graph, "t1")
        svc.update_task_node_info(_patch(
            "t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="bot1"
        ))
        root.run_info.extend_props["relay_root"] = True

        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=1.0)
        h.register("t1")
        h._poll_once()
        clock.advance(10.0)

        assert h._poll_once() == []
        assert root.status == Status.RUNNING
        assert rec.patches == []

    def test_relay_successor_is_not_reset_by_pure_timeout(self, svc, graph):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(svc, rec, clock=clock, default_sla_timeout=1.0)
        h.register("t1")
        h._poll_once()
        clock.advance(10.0)
        resets = h._poll_once()

        assert resets == []
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert rec.patches == []

    def test_relay_active_with_progress_is_not_reset(self, svc, graph):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        probe = _ScriptedProbe(_runtime_result("active", progressed=True))
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
        )
        h.register("t1")
        h._poll_once()
        clock.advance(10.0)

        assert h._poll_once() == []
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert probe.calls == 1
        assert rec.patches == []

    def test_relay_runtime_failure_continues_same_session(self, svc, graph):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(
            svc, graph, "c1", run_mode="single_bot", assignee="bot1"
        )
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        node.run_info.extend_props["relay_token"] = "token-1"
        probe = _ScriptedProbe(_runtime_result("terminal_failed"))
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()
        clock.advance(10.0)
        resets = h._poll_once()

        assert resets == []
        assert node.status == Status.RUNNING
        assert node.run_info.run_mode == "single_bot"
        assert node.run_info.assignee == "bot1"
        assert node.run_info.extend_props["relay_token"] == "token-1"
        assert node.run_info.extend_props["relay_harness_resume_attempts"] == 1
        assert rec.patches == []
        assert len(recovery.nodes) == 1

    def test_relay_resume_send_failures_are_bounded(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        probe = _ScriptedProbe(_runtime_result("terminal_failed"))
        recovery = _FakeRelayRecovery(error=RuntimeError("service unavailable"))
        clock = _Clock(0.0)
        h = TaskHarness(
            svc, Recorder(), clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            relay_recovery=recovery,
            relay_resume_max_send_failures=5,
        )
        h.register("t1")
        h._poll_once()

        for i in range(1, 5):
            clock.advance(10.0)
            assert h._poll_once() == []
            seen = h._runtime_seen[("t1", "c1")]
            assert seen["resume_send_failures"] == i
            assert seen["resume_attempts"] == 0
            assert node.status == Status.RUNNING

        clock.advance(10.0)
        patches = h._poll_once()
        assert len(patches) == 1
        assert patches[0].status == Status.HUNG
        assert "relay_harness_transport_exhausted" in patches[0].failure_reason
        assert node.status == Status.HUNG

    def test_relay_precondition_failures_do_not_fill_transport_failures(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        probe = _ScriptedProbe(_runtime_result("terminal_failed"))
        recovery = _FakeRelayRecovery(
            error=RelaySessionPreconditionError(
                "relay node has no existing session_id to resume"
            )
        )
        clock = _Clock(0.0)
        h = TaskHarness(
            svc, Recorder(), clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()

        clock.advance(10.0)
        assert h._poll_once() == []
        seen = h._runtime_seen[("t1", "c1")]
        assert seen["resume_precondition_failures"] == 1
        assert seen["resume_send_failures"] == 0
        assert seen["resume_attempts"] == 0
        assert node.status == Status.RUNNING

        clock.advance(10.0)
        patches = h._poll_once()
        assert len(patches) == 1
        assert patches[0].status == Status.HUNG
        assert "relay_harness_precondition_exhausted" in patches[0].failure_reason
        props = patches[0].extend_props_patch or {}
        assert props["relay_harness_precondition_failures"] == 2
        assert props["harness_probe"]["resume_send_failures"] == 0
        assert node.status == Status.HUNG

    def test_relay_resume_exhaustion_counts_only_delivered_resumes(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        probe = _ScriptedProbe(_runtime_result("terminal_failed"))
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        h = TaskHarness(
            svc, Recorder(), clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()

        clock.advance(10.0)
        assert h._poll_once() == []
        assert node.run_info.extend_props["relay_harness_resume_attempts"] == 1
        clock.advance(10.0)
        assert h._poll_once() == []
        assert node.run_info.extend_props["relay_harness_resume_attempts"] == 2

        clock.advance(10.0)
        patches = h._poll_once()
        assert len(patches) == 1
        assert patches[0].status == Status.HUNG
        assert patches[0].failure_reason == "relay_harness_resume_exhausted: runtime_failed"
        assert node.status == Status.HUNG

    def test_relay_unknown_requires_consecutive_failures_before_resume(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        probe = _ScriptedProbe(_runtime_result("unknown"))
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            runtime_retry_threshold=3, relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()
        clock.advance(10.0)
        assert h._poll_once() == []
        clock.advance(10.0)
        assert h._poll_once() == []
        assert recovery.nodes == []
        clock.advance(10.0)
        resets = h._poll_once()

        assert resets == []
        assert node.status == Status.RUNNING
        assert len(recovery.nodes) == 1

    def test_relay_probe_unavailable_does_not_consume_runtime_recovery(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        node.run_info.extend_props["relay_token"] = "token-1"
        probe = _ScriptedProbe(
            _runtime_result("unknown"),
            _runtime_result("probe_unavailable"),
            _runtime_result("probe_unavailable"),
            _runtime_result("probe_unavailable"),
            _runtime_result("unknown"),
        )
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=1,
            runtime_probe=probe,
            runtime_probe_threshold=1,
            runtime_retry_threshold=3,
            relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()

        for _ in range(4):
            clock.advance(10.0)
            assert h._poll_once() == []

        clock.advance(10.0)
        resets = h._poll_once()

        assert resets == []
        assert node.status == Status.RUNNING
        assert recovery.nodes == []
        assert "relay_harness_resume_attempts" not in node.run_info.extend_props
        assert rec.patches == []

    def test_relay_probe_unavailable_has_a_finite_backstop(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        probe = _ScriptedProbe(_runtime_result("probe_unavailable"))
        clock = _Clock(0.0)
        h = TaskHarness(
            svc, Recorder(), clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            probe_unavailable_max_cycles=2,
        )
        h.register("t1")
        h._poll_once()

        clock.advance(10.0)
        assert h._poll_once() == []
        assert h._runtime_seen[("t1", "c1")]["probe_unavailable"] == 1

        clock.advance(10.0)
        patches = h._poll_once()
        assert len(patches) == 1
        assert patches[0].status == Status.HUNG
        assert patches[0].failure_reason == "relay_harness_probe_unavailable_exhausted"

    def test_relay_active_no_progress_resumes_same_session(self, svc, graph):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        node.run_info.extend_props["relay_token"] = "token-1"
        probe = _ScriptedProbe(_runtime_result("active"))
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            runtime_retry_threshold=3, relay_recovery=recovery,
            active_no_progress_max_cycles=3,
        )
        h.register("t1")
        h._poll_once()

        for _ in range(3):
            clock.advance(1.0)
            assert h._poll_once() == []
            assert recovery.nodes == []
            assert node.status == Status.RUNNING
            assert "relay_harness_resume_attempts" not in node.run_info.extend_props

        clock.advance(1.0)
        resets = h._poll_once()
        assert resets == []
        assert node.status == Status.RUNNING
        assert node.run_info.extend_props["relay_token"] == "token-1"
        assert len(recovery.nodes) == 1
        assert recovery.resume_steps == ["S1-S4a"]
        assert node.run_info.extend_props["relay_harness_resume_attempts"] == 1
        assert h._runtime_seen[("t1", "c1")]["no_progress"] == 0
        assert rec.patches == []

    def test_relay_resume_ignores_unpublished_projection_when_graph_has_no_accepted_phase(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["relay_recovery_projection"] = {
            "schema_version": 1, "recovery_anchor": "S4A_RECONCILE"
        }

        assert resolve_relay_resume_step("t1", node, svc) == "S1-S4a"

    def test_relay_resume_is_step_aware_after_accepted_execution(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        svc.update_task_node_info(
            _patch("t1", "c1", execution_decision="ACCEPTED", actual_goal=Goal(
                objective="done", acceptances=[]
            ))
        )
        node = svc._get_node(graph, "c1")

        assert resolve_relay_resume_step("t1", node, svc) == "S5"

    def test_relay_terminal_success_window_starts_after_observation(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        probe = _ScriptedProbe(_runtime_result("terminal_success"))
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=1,
            runtime_probe=probe, runtime_probe_threshold=1,
            runtime_retry_threshold=3, relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()

        clock.advance(400.0)
        assert h._poll_once() == []
        assert recovery.nodes == []
        seen = h._runtime_seen[("t1", "c1")]
        assert seen["terminal_success_observed_at"] == 400.0
        assert seen["success_unreported"] == 1

        clock.advance(359.0)
        assert h._poll_once() == []
        assert recovery.nodes == []
        assert seen["terminal_success_observed_at"] == 400.0
        assert seen["success_unreported"] == 2

        clock.advance(1.0)
        assert h._poll_once() == []
        assert node.status == Status.RUNNING
        assert len(recovery.nodes) == 1
        assert node.run_info.extend_props["relay_harness_resume_attempts"] == 1
        assert rec.patches == []

    def test_relay_terminal_success_unreported_resumes_same_session(
        self, svc, graph
    ):
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
        _dispatch_running(svc, graph, "c1")
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["session_id"] = "s1"
        probe = _ScriptedProbe(_runtime_result("terminal_success"))
        recovery = _FakeRelayRecovery()
        clock = _Clock(0.0)
        rec = Recorder()
        h = TaskHarness(
            svc, rec, clock=clock, default_sla_timeout=30,
            runtime_probe=probe, runtime_probe_threshold=1,
            runtime_retry_threshold=3, relay_recovery=recovery,
        )
        h.register("t1")
        h._poll_once()

        clock.advance(10.0)
        h._poll_once()
        clock.advance(360.0)
        resets = h._poll_once()

        assert resets == []
        assert node.status == Status.RUNNING
        assert len(recovery.nodes) == 1
        assert recovery.resume_steps == ["S1-S4a"]
        assert node.run_info.extend_props["relay_harness_resume_run_id"] == "r-resume"
        assert rec.patches == []

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
    def test_coop_group_default_timeout_is_fifteen_minutes(self, svc, graph):
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


class TestOnHarnessSafely:
    def test_isolates_each_patch(self, svc, graph):
        class _FailOnceRecorder(Recorder):
            def __call__(self, patch):
                self.patches.append(patch)
                if len(self.patches) == 1:
                    raise RuntimeError("first patch failed")

        rec = _FailOnceRecorder()
        h = TaskHarness(
            svc, rec, clock=_Clock(0.0), default_sla_timeout=1, interval=0,
        )

        h._on_harness_safely(_patch("t1", "c1"))
        h._on_harness_safely(_patch("t1", "c2"))

        assert [p.node_id for p in rec.patches] == ["c1", "c2"]


class TestRunPollLoop:
    def test_poll_once_exception_does_not_kill_daemon(self, svc, graph):
        calls = {"n": 0}
        stop = threading.Event()
        h = TaskHarness(svc, Recorder(), clock=_Clock(0.0), interval=0)

        def fake_poll_once():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("poll cycle failed")
            if calls["n"] >= 2:
                stop.set()

        h._poll_once = fake_poll_once
        h._sleep = lambda dt: None

        h.run_poll_loop(stop)
        assert calls["n"] == 2

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


def test_relay_recovery_is_serialized_per_node_and_releases_on_error(svc, graph):
    import asyncio
    from unittest.mock import patch
    from agentclaw.community.core.task.task_harness import relay_runtime_monitor
    _dispatch_running(svc, graph, "c1")
    node = svc._get_node(graph, "c1")
    h = TaskHarness(svc, Recorder())

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def monitor(*args, **kwargs):
            calls.append(1)
            started.set()
            await release.wait()
            raise RuntimeError("probe failure")

        with patch.object(relay_runtime_monitor, "monitor_relay_runtime", side_effect=monitor):
            first = asyncio.create_task(h._relay_runtime_harness("t1", node, elapsed=1, sla=1))
            await started.wait()
            assert await h._relay_runtime_harness("t1", node, elapsed=1, sla=1) is None
            release.set()
            with pytest.raises(RuntimeError):
                await first
        assert calls == [1]
        assert not h._relay_recovering

    asyncio.run(scenario())


def test_relay_stagnation_uses_elapsed_time_and_survives_probe_outage(svc, graph):
    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    _dispatch_running(svc, graph, "c1")
    node = svc._get_node(graph, "c1")
    node.run_info.extend_props["session_id"] = "s1"
    clock = _Clock(0.0)
    probe = _ScriptedProbe(_runtime_result("active"))
    recovery = _FakeRelayRecovery()
    h = TaskHarness(svc, Recorder(), clock=clock, runtime_probe=probe,
                    runtime_probe_threshold=120, relay_recovery=recovery)
    h.register("t1")
    h._poll_once()
    clock.advance(120)
    h._poll_once()
    probe.observations = [_runtime_result("probe_unavailable")]
    clock.advance(120)
    h._poll_once()
    assert not recovery.nodes
    probe.observations = [_runtime_result("active")]
    clock.advance(479)
    h._poll_once()
    assert not recovery.nodes  # 599 seconds of silence is below the default window.
    clock.advance(1)
    h._poll_once()
    assert len(recovery.nodes) == 1


def test_delivered_resume_has_observation_window_before_second_recovery(svc, graph):
    import asyncio
    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    _dispatch_running(svc, graph, "c1")
    node = svc._get_node(graph, "c1")
    node.run_info.extend_props["session_id"] = "s1"
    clock = _Clock(100)
    recovery = _FakeRelayRecovery()
    h = TaskHarness(svc, Recorder(), clock=clock,
                    runtime_probe=_ScriptedProbe(_runtime_result("terminal_failed")),
                    runtime_probe_threshold=120, relay_recovery=recovery)
    asyncio.run(h._relay_runtime_harness("t1", node, elapsed=100, sla=900))
    clock.advance(1)
    asyncio.run(h._relay_runtime_harness("t1", node, elapsed=101, sla=900))
    assert len(recovery.nodes) == 1


def test_active_silence_exhaustion_does_not_fail_long_running_call(svc, graph):
    import asyncio
    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    _dispatch_running(svc, graph, "c1")
    node = svc._get_node(graph, "c1")
    node.run_info.extend_props["relay_harness_resume_attempts"] = 2
    clock = _Clock(100)
    h = TaskHarness(svc, Recorder(), clock=clock,
                    runtime_probe=_ScriptedProbe(_runtime_result("active")),
                    runtime_probe_threshold=120)
    asyncio.run(h._relay_runtime_harness("t1", node, elapsed=100, sla=900))
    clock.advance(600)
    patch = asyncio.run(h._relay_runtime_harness("t1", node, elapsed=700, sla=900))
    assert patch is None
    assert node.status == Status.RUNNING


def test_activity_query_outage_never_resumes_or_fails_task_and_restarts_window(svc, graph):
    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    _dispatch_running(svc, graph, "c1")
    node = svc._get_node(graph, "c1")
    node.run_info.extend_props["session_id"] = "s1"
    clock = _Clock(0.0)
    probe = _ScriptedProbe(_runtime_result("active", heartbeat_cursor="runtime:one"))
    recovery = _FakeRelayRecovery()
    recorder = Recorder()
    harness = TaskHarness(svc, recorder, clock=clock, runtime_probe=probe,
                          runtime_probe_threshold=120, relay_recovery=recovery)
    harness.register("t1")
    harness._poll_once()
    probe.observations = [_runtime_result("activity_unknown")]
    for _ in range(20):
        clock.advance(600)
        harness._poll_once()
    assert not recovery.nodes
    assert not recorder.patches
    assert node.status == Status.RUNNING
    probe.observations = [_runtime_result("active", heartbeat_cursor="runtime:one")]
    clock.advance(1200)
    harness._poll_once()
    assert not recovery.nodes  # Unobserved time never consumes the quiet window.
    clock.advance(599)
    harness._poll_once()
    assert not recovery.nodes
    clock.advance(1)
    harness._poll_once()
    assert len(recovery.nodes) == 1


def test_activity_generation_reset_restarts_silence_window(svc, graph):
    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    _dispatch_running(svc, graph, "c1")
    clock = _Clock(0.0)
    probe = _ScriptedProbe(_runtime_result("active", baseline_reset=True))
    recovery = _FakeRelayRecovery()
    harness = TaskHarness(svc, Recorder(), clock=clock, runtime_probe=probe,
                          runtime_probe_threshold=120, relay_recovery=recovery)
    harness.register("t1")
    harness._poll_once()
    clock.advance(700)
    harness._poll_once()
    assert not recovery.nodes
    probe.observations = [_runtime_result("active")]
    clock.advance(599)
    harness._poll_once()
    assert not recovery.nodes
    clock.advance(1)
    harness._poll_once()
    assert len(recovery.nodes) == 1


@pytest.mark.parametrize("mode", ["runtime", "fallback", "unavailable"])
def test_real_bot_probe_drives_harness_without_resuming_on_probe_failure(svc, graph, mode):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agentclaw.community.core.task.task_harness.runtime_probe import TaskRuntimeLivenessProbe
    from agentclaw.community.core.task.task_runner.client.ports import BotSessionRuntimeStatus

    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    _dispatch_running(svc, graph, "c1")
    node = svc._get_node(graph, "c1")
    node.run_info.extend_props["session_id"] = "s1"
    bot = SimpleNamespace(
        get_session_runtime_status=AsyncMock(side_effect=TimeoutError("status unavailable")),
        get_session_messages=AsyncMock(return_value=[{"id": "m1", "content": "working"}]),
    )
    if mode == "unavailable":
        bot.get_session_messages.side_effect = TimeoutError("messages unavailable")
    recovery = _FakeRelayRecovery()
    clock = _Clock(0.0)
    recorder = Recorder()
    harness = TaskHarness(svc, recorder, clock=clock, runtime_probe=TaskRuntimeLivenessProbe(bot=bot),
                          runtime_probe_threshold=120, relay_recovery=recovery)
    harness.register("t1")
    harness._poll_once()
    for seq in range(7):
        if mode == "runtime":
            bot.get_session_runtime_status.side_effect = None
            bot.get_session_runtime_status.return_value = BotSessionRuntimeStatus(
                session_id="s1", found=True, run_id="engine-run", state="running",
                started_at=1000, last_event_seq=seq,
            )
        clock.advance(120)
        harness._poll_once()
    assert node.status == Status.RUNNING
    assert not recorder.patches
    assert len(recovery.nodes) == (1 if mode == "fallback" else 0)
    if mode == "runtime":
        bot.get_session_messages.assert_not_awaited()
    elif mode == "unavailable":
        assert harness._runtime_seen[("t1", "c1")]["no_progress"] == 0
        assert "relay_harness_resume_attempts" not in node.run_info.extend_props
