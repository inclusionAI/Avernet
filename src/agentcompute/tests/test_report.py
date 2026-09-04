from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.core import DAGNode, DAGPlan, Driver, render_report
from agentcompute.community.plugins import register_agents, register_plugins
from agentcompute.community.spi import AgentSpec


def _setup():
    register_plugins()
    register_agents(
        [
            AgentSpec(name="searcher", role="finds"),
            AgentSpec(name="summarizer", role="summarizes"),
            AgentSpec(name="programmer", role="writes code"),
        ]
    )
    set_config(Config(llm_provider="stub", options={"llm": {}}))


def _diamond_plan() -> DAGPlan:
    plan = DAGPlan()
    plan.add_node(DAGNode(id="node-1", agent="searcher", input={"goal": "find sources"}))
    plan.add_node(DAGNode(id="node-2", agent="summarizer", input={"goal": "summarize"}))
    plan.add_node(DAGNode(id="node-3", agent="programmer", input={"goal": "write code"}))
    plan.add_node(DAGNode(id="node-4", agent="summarizer", input={"goal": "merge"}))
    for src, dst in (
        ("node-1", "node-2"),
        ("node-1", "node-3"),
        ("node-2", "node-4"),
        ("node-3", "node-4"),
    ):
        plan.add_edge(src, dst)
    return plan


def test_diamond_dag_executes_all_nodes():
    _setup()
    plan = _diamond_plan()
    result = Driver(max_workers=4).run(plan)
    assert result.succeeded
    assert {nid: s.value for nid, s in result.log.statuses.items()} == {
        "node-1": "SUCCEEDED",
        "node-2": "SUCCEEDED",
        "node-3": "SUCCEEDED",
        "node-4": "SUCCEEDED",
    }
    # sink node-4 receives outputs from both upstream branches
    assert result.final_output is not None


def test_report_contains_full_task_detail():
    _setup()
    plan = _diamond_plan()
    result = Driver(max_workers=4).run(plan)
    html = render_report(
        plan,
        goal="research frameworks",
        final_output=result.final_output,
        succeeded=result.succeeded,
    )
    # request / goal + agent roster
    assert "research frameworks" in html
    assert "searcher" in html
    assert "programmer" in html
    # DAG svg
    assert "<svg" in html
    # node detail: id, agent, input, output, timing
    for nid in ("node-1", "node-2", "node-3", "node-4"):
        assert nid in html
    assert "Started" in html
    assert "Finished" in html
    assert "Final Result" in html
    # every node has a recorded start & finish timestamp
    for node in plan.nodes.values():
        assert node.started_at is not None
        assert node.finished_at is not None
