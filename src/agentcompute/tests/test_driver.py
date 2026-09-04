from agentcompute.community.bootstrap import Config, get_container, set_config
from agentcompute.community.core import DAGNode, DAGPlan, Driver
from agentcompute.community.plugins import register_agents, register_plugins
from agentcompute.community.spi import AgentSpec


def _register_two_agents():
    register_plugins()
    set_config(Config(llm_provider="stub", options={"llm": {}}))
    register_agents(
        [
            AgentSpec(name="searcher", role="Searches sources"),
            AgentSpec(name="summarizer", role="Summarizes findings"),
        ]
    )


def test_diamond_dag_runs_in_order_and_succeeds():
    _register_two_agents()
    plan = DAGPlan()
    for nid in ("a", "b", "c"):
        plan.add_node(DAGNode(id=nid, agent="searcher", input={"goal": "g"}))
    plan.add_edge("a", "b")
    plan.add_edge("a", "c")

    result = Driver(max_workers=4).run(plan)
    assert result.succeeded
    assert result.log.statuses["b"].value == "SUCCEEDED"
    assert result.log.statuses["c"].value == "SUCCEEDED"


def test_failure_skips_dependents():
    _register_two_agents()
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="searcher", input={"goal": "g"}))
    plan.add_node(DAGNode(id="b", agent="nonexistent", input={"goal": "g"}))
    plan.add_edge("a", "b")

    result = Driver(max_workers=2).run(plan)
    assert not result.succeeded
    assert result.log.statuses["b"].value == "FAILED"


def test_provider_selection_is_config_driven():
    _register_two_agents()
    assert get_container().config.llm_provider == "stub"
    assert get_container().plugins().llm_provider() is not None
