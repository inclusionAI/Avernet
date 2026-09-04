from agentcompute.community.core import DAGNode, DAGPlan, NodeStatus, export_plan


def test_export_plan_writes_html(tmp_path):
    plan = DAGPlan()
    for nid, agent in (("a", "searcher"), ("b", "summarizer")):
        plan.add_node(DAGNode(id=nid, agent=agent))
    plan.add_edge("a", "b")

    path = tmp_path / "dag.html"
    result = export_plan(plan, path)
    assert result == path
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "<svg" in content
    assert "searcher" in content
    assert "summarizer" in content


def test_export_plan_reflects_status_colors(tmp_path):
    plan = DAGPlan()
    node = DAGNode(id="x", agent="agent")
    node.status = NodeStatus.SUCCEEDED
    plan.add_node(node)

    path = tmp_path / "dag.html"
    export_plan(plan, path)
    assert "#4caf50" in path.read_text(encoding="utf-8")


def test_export_plan_empty_dag(tmp_path):
    plan = DAGPlan()
    path = tmp_path / "empty.html"
    export_plan(plan, path)
    assert "svg" in path.read_text(encoding="utf-8")
