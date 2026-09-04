import json

from agentcompute.community import register_plugin_option
from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.core import DAGNode, DAGPlan, Driver, NodeStatus, Planner
from agentcompute.community.plugins import register_agents, register_plugins
from agentcompute.community.spi import Agent, AgentContext, AgentSpec, NodeResult


class _ThrowingTeardownAgent(Agent):
    name = "throwing"

    def setup(self) -> None:
        pass

    def execute(self, ctx: AgentContext) -> NodeResult:
        return NodeResult(node_id=ctx.node_id, output="ok")

    def teardown(self) -> None:
        raise RuntimeError("teardown boom")


class _FlakyAgent(Agent):
    name = "flaky"
    attempts = 0

    def setup(self) -> None:
        pass

    def execute(self, ctx: AgentContext) -> NodeResult:
        type(self).attempts += 1
        if type(self).attempts < 3:
            raise RuntimeError("flaky failure")
        return NodeResult(node_id=ctx.node_id, output="ok")

    def teardown(self) -> None:
        pass


class _AlwaysFailAgent(Agent):
    name = "alwaysfail"

    def setup(self) -> None:
        pass

    def execute(self, ctx: AgentContext) -> NodeResult:
        raise RuntimeError("always fails")

    def teardown(self) -> None:
        pass


class _DictProvider:
    def complete(self, prompt, **kwargs):
        return json.dumps(
            {
                "nodes": {
                    "n1": {"agent": "searcher", "input": {"goal": "g"}},
                    "n2": {"agent": "summarizer", "input": {"goal": "g"}},
                },
                "edges": [["n1", "n2"]],
            }
        )


def _setup(provider="stub"):
    register_plugins()
    register_plugin_option("llm_provider", "dict", _DictProvider)
    set_config(Config(llm_provider=provider, options={"llm": {}}))
    register_agents(
        [
            AgentSpec(name="searcher", role="finds"),
            AgentSpec(name="summarizer", role="summarizes"),
        ]
    )


def test_sequential_mode_runs():
    _setup()
    plan = DAGPlan()
    for nid in ("a", "b"):
        plan.add_node(DAGNode(id=nid, agent="searcher", input={"goal": "g"}))
    plan.add_edge("a", "b")
    result = Driver(max_workers=1).run(plan)
    assert result.succeeded


def test_teardown_exception_does_not_crash():
    _setup()
    register_plugin_option("agent", "throwing", _ThrowingTeardownAgent)
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="throwing", input={"goal": "g"}))
    result = Driver().run(plan)
    assert result.succeeded


def test_skip_cascade_when_dependency_fails():
    _setup()
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="nonexistent", input={"goal": "g"}))
    plan.add_node(DAGNode(id="b", agent="searcher", input={"goal": "g"}))
    plan.add_edge("a", "b")
    result = Driver().run(plan)
    assert not result.succeeded
    assert result.log.statuses["a"] == NodeStatus.FAILED
    assert result.log.statuses["b"] == NodeStatus.SKIPPED


def test_agent_failure_is_retried_until_success():
    _setup()
    register_plugin_option("agent", "flaky", _FlakyAgent)
    _FlakyAgent.attempts = 0
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="flaky", input={"goal": "g"}))
    result = Driver(max_retries=3).run(plan)
    assert result.succeeded
    assert _FlakyAgent.attempts == 3


def test_agent_failure_exhausts_retries_then_fails():
    _setup()
    register_plugin_option("agent", "alwaysfail", _AlwaysFailAgent)
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="alwaysfail", input={"goal": "g"}))
    result = Driver(max_retries=3).run(plan)
    assert not result.succeeded
    assert result.log.statuses["a"] == NodeStatus.FAILED
    assert "always fails" in result.log.errors["a"]


def test_multi_sink_and_single_sink_final_output():
    _setup()
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="searcher", input={"goal": "g"}))
    plan.add_node(DAGNode(id="b", agent="summarizer", input={"goal": "g"}))
    result = Driver().run(plan)
    assert result.succeeded
    assert set(result.final_output.keys()) == {"a", "b"}

    single = DAGPlan()
    single.add_node(DAGNode(id="a", agent="searcher", input={"goal": "g"}))
    single_result = Driver().run(single)
    assert isinstance(single_result.final_output, str)
    assert "[searcher]" in single_result.final_output


def test_progress_callback_is_invoked():
    _setup()
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="searcher", input={"goal": "g"}))
    events = []
    Driver(on_progress=lambda nid, status, progress: events.append((nid, status))).run(plan)
    statuses = [s for _, s in events]
    assert NodeStatus.RUNNING in statuses
    assert NodeStatus.SUCCEEDED in statuses


def test_planner_uses_llm_dag_when_provider_returns_it():
    _setup(provider="dict")
    plan = Planner().plan(
        "g", [AgentSpec(name="searcher", role="f"), AgentSpec(name="summarizer", role="s")]
    )
    assert plan.nodes["n1"].agent == "searcher"
    assert plan.edges == [("n1", "n2")]


def test_planner_falls_back_to_pipeline():
    _setup()
    plan = Planner().plan("g", [AgentSpec(name="searcher", role="f")])
    plan.validate()
    assert "1" in plan.nodes
