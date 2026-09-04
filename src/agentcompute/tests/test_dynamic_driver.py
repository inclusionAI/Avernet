"""Tests for the DynamicDriver plugin."""

from __future__ import annotations

from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.core import DAGNode, DAGPlan, NodeStatus
from agentcompute.community.plugins import DynamicDriver, register_agents, register_plugins
from agentcompute.community.spi import (
    Agent,
    AgentContext,
    AgentSpec,
    HaltReason,
    NodeResult,
    PlanExtension,
    ReplanError,
    Replanner,
    RunEvent,
)


class _StdAgent(Agent):
    name = "searcher"

    def setup(self) -> None:
        pass

    def execute(self, ctx: AgentContext) -> NodeResult:
        return NodeResult(node_id=ctx.node_id, output=f"ok:{ctx.goal}")

    def teardown(self) -> None:
        pass


class _StubReplanner(Replanner):
    def __init__(self, extensions: list[PlanExtension | ReplanError]) -> None:
        self._extensions = list(extensions)
        self.calls = 0

    def extend(self, goal, agents, prior, results, errors):
        self.calls += 1
        if not self._extensions:
            return PlanExtension(rationale="no-op")
        item = self._extensions.pop(0)
        if isinstance(item, ReplanError):
            raise item
        return item


def _setup() -> None:
    register_plugins()
    register_agents([AgentSpec(name="searcher", role="finds")])
    set_config(Config(llm_provider="stub", options={"llm": {}}))


def _single_node_plan() -> DAGPlan:
    plan = DAGPlan(goal="g", available_agents=["searcher"])
    plan.add_node(DAGNode(id="1", agent="searcher", input={"goal": "do X"}))
    return plan


def test_dynamic_driver_appplies_extension_and_runs_new_node():
    _setup()
    ext = PlanExtension(
        extensions=[DAGNode(id="r1", agent="searcher", input={"goal": "verify X"})],
        new_edges=[("1", "r1")],
        rationale="follow-up",
    )
    replanner = _StubReplanner([ext])
    driver = DynamicDriver(replanner=replanner, replan_strategy="after_wave")
    plan = _single_node_plan()

    events: list[RunEvent] = []
    result = driver.run(plan, on_event=events.append)

    assert result.succeeded
    assert replanner.calls == 2
    assert "r1" in plan.nodes
    assert plan.nodes["r1"].status == NodeStatus.SUCCEEDED
    assert len(plan.extension_history) == 1
    rec = plan.extension_history[0]
    assert rec["rationale"] == "follow-up"
    assert rec["nodes_added"][0]["id"] == "r1"
    assert any(e.kind == "replan_applied" for e in events)
    assert any(e.kind == "replan_called" for e in events)


def test_dynamic_driver_halt_done_stops_run_with_succeeded():
    _setup()
    ext = PlanExtension(
        extensions=[DAGNode(id="r1", agent="searcher", input={"goal": "skip me"})],
        new_edges=[("1", "r1")],
        halt_reason=HaltReason.DONE,
        rationale="goal achieved",
    )
    replanner = _StubReplanner([ext])
    driver = DynamicDriver(replanner=replanner)
    plan = _single_node_plan()
    plan.add_node(DAGNode(id="2", agent="searcher", input={"goal": "extra"}))
    plan.add_edge("1", "2")

    events: list[RunEvent] = []
    result = driver.run(plan, on_event=events.append)

    assert result.succeeded
    assert driver._halt_decision == HaltReason.DONE
    assert plan.nodes["2"].status == NodeStatus.SUCCEEDED
    assert plan.extension_history[0]["halt_reason"] == "done"


def test_dynamic_driver_replan_error_aborts_when_halt_on_error():
    _setup()
    replanner = _StubReplanner([ReplanError("LLM down")])
    driver = DynamicDriver(replanner=replanner, halt_on_replan_error=True)
    plan = _single_node_plan()

    events: list[RunEvent] = []
    result = driver.run(plan, on_event=events.append)

    assert not result.succeeded
    assert driver._halt_decision == HaltReason.ABORT
    assert any(e.kind == "replan_rejected" for e in events)


def test_dynamic_driver_replan_error_continues_when_not_halt_on_error():
    _setup()
    replanner = _StubReplanner([ReplanError("LLM down")])
    driver = DynamicDriver(replanner=replanner, halt_on_replan_error=False)
    plan = _single_node_plan()

    result = driver.run(plan)
    assert result.succeeded
    assert driver._halt_decision is None


def test_replan_max_calls_caps_replan():
    _setup()
    ext = PlanExtension(extensions=[], new_edges=[], rationale="no-op")
    replanner = _StubReplanner([ext])
    driver = DynamicDriver(replanner=replanner, replan_max_calls=1)
    plan = _single_node_plan()

    driver.run(plan)
    assert replanner.calls == 1


def test_transactional_merge_rolls_back_on_invalid_edge():
    _setup()
    ext = PlanExtension(
        extensions=[DAGNode(id="r1", agent="searcher", input={"goal": "x"})],
        new_edges=[("nonexistent", "r1")],
        rationale="bad edge",
    )
    replanner = _StubReplanner([ext])
    driver = DynamicDriver(replanner=replanner)
    plan = _single_node_plan()
    before_nodes = dict(plan.nodes)
    before_edges = list(plan.edges)

    events: list[RunEvent] = []
    driver.run(plan, on_event=events.append)

    assert plan.nodes == before_nodes
    assert plan.edges == before_edges
    assert plan.extension_history == []
    assert any(e.kind == "replan_rejected" for e in events)


def test_extension_history_serializes_through_dagplan_round_trip():
    _setup()
    ext = PlanExtension(
        extensions=[DAGNode(id="r1", agent="searcher", input={"goal": "x"})],
        new_edges=[("1", "r1")],
        rationale="ext",
    )
    replanner = _StubReplanner([ext])
    driver = DynamicDriver(replanner=replanner)
    plan = _single_node_plan()
    driver.run(plan)

    ser = plan.to_full_dict()
    assert "extension_history" in ser
    assert len(ser["extension_history"]) == 1

    restored = DAGPlan.from_full_dict(ser)
    assert len(restored.extension_history) == 1
    assert restored.extension_history[0]["rationale"] == "ext"


def test_replanner_not_called_while_ready_work_exists():
    _setup()
    replanner = _StubReplanner([])
    driver = DynamicDriver(replanner=replanner, replan_strategy="after_wave")
    plan = DAGPlan(goal="g", available_agents=["searcher"])
    plan.add_node(DAGNode(id="1", agent="searcher", input={"goal": "root"}))
    for i in range(2, 5):
        plan.add_node(DAGNode(id=str(i), agent="searcher", input={"goal": f"sub {i}"}))
        plan.add_edge("1", str(i))

    result = driver.run(plan)

    assert result.succeeded
    assert replanner.calls == 1
    assert all(n.status == NodeStatus.SUCCEEDED for n in plan.nodes.values())


def test_replan_min_calls_forces_replan_after_done():
    _setup()
    extensions = [
        PlanExtension(halt_reason=HaltReason.DONE, rationale="goal done v1"),
        PlanExtension(halt_reason=HaltReason.DONE, rationale="goal done v2"),
    ]
    replanner = _StubReplanner(extensions)
    driver = DynamicDriver(replanner=replanner, replan_min_calls=2)
    plan = _single_node_plan()

    events: list[RunEvent] = []
    result = driver.run(plan, on_event=events.append)

    assert result.succeeded
    assert driver._halt_decision == HaltReason.DONE
    assert replanner.calls == 2
    rejected = [e for e in events if e.kind == "replan_rejected"]
    assert rejected and "halt=DONE ignored" in rejected[0].rationale


def test_replan_min_calls_forces_replan_after_noop():
    _setup()
    extensions = [
        PlanExtension(extensions=[], new_edges=[], rationale="nothing to add"),
        PlanExtension(halt_reason=HaltReason.DONE, rationale="goal done"),
    ]
    replanner = _StubReplanner(extensions)
    driver = DynamicDriver(replanner=replanner, replan_min_calls=2)
    plan = _single_node_plan()

    events: list[RunEvent] = []
    result = driver.run(plan, on_event=events.append)

    assert result.succeeded
    assert driver._halt_decision == HaltReason.DONE
    assert replanner.calls == 2


def test_replan_min_calls_zero_lets_done_halt_immediately():
    _setup()
    replanner = _StubReplanner(
        [PlanExtension(halt_reason=HaltReason.DONE, rationale="goal done")]
    )
    driver = DynamicDriver(replanner=replanner)
    plan = _single_node_plan()

    result = driver.run(plan)

    assert result.succeeded
    assert driver._halt_decision == HaltReason.DONE
    assert replanner.calls == 1


def test_replan_min_calls_does_not_override_abort():
    _setup()
    replanner = _StubReplanner(
        [PlanExtension(halt_reason=HaltReason.ABORT, rationale="unrecoverable")]
    )
    driver = DynamicDriver(
        replanner=replanner,
        replan_min_calls=5,
    )
    plan = _single_node_plan()

    result = driver.run(plan)

    assert not result.succeeded
    assert driver._halt_decision == HaltReason.ABORT
    assert replanner.calls == 1